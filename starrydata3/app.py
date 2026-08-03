# -*- coding: utf-8 -*-
"""
Starrydata3 — a private, KMDS-based materials-data service.

The canonical stored object is a full **KMDS record** (the relational schema
sensei's group uses: publication -> samples / figures -> graphs / materials).
Records are validated against the KMDS schema on ingest, stored whole, and the
digitized curves are indexed so the classic property-pair query (Temperature
vs ZT, ...) works — the same value Starrydata2 provides — but writes go through
a proper REST API with API-key auth instead of a session-only web form.

    python -m starrydata3.manage initdb
    python -m starrydata3.manage seedprops
    python -m starrydata3.manage createkey ada --scope write
    uvicorn starrydata3.app:app --host 0.0.0.0 --port 8300

Docs at /docs. Writes require  X-API-Key: <key>.
AutoLineDigitizer's merged record (window.__STUDIO_RECORD) is a valid input.
"""
import hashlib
import json
import os
import secrets
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Dict, List, Optional

from fastapi import Body, Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from sqlmodel import Field, Session, SQLModel, create_engine, select

from starrydata3 import checks, composition, units

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DB_PATH = os.environ.get("SD3_DB", os.path.join(ROOT, "starrydata3.db"))
SCHEMA_PATH = os.environ.get("SD3_SCHEMA", os.path.join(ROOT, "kmds_schema.json"))
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@lru_cache(maxsize=1)
def _raw_kmds_schema() -> Dict[str, Any]:
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def kmds_schema() -> Dict[str, Any]:
    """The Starrydata3 schema: the official KMDS schema PLUS the extensions
    Starrydata3 needs to store digitized curves — (1) `digitization_data` is a
    valid field on a graph (the base schema only has a text `digitization`
    note), and (2) the strict `g-NNN`/`f-NNN` local-id patterns are relaxed to
    accept AutoLineDigitizer's stub ids ("Figure 8"). Everything else is the
    real KMDS schema, so records stay KMDS-conformant where it matters."""
    import copy
    sd = copy.deepcopy(_raw_kmds_schema())
    graph = (sd.get("$defs") or {}).get("graph") or {}
    gp = graph.get("properties")
    if isinstance(gp, dict):
        gp["digitization_data"] = {
            "type": ["object", "null"],
            "description": "digitized curve data (Starrydata3 extension)"}

    def relax(node):
        if isinstance(node, dict):
            props = node.get("properties")
            if isinstance(props, dict):
                for key, val in props.items():
                    if key.endswith("local id") and isinstance(val, dict):
                        val.pop("pattern", None)   # accept non-canonical ids
            for k, v in node.items():
                relax(v)
        elif isinstance(node, list):
            for x in node:
                relax(x)

    relax(sd)
    return sd


def validate_kmds(record: Dict[str, Any]) -> List[str]:
    """Return a list of human-readable schema violations ([] == valid)."""
    try:
        import jsonschema
    except ImportError:
        return []   # validation optional; never block ingest if lib absent
    v = jsonschema.Draft202012Validator(kmds_schema())
    out = []
    for e in sorted(v.iter_errors(record), key=lambda e: list(e.path)):
        loc = "/".join(str(x) for x in e.path) or "(root)"
        out.append(f"{loc}: {e.message}")
    return out[:100]


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

class APIKey(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    key_hash: str = Field(index=True)
    scope: str = "write"                 # read | write | admin
    active: bool = True
    created_at: str = Field(default_factory=_now)


class Record(SQLModel, table=True):
    """One KMDS record (whole JSON kept verbatim)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    sid: Optional[int] = Field(default=None, index=True)
    doi: str = Field(index=True, unique=True)
    title: str = ""
    project: str = "default"
    record_json: str = ""                # the full KMDS record
    n_violations: int = 0
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


class GraphCurve(SQLModel, table=True):
    """A digitized curve, extracted from a record for property-pair queries."""
    id: Optional[int] = Field(default=None, primary_key=True)
    record_sid: int = Field(index=True)
    doi: str = Field(index=True)
    sample_name: str = ""
    composition: str = ""
    prop_x: str = Field(default="", index=True)
    prop_y: str = Field(default="", index=True)
    unit_x: str = ""
    unit_y: str = ""
    points_json: str = "[]"       # points normalized to canonical KMDS units
    raw_points_json: str = "[]"   # original values as digitized
    n_points: int = 0
    kmds: bool = True          # both axis properties are in the KMDS vocabulary
    unit_normalized: bool = False  # x and/or y were converted to canonical units
    comp_key: str = Field(default="", index=True)  # canonical composition key
    elements: str = ""             # space-separated element symbols, sorted


class PointValue(SQLModel, table=True):
    """A single measured property value, extracted from a record's material
    property trees and data tables — the KMDS data most papers carry even when
    nothing was digitized from a plot."""
    id: Optional[int] = Field(default=None, primary_key=True)
    record_sid: int = Field(index=True)
    doi: str = Field(index=True)
    entity: str = ""                 # the material / sample the value belongs to
    source: str = "material"         # material (property tree) | table
    prop: str = Field(default="", index=True)   # canonical KMDS term, or raw name
    raw_prop: str = ""
    category: str = ""               # KMDS property-tree branch, if known
    value: float = 0.0               # in `unit` (canonical where convertible)
    raw_value: float = 0.0
    unit: str = ""
    raw_unit: str = ""
    env_temperature: Optional[float] = None      # measurement temperature, °C as recorded
    kmds: bool = True
    unit_normalized: bool = False


class Property(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    category: str = ""
    default_unit: str = ""


class Provenance(SQLModel, table=True):
    """The audit trail of one digitized figure: the source figure crop, the
    axis calibration used, and which extractor produced it — so every stored
    curve can be traced back to what the curator actually saw."""
    id: Optional[int] = Field(default=None, primary_key=True)
    record_sid: int = Field(index=True)
    doi: str = Field(index=True)
    label: str = ""                 # figure caption/name, e.g. "Fig 3b"
    page: Optional[int] = None
    image_png: bytes = b""          # the digitized figure crop
    calibration_json: str = "{}"    # axis calibration (ticks, log flags, ...)
    extractor_json: str = "{}"      # app + model versions
    curator: str = ""
    created_at: str = Field(default_factory=_now)


class Paper(SQLModel, table=True):
    """The digitization work queue: papers registered by DOI (bibliography
    auto-fetched from CrossRef) and tracked from queued → digitized."""
    id: Optional[int] = Field(default=None, primary_key=True)
    doi: str = Field(index=True, unique=True)
    title: str = ""
    journal: str = ""
    year: Optional[int] = None
    authors: str = ""
    project: str = "default"
    status: str = "queued"          # queued | digitized
    added_at: str = Field(default_factory=_now)


class ReviewSession(SQLModel, table=True):
    """One batch-extracted paper awaiting curator review on the web portal.
    The GPU batch runner pushes these; approving one ingests the (possibly
    curator-edited) record + figures into the real database."""
    id: Optional[int] = Field(default=None, primary_key=True)
    pdf_name: str = ""
    doi: str = Field(default="", index=True)
    title: str = ""
    status: str = "ready"           # ready | in_review | approved
    record_json: str = "{}"         # KMDS record (curator-editable)
    n_figures: int = 0
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)


class ReviewFigure(SQLModel, table=True):
    """One digitized figure inside a review session: the crop image plus
    the editable digitization state (pixel-space curves, axis config)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(index=True)
    idx: int = 0
    label: str = ""
    page: Optional[int] = None
    image_png: bytes = b""
    state_json: str = "{}"          # series_px/series_names/axis_config/...
    status: str = "pending"         # pending | edited | approved | rejected


class QualityFlag(SQLModel, table=True):
    """Result of a cross-property consistency check on one sample's curves
    (checks.py). Recomputed on every ingest of the record."""
    id: Optional[int] = Field(default=None, primary_key=True)
    record_sid: int = Field(index=True)
    sample: str = ""
    check_name: str = ""            # e.g. "ZT = S²σT/κ"
    status: str = "ok"              # ok | warn | fail
    median_dev: float = 0.0         # median relative deviation
    n_points: int = 0
    detail: str = ""
    created_at: str = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def new_api_key() -> str:
    return "sd3_" + secrets.token_urlsafe(32)


_SCOPE_RANK = {"read": 0, "write": 1, "admin": 2}


def require_scope(min_scope: str):
    def dep(x_api_key: str = Header(default="", alias="X-API-Key")) -> APIKey:
        if not x_api_key:
            raise HTTPException(401, "Missing X-API-Key header")
        with Session(engine) as s:
            key = s.exec(select(APIKey).where(
                APIKey.key_hash == hash_key(x_api_key), APIKey.active == True)).first()  # noqa: E712
        if not key:
            raise HTTPException(401, "Invalid or inactive API key")
        if _SCOPE_RANK.get(key.scope, 0) < _SCOPE_RANK[min_scope]:
            raise HTTPException(403, f"API key needs '{min_scope}' scope (has '{key.scope}')")
        return key
    return dep


# ---------------------------------------------------------------------------
# KMDS record helpers
# ---------------------------------------------------------------------------

def _publication(record: Dict[str, Any]) -> Dict[str, Any]:
    return (record.get("metadata") or {}).get("publication") or {}


def _norm_prop(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


# Hand synonyms: alternate wording -> a term that is ACTUALLY in the KMDS
# vocabulary. Every target here is verified to exist; we never self-map a term
# just to make it look conformant. Independent variables that KMDS's
# material-property vocabulary does not define (frequency, voltage, capacity,
# time, cycle number, ...) are intentionally absent, so they stay flagged.
_PROP_SYNONYMS = {
    "temp": "temperature",
    "seebeck": "Seebeck coefficient", "thermopower": "Seebeck coefficient",
    "seebeck coeff": "Seebeck coefficient",
    "figure of merit": "ZT", "thermoelectric figure of merit": "ZT",
    "permittivity": "dielectric constant", "relative permittivity": "dielectric constant",
    "dielectric loss": "loss tangent", "loss factor": "loss tangent",
    "tan delta": "loss tangent", "dissipation factor": "loss tangent",
    "t g": "glass transition temperature", "tg": "glass transition temperature",
    # battery axis wordings -> extension vocabulary terms (seeded from
    # kmds_vocab_extensions.json; honestly categorized "(extension)")
    "amphrs": "capacity", "amp hrs": "capacity", "amp hours": "capacity",
    "ah": "capacity", "mah": "capacity", "amp hr": "capacity",
    "volts": "voltage", "volt": "voltage", "cell voltage": "voltage", "discharge capacity": "capacity",
    "charge capacity": "capacity",
    "ic": "incremental capacity", "dq dv": "incremental capacity",
    "dqdv": "incremental capacity", "power output": "output power",
    "delta t": "temperature difference",
}


def match_property(name: str, vocab_norm: Dict[str, str],
                   unit: str = "") -> Optional[str]:
    """Map an axis/OCR property name onto a canonical KMDS vocabulary term.
    Exact-normalized match, then a hand synonym, then a unique substring hit.
    The unit disambiguates single-letter symbols ('T' + kelvin -> temperature).
    Returns the canonical term, or None if it is not a KMDS property."""
    import re
    n = _norm_prop(name)
    if not n:
        return None
    if n in vocab_norm:
        return vocab_norm[n]
    syn = _PROP_SYNONYMS.get(n)
    if syn:
        return syn
    # ΔT is temperature DIFFERENCE — check before the bare-T rule (the Δ is
    # stripped by normalization, leaving a bare 't')
    if re.match(r"^\s*(Δ|∆|[Dd]elta)\s*T\s*$", (name or "").strip()):
        return vocab_norm.get("temperature difference")
    if n == "t" and re.match(r"^\s*(K|°\s*C|deg\s*C|℃)\s*$", unit or ""):
        return "temperature"
    hits = [canon for k, canon in vocab_norm.items() if n == k or (len(n) > 4 and n in k)]
    return hits[0] if len(set(hits)) == 1 else None


def _placeholder(term: Optional[str]) -> bool:
    return (term or "").strip().upper() in ("", "X", "Y")


def _tick_range(ax):
    t = [v for v in ((ax or {}).get("reference ticks") or []) if isinstance(v, (int, float))]
    return (min(t), max(t)) if len(t) >= 2 else None


def _contain(dig, donor):
    if not dig or not donor:
        return 0.0
    (a, b), (c, d) = dig, donor
    if b <= a:
        return 1.0 if c <= a <= d else 0.0
    span = (d - c) or 1.0
    c, d = c - 0.1 * span, d + 0.1 * span
    return max(0.0, min(b, d) - max(a, c)) / (b - a)


_UNIT_TEX = [(r"\^\{\\circ\}\s*C", "°C"), (r"\\circ", "°"), (r"\\Omega", "Ω"),
             (r"\\cdot", "·"), (r"\\times", "×"), (r"\\mu", "µ"), (r"\\Delta", "Δ")]


def sanitize_unit(unit: Optional[str]) -> str:
    """KMDS units carry LaTeX ('mA\\ h\\ g^{-1}', '^{\\circ}C', '\\Omega'); make
    them plain text for storage + display."""
    import re
    u = unit or "-"
    for pat, rep in _UNIT_TEX:
        u = re.sub(pat, rep, u)
    u = re.sub(r"\^\{([^}]*)\}", r"^\1", u)
    u = re.sub(r"_\{([^}]*)\}", r"_\1", u)
    u = u.replace("\\ ", " ").replace("\\,", " ")
    u = u.replace("{", "").replace("}", "").replace("\\", "")
    u = re.sub(r"\s+", " ", u).strip()
    return u or "-"


def _collect_donors(pub):
    donors = []
    for fig in pub.get("figures") or []:
        for g in fig.get("graphs") or []:
            axes = {str(a.get("axis") or "").lower(): a for a in (g.get("axes") or [])}
            xa, ya = axes.get("x", {}), axes.get("y", {})
            xt = (xa.get("quantity") or {}).get("term")
            yt = (ya.get("quantity") or {}).get("term")
            if _placeholder(xt) or _placeholder(yt):
                continue
            donors.append({"xa": xa, "ya": ya, "xt": xt, "yt": yt,
                           "xr": _tick_range(xa), "yr": _tick_range(ya),
                           "samples": g.get("samples") or [], "used": False})
    return donors


def _match_donor(xr, yr, n_series, donors):
    best, score = None, 0.0
    for d in donors:
        if d["used"] or d["xr"] is None or d["yr"] is None:
            continue
        cx, cy = _contain(xr, d["xr"]), _contain(yr, d["yr"])
        if cx < 0.6 or cy < 0.6:
            continue
        sc = cx + cy + (0.5 if n_series == len(d["samples"]) else 0.0)
        if sc > score:
            best, score = d, sc
    return best


def extract_curves(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Pull digitized curves out of a KMDS record for the property-pair index.
    Graphs whose own axes are real are used directly; curves on placeholder
    (X/Y) stub graphs are matched to a KMDS donor graph by value-range so they
    still inherit real axis identity + samples. Uncalibrated (pixel-space)
    curves match nothing and are skipped."""
    pub = _publication(record)
    sample_names = {}
    for smp in pub.get("samples") or []:
        comps = [c.get("name") for c in (smp.get("components") or []) if c.get("name")]
        sample_names[smp.get("sample local id")] = {
            "name": smp.get("name") or (comps[0] if comps else ""),
            "composition": smp.get("name") or "; ".join(comps),
            "components": comps}
    donors = _collect_donors(pub)
    curves, seen = [], set()

    for fig in pub.get("figures") or []:
        for g in fig.get("graphs") or []:
            dd = g.get("digitization_data")
            if not (dd and dd.get("series")):
                continue
            axes = {str(a.get("axis") or "").lower(): a for a in (g.get("axes") or [])}
            xa, ya = axes.get("x", {}), axes.get("y", {})
            xt = (xa.get("quantity") or {}).get("term")
            yt = (ya.get("quantity") or {}).get("term")
            series = dd["series"]
            allpts = [p for s in series for p in (s.get("points_data") or []) if len(p) >= 2]
            if not allpts:
                continue
            xr = (min(p[0] for p in allpts), max(p[0] for p in allpts))
            yr = (min(p[1] for p in allpts), max(p[1] for p in allpts))
            linked = g.get("samples") or []

            if _placeholder(xt) or _placeholder(yt):
                m = _match_donor(xr, yr, len(series), donors)
                if m is None:
                    continue                         # uncalibrated / no match
                m["used"] = True
                xa, ya, xt, yt = m["xa"], m["ya"], m["xt"], m["yt"]
                linked = m["samples"] or linked

            key = (xt, yt, round(xr[0], 3), round(yr[0], 3), len(series))
            if key in seen:
                continue
            seen.add(key)
            for i, sers in enumerate(series):
                pts = [[p[0], p[1]] for p in (sers.get("points_data") or []) if len(p) >= 2]
                if not pts:
                    continue
                if len(linked) == len(series) and i < len(linked) and linked[i] in sample_names:
                    nm = sample_names[linked[i]]
                    sname, comp = nm["name"], nm["composition"]
                    components = nm["components"]
                else:
                    sname = sers.get("label") or f"Line {i + 1}"
                    comp = sname
                    components = []
                curves.append({"sample_name": sname, "composition": comp,
                               "components": components,
                               "prop_x": xt, "prop_y": yt,
                               "unit_x": sanitize_unit(xa.get("unit")),
                               "unit_y": sanitize_unit(ya.get("unit")),
                               "points": pts})
    return curves


def _value_leaves(node, path=()):
    """Yield (path, value_obj) for every {"value": scalar, ...} leaf in a KMDS
    material property tree. A value object is not recursed into (its
    `measurement` block holds environment fields, not further properties)."""
    if isinstance(node, dict):
        if "value" in node and not isinstance(node.get("value"), (dict, list)):
            yield path, node
            return
        for k, v in node.items():
            yield from _value_leaves(v, path + (k,))
    elif isinstance(node, list):
        for x in node:
            yield from _value_leaves(x, path)


def _split_header_unit(header: str):
    """'T_g (°C)' -> ('T_g', '°C'); also pulls a DMA-style measurement
    temperature: \"E' at 30 °C (MPa)\" -> ('E\\'', 'MPa', 30.0)."""
    import re
    name, unit = header.strip(), ""
    m = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", name)
    if m:
        name, unit = m.group(1).strip(), m.group(2).strip()
    env_t = None
    m = re.search(r"\bat\s+(-?\d+(?:\.\d+)?)\s*°?\s*C\b", name)
    if m:
        env_t = float(m.group(1))
        name = (name[:m.start()] + name[m.end():]).strip()
    return name, unit, env_t


# DMA notation used in table headers; targets verified in the KMDS vocabulary.
_DMA_HEADER = {"E'": "storage modulus", "E''": "loss modulus"}


def _caption_symbol_map(tbl: Dict[str, Any]) -> Dict[str, str]:
    """Papers define their column symbols in the table caption — \"Young's
    modulus (E) and Poisson's ratio (γ)\" — so a header symbol like 'E' or 'γ'
    is resolved through the paper's OWN definition, never a guess."""
    import re
    text = " ".join([tbl.get("table name") or "", tbl.get("caption summary") or "",
                     tbl.get("description") or ""])
    out: Dict[str, str] = {}
    for m in re.finditer(
            r"([A-Za-zΑ-Ωα-ω'’′\- ]{4,}?)\s*\(\s*([^\s()]{1,6})\s*\)", text):
        term = re.sub(r"^\s*(and|or|the|of|for)\s+", "", m.group(1), flags=re.I)
        term = term.strip(" ,;.")
        sym = m.group(2).strip()
        if len(term) >= 4 and term.lower() != sym.lower():
            out.setdefault(sym, term)
    return out


def extract_point_values(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Pull single measured values out of a KMDS record: (1) every value leaf
    of each material's `property` tree, (2) every numeric cell of each data
    table, keyed by the row's label column. These are what a paper contributes
    when its plots were not digitized."""
    out = []
    for mat in record.get("materials") or []:
        entity = mat.get("name") or mat.get("id") or ""
        for path, vo in _value_leaves(mat.get("property") or {}):
            val = vo.get("value")
            if not isinstance(val, (int, float)):
                continue
            meas = vo.get("measurement") or []
            env = (meas[0].get("analysis environment") or {}) if meas and isinstance(meas[0], dict) else {}
            env_t = env.get("temperature")
            out.append({"entity": entity, "source": "material",
                        "raw_prop": path[-1] if path else "", "raw_unit": "",
                        "category": path[0] if path else "",
                        "value": float(val),
                        "env_temperature": float(env_t) if isinstance(env_t, (int, float)) else None})

    pub = _publication(record)
    for tbl in pub.get("tables") or []:
        headers = tbl.get("headers") or []
        sym_map = _caption_symbol_map(tbl)
        for row in tbl.get("rows") or []:
            if not isinstance(row, (list, tuple)) or not row:
                continue
            entity = str(row[0])
            for j, header in enumerate(headers[1:], start=1):
                if j >= len(row) or not isinstance(row[j], (int, float)):
                    continue
                name, unit, env_t = _split_header_unit(str(header))
                name = _DMA_HEADER.get(name) or sym_map.get(name) or name
                out.append({"entity": entity, "source": "table",
                            "raw_prop": name, "raw_unit": unit, "category": "",
                            "value": float(row[j]), "env_temperature": env_t})
    return out


def _normalize_point(value: float, raw_unit: str, canon: str):
    """(value, unit, normalized?) with the °C→K offset units.py can't express."""
    if not canon:
        return value, raw_unit, False
    if not raw_unit:
        return value, canon, False       # KMDS property trees imply canonical units
    if canon == "K" and raw_unit.replace(" ", "") in ("°C", "C", "degC", "℃"):
        return value + 273.15, "K", True
    f, ok = units.convert_factor(raw_unit, canon)
    if ok and f is not None:
        return value * f, canon, f != 1.0
    return value, raw_unit, False


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Starrydata3", version="0.0.1",
              description="Private, KMDS-based materials-data service with API-key writes.")


from fastapi.staticfiles import StaticFiles as _StaticFiles
_dig = os.path.join(os.path.dirname(os.path.abspath(__file__)), "digitizer")
if os.path.isdir(_dig):
    app.mount("/digitizer", _StaticFiles(directory=_dig, html=True),
              name="digitizer")


def init_db():
    SQLModel.metadata.create_all(engine)
    # additive migrations for columns create_all won't add to existing tables
    from sqlalchemy import text
    with engine.connect() as conn:
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(graphcurve)"))}
        for col, decl in (("comp_key", "VARCHAR DEFAULT ''"),
                          ("elements", "VARCHAR DEFAULT ''")):
            if col not in cols:
                conn.execute(text(f"ALTER TABLE graphcurve ADD COLUMN {col} {decl}"))
        conn.commit()


@app.get("/health")
def health():
    with Session(engine) as s:
        n = len(s.exec(select(Record)).all())
    return {"ok": True, "service": "starrydata3", "records": n,
            "schema": os.path.basename(SCHEMA_PATH), "time": _now()}


@app.post("/api/v1/validate")
def validate_only(record: Dict[str, Any] = Body(...),
                  key: APIKey = Depends(require_scope("read"))):
    """Check a KMDS record against the schema WITHOUT storing it."""
    viol = validate_kmds(record)
    return {"valid": not viol, "n_violations": len(viol), "violations": viol,
            "n_curves": len(extract_curves(record))}


def store_record(record: Dict[str, Any], strict: bool = False) -> Dict[str, Any]:
    """Core ingest (no auth): validate, upsert by DOI, normalize + index curves.
    Shared by the /records endpoint and the PDF pipeline. Raises HTTPException
    on a missing DOI or (when strict) schema violations."""
    pub = _publication(record)
    doi = (pub.get("DOI") or "").strip()
    if not doi:
        raise HTTPException(400, "record has no metadata.publication.DOI")
    viol = validate_kmds(record)
    if strict and viol:
        raise HTTPException(422, {"error": "schema violations", "violations": viol})

    with Session(engine) as s:
        rec = s.exec(select(Record).where(Record.doi == doi)).first()
        if rec is None:
            rec = Record(doi=doi)
            s.add(rec); s.commit(); s.refresh(rec)
            rec.sid = 60000 + rec.id
        rec.title = (pub.get("title") or "")[:500]
        rec.project = record.get("project") or rec.project or "default"
        rec.record_json = json.dumps(record, ensure_ascii=False)
        rec.n_violations = len(viol)
        rec.updated_at = _now()
        s.add(rec); s.commit(); s.refresh(rec)
        sid, title = rec.sid, rec.title    # capture before the session closes

        # the paper queue tracks digitization progress by DOI
        qp = s.exec(select(Paper).where(Paper.doi == doi)).first()
        if qp and qp.status != "digitized":
            qp.status = "digitized"
            s.add(qp); s.commit()

        # re-index curves for this record
        for old in s.exec(select(GraphCurve).where(GraphCurve.record_sid == sid)).all():
            s.delete(old)
        curves = extract_curves(record)
        # normalize axis properties to the KMDS vocabulary + convert values to
        # each property's canonical KMDS unit (data unification). Keep raw term
        # / raw values + flags where a match or conversion isn't possible.
        props = s.exec(select(Property)).all()
        vocab_norm = {_norm_prop(p.name): p.name for p in props}
        canon_unit = {p.name: (p.default_unit or "") for p in props}
        non_kmds = set()
        n_kmds = n_norm = 0
        for c in curves:
            cx = match_property(c["prop_x"], vocab_norm, unit=c["unit_x"])
            cy = match_property(c["prop_y"], vocab_norm, unit=c["unit_y"])
            if cx is None:
                non_kmds.add(c["prop_x"])
            if cy is None:
                non_kmds.add(c["prop_y"])
            is_kmds = cx is not None and cy is not None

            ux, uy = c["unit_x"], c["unit_y"]
            fx, fy = 1.0, 1.0
            normed = False
            if cx is not None and canon_unit.get(cx):
                f, ok = units.convert_factor(ux, canon_unit[cx])
                if ok and f is not None and f != 1.0:
                    fx = f; ux = canon_unit[cx]; normed = True
                elif ok:
                    ux = canon_unit[cx]
            if cy is not None and canon_unit.get(cy):
                f, ok = units.convert_factor(uy, canon_unit[cy])
                if ok and f is not None and f != 1.0:
                    fy = f; uy = canon_unit[cy]; normed = True
                elif ok:
                    uy = canon_unit[cy]
            raw = c["points"]
            pts = ([[p[0] * fx, p[1] * fy] for p in raw]
                   if (fx != 1.0 or fy != 1.0) else raw)
            n_kmds += 1 if is_kmds else 0
            n_norm += 1 if normed else 0
            ck, els = "", ""
            for cand in ([c["composition"] or c["sample_name"]]
                         + (c.get("components") or [])):
                ck, els = composition.comp_key(cand)
                if ck:
                    break
            s.add(GraphCurve(record_sid=sid, doi=doi, sample_name=c["sample_name"],
                             composition=c["composition"],
                             prop_x=cx or c["prop_x"], prop_y=cy or c["prop_y"],
                             unit_x=ux, unit_y=uy, kmds=is_kmds, unit_normalized=normed,
                             points_json=json.dumps(pts), raw_points_json=json.dumps(raw),
                             n_points=len(pts), comp_key=ck, elements=els))

        # re-index single measured values (material property trees + tables)
        for old in s.exec(select(PointValue).where(PointValue.record_sid == sid)).all():
            s.delete(old)
        pvs = extract_point_values(record)
        n_kmds_pts = 0
        for p in pvs:
            cp = match_property(p["raw_prop"], vocab_norm, unit=p["raw_unit"])
            if cp is None:
                non_kmds.add(p["raw_prop"])
            else:
                n_kmds_pts += 1
            val, unit, normed = _normalize_point(
                p["value"], p["raw_unit"], canon_unit.get(cp or "", ""))
            s.add(PointValue(record_sid=sid, doi=doi, entity=p["entity"],
                             source=p["source"], prop=cp or p["raw_prop"],
                             raw_prop=p["raw_prop"], category=p["category"],
                             value=val, raw_value=p["value"], unit=unit,
                             raw_unit=p["raw_unit"], env_temperature=p["env_temperature"],
                             kmds=cp is not None, unit_normalized=normed))
        s.commit()

        # cross-property consistency checks (PF = S²σ, ZT = S²σT/κ, ...)
        flags = run_checks(sid, s)

    return {"ok": True, "sid": sid, "doi": doi, "title": title,
            "n_violations": len(viol), "violations": viol[:20],
            "curves_indexed": len(curves),
            "kmds_curves": n_kmds, "unit_normalized_curves": n_norm,
            "points_indexed": len(pvs), "kmds_points": n_kmds_pts,
            "non_kmds_properties": sorted(non_kmds),
            "consistency_flags": flags}


def run_checks(sid: int, s: Session) -> List[Dict[str, Any]]:
    """Recompute a record's consistency flags from its indexed curves and
    persist them (replacing older flags). Returns the flag dicts."""
    rows = s.exec(select(GraphCurve).where(GraphCurve.record_sid == sid)).all()
    flags = checks.check_curves(checks.curves_from_rows(rows))
    for old in s.exec(select(QualityFlag)
                      .where(QualityFlag.record_sid == sid)).all():
        s.delete(old)
    for f in flags:
        s.add(QualityFlag(record_sid=sid, sample=f["sample"],
                          check_name=f["check"], status=f["status"],
                          median_dev=f["median_dev"],
                          n_points=f["n_points"], detail=f["detail"]))
    s.commit()
    return flags


def _digitization_stub(record: Dict[str, Any]) -> bool:
    """A record that carries digitized curves but no publication metadata —
    what the desktop app produces when no KMDS record was loaded. Uploading
    one must enrich an existing stored record, never clobber it."""
    pub = _publication(record)
    has_dig = any(g.get("digitization_data")
                  for f in pub.get("figures") or []
                  for g in f.get("graphs") or [])
    has_meta = bool(pub.get("title") or pub.get("abstract")
                    or pub.get("samples") or record.get("materials"))
    return has_dig and not has_meta


@app.post("/api/v1/records")
def submit_record(record: Dict[str, Any] = Body(...),
                  strict: bool = False,
                  key: APIKey = Depends(require_scope("write"))):
    """Ingest a full KMDS record: validate, upsert by DOI, index its curves.
    strict=true rejects a record with any schema violation (422). A
    digitization-only stub (curves, no metadata) whose DOI already exists is
    MERGED into the stored record instead of replacing it."""
    if _digitization_stub(record):
        record = merge_digitized(record)
    return store_record(record, strict=strict)


# ---------------------------------------------------------------------------
# Digitization uploads come FROM AutoLineDigitizer (the desktop app does the
# digitizing); the guard below keeps them from damaging stored records.
# ---------------------------------------------------------------------------
def merge_digitized(record: Dict[str, Any]) -> Dict[str, Any]:
    """A digitization-only record whose DOI is already in the database is
    GRAFTED into the stored record — its figures and samples are appended
    (ids remapped to avoid collisions) so digitizing a paper's PDF enriches
    the existing KMDS record instead of replacing it."""
    pub = _publication(record)
    doi = (pub.get("DOI") or "").strip()
    if not doi:
        return record
    with Session(engine) as s:
        rec = s.exec(select(Record).where(Record.doi == doi)).first()
    if rec is None:
        return record
    existing = json.loads(rec.record_json)
    epub = existing.setdefault("metadata", {}).setdefault("publication", {})

    taken = {smp.get("sample local id") for smp in epub.get("samples") or []}
    remap: Dict[str, str] = {}
    n = 0
    for smp in pub.get("samples") or []:
        old = smp.get("sample local id")
        new = old
        while new in taken:
            n += 1
            new = f"s-d{n:02d}"
        taken.add(new)
        remap[old] = new
        smp["sample local id"] = new
    epub["samples"] = (epub.get("samples") or []) + (pub.get("samples") or [])

    offset = len(epub.get("figures") or [])
    for i, fig in enumerate(pub.get("figures") or [], start=1):
        fig["figure local id"] = f"f-{offset + i:03d}"
        for g in fig.get("graphs") or []:
            g["samples"] = [remap.get(x, x) for x in (g.get("samples") or [])]
    epub["figures"] = (epub.get("figures") or []) + (pub.get("figures") or [])
    return existing


@app.get("/api/v1/records")
def list_records(key: APIKey = Depends(require_scope("read"))):
    with Session(engine) as s:
        recs = s.exec(select(Record)).all()
    return {"count": len(recs),
            "records": [{"sid": r.sid, "doi": r.doi, "title": r.title,
                         "n_violations": r.n_violations} for r in recs]}


@app.get("/api/v1/records/{sid}")
def get_record(sid: int, key: APIKey = Depends(require_scope("read"))):
    with Session(engine) as s:
        rec = s.exec(select(Record).where(Record.sid == sid)).first()
        if not rec:
            raise HTTPException(404, f"no record with sid {sid}")
        return json.loads(rec.record_json)


@app.get("/api/v1/graphs")
def graphs(prop_x: str, prop_y: str, key: APIKey = Depends(require_scope("read"))):
    """Property-pair query — every digitized curve of prop_x vs prop_y across
    all records (the classic Starrydata bulk-data view)."""
    with Session(engine) as s:
        rows = s.exec(select(GraphCurve).where(
            GraphCurve.prop_x == prop_x, GraphCurve.prop_y == prop_y)).all()
    return {"prop_x": prop_x, "prop_y": prop_y, "count": len(rows),
            "curves": [{"sid": r.record_sid, "doi": r.doi, "sample": r.sample_name,
                        "composition": r.composition, "unit_x": r.unit_x, "unit_y": r.unit_y,
                        "points": json.loads(r.points_json)} for r in rows]}


@app.get("/api/v1/graph_list")
def graph_list(key: APIKey = Depends(require_scope("read"))):
    """All available property pairs and their curve counts."""
    with Session(engine) as s:
        rows = s.exec(select(GraphCurve)).all()
    counts: Dict[tuple, int] = {}
    for r in rows:
        counts[(r.prop_x, r.prop_y)] = counts.get((r.prop_x, r.prop_y), 0) + 1
    return {"graphs": [{"prop_x": x, "prop_y": y, "count": n}
                       for (x, y), n in sorted(counts.items(), key=lambda kv: -kv[1])]}


@app.get("/api/v1/properties")
def list_properties(key: APIKey = Depends(require_scope("read"))):
    with Session(engine) as s:
        props = s.exec(select(Property)).all()
    return {"count": len(props),
            "properties": [{"name": p.name, "category": p.category,
                            "default_unit": p.default_unit} for p in props]}


@app.post("/api/v1/properties")
def add_property(body: Dict[str, Any] = Body(...),
                 key: APIKey = Depends(require_scope("write"))):
    """Grow the vocabulary with an EXTENSION term (new properties inevitably
    turn up — AutoLineDigitizer posts Claude-proposed terms here so app and
    server stay in sync). The term is added to the Property table AND appended
    to kmds_vocab_extensions.json, so a reseed/fresh install keeps it. Official
    KMDS terms are never modified; duplicates are no-ops."""
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "property needs a name")
    category = (body.get("category") or "property").strip()
    if "(extension)" not in category:
        category += " (extension)"
    unit = (body.get("default_unit") or body.get("unit") or "").strip()

    with Session(engine) as s:
        props = s.exec(select(Property)).all()
        vocab_norm = {_norm_prop(p.name): p.name for p in props}
        existing = vocab_norm.get(_norm_prop(name))
        if existing:
            return {"ok": True, "added": False, "name": existing,
                    "detail": "already in the vocabulary"}
        s.add(Property(name=name, category=category, default_unit=unit))
        s.commit()

    ext_path = os.environ.get("SD3_VOCAB_EXT",
                              os.path.join(ROOT, "kmds_vocab_extensions.json"))
    try:
        with open(ext_path, encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:  # noqa: BLE001
        doc = {"extensions": []}
    if not any(_norm_prop(e.get("name", "")) == _norm_prop(name)
               for e in doc.get("extensions") or []):
        entry = {"name": name, "category": category, "unit": unit}
        if body.get("added_by"):
            entry["added_by"] = str(body["added_by"])
        doc.setdefault("extensions", []).append(entry)
        with open(ext_path, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)

    return {"ok": True, "added": True, "name": name,
            "category": category, "default_unit": unit}


# ---------------------------------------------------------------------------
# UI data endpoints (public, read-only JSON for the web frontend).
# ---------------------------------------------------------------------------

@app.get("/ui/stats")
def ui_stats():
    with Session(engine) as s:
        recs = s.exec(select(Record)).all()
        curves = s.exec(select(GraphCurve)).all()
        props = s.exec(select(Property)).all()
        pvs = s.exec(select(PointValue)).all()
    pairs = {(c.prop_x, c.prop_y, c.unit_x, c.unit_y) for c in curves}
    pairs |= {("temperature", p.prop, "K", p.unit) for p in pvs
              if p.env_temperature is not None}
    comps = {c.composition for c in curves if c.composition}
    comps |= {p.entity for p in pvs if p.entity}
    return {"records": len(recs), "curves": len(curves), "property_pairs": len(pairs),
            "compositions": len(comps), "vocabulary": len(props),
            "points": sum(c.n_points for c in curves) + len(pvs)}


_ELEMENTS = set(("H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn "
    "Fe Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te "
    "I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg "
    "Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu").split())


def _atoms_in(text):
    import re
    return set(re.findall(r"[A-Z][a-z]?", text or ""))


@app.get("/ui/papers")
def ui_papers(q: str = "", atom: str = ""):
    """Paper list filtered by free text (title/DOI/composition/property) and/or
    by required elements — `atom=Bi,Te` keeps papers whose compositions contain
    ALL those elements (mirrors Starrydata's atom search). An all-element query
    in `q` (e.g. 'Bi Te') is auto-detected as an atom search too."""
    ql = q.strip().lower()
    want_atoms = {a for a in _atoms_in(atom.replace(",", " ")) if a in _ELEMENTS}
    if not want_atoms and q:
        toks = [t for t in q.replace(",", " ").split() if t]
        if toks and all(t in _ELEMENTS for t in toks):
            want_atoms = set(toks); ql = ""     # pure-element query -> atom search
    with Session(engine) as s:
        recs = s.exec(select(Record)).all()
        curves = s.exec(select(GraphCurve)).all()
        pvs = s.exec(select(PointValue)).all()
    by_sid = {}
    for c in curves:
        d = by_sid.setdefault(c.record_sid, {"n": 0, "pts": 0, "comps": set(), "props": set()})
        d["n"] += 1
        if c.composition:
            d["comps"].add(c.composition)
        d["props"].add(c.prop_x); d["props"].add(c.prop_y)
    for p in pvs:
        d = by_sid.setdefault(p.record_sid, {"n": 0, "pts": 0, "comps": set(), "props": set()})
        d["pts"] += 1
        if p.entity:
            d["comps"].add(p.entity)
        d["props"].add(p.prop)
    out = []
    for r in recs:
        info = by_sid.get(r.sid, {"n": 0, "pts": 0, "comps": set(), "props": set()})
        pub = _publication(json.loads(r.record_json or "{}"))
        n_samples = len(pub.get("samples") or []) or len(info["comps"])
        if want_atoms:
            comp_atoms = set().union(*[_atoms_in(c) for c in info["comps"]]) if info["comps"] else set()
            if not want_atoms.issubset(comp_atoms):
                continue
        hay = " ".join([r.title, r.doi, " ".join(info["comps"]), " ".join(info["props"])]).lower()
        if ql and ql not in hay:
            continue
        out.append({"sid": r.sid, "doi": r.doi, "title": r.title,
                    "curves": info["n"], "points": info["pts"], "n_samples": n_samples,
                    "compositions": sorted(info["comps"])[:6],
                    "properties": sorted(info["props"])})
    out.sort(key=lambda x: -(x["curves"] + x["points"]))
    return {"count": len(out), "papers": out}


@app.get("/ui/pairs")
def ui_pairs():
    """Property pairs are split by UNIT as well: a frequency sweep in Hz and
    one in GHz are different axes, and without a canonical KMDS unit for the
    property they must never be overlaid on one plot."""
    with Session(engine) as s:
        curves = s.exec(select(GraphCurve)).all()
        pvs = s.exec(select(PointValue)).all()
    counts = {}
    for c in curves:
        d = counts.setdefault((c.prop_x, c.prop_y, c.unit_x, c.unit_y),
                              {"n": 0, "kmds": True, "points": False})
        d["n"] += 1
        if not c.kmds:
            d["kmds"] = False
    for p in pvs:
        if p.env_temperature is None:
            continue
        d = counts.setdefault(("temperature", p.prop, "K", p.unit),
                              {"n": 0, "kmds": True, "points": False})
        d["n"] += 1
        d["points"] = True
        if not p.kmds:
            d["kmds"] = False
    return {"pairs": [{"prop_x": x, "prop_y": y, "unit_x": ux, "unit_y": uy,
                       "count": d["n"], "kmds": d["kmds"], "points": d["points"]}
                      for (x, y, ux, uy), d in sorted(counts.items(), key=lambda kv: -kv[1]["n"])]}


def _point_series(prop_x: str, prop_y: str):
    """Measurement-temperature point values of prop_y, grouped per entity, as
    marker-only series for the property-pair plot."""
    if prop_x != "temperature":
        return []
    with Session(engine) as s:
        rows = s.exec(select(PointValue).where(PointValue.prop == prop_y)).all()
    by_entity: Dict[tuple, Dict[str, Any]] = {}
    for p in rows:
        if p.env_temperature is None:
            continue
        d = by_entity.setdefault((p.record_sid, p.entity), {
            "sid": p.record_sid, "doi": p.doi, "sample": p.entity,
            "composition": p.entity, "points": [], "type": "points", "unit_y": p.unit})
        d["points"].append([p.env_temperature + 273.15, p.value])   # °C as recorded -> K
    return list(by_entity.values())


@app.get("/ui/graph")
def ui_graph(prop_x: str, prop_y: str, unit_x: Optional[str] = None,
             unit_y: Optional[str] = None):
    with Session(engine) as s:
        q = select(GraphCurve).where(
            GraphCurve.prop_x == prop_x, GraphCurve.prop_y == prop_y)
        if unit_x is not None:
            q = q.where(GraphCurve.unit_x == unit_x)
        if unit_y is not None:
            q = q.where(GraphCurve.unit_y == unit_y)
        rows = s.exec(q).all()
    curves = [{"sid": r.record_sid, "doi": r.doi, "sample": r.sample_name,
               "composition": r.composition, "points": json.loads(r.points_json)}
              for r in rows]
    pts = _point_series(prop_x, prop_y)
    if unit_y is not None:
        pts = [p for p in pts if p.get("unit_y", "") == unit_y]
    if unit_x is not None and unit_x != "K":
        pts = []                       # env-temperature points are on a K axis
    ux = rows[0].unit_x if rows else ("K" if pts else (unit_x or ""))
    uy = rows[0].unit_y if rows else (pts[0].get("unit_y", "") if pts else (unit_y or ""))
    return {"prop_x": prop_x, "prop_y": prop_y, "count": len(curves) + len(pts),
            "unit_x": ux, "unit_y": uy, "curves": curves + pts}


def _crossref_meta(doi: str) -> Dict[str, Any]:
    """Bibliography for a DOI from the CrossRef API; empty fields on any
    failure so queueing never blocks on the network."""
    try:
        import httpx
        r = httpx.get(f"https://api.crossref.org/works/{doi}",
                      timeout=15.0,
                      headers={"User-Agent": "starrydata3 (mailto:kmds@local)"})
        r.raise_for_status()
        m = r.json().get("message") or {}
        authors = ", ".join(
            " ".join(x for x in (a.get("given"), a.get("family")) if x)
            for a in (m.get("author") or [])[:12])
        year = None
        for k in ("published-print", "published-online", "issued"):
            parts = ((m.get(k) or {}).get("date-parts") or [[None]])[0]
            if parts and parts[0]:
                year = int(parts[0])
                break
        return {"title": " ".join(m.get("title") or [])[:500],
                "journal": " ".join(m.get("container-title") or [])[:200],
                "year": year, "authors": authors[:500]}
    except Exception:
        return {"title": "", "journal": "", "year": None, "authors": ""}


@app.post("/api/v1/queue")
def queue_papers(body: Dict[str, Any] = Body(...),
                 key: APIKey = Depends(require_scope("write"))):
    """Register papers to digitize by DOI list; CrossRef fills the
    bibliography. DOIs already queued are skipped; DOIs already in the
    database come in as digitized."""
    dois = [d.strip() for d in (body.get("dois") or []) if d and d.strip()]
    if not dois:
        raise HTTPException(400, "no dois given")
    project = (body.get("project") or "default").strip() or "default"
    added, skipped = [], []
    with Session(engine) as s:
        have_records = {r.doi for r in s.exec(select(Record)).all()}
        for doi in dois:
            doi = doi.removeprefix("https://doi.org/").removeprefix("doi:")
            if s.exec(select(Paper).where(Paper.doi == doi)).first():
                skipped.append(doi)
                continue
            meta = _crossref_meta(doi)
            s.add(Paper(doi=doi, project=project,
                        status="digitized" if doi in have_records else "queued",
                        **meta))
            added.append(doi)
        s.commit()
    return {"ok": True, "added": added, "skipped_existing": skipped}


@app.get("/ui/queue")
def ui_queue():
    with Session(engine) as s:
        papers = s.exec(select(Paper)).all()
        have = {r.doi: r.sid for r in s.exec(select(Record)).all()}
    rows = [{"doi": p.doi, "title": p.title, "journal": p.journal,
             "year": p.year, "authors": p.authors, "project": p.project,
             "status": "digitized" if p.doi in have else p.status,
             "sid": have.get(p.doi), "added_at": p.added_at[:10]}
            for p in papers]
    rows.sort(key=lambda r: (r["status"] != "queued", r["added_at"]))
    n_q = sum(1 for r in rows if r["status"] == "queued")
    return {"count": len(rows), "queued": n_q, "digitized": len(rows) - n_q,
            "papers": rows}


@app.get("/ui/materials")
def ui_materials():
    """Every normalized composition in the database with its coverage — the
    per-material dossier index."""
    with Session(engine) as s:
        rows = s.exec(select(GraphCurve).where(GraphCurve.comp_key != "")).all()
    mats: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        m = mats.setdefault(r.comp_key, {
            "comp_key": r.comp_key, "elements": r.elements,
            "compositions": set(), "records": set(), "properties": set(),
            "n_curves": 0})
        m["compositions"].add(r.composition or r.sample_name)
        m["records"].add(r.record_sid)
        m["properties"].add(r.prop_y)
        m["n_curves"] += 1
    out = [{**m, "compositions": sorted(m["compositions"])[:6],
            "records": sorted(m["records"]),
            "properties": sorted(m["properties"])}
           for m in mats.values()]
    out.sort(key=lambda m: (-len(m["records"]), m["comp_key"]))
    return {"count": len(out), "materials": out}


@app.get("/ui/facet")
def ui_facet(prop: str, vmin: Optional[float] = None, vmax: Optional[float] = None,
             xmin: Optional[float] = None, xmax: Optional[float] = None,
             element: str = "", prop_x: str = "temperature"):
    """Range-at-condition search over digitized curves: e.g. curves of `prop`
    (ZT) whose value is within [vmin, vmax] somewhere in the x-window
    [xmin, xmax] (500–700 K), optionally containing `element` (Te).
    Returns the matching curves with their best value in the window."""
    els = [e.strip().capitalize() for e in element.replace(",", " ").split()
           if e.strip()]
    with Session(engine) as s:
        q = select(GraphCurve).where(GraphCurve.prop_y == prop)
        if prop_x:
            q = q.where(GraphCurve.prop_x == prop_x)
        rows = s.exec(q).all()
        titles = {r.sid: r.title for r in s.exec(select(Record)).all()}
    hits = []
    for r in rows:
        if els:
            have = set((r.elements or "").split())
            if not all(e in have for e in els):
                continue
        pts = [p for p in json.loads(r.points_json or "[]") if len(p) >= 2]
        window = [p for p in pts
                  if (xmin is None or p[0] >= xmin)
                  and (xmax is None or p[0] <= xmax)]
        if not window:
            continue
        matching = [p for p in window
                    if (vmin is None or p[1] >= vmin)
                    and (vmax is None or p[1] <= vmax)]
        if not matching:
            continue
        best = max(matching, key=lambda p: p[1])
        hits.append({"sid": r.record_sid, "doi": r.doi,
                     "title": titles.get(r.record_sid, ""),
                     "sample": r.sample_name, "composition": r.composition,
                     "comp_key": r.comp_key, "unit": r.unit_y,
                     "best_value": best[1], "best_x": best[0],
                     "n_matching_points": len(matching)})
    hits.sort(key=lambda h: -h["best_value"])
    return {"prop": prop, "prop_x": prop_x, "count": len(hits), "hits": hits}


# ---------------------------------------------------------------------------
# Review portal: batch-extracted papers land here for curator review;
# approval ingests them into the real database.
# ---------------------------------------------------------------------------

@app.post("/api/v1/review/sessions")
def push_review_session(body: Dict[str, Any] = Body(...),
                        key: APIKey = Depends(require_scope("write"))):
    """Batch runner drop-off: KMDS record + digitized figures for one paper.
    Replaces an existing session with the same pdf_name (re-runs stay clean)."""
    import base64
    pdf_name = (body.get("pdf") or "").strip()
    if not pdf_name:
        raise HTTPException(400, "session needs a pdf name")
    record = body.get("record") or {}
    pub = (record.get("metadata") or {}).get("publication") or {}
    with Session(engine) as s:
        old = s.exec(select(ReviewSession)
                     .where(ReviewSession.pdf_name == pdf_name)).first()
        if old:
            for f in s.exec(select(ReviewFigure)
                            .where(ReviewFigure.session_id == old.id)).all():
                s.delete(f)
            s.delete(old)
            s.commit()
        figures = body.get("figures") or []
        sess = ReviewSession(
            pdf_name=pdf_name,
            doi=(body.get("doi") or pub.get("DOI") or "").strip(),
            title=(pub.get("title") or body.get("title") or "")[:500],
            record_json=json.dumps(record, ensure_ascii=False),
            n_figures=len(figures))
        s.add(sess); s.commit(); s.refresh(sess)
        for f in figures:
            png = b""
            if f.get("png_b64"):
                try:
                    png = base64.b64decode(f["png_b64"])
                except Exception:
                    png = b""
            if len(png) > _MAX_PROV_IMAGE:
                png = b""
            s.add(ReviewFigure(
                session_id=sess.id, idx=int(f.get("idx") or 0),
                label=(f.get("label") or "")[:300], page=f.get("page"),
                image_png=png,
                state_json=json.dumps(f.get("state") or {},
                                      ensure_ascii=False)))
        s.commit()
        return {"ok": True, "session_id": sess.id, "figures": len(figures)}


ASSIST_URL = os.environ.get("SD3_ASSIST_URL", "http://127.0.0.1:8390")
_ASSIST_OPS = {"axes", "scatter", "label", "fix_axes", "verify"}


@app.post("/api/v1/assist/{op}")
def assist(op: str, body: Dict[str, Any] = Body(...),
           key: APIKey = Depends(require_scope("write"))):
    """Proxy a figure-editor assist request (axis detection, scatter
    detection, AI labelling/verification) to the AutoLineDigitizer compute
    sidecar — the server itself stays ML-free."""
    import base64
    if op not in _ASSIST_OPS:
        raise HTTPException(404, f"unknown assist op {op!r}")
    fid = body.get("fid")
    with Session(engine) as s:
        f = s.get(ReviewFigure, int(fid)) if fid is not None else None
    if f is None or not f.image_png:
        raise HTTPException(404, "figure (or its image) not found")
    payload = {"png_b64": base64.b64encode(f.image_png).decode()}
    if "series_px" in body:
        payload["series_px"] = body["series_px"]
    try:
        import httpx
        r = httpx.post(f"{ASSIST_URL}/{op}", json=payload, timeout=600)
        r.raise_for_status()
        return r.json()
    except httpx.ConnectError:
        raise HTTPException(503, "assist sidecar is not running — start it "
                                 "with: python src/assist_server.py "
                                 "(AutoLineDigitizer)")
    except httpx.HTTPStatusError as e:
        raise HTTPException(502, f"assist failed: {e.response.text[:300]}")


@app.get("/ui/review/sessions")
def ui_review_sessions():
    with Session(engine) as s:
        rows = s.exec(select(ReviewSession)).all()
        figs = s.exec(select(ReviewFigure)).all()
    by_sess: Dict[int, List[ReviewFigure]] = {}
    for f in figs:
        by_sess.setdefault(f.session_id, []).append(f)
    out = []
    for r in sorted(rows, key=lambda x: x.created_at, reverse=True):
        ff = by_sess.get(r.id, [])
        out.append({"id": r.id, "pdf": r.pdf_name, "doi": r.doi,
                    "title": r.title, "status": r.status,
                    "n_figures": len(ff),
                    "n_reviewed": sum(1 for x in ff
                                      if x.status in ("approved", "rejected",
                                                      "edited")),
                    "created_at": r.created_at[:16]})
    return {"count": len(out), "sessions": out}


@app.get("/ui/review/session/{sid}")
def ui_review_session(sid: int):
    with Session(engine) as s:
        r = s.get(ReviewSession, sid)
        if r is None:
            raise HTTPException(404, "no such session")
        figs = s.exec(select(ReviewFigure)
                      .where(ReviewFigure.session_id == sid)).all()
    return {"id": r.id, "pdf": r.pdf_name, "doi": r.doi, "title": r.title,
            "status": r.status, "record": json.loads(r.record_json or "{}"),
            "figures": [{"fid": f.id, "idx": f.idx, "label": f.label,
                         "page": f.page, "status": f.status,
                         "state": json.loads(f.state_json or "{}"),
                         "has_image": bool(f.image_png)}
                        for f in sorted(figs, key=lambda x: x.idx)]}


@app.get("/ui/review/figure/{fid}.png")
def ui_review_figure_png(fid: int):
    from fastapi.responses import Response
    with Session(engine) as s:
        f = s.get(ReviewFigure, fid)
    if f is None or not f.image_png:
        raise HTTPException(404, "no image")
    return Response(content=f.image_png, media_type="image/png",
                    headers={"Cache-Control": "max-age=86400"})


@app.patch("/api/v1/review/figure/{fid}")
def patch_review_figure(fid: int, body: Dict[str, Any] = Body(...),
                        key: APIKey = Depends(require_scope("write"))):
    """Curator saves an edited figure state and/or flips its status."""
    with Session(engine) as s:
        f = s.get(ReviewFigure, fid)
        if f is None:
            raise HTTPException(404, "no such figure")
        if "state" in body and isinstance(body["state"], dict):
            f.state_json = json.dumps(body["state"], ensure_ascii=False)
            if f.status == "pending":
                f.status = "edited"
        if body.get("status") in ("pending", "edited", "approved", "rejected"):
            f.status = body["status"]
        s.add(f)
        sess = s.get(ReviewSession, f.session_id)
        if sess and sess.status == "ready":
            sess.status = "in_review"
            sess.updated_at = _now()
            s.add(sess)
        s.commit()
        return {"ok": True, "fid": fid, "status": f.status}


@app.patch("/api/v1/review/session/{sid}")
def patch_review_session(sid: int, body: Dict[str, Any] = Body(...),
                         key: APIKey = Depends(require_scope("write"))):
    """Curator saves the edited KMDS record; response includes live schema
    validation so the portal can show violations as they edit."""
    with Session(engine) as s:
        r = s.get(ReviewSession, sid)
        if r is None:
            raise HTTPException(404, "no such session")
        if "record" in body and isinstance(body["record"], dict):
            r.record_json = json.dumps(body["record"], ensure_ascii=False)
            r.updated_at = _now()
            if r.status == "ready":
                r.status = "in_review"
            s.add(r); s.commit()
        viol = validate_kmds(json.loads(r.record_json))
    return {"ok": True, "n_violations": len(viol), "violations": viol[:20]}


@app.post("/api/v1/review/session/{sid}/approve")
def approve_review_session(sid: int, key: APIKey = Depends(require_scope("write"))):
    """Graft every non-rejected figure's (curator-edited) curves into the
    KMDS record, ingest it into the real database, and attach provenance
    (crop + calibration) — the batch papers' final step."""
    import base64
    with Session(engine) as s:
        r = s.get(ReviewSession, sid)
        if r is None:
            raise HTTPException(404, "no such session")
        figs = s.exec(select(ReviewFigure)
                      .where(ReviewFigure.session_id == sid)).all()
    record = json.loads(r.record_json or "{}")
    pub = record.setdefault("metadata", {}).setdefault("publication", {})
    figures = pub.setdefault("figures", [])
    used = [f for f in sorted(figs, key=lambda x: x.idx)
            if f.status != "rejected"]
    for f in used:
        st = json.loads(f.state_json or "{}")
        cfg = st.get("axis_config") or {}
        series_px = st.get("series_px") or []
        names = st.get("series_names") or []
        pts = []
        for i, sp in enumerate(series_px):
            data = [_px_to_data(cfg, p[0], p[1]) for p in sp if len(p) >= 2]
            data = [d for d in data if d is not None]
            if data:
                pts.append({"label": (names[i] if i < len(names) else None)
                            or f"Line {i + 1}", "points_data": data})
        if not pts:
            continue
        figures.append({
            "figure local id": f"f-b{f.idx + 1:02d}",
            "figure name": f.label or f"Figure {f.idx + 1}",
            "graphs": [{
                "graph local id": f"g-b{f.idx + 1:02d}",
                "axes": [
                    {"axis": "x", "quantity": {"term": st.get("x_name") or "X"},
                     "unit": st.get("x_unit") or ""},
                    {"axis": "y", "quantity": {"term": st.get("y_name") or "Y"},
                     "unit": st.get("y_unit") or ""},
                ],
                "digitization_data": {
                    "x_axis": {"name": st.get("x_name") or "",
                               "is_log": bool(cfg.get("xIsLogScale"))},
                    "y_axis": {"name": st.get("y_name") or "",
                               "is_log": bool(cfg.get("yIsLogScale"))},
                    "series": pts,
                    "provenance": {"page": f.page, "batch": True,
                                   "reviewed": f.status},
                },
            }],
        })
    result = store_record(record)
    sid_new = result.get("sid")
    doi = result.get("doi") or ""
    with Session(engine) as s:
        for f in used:
            if not f.image_png:
                continue
            st = json.loads(f.state_json or "{}")
            for old in s.exec(select(Provenance).where(
                    Provenance.record_sid == sid_new,
                    Provenance.label == (f.label or f"Figure {f.idx + 1}"))).all():
                s.delete(old)
            s.add(Provenance(
                record_sid=sid_new, doi=doi,
                label=f.label or f"Figure {f.idx + 1}", page=f.page,
                image_png=f.image_png,
                calibration_json=json.dumps(st.get("axis_config") or {}),
                extractor_json=json.dumps({"app": "batch_run + review portal"}),
                curator="portal"))
        r2 = s.get(ReviewSession, sid)
        r2.status = "approved"
        r2.updated_at = _now()
        s.add(r2)
        s.commit()
    return {"ok": True, "sid": sid_new, "figures_ingested": len(used),
            "consistency_flags": result.get("consistency_flags")}


def _px_to_data(cfg: Dict[str, Any], px: float, py: float):
    """Pixel -> data using the app's axis_config convention (two reference
    points per axis, optional log scale)."""
    import math
    try:
        x1p, x2p = float(cfg["x1_px"]), float(cfg["x2_px"])
        y1p, y2p = float(cfg["y1_py"]), float(cfg["y2_py"])
        x1v, x2v = float(cfg["x1_val"]), float(cfg["x2_val"])
        y1v, y2v = float(cfg["y1_val"]), float(cfg["y2_val"])
        if x2p == x1p or y2p == y1p:
            return None
        fx = (float(px) - x1p) / (x2p - x1p)
        fy = (float(py) - y1p) / (y2p - y1p)
        if cfg.get("xIsLogScale") and x1v > 0 and x2v > 0:
            x = 10 ** (math.log10(x1v) + fx * (math.log10(x2v) - math.log10(x1v)))
        else:
            x = x1v + fx * (x2v - x1v)
        if cfg.get("yIsLogScale") and y1v > 0 and y2v > 0:
            y = 10 ** (math.log10(y1v) + fy * (math.log10(y2v) - math.log10(y1v)))
        else:
            y = y1v + fy * (y2v - y1v)
        return [x, y]
    except (KeyError, TypeError, ValueError):
        return None


_MAX_PROV_IMAGE = 4 * 1024 * 1024   # 4 MB decoded — figure crops are ~100 KB


@app.post("/api/v1/provenance")
def submit_provenance(body: Dict[str, Any] = Body(...),
                      key: APIKey = Depends(require_scope("write"))):
    """Attach digitization provenance (figure crop + axis calibration +
    extractor versions) to an existing record, keyed by DOI. Entries replace
    older ones with the same figure label, so re-uploads stay clean."""
    import base64
    doi = (body.get("doi") or "").strip()
    if not doi:
        raise HTTPException(400, "provenance needs a doi")
    figures = body.get("figures") or []
    with Session(engine) as s:
        rec = s.exec(select(Record).where(Record.doi == doi)).first()
        if rec is None:
            raise HTTPException(404, f"no record with DOI {doi}")
        stored = 0
        for f in figures:
            label = (f.get("label") or "").strip() or f"figure {stored + 1}"
            png = b""
            if f.get("png_b64"):
                try:
                    png = base64.b64decode(f["png_b64"])
                except Exception:
                    raise HTTPException(400, f"figure {label!r}: bad png_b64")
                if len(png) > _MAX_PROV_IMAGE:
                    raise HTTPException(413, f"figure {label!r}: image too large")
            for old in s.exec(select(Provenance)
                              .where(Provenance.record_sid == rec.sid,
                                     Provenance.label == label)).all():
                s.delete(old)
            s.add(Provenance(
                record_sid=rec.sid, doi=doi, label=label,
                page=f.get("page"), image_png=png,
                calibration_json=json.dumps(f.get("calibration") or {}),
                extractor_json=json.dumps(f.get("extractor") or {}),
                curator=(f.get("curator") or "")[:100]))
            stored += 1
        sid = rec.sid
        s.commit()
    return {"ok": True, "sid": sid, "doi": doi, "figures_stored": stored}


@app.get("/ui/provenance/{sid}")
def ui_provenance(sid: int):
    """A record's provenance entries (metadata only — images by id)."""
    with Session(engine) as s:
        rows = s.exec(select(Provenance)
                      .where(Provenance.record_sid == sid)).all()
    return {"sid": sid, "entries": [
        {"id": r.id, "label": r.label, "page": r.page,
         "calibration": json.loads(r.calibration_json or "{}"),
         "extractor": json.loads(r.extractor_json or "{}"),
         "curator": r.curator, "created_at": r.created_at,
         "has_image": bool(r.image_png)} for r in rows]}


@app.get("/ui/provenance/{sid}/{pid}.png")
def ui_provenance_image(sid: int, pid: int):
    from fastapi.responses import Response
    with Session(engine) as s:
        row = s.get(Provenance, pid)
    if row is None or row.record_sid != sid or not row.image_png:
        raise HTTPException(404, "no such provenance image")
    return Response(content=row.image_png, media_type="image/png",
                    headers={"Cache-Control": "max-age=86400"})


def _percentile(sorted_vals: List[float], q: float) -> float:
    """Linear-interpolated percentile of an ascending list (q in 0..1)."""
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    pos = q * (n - 1)
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def property_band(prop_x: str, prop_y: str, unit_x: str = "", unit_y: str = "",
                  exclude_sid: Optional[int] = None, n_bins: int = 40
                  ) -> Optional[Dict[str, Any]]:
    """Percentile envelope (p10/p50/p90 per x-bin) of EVERY stored curve of a
    property pair — the corpus a new curve should be judged against. A curve
    lying far outside the band usually means a wrong axis unit or scale.
    Returns None when fewer than 2 other records contribute."""
    with Session(engine) as s:
        q = select(GraphCurve).where(
            GraphCurve.prop_x == prop_x, GraphCurve.prop_y == prop_y)
        if unit_x:
            q = q.where(GraphCurve.unit_x == unit_x)
        if unit_y:
            q = q.where(GraphCurve.unit_y == unit_y)
        rows = s.exec(q).all()
    rows = [r for r in rows if r.record_sid != exclude_sid]
    if len({r.record_sid for r in rows}) < 2:
        return None
    pts = []
    for r in rows:
        pts.extend([p for p in json.loads(r.points_json or "[]") if len(p) >= 2])
    if len(pts) < n_bins:
        return None
    xmin = min(p[0] for p in pts)
    xmax = max(p[0] for p in pts)
    if xmax <= xmin:
        return None
    width = (xmax - xmin) / n_bins
    bins: List[List[float]] = [[] for _ in range(n_bins)]
    for x, y in ((p[0], p[1]) for p in pts):
        i = min(int((x - xmin) / width), n_bins - 1)
        bins[i].append(y)
    out = {"x": [], "p10": [], "p50": [], "p90": []}
    for i, ys in enumerate(bins):
        if len(ys) < 3:
            continue
        ys.sort()
        out["x"].append(xmin + (i + 0.5) * width)
        out["p10"].append(_percentile(ys, 0.10))
        out["p50"].append(_percentile(ys, 0.50))
        out["p90"].append(_percentile(ys, 0.90))
    if len(out["x"]) < 2:
        return None
    return {**out, "n_curves": len(rows),
            "n_records": len({r.record_sid for r in rows})}


@app.get("/ui/band")
def ui_band(prop_x: str, prop_y: str, unit_x: str = "", unit_y: str = "",
            exclude_sid: Optional[int] = None):
    """Corpus percentile band for a property pair (see property_band)."""
    band = property_band(prop_x, prop_y, unit_x, unit_y, exclude_sid)
    return band or {"x": [], "n_curves": 0, "n_records": 0}


@app.get("/ui/record_points/{sid}")
def ui_record_points(sid: int):
    """A record's measured values grouped per property — feeds the per-paper
    property charts on the record page."""
    with Session(engine) as s:
        rows = s.exec(select(PointValue).where(PointValue.record_sid == sid)).all()
        flag_rows = s.exec(select(QualityFlag)
                           .where(QualityFlag.record_sid == sid)).all()
    groups: Dict[tuple, Dict[str, Any]] = {}
    for p in rows:
        g = groups.setdefault((p.prop, p.unit, p.source), {
            "prop": p.prop, "unit": p.unit, "source": p.source,
            "kmds": p.kmds, "category": p.category, "entries": []})
        g["entries"].append({"entity": p.entity, "value": p.value,
                             "env_temperature": p.env_temperature})
    order = {"fail": 0, "warn": 1, "ok": 2}
    flags = sorted(({"sample": f.sample, "check": f.check_name,
                     "status": f.status, "median_dev": f.median_dev,
                     "n_points": f.n_points, "detail": f.detail}
                    for f in flag_rows),
                   key=lambda f: (order.get(f["status"], 3), f["sample"]))
    return {"sid": sid, "groups": sorted(groups.values(),
                                         key=lambda g: (not g["kmds"], g["prop"])),
            "consistency": flags}


def _csv_response(rows: List[List[Any]], filename: str):
    import csv
    import io
    from fastapi import Response
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["sid", "doi", "kind", "sample", "prop_x", "unit_x", "x",
                "prop_y", "unit_y", "y"])
    w.writerows(rows)
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _curve_csv_rows(rows) -> List[List[Any]]:
    out = []
    for r in rows:
        for p in json.loads(r.points_json):
            out.append([r.record_sid, r.doi, "curve", r.sample_name,
                        r.prop_x, r.unit_x, p[0], r.prop_y, r.unit_y, p[1]])
    return out


def _point_csv_rows(pvs) -> List[List[Any]]:
    return [[p.record_sid, p.doi, f"point:{p.source}", p.entity,
             "temperature" if p.env_temperature is not None else "",
             "°C" if p.env_temperature is not None else "",
             p.env_temperature if p.env_temperature is not None else "",
             p.prop, p.unit, p.value] for p in pvs]


@app.get("/ui/export.csv")
def ui_export_csv():
    """The whole dataset in long form — every digitized curve point and every
    measured value (the Starrydata-style bulk download)."""
    with Session(engine) as s:
        curves = s.exec(select(GraphCurve)).all()
        pvs = s.exec(select(PointValue)).all()
    return _csv_response(_curve_csv_rows(curves) + _point_csv_rows(pvs),
                         "starrydata3_dataset.csv")


@app.get("/ui/export.json")
def ui_export_json():
    """Every stored KMDS record, verbatim — the full-fidelity local backup.
    Re-uploading each record recreates the database (curves and measured
    values are re-indexed on ingest)."""
    from fastapi import Response
    with Session(engine) as s:
        recs = s.exec(select(Record)).all()
    dump = {"service": "starrydata3", "exported_at": _now(), "count": len(recs),
            "records": [{"sid": r.sid, "doi": r.doi, "title": r.title,
                         "n_violations": r.n_violations, "updated_at": r.updated_at,
                         "record": json.loads(r.record_json or "{}")} for r in recs]}
    return Response(json.dumps(dump, ensure_ascii=False, indent=1),
                    media_type="application/json",
                    headers={"Content-Disposition":
                             'attachment; filename="starrydata3_records.json"'})


@app.get("/ui/graph.csv")
def ui_graph_csv(prop_x: str, prop_y: str, unit_x: Optional[str] = None,
                 unit_y: Optional[str] = None):
    """The currently-plotted property pair as CSV."""
    with Session(engine) as s:
        q = select(GraphCurve).where(
            GraphCurve.prop_x == prop_x, GraphCurve.prop_y == prop_y)
        if unit_x is not None:
            q = q.where(GraphCurve.unit_x == unit_x)
        if unit_y is not None:
            q = q.where(GraphCurve.unit_y == unit_y)
        curves = s.exec(q).all()
        pvs = (s.exec(select(PointValue).where(PointValue.prop == prop_y)).all()
               if prop_x == "temperature" and unit_x in (None, "K") else [])
    pvs = [p for p in pvs if p.env_temperature is not None
           and (unit_y is None or p.unit == unit_y)]
    name = f"{prop_x}_vs_{prop_y}".replace(" ", "_").replace("/", "-")
    return _csv_response(_curve_csv_rows(curves) + _point_csv_rows(pvs), f"{name}.csv")


# ---------------------------------------------------------------------------
# Web UI (Starrydata-style: searchable database, explorer, record view).
# ---------------------------------------------------------------------------
from starrydata3.web import register_ui  # noqa: E402
register_ui(app, engine, Record, GraphCurve, select, Session)


# Create tables at import so every entrypoint (uvicorn, TestClient, gunicorn)
# has a ready schema without relying on a startup hook.
init_db()
