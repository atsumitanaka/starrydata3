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

from fastapi import Body, Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from sqlmodel import Field, Session, SQLModel, create_engine, select

from starrydata3 import units

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
            "composition": smp.get("name") or "; ".join(comps)}
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
                else:
                    sname = sers.get("label") or f"Line {i + 1}"
                    comp = sname
                curves.append({"sample_name": sname, "composition": comp,
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

app = FastAPI(title="Starrydata3", version="0.1.0",
              description="Private, KMDS-based materials-data service with API-key writes.")


def init_db():
    SQLModel.metadata.create_all(engine)


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
            s.add(GraphCurve(record_sid=sid, doi=doi, sample_name=c["sample_name"],
                             composition=c["composition"],
                             prop_x=cx or c["prop_x"], prop_y=cy or c["prop_y"],
                             unit_x=ux, unit_y=uy, kmds=is_kmds, unit_normalized=normed,
                             points_json=json.dumps(pts), raw_points_json=json.dumps(raw),
                             n_points=len(pts)))

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

    return {"ok": True, "sid": sid, "doi": doi, "title": title,
            "n_violations": len(viol), "violations": viol[:20],
            "curves_indexed": len(curves),
            "kmds_curves": n_kmds, "unit_normalized_curves": n_norm,
            "points_indexed": len(pvs), "kmds_points": n_kmds_pts,
            "non_kmds_properties": sorted(non_kmds)}


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
# Server-side digitization: upload a PDF, run the AutoLineDigitizer pipeline,
# store the resulting KMDS record. Background job + status polling.
# ---------------------------------------------------------------------------
_JOBS: Dict[str, Dict[str, Any]] = {}


@app.post("/api/v1/process_pdf")
def process_pdf(file: UploadFile = File(...), doi: str = "",
                key: APIKey = Depends(require_scope("write"))):
    """Digitize an uploaded PDF end-to-end (figures → calibrate → curves →
    legend → store) using the AutoLineDigitizer engine. Returns a job id;
    poll /api/v1/jobs/{id}."""
    import shutil
    import tempfile
    import threading
    from starrydata3 import pipeline
    if not pipeline.pipeline_available():
        raise HTTPException(503, "digitization pipeline unavailable — run "
                            "Starrydata3 from the alddev env with $ALD_SRC set")
    job_id = secrets.token_hex(8)
    tmp = tempfile.mkdtemp(prefix="sd3_pdf_")
    path = os.path.join(tmp, os.path.basename(file.filename or "paper.pdf"))
    with open(path, "wb") as f:
        f.write(file.file.read())
    _JOBS[job_id] = {"status": "running", "stage": "loading models",
                     "sid": None, "error": None}

    def _run():
        try:
            def prog(msg):
                _JOBS[job_id]["stage"] = msg
            rec = pipeline.get_pipeline().process_pdf(path, doi=doi or None, progress=prog)
            _JOBS[job_id]["stage"] = "storing"
            res = store_record(merge_digitized(rec))
            _JOBS[job_id].update(status="done", sid=res["sid"], result=res)
        except HTTPException as e:
            _JOBS[job_id].update(status="error", error=str(e.detail))
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            _JOBS[job_id].update(status="error", error=f"{type(e).__name__}: {e}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    threading.Thread(target=_run, daemon=True).start()
    return {"job_id": job_id, "status": "running"}


@app.get("/api/v1/jobs/{job_id}")
def job_status(job_id: str, key: APIKey = Depends(require_scope("read"))):
    j = _JOBS.get(job_id)
    if not j:
        raise HTTPException(404, "no such job")
    return j


# ---------------------------------------------------------------------------
# Interactive digitization with review: analyze a PDF, hold the per-figure
# results in a session, let a human verify/correct every figure in the web UI,
# then commit the approved figures as a KMDS record. Everything runs locally —
# the ML weights live in the AutoLineDigitizer install on this machine.
# ---------------------------------------------------------------------------
_REVIEWS: Dict[str, Dict[str, Any]] = {}


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


@app.post("/api/v1/review/start")
def review_start(file: UploadFile = File(...), doi: str = "",
                 key: APIKey = Depends(require_scope("write"))):
    """Digitize an uploaded PDF into a REVIEW SESSION (nothing stored yet).
    Returns a job id; when the job is done it carries a review_id — fetch
    /api/v1/review/{review_id} and render the verification UI."""
    import shutil
    import tempfile
    import threading
    from starrydata3 import pipeline
    if not pipeline.pipeline_available():
        raise HTTPException(503, "digitization pipeline unavailable — run "
                            "Starrydata3 from the alddev env with $ALD_SRC set")
    job_id = secrets.token_hex(8)
    tmp = tempfile.mkdtemp(prefix="sd3_pdf_")
    path = os.path.join(tmp, os.path.basename(file.filename or "paper.pdf"))
    with open(path, "wb") as f:
        f.write(file.file.read())
    _JOBS[job_id] = {"status": "running", "stage": "loading models",
                     "review_id": None, "error": None}

    def _run():
        try:
            import cv2

            def prog(msg):
                _JOBS[job_id]["stage"] = msg
            analysis = pipeline.get_pipeline().analyze_pdf(path, progress=prog)
            rid = secrets.token_hex(12)
            rdir = tempfile.mkdtemp(prefix="sd3_review_")
            figures = []
            for f in analysis["figures"]:
                img = f.pop("image_bgr")
                img_path = os.path.join(rdir, f"fig_{f['index']}.png")
                cv2.imwrite(img_path, img)
                f["width"], f["height"] = int(img.shape[1]), int(img.shape[0])
                f["image_path"] = img_path
                figures.append(f)
            _REVIEWS[rid] = {"doi": doi or analysis["doi"] or "",
                             "figures": figures, "dir": rdir, "created": _now()}
            _JOBS[job_id].update(status="done", review_id=rid)
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            _JOBS[job_id].update(status="error", error=f"{type(e).__name__}: {e}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    threading.Thread(target=_run, daemon=True).start()
    return {"job_id": job_id, "status": "running"}


def _review_or_404(rid: str) -> Dict[str, Any]:
    r = _REVIEWS.get(rid)
    if not r:
        raise HTTPException(404, "no such review session (already committed or discarded?)")
    return r


def _series_range(s):
    xs = [p[0] for p in s["points_data"]]
    ys = [p[1] for p in s["points_data"]]
    return {"x_min": min(xs), "x_max": max(xs), "y_min": min(ys), "y_max": max(ys)}


@app.get("/api/v1/review/{rid}")
def review_get(rid: str):
    """The review session's figures for the verification UI. Curve points stay
    server-side; the client sees overlay pixels, counts and DATA RANGES (the
    ranges are how a reviewer spots a miscalibrated axis) and edits labels."""
    r = _review_or_404(rid)
    return {"review_id": rid, "doi": r["doi"], "created": r["created"],
            "figures": [{
                "index": f["index"], "caption": f["caption"],
                "calibrated": f["calibrated"], "error": f.get("error", ""),
                "calibration": f.get("calibration", "auto"),
                "detector": f.get("detector", ""),
                "x_name": f["x_name"], "y_name": f["y_name"],
                "x_log": f["x_log"], "y_log": f["y_log"],
                "width": f["width"], "height": f["height"],
                "image_url": f"/api/v1/review/{rid}/figure/{f['index']}.png",
                "series": [{"label": s["label"], "n_points": len(s["points_data"]),
                            "range": _series_range(s),
                            "points_px": s["points_px"]} for s in f["series"]],
            } for f in r["figures"]]}


@app.post("/api/v1/review/{rid}/recalibrate/{index}")
def review_recalibrate(rid: str, index: int,
                       key: APIKey = Depends(require_scope("write"))):
    """Re-read this figure's axes with Claude (the 'detect axis' fix) and
    recompute every series' data values from the stored pixel traces. For when
    the classic calibration succeeded but read the ticks wrong (e.g. log-axis
    labels like 10^2 digitized as 102)."""
    import cv2
    from starrydata3 import pipeline
    r = _review_or_404(rid)
    fig = next((f for f in r["figures"] if f["index"] == index), None)
    if fig is None:
        raise HTTPException(404, "no such figure")
    img = cv2.imread(fig["image_path"])
    if img is None:
        raise HTTPException(500, "figure image missing from the session")
    pl = pipeline.get_pipeline()
    pl._ensure_models()
    cfg = pl._claude_axis_fallback(img)
    if not cfg:
        raise HTTPException(502, "Claude could not calibrate this figure "
                            "(is an Anthropic key configured?)")
    for s in fig["series"]:
        px = s.get("points_px_ds") or s["points_px"]
        s["points_data"] = [list(pl.app.pixel_to_data_cfg(cfg, p[0], p[1])) for p in px]
    fig.update(calibrated=True, calibration="claude",
               x_log=bool(cfg.get("xIsLogScale")), y_log=bool(cfg.get("yIsLogScale")))
    return {"ok": True, "index": index, "calibration": "claude",
            "x_log": fig["x_log"], "y_log": fig["y_log"],
            "series": [{"label": s["label"], "range": _series_range(s)}
                       for s in fig["series"]]}


@app.get("/api/v1/review/{rid}/figure/{index}.png")
def review_figure(rid: str, index: int):
    from fastapi.responses import FileResponse
    r = _review_or_404(rid)
    for f in r["figures"]:
        if f["index"] == index:
            return FileResponse(f["image_path"], media_type="image/png")
    raise HTTPException(404, "no such figure")


@app.post("/api/v1/review/{rid}/commit")
def review_commit(rid: str, body: Dict[str, Any] = Body(...),
                  key: APIKey = Depends(require_scope("write"))):
    """Store the verified digitization as a KMDS record. Body: {doi, title,
    figures: [{index, include, x_name, x_unit, y_name, y_unit, x_log, y_log,
    series: [{label, include}]}]} — series points come from the session."""
    import shutil
    from starrydata3 import pipeline
    r = _review_or_404(rid)
    by_index = {f["index"]: f for f in r["figures"]}
    payloads = []
    for edit in body.get("figures") or []:
        f = by_index.get(edit.get("index"))
        if f is None or not edit.get("include") or not f["series"]:
            continue
        series = []
        edits = edit.get("series") or []
        for j, s in enumerate(f["series"]):
            e = edits[j] if j < len(edits) else {}
            if not e.get("include", True):
                continue
            series.append({"label": (e.get("label") or s["label"]).strip(),
                           "points_data": s["points_data"]})
        if not series:
            continue
        payloads.append({"caption": f["caption"],
                         "x_name": (edit.get("x_name") or f["x_name"]).strip(),
                         "y_name": (edit.get("y_name") or f["y_name"]).strip(),
                         "x_unit": (edit.get("x_unit") or "").strip(),
                         "y_unit": (edit.get("y_unit") or "").strip(),
                         "x_log": bool(edit.get("x_log", f["x_log"])),
                         "y_log": bool(edit.get("y_log", f["y_log"])),
                         "series": series})
    if not payloads:
        raise HTTPException(422, "nothing to store — no figure was approved")
    doi = (body.get("doi") or r["doi"] or "").strip()
    record = pipeline.assemble_record(doi, payloads, title=(body.get("title") or "").strip())
    res = store_record(merge_digitized(record))
    shutil.rmtree(r["dir"], ignore_errors=True)
    _REVIEWS.pop(rid, None)
    return res


@app.delete("/api/v1/review/{rid}")
def review_discard(rid: str, key: APIKey = Depends(require_scope("write"))):
    import shutil
    r = _review_or_404(rid)
    shutil.rmtree(r["dir"], ignore_errors=True)
    _REVIEWS.pop(rid, None)
    return {"ok": True, "discarded": rid}


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


@app.get("/ui/record_points/{sid}")
def ui_record_points(sid: int):
    """A record's measured values grouped per property — feeds the per-paper
    property charts on the record page."""
    with Session(engine) as s:
        rows = s.exec(select(PointValue).where(PointValue.record_sid == sid)).all()
    groups: Dict[tuple, Dict[str, Any]] = {}
    for p in rows:
        g = groups.setdefault((p.prop, p.unit, p.source), {
            "prop": p.prop, "unit": p.unit, "source": p.source,
            "kmds": p.kmds, "category": p.category, "entries": []})
        g["entries"].append({"entity": p.entity, "value": p.value,
                             "env_temperature": p.env_temperature})
    return {"sid": sid, "groups": sorted(groups.values(),
                                         key=lambda g: (not g["kmds"], g["prop"]))}


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
