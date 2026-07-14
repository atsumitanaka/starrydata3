# -*- coding: utf-8 -*-
"""
web.py — Starrydata3's web UI: a Starrydata-style experience (searchable paper
database, interactive cross-paper property-pair explorer, rich per-record view)
served straight from the FastAPI app. Server-rendered shells + vanilla JS that
reads the public /ui/* JSON endpoints; no build step, no external libraries.

register_ui(app, engine, models) wires the routes onto the existing app.
"""
import json

from fastapi.responses import HTMLResponse

PALETTE = ["#1d5fa0", "#c0392b", "#27ae60", "#8e44ad", "#d35400", "#16a085",
           "#e67e22", "#2c3e50", "#c0329b", "#7f8c8d", "#2980b9", "#d4ac0d"]


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


CSS = """
:root{--navy:#0d2544;--navy2:#143458;--blue:#1d5fa0;--blue2:#3a7fc4;--pane:#eef4fa;
--card:#ffffff;--ink:#12263d;--ink2:#5b7590;--line:#d2e2f1;--gold:#c89211}
*{box-sizing:border-box}html,body{margin:0}
body{background:var(--pane);color:var(--ink);
font:14px/1.55 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif;-webkit-font-smoothing:antialiased}
a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}
header.top{background:var(--navy);color:#eaf2fb;padding:0 26px;display:flex;align-items:center;
gap:22px;height:56px;position:sticky;top:0;z-index:10;box-shadow:0 2px 12px rgba(13,37,68,.25)}
header.top .brand{display:flex;align-items:center;gap:9px;font-weight:700;font-size:17px}
header.top .brand .star{color:var(--gold);font-size:20px}
header.top nav{display:flex;gap:4px;margin-left:8px}
header.top nav a{color:#bcd4ec;padding:7px 12px;border-radius:7px;font-size:13.5px}
header.top nav a:hover{background:var(--navy2);text-decoration:none;color:#fff}
header.top nav a.on{background:var(--blue);color:#fff}
header.top .sp{margin-left:auto;font-size:12px;color:#9fc0e2}
main{max-width:1120px;margin:0 auto;padding:24px 26px 60px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px 20px;margin-bottom:18px;
box-shadow:0 1px 0 rgba(13,37,68,.04),0 14px 30px -22px rgba(13,37,68,.3)}
h1{font-size:22px;margin:.1em 0 .3em}h2{font-size:16px;color:var(--navy);margin:.2em 0 .5em}
.muted{color:var(--ink2)}.small{font-size:12px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:12px;margin-bottom:18px}
.stat{background:linear-gradient(160deg,var(--navy),var(--navy2));color:#eaf2fb;border-radius:12px;padding:14px 16px}
.stat b{display:block;font-size:26px;line-height:1.1;color:#fff}
.stat span{font-size:11.5px;letter-spacing:.06em;text-transform:uppercase;color:#9fc0e2}
input.search{width:100%;padding:11px 14px;border:1px solid var(--line);border-radius:10px;font-size:15px;
background:#fff;color:var(--ink)}input.search:focus{outline:2px solid var(--blue2);border-color:var(--blue)}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--ink2);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.04em}
tr:hover td{background:#f6faff}
.chip{display:inline-block;background:var(--pane);border:1px solid var(--line);border-radius:20px;
padding:2px 10px;margin:2px 5px 2px 0;font-size:12px;color:var(--navy)}
.chip b{color:var(--blue)}a.chip:hover{background:#e1ecf7;text-decoration:none}
.pairbtn{display:inline-flex;align-items:center;gap:8px;background:#fff;border:1px solid var(--line);
border-radius:10px;padding:8px 12px;margin:0 8px 8px 0;cursor:pointer;font-size:13px;color:var(--ink)}
.pairbtn:hover{border-color:var(--blue);background:#f6faff}.pairbtn.on{background:var(--blue);color:#fff;border-color:var(--blue)}
.pairbtn .n{background:var(--pane);color:var(--navy);border-radius:12px;padding:0 7px;font-size:11px}
.pairbtn.on .n{background:rgba(255,255,255,.25);color:#fff}
.legend{display:flex;flex-wrap:wrap;gap:4px 14px;margin-top:10px}
.legend span{display:inline-flex;align-items:center;gap:6px;font-size:12px;color:var(--ink2)}
.legend i{width:14px;height:3px;border-radius:2px;display:inline-block}
svg,canvas{max-width:100%}.plot{border:1px solid var(--line);border-radius:8px;background:#fff}
.samps td:first-child{font-weight:600}
.toolbar{display:flex;gap:14px;align-items:center;flex-wrap:wrap;margin-bottom:8px}
label.ck{font-size:12.5px;color:var(--ink2);display:inline-flex;gap:5px;align-items:center;cursor:pointer}
"""


def layout(title, active, body, extra_js=""):
    nav = "".join(
        f'<a href="{href}" class="{"on" if active==key else ""}">{label}</a>'
        for key, href, label in [("home", "/", "Database"),
                                  ("explore", "/explore", "Explore"),
                                  ("upload", "/upload", "Upload"),
                                  ("api", "/docs", "API")])
    return HTMLResponse(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} · Starrydata3</title>
<style>{CSS}</style></head><body>
<header class="top"><div class="brand"><span class="star">&#9733;</span>Starrydata3</div>
<nav>{nav}</nav><div class="sp">KMDS materials-data service</div></header>
<main>{body}</main><script>{extra_js}</script></body></html>""")


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

HOME_JS = """
async function load(q){
  const r = await fetch('/ui/papers'+(q?('?q='+encodeURIComponent(q)):''));
  const d = await r.json();
  document.getElementById('count').textContent = d.count+' paper'+(d.count==1?'':'s');
  const rows = d.papers.map(p=>`<tr>
    <td><a href="/view/${p.sid}">SID-${p.sid}</a></td>
    <td><a href="/view/${p.sid}">${esc(p.title||'(untitled)')}</a>
      <div class="muted small">${esc(p.doi)}</div>
      <div>${(p.compositions||[]).map(c=>`<span class="chip">${esc(c)}</span>`).join('')}</div></td>
    <td>${p.n_samples}</td><td>${p.curves}</td></tr>`).join('');
  document.getElementById('rows').innerHTML = rows ||
    '<tr><td colspan=4 class=muted>no matching papers</td></tr>';
}
function esc(s){return (s+'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
let t; const box=document.getElementById('q');
box.addEventListener('input',()=>{clearTimeout(t);t=setTimeout(()=>load(box.value),150)});
load('');
"""


def home_page():
    body = """
<div class="stats" id="stats"></div>
<div class="card">
  <h1>Paper database</h1>
  <p class="muted" style="margin-top:0">Search by title, DOI, composition, or property.</p>
  <input class="search" id="q" placeholder="Search papers…  title · DOI · property · or elements (Bi Te)">
  <div class="muted small" style="margin:8px 2px" id="count"></div>
  <table><thead><tr><th>ID</th><th>Paper</th><th>Samples</th><th>Curves</th></tr></thead>
  <tbody id="rows"></tbody></table>
</div>"""
    js = """
fetch('/ui/stats').then(r=>r.json()).then(s=>{
  const cells=[['records','Papers'],['curves','Curves'],['points','Data points'],
    ['property_pairs','Property pairs'],['compositions','Compositions'],['vocabulary','KMDS properties']];
  document.getElementById('stats').innerHTML = cells.map(([k,l])=>
    `<div class="stat"><b>${(s[k]??0).toLocaleString()}</b><span>${l}</span></div>`).join('');
});
""" + HOME_JS
    return layout("Database", "home", body, js)


EXPLORE_JS = """
let PAIRS=[], CUR=null;
const COL=__PALETTE__;
function esc(s){return (s+'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
async function init(){
  PAIRS=(await (await fetch('/ui/pairs')).json()).pairs;
  document.getElementById('pairs').innerHTML = PAIRS.map((p,i)=>
    `<button class="pairbtn" data-i="${i}" onclick="pick(${i})">${esc(p.prop_x)} vs ${esc(p.prop_y)}<span class="n">${p.count}</span></button>`).join('');
  if(PAIRS.length) pick(0);
}
async function pick(i){
  document.querySelectorAll('.pairbtn').forEach(b=>b.classList.toggle('on',+b.dataset.i===i));
  const p=PAIRS[i];
  const d=await (await fetch('/ui/graph?prop_x='+encodeURIComponent(p.prop_x)+'&prop_y='+encodeURIComponent(p.prop_y))).json();
  CUR=d; draw();
  document.getElementById('ptitle').textContent = p.prop_x+' vs '+p.prop_y;
  document.getElementById('pmeta').textContent = d.count+' curve(s) across '+new Set(d.curves.map(c=>c.sid)).size+' paper(s)';
}
function draw(){
  if(!CUR) return;
  const logx=document.getElementById('logx').checked, logy=document.getElementById('logy').checked;
  const cv=document.getElementById('cv'), dpr=window.devicePixelRatio||1;
  const W=cv.clientWidth, H=380; cv.width=W*dpr; cv.height=H*dpr;
  const g=cv.getContext('2d'); g.scale(dpr,dpr); g.clearRect(0,0,W,H);
  const P=54, tx=v=>logx?Math.log10(Math.max(v,1e-12)):v, ty=v=>logy?Math.log10(Math.max(v,1e-12)):v;
  let xs=[],ys=[]; CUR.curves.forEach(c=>c.points.forEach(p=>{xs.push(tx(p[0]));ys.push(ty(p[1]))}));
  if(!xs.length){g.fillStyle='#5b7590';g.fillText('no data',P,H/2);return;}
  const xmin=Math.min(...xs),xmax=Math.max(...xs),ymin=Math.min(...ys),ymax=Math.max(...ys);
  const sx=v=>P+(W-P-20)*((tx(v)-xmin)/((xmax-xmin)||1)), sy=v=>H-P-(H-P-20)*((ty(v)-ymin)/((ymax-ymin)||1));
  // grid + axes
  g.strokeStyle='#eef2f7';g.lineWidth=1;
  for(let k=0;k<=5;k++){const y=H-P-(H-P-20)*k/5;g.beginPath();g.moveTo(P,y);g.lineTo(W-20,y);g.stroke();}
  g.strokeStyle='#9fb6cf';g.beginPath();g.moveTo(P,20);g.lineTo(P,H-P);g.lineTo(W-20,H-P);g.stroke();
  g.fillStyle='#5b7590';g.font='10px sans-serif';
  const fmt=v=>{const a=Math.abs(v);return (a&&(a<0.01||a>=1e4))?v.toExponential(1):(+v.toPrecision(4))};
  g.fillText(fmt(logx?10**xmin:xmin),P,H-P+14);g.fillText(fmt(logx?10**xmax:xmax),W-60,H-P+14);
  g.fillText(fmt(logy?10**ymax:ymax),6,26);g.fillText(fmt(logy?10**ymin:ymin),6,H-P);
  g.fillStyle='#12263d';g.font='12px sans-serif';g.textAlign='center';
  g.fillText(CUR.prop_x+' ('+CUR.unit_x+')'+(logx?' log':''),W/2,H-14);
  g.save();g.translate(14,H/2);g.rotate(-Math.PI/2);g.fillText(CUR.prop_y+' ('+CUR.unit_y+')'+(logy?' log':''),0,0);g.restore();
  g.textAlign='left';
  // color by composition
  const comps=[...new Set(CUR.curves.map(c=>c.composition||c.sample))];
  const cmap={}; comps.forEach((c,i)=>cmap[c]=COL[i%COL.length]);
  CUR.curves.forEach(c=>{
    const col=cmap[c.composition||c.sample];
    const sp=[...c.points].sort((a,b)=>a[0]-b[0]);   // draw line in x order (no zig-zag)
    g.strokeStyle=col;g.lineWidth=1.6;g.beginPath();
    sp.forEach((p,j)=>{const X=sx(p[0]),Y=sy(p[1]);j?g.lineTo(X,Y):g.moveTo(X,Y)});g.stroke();
    g.fillStyle=col;
    sp.forEach((p,j)=>{if(j%3)return;g.beginPath();g.arc(sx(p[0]),sy(p[1]),1.6,0,7);g.fill()});
  });
  document.getElementById('legend').innerHTML=comps.map(c=>
    `<span><i style="background:${cmap[c]}"></i>${esc(c)}</span>`).join('');
}
window.addEventListener('resize',draw);
init();
""".replace("__PALETTE__", json.dumps(PALETTE))


def explore_page():
    body = """
<div class="card">
  <h1>Property explorer</h1>
  <p class="muted" style="margin-top:0">Every digitized curve of a property pair, across all papers — the cross-paper view.</p>
  <div id="pairs"></div>
</div>
<div class="card">
  <h2 id="ptitle">—</h2><div class="muted small" id="pmeta"></div>
  <div class="toolbar">
    <label class="ck"><input type="checkbox" id="logx" onchange="draw()">log x</label>
    <label class="ck"><input type="checkbox" id="logy" onchange="draw()">log y</label>
  </div>
  <canvas id="cv" class="plot" style="width:100%;height:380px"></canvas>
  <div class="legend" id="legend"></div>
</div>"""
    return layout("Explore", "explore", body, EXPLORE_JS)


UPLOAD_JS = """
const drop=document.getElementById('drop'), fileIn=document.getElementById('file');
let RECORD=null, FNAME='';
function esc(s){return (s+'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function extract(text){
  // accept a raw KMDS json OR an AutoLineDigitizer paper_record_view.html
  const m=text.match(/window\\.__STUDIO_RECORD\\s*=\\s*(\\{[\\s\\S]*?\\});\\s*\\n/);
  return JSON.parse(m?m[1]:text);
}
function show(){
  const pub=(RECORD.metadata||{}).publication||{};
  document.getElementById('info').innerHTML=
    `<b>${esc(pub.title||'(untitled)')}</b><div class="muted small">DOI ${esc(pub.DOI||'—')} · `+
    `${(pub.samples||[]).length} samples · ${(pub.figures||[]).length} figures · from ${esc(FNAME)}</div>`;
  document.getElementById('go').disabled=false;
}
function read(f){FNAME=f.name; f.text().then(t=>{try{RECORD=extract(t);show();}
  catch(e){document.getElementById('info').innerHTML='<span style="color:#c0392b">Could not parse: '+esc(e.message)+'</span>';}});}
fileIn.addEventListener('change',e=>e.target.files[0]&&read(e.target.files[0]));
['dragover','dragenter'].forEach(ev=>drop.addEventListener(ev,e=>{e.preventDefault();drop.style.borderColor='#1d5fa0';}));
['dragleave','drop'].forEach(ev=>drop.addEventListener(ev,e=>{e.preventDefault();drop.style.borderColor='';}));
drop.addEventListener('drop',e=>e.dataTransfer.files[0]&&read(e.dataTransfer.files[0]));
document.getElementById('go').addEventListener('click',async()=>{
  const key=document.getElementById('key').value.trim();
  if(!key){alert('Enter your API key');return;}
  const out=document.getElementById('out'); out.textContent='Uploading…';
  const r=await fetch('/api/v1/records',{method:'POST',
    headers:{'Content-Type':'application/json','X-API-Key':key},body:JSON.stringify(RECORD)});
  const d=await r.json();
  if(r.ok){out.innerHTML=`<div style="color:#27ae60">✓ Stored as <a href="/view/${d.sid}">SID-${d.sid}</a> — `+
    `${d.curves_indexed} curves indexed, ${d.n_violations} schema note(s).</div>`;}
  else{out.innerHTML='<span style="color:#c0392b">HTTP '+r.status+': '+esc(JSON.stringify(d.detail||d))+'</span>';}
});
"""


def upload_page():
    body = """
<div class="card">
  <h1>Upload a record</h1>
  <p class="muted" style="margin-top:0">Drop a KMDS record (<code>.json</code>) or an
  AutoLineDigitizer <code>paper_record_view.html</code>. It's validated against the KMDS
  schema, stored, and its curves indexed.</p>
  <div id="drop" style="border:2px dashed var(--line);border-radius:12px;padding:26px;text-align:center;
    background:#f6faff;cursor:pointer" onclick="document.getElementById('file').click()">
    <div style="font-size:26px">&#8681;</div>
    <div>Drop file here, or <b style="color:var(--blue)">choose file</b></div>
    <input type="file" id="file" accept=".json,.html" style="display:none">
  </div>
  <div id="info" style="margin:14px 0"></div>
  <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
    <input class="search" id="key" placeholder="API key (sd3_…)" style="max-width:420px" type="password">
    <button id="go" disabled class="pairbtn on" style="cursor:pointer">Upload to database</button>
  </div>
  <div id="out" style="margin-top:14px"></div>
</div>"""
    return layout("Upload", "upload", body, UPLOAD_JS)


def _svg_plot(curves):
    pts = [p for c in curves for p in c["points"]]
    if not pts:
        return ""
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
    W, H, P = 620, 280, 48
    sx = lambda v: P + (W - P - 18) * ((v - xmin) / (xmax - xmin) if xmax > xmin else 0.5)
    sy = lambda v: H - P - (H - P - 18) * ((v - ymin) / (ymax - ymin) if ymax > ymin else 0.5)
    marks, leg = [], []
    for i, c in enumerate(curves):
        col = PALETTE[i % len(PALETTE)]
        sp = sorted(c["points"], key=lambda p: p[0])   # x order, no zig-zag
        poly = " ".join(f"{sx(p[0]):.1f},{sy(p[1]):.1f}" for p in sp)
        marks.append(f'<polyline points="{poly}" fill="none" stroke="{col}" stroke-width="1.5"/>')
        leg.append(f'<span><i style="background:{col}"></i>{esc(c["sample"])}</span>')
    fmt = lambda v: (f"{v:.2e}" if v and (abs(v) < 0.01 or abs(v) >= 1e4) else f"{v:.4g}")
    ax = (f'<line x1="{P}" y1="{H-P}" x2="{W-18}" y2="{H-P}" stroke="#9fb6cf"/>'
          f'<line x1="{P}" y1="18" x2="{P}" y2="{H-P}" stroke="#9fb6cf"/>'
          f'<text x="{P}" y="{H-P+15}" font-size="10" fill="#5b7590">{fmt(xmin)}</text>'
          f'<text x="{W-18}" y="{H-P+15}" text-anchor="end" font-size="10" fill="#5b7590">{fmt(xmax)}</text>'
          f'<text x="{P-6}" y="{H-P}" text-anchor="end" font-size="10" fill="#5b7590">{fmt(ymin)}</text>'
          f'<text x="{P-6}" y="24" text-anchor="end" font-size="10" fill="#5b7590">{fmt(ymax)}</text>')
    return (f'<svg viewBox="0 0 {W} {H}" class="plot" style="width:100%;max-width:{W}px">{ax}{"".join(marks)}</svg>'
            f'<div class="legend">{"".join(leg)}</div>')


def record_page(rec, curves, record_json):
    """rec: Record row; curves: GraphCurve rows; record_json: parsed KMDS dict."""
    pub = (record_json.get("metadata") or {}).get("publication") or {}
    # group curves by property pair for plotting
    groups = {}
    for c in curves:
        groups.setdefault((c.prop_x, c.prop_y), []).append(
            {"sample": c.sample_name, "composition": c.composition,
             "points": json.loads(c.points_json)})
    plots = ""
    for (px, py), cs in groups.items():
        plots += (f'<div class="card"><h2>{esc(px)} vs {esc(py)}</h2>'
                  f'<div class="muted small">{len(cs)} curve(s), {sum(len(c["points"]) for c in cs)} points</div>'
                  f'{_svg_plot(cs)}</div>')
    # samples table
    samps = ""
    for smp in pub.get("samples") or []:
        comps = ", ".join(c.get("name", "") for c in (smp.get("components") or []))
        samps += (f'<tr><td>{esc(smp.get("sample local id",""))}</td>'
                  f'<td>{esc(smp.get("name",""))}</td><td>{esc(comps)}</td>'
                  f'<td class="muted">{esc((smp.get("mixing type") or ""))}</td></tr>')
    samp_card = (f'<div class="card"><h2>Samples ({len(pub.get("samples") or [])})</h2>'
                 f'<table class="samps"><thead><tr><th>ID</th><th>Name</th><th>Components</th>'
                 f'<th>Mixing</th></tr></thead><tbody>{samps}</tbody></table></div>') if samps else ""
    # materials
    mats = record_json.get("materials") or []
    mat_chips = "".join(f'<span class="chip">{esc(m.get("name",""))}</span>' for m in mats if m.get("name"))
    mat_card = f'<div class="card"><h2>Materials ({len(mats)})</h2>{mat_chips or "<span class=muted>none</span>"}</div>' if mats else ""

    head = (f'<div class="card"><div class="muted small">SID-{rec.sid} · {esc(rec.doi)} · '
            f'{"✓ valid" if rec.n_violations==0 else str(rec.n_violations)+" schema notes"}</div>'
            f'<h1>{esc(rec.title or "(untitled)")}</h1>'
            f'<a href="/">&larr; database</a> &nbsp;·&nbsp; '
            f'<a href="/explore">explore curves</a> &nbsp;·&nbsp; '
            f'<a href="/ui/record/{rec.sid}">raw KMDS JSON</a></div>')
    body = head + samp_card + mat_card + (plots or '<div class="card muted">no digitized curves</div>')
    return layout(f"SID-{rec.sid}", "home", body)


def register_ui(app, engine, Record, GraphCurve, select, Session):
    @app.get("/", response_class=HTMLResponse)
    def _home():
        return home_page()

    @app.get("/explore", response_class=HTMLResponse)
    def _explore():
        return explore_page()

    @app.get("/upload", response_class=HTMLResponse)
    def _upload():
        return upload_page()

    @app.get("/ui/record/{sid}")
    def _raw(sid: int):
        with Session(engine) as s:
            rec = s.exec(select(Record).where(Record.sid == sid)).first()
            if not rec:
                return {"error": f"no record {sid}"}
            return json.loads(rec.record_json)

    @app.get("/view/{sid}", response_class=HTMLResponse)
    def _view(sid: int):
        with Session(engine) as s:
            rec = s.exec(select(Record).where(Record.sid == sid)).first()
            if not rec:
                return layout("not found", "home",
                              f'<div class="card">No record SID-{sid}. <a href="/">back</a></div>')
            curves = s.exec(select(GraphCurve).where(GraphCurve.record_sid == sid)).all()
            record_json = json.loads(rec.record_json)
            return record_page(rec, curves, record_json)
