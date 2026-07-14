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
                               "unit_x": xa.get("unit") or "-", "unit_y": ya.get("unit") or "-",
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
        for c in curves:
            s.add(GraphCurve(record_sid=sid, doi=doi, sample_name=c["sample_name"],
                             composition=c["composition"], prop_x=c["prop_x"], prop_y=c["prop_y"],
                             unit_x=c["unit_x"], unit_y=c["unit_y"],
                             points_json=json.dumps(c["points"]), n_points=len(c["points"])))
        s.commit()

    return {"ok": True, "sid": sid, "doi": doi, "title": title,
            "n_violations": len(viol), "violations": viol[:20],
            "curves_indexed": len(curves)}


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
# Minimal web UI (server-rendered) — browse records and plotted curves.
# Read-only; no API key needed for the display pages.
# ---------------------------------------------------------------------------

_PALETTE = ["#c0392b", "#2980b9", "#27ae60", "#8e44ad", "#d35400",
            "#16a085", "#2c3e50", "#f39c12", "#7f8c8d", "#c0329b"]
_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title><style>
:root{{--navy:#0d2544;--blue:#1d5fa0;--pane:#eef4fa;--ink:#12263d;--ink2:#5b7590;--gold:#c89211}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--pane);color:var(--ink);
font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}}
header{{background:var(--navy);color:#eaf2fb;padding:14px 26px;display:flex;align-items:center;gap:12px}}
header .star{{color:var(--gold);font-size:20px}}header b{{font-size:17px}}
header a{{color:#9fc0e2;text-decoration:none;margin-left:auto;font-size:13px}}
main{{max-width:1000px;margin:0 auto;padding:22px 26px}}
.card{{background:#fff;border:1px solid #d2e2f1;border-radius:10px;padding:16px 18px;margin-bottom:16px;
box-shadow:0 1px 0 rgba(13,37,68,.05),0 10px 24px -18px rgba(13,37,68,.25)}}
h1{{font-size:20px;margin:.2em 0}}h2{{font-size:15px;color:var(--navy);margin:.2em 0 .1em}}
a.rec{{text-decoration:none;color:var(--blue);font-weight:600}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{text-align:left;padding:6px 8px;border-bottom:1px solid #e1ecf7}}th{{color:var(--ink2)}}
.chip{{display:inline-block;background:var(--pane);border:1px solid #d2e2f1;border-radius:20px;
padding:2px 9px;margin:2px 4px 2px 0;font-size:12px}}
.muted{{color:var(--ink2);font-size:12px}}svg{{background:#fff;border:1px solid #e1ecf7;border-radius:8px}}
.legend span{{display:inline-flex;align-items:center;gap:5px;margin-right:12px;font-size:12px;color:var(--ink2)}}
.legend i{{width:14px;height:3px;border-radius:2px;display:inline-block}}
</style></head><body>
<header><span class="star">&#9733;</span><b>Starrydata3</b>
<span class="muted" style="color:#9fc0e2">KMDS materials-data service</span>
<a href="/">records</a><a href="/docs">API</a></header>
<main>{body}</main></body></html>"""


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _plot_svg(curves):
    """One SVG scatter/line plot for a list of curves sharing a property pair."""
    pts_all = [p for c in curves for p in json.loads(c.points_json)]
    if not pts_all:
        return ""
    xs = [p[0] for p in pts_all]; ys = [p[1] for p in pts_all]
    xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
    W, H, P = 640, 300, 46
    sx = lambda v: P + (W - 2 * P) * ((v - xmin) / (xmax - xmin) if xmax > xmin else 0.5)
    sy = lambda v: H - P - (H - 2 * P) * ((v - ymin) / (ymax - ymin) if ymax > ymin else 0.5)
    marks, legend = [], []
    for i, c in enumerate(curves):
        col = _PALETTE[i % len(_PALETTE)]
        pts = json.loads(c.points_json)
        poly = " ".join(f"{sx(p[0]):.1f},{sy(p[1]):.1f}" for p in pts)
        dots = "".join(f'<circle cx="{sx(p[0]):.1f}" cy="{sy(p[1]):.1f}" r="1.7" fill="{col}"/>' for p in pts[::3])
        marks.append(f'<polyline points="{poly}" fill="none" stroke="{col}" stroke-width="1.6"/>{dots}')
        legend.append(f'<span><i style="background:{col}"></i>{_esc(c.sample_name)} '
                      f'<span class="muted">({c.n_points})</span></span>')
    fmt = lambda v: f"{v:.3g}"
    axis = (f'<line x1="{P}" y1="{H-P}" x2="{W-P}" y2="{H-P}" stroke="#9fb6cf"/>'
            f'<line x1="{P}" y1="{P}" x2="{P}" y2="{H-P}" stroke="#9fb6cf"/>'
            f'<text x="{P}" y="{H-P+16}" font-size="10" fill="#5b7590">{fmt(xmin)}</text>'
            f'<text x="{W-P}" y="{H-P+16}" text-anchor="end" font-size="10" fill="#5b7590">{fmt(xmax)}</text>'
            f'<text x="{P-6}" y="{H-P}" text-anchor="end" font-size="10" fill="#5b7590">{fmt(ymin)}</text>'
            f'<text x="{P-6}" y="{P+6}" text-anchor="end" font-size="10" fill="#5b7590">{fmt(ymax)}</text>'
            f'<text x="{W/2}" y="{H-8}" text-anchor="middle" font-size="12" fill="#12263d">'
            f'{_esc(curves[0].prop_x)} ({_esc(curves[0].unit_x)})</text>'
            f'<text x="14" y="{H/2}" text-anchor="middle" font-size="12" fill="#12263d" '
            f'transform="rotate(-90 14 {H/2})">{_esc(curves[0].prop_y)} ({_esc(curves[0].unit_y)})</text>')
    return (f'<svg viewBox="0 0 {W} {H}" style="width:100%;max-width:{W}px">{axis}{"".join(marks)}</svg>'
            f'<div class="legend">{"".join(legend)}</div>')


@app.get("/", response_class=HTMLResponse)
def ui_index():
    with Session(engine) as s:
        recs = s.exec(select(Record)).all()
        rows = ""
        for r in recs:
            n = len(s.exec(select(GraphCurve).where(GraphCurve.record_sid == r.sid)).all())
            rows += (f'<tr><td><a class="rec" href="/view/{r.sid}">SID-{r.sid}</a></td>'
                     f'<td>{_esc(r.title[:70])}</td><td class="muted">{_esc(r.doi)}</td>'
                     f'<td>{n}</td></tr>')
        pairs = {}
        for g in s.exec(select(GraphCurve)).all():
            pairs[(g.prop_x, g.prop_y)] = pairs.get((g.prop_x, g.prop_y), 0) + 1
    chips = "".join(f'<span class="chip">{_esc(x)} vs {_esc(y)} <b>{n}</b></span>'
                    for (x, y), n in sorted(pairs.items(), key=lambda kv: -kv[1]))
    body = (f'<div class="card"><h1>Records</h1><table>'
            f'<tr><th>ID</th><th>Title</th><th>DOI</th><th>curves</th></tr>'
            f'{rows or "<tr><td colspan=4 class=muted>no records yet</td></tr>"}</table></div>'
            f'<div class="card"><h2>Property pairs indexed</h2>{chips or "<span class=muted>none</span>"}</div>')
    return _PAGE.format(title="Starrydata3", body=body)


@app.get("/view/{sid}", response_class=HTMLResponse)
def ui_view(sid: int):
    with Session(engine) as s:
        rec = s.exec(select(Record).where(Record.sid == sid)).first()
        if not rec:
            return HTMLResponse(_PAGE.format(title="not found",
                                body=f'<div class="card">No record SID-{sid}. '
                                     f'<a href="/">back</a></div>'), status_code=404)
        curves = s.exec(select(GraphCurve).where(GraphCurve.record_sid == sid)).all()
    groups = {}
    for c in curves:
        groups.setdefault((c.prop_x, c.prop_y, c.unit_x, c.unit_y), []).append(c)
    plots = ""
    for (px, py, _, _), cs in groups.items():
        plots += (f'<div class="card"><h2>{_esc(px)} vs {_esc(py)}</h2>'
                  f'<div class="muted">{len(cs)} curve(s), '
                  f'{sum(c.n_points for c in cs)} points</div>{_plot_svg(cs)}</div>')
    head = (f'<div class="card"><div class="muted">SID-{rec.sid} · {_esc(rec.doi)} · '
            f'{rec.n_violations} schema violation(s)</div><h1>{_esc(rec.title)}</h1>'
            f'<a href="/">&larr; all records</a> &nbsp; '
            f'<a href="/api/v1/records/{sid}">raw KMDS JSON</a></div>')
    return _PAGE.format(title=f"SID-{sid}", body=head + (plots or '<div class="card muted">no digitized curves</div>'))


# Create tables at import so every entrypoint (uvicorn, TestClient, gunicorn)
# has a ready schema without relying on a startup hook.
init_db()
