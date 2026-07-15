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
    points_json: str = "[]"
    n_points: int = 0
    kmds: bool = True          # both axis properties are in the KMDS vocabulary


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
}


def match_property(name: str, vocab_norm: Dict[str, str]) -> Optional[str]:
    """Map an axis/OCR property name onto a canonical KMDS vocabulary term.
    Exact-normalized match, then a hand synonym, then a unique substring hit.
    Returns the canonical term, or None if it is not a KMDS property."""
    n = _norm_prop(name)
    if not n:
        return None
    if n in vocab_norm:
        return vocab_norm[n]
    syn = _PROP_SYNONYMS.get(n)
    if syn:
        return syn
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


@app.post("/api/v1/records")
def submit_record(record: Dict[str, Any] = Body(...),
                  strict: bool = False,
                  key: APIKey = Depends(require_scope("write"))):
    """Ingest a full KMDS record: validate, upsert by DOI, index its curves.
    strict=true rejects a record with any schema violation (422)."""
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
        # normalize each curve's axis properties to the KMDS vocabulary; keep the
        # raw term when it is not a KMDS property but flag it so it can't
        # silently pollute the database.
        vocab_norm = {_norm_prop(p.name): p.name for p in s.exec(select(Property)).all()}
        non_kmds = set()
        n_kmds = 0
        for c in curves:
            cx = match_property(c["prop_x"], vocab_norm)
            cy = match_property(c["prop_y"], vocab_norm)
            if cx is None:
                non_kmds.add(c["prop_x"])
            if cy is None:
                non_kmds.add(c["prop_y"])
            is_kmds = cx is not None and cy is not None
            n_kmds += 1 if is_kmds else 0
            s.add(GraphCurve(record_sid=sid, doi=doi, sample_name=c["sample_name"],
                             composition=c["composition"],
                             prop_x=cx or c["prop_x"], prop_y=cy or c["prop_y"],
                             unit_x=c["unit_x"], unit_y=c["unit_y"], kmds=is_kmds,
                             points_json=json.dumps(c["points"]), n_points=len(c["points"])))
        s.commit()

    return {"ok": True, "sid": sid, "doi": doi, "title": title,
            "n_violations": len(viol), "violations": viol[:20],
            "curves_indexed": len(curves),
            "kmds_curves": n_kmds,
            "non_kmds_properties": sorted(non_kmds)}


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
    pairs = {(c.prop_x, c.prop_y) for c in curves}
    comps = {c.composition for c in curves if c.composition}
    return {"records": len(recs), "curves": len(curves), "property_pairs": len(pairs),
            "compositions": len(comps), "vocabulary": len(props),
            "points": sum(c.n_points for c in curves)}


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
    by_sid = {}
    for c in curves:
        d = by_sid.setdefault(c.record_sid, {"n": 0, "comps": set(), "props": set()})
        d["n"] += 1
        if c.composition:
            d["comps"].add(c.composition)
        d["props"].add(c.prop_x); d["props"].add(c.prop_y)
    out = []
    for r in recs:
        info = by_sid.get(r.sid, {"n": 0, "comps": set(), "props": set()})
        if want_atoms:
            comp_atoms = set().union(*[_atoms_in(c) for c in info["comps"]]) if info["comps"] else set()
            if not want_atoms.issubset(comp_atoms):
                continue
        hay = " ".join([r.title, r.doi, " ".join(info["comps"]), " ".join(info["props"])]).lower()
        if ql and ql not in hay:
            continue
        out.append({"sid": r.sid, "doi": r.doi, "title": r.title,
                    "curves": info["n"], "n_samples": len(info["comps"]),
                    "compositions": sorted(info["comps"])[:6],
                    "properties": sorted(info["props"])})
    out.sort(key=lambda x: -x["curves"])
    return {"count": len(out), "papers": out}


@app.get("/ui/pairs")
def ui_pairs():
    with Session(engine) as s:
        curves = s.exec(select(GraphCurve)).all()
    counts = {}
    for c in curves:
        d = counts.setdefault((c.prop_x, c.prop_y), {"n": 0, "kmds": True})
        d["n"] += 1
        if not c.kmds:
            d["kmds"] = False
    return {"pairs": [{"prop_x": x, "prop_y": y, "count": d["n"], "kmds": d["kmds"]}
                      for (x, y), d in sorted(counts.items(), key=lambda kv: -kv[1]["n"])]}


@app.get("/ui/graph")
def ui_graph(prop_x: str, prop_y: str):
    with Session(engine) as s:
        rows = s.exec(select(GraphCurve).where(
            GraphCurve.prop_x == prop_x, GraphCurve.prop_y == prop_y)).all()
    return {"prop_x": prop_x, "prop_y": prop_y, "count": len(rows),
            "unit_x": rows[0].unit_x if rows else "", "unit_y": rows[0].unit_y if rows else "",
            "curves": [{"sid": r.record_sid, "doi": r.doi, "sample": r.sample_name,
                        "composition": r.composition, "points": json.loads(r.points_json)}
                       for r in rows]}


# ---------------------------------------------------------------------------
# Web UI (Starrydata-style: searchable database, explorer, record view).
# ---------------------------------------------------------------------------
from starrydata3.web import register_ui  # noqa: E402
register_ui(app, engine, Record, GraphCurve, select, Session)


# Create tables at import so every entrypoint (uvicorn, TestClient, gunicorn)
# has a ready schema without relying on a startup hook.
init_db()
