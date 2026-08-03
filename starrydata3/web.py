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
.pv{margin:16px 0}.pv-head{margin-bottom:6px}
.bar-row{display:grid;grid-template-columns:230px 1fr 130px;gap:10px;align-items:center;padding:3px 0}
.bar-label{font-size:12.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bar-track{background:var(--pane);border-radius:6px;height:14px}
.bar-fill{background:linear-gradient(90deg,var(--blue),var(--blue2));height:100%;border-radius:6px;min-width:2px}
.bar-val{font-size:12px;color:var(--ink2);text-align:right;white-space:nowrap}
.warn{color:#c0392b}
.abstract{color:var(--ink2);font-size:13.5px;max-width:86ch}
.figrow{padding:10px 0;border-bottom:1px solid var(--line)}.figrow:last-child{border-bottom:0}
"""


def layout(title, active, body, extra_js=""):
    nav = "".join(
        f'<a href="{href}" class="{"on" if active==key else ""}">{label}</a>'
        for key, href, label in [("home", "/", "Database"),
                                  ("explore", "/explore", "Explore"),
                                  ("materials", "/materials", "Materials"),
                                  ("find", "/find", "Find"),
                                  ("queue", "/queue", "Queue"),
                                  ("review", "/review", "Review"),
                                  ("upload", "/upload", "Upload"),
                                  ("api", "/docs", "API")])
    return HTMLResponse(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} · Starrydata3</title>
<style>{CSS}</style></head><body>
<header class="top"><div class="brand"><span class="star">&#9733;</span>Starrydata3</div>
<nav>{nav}</nav><div class="sp">KMDS materials-data service</div></header>
<main>{body}</main><script>{extra_js}</script></body></html>""",
        headers={"Cache-Control": "no-store, must-revalidate", "Pragma": "no-cache"})


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
    <td>${p.n_samples}</td><td>${p.curves}</td><td>${p.points||0}</td></tr>`).join('');
  document.getElementById('rows').innerHTML = rows ||
    '<tr><td colspan=5 class=muted>no matching papers</td></tr>';
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
  <table><thead><tr><th>ID</th><th>Paper</th><th>Samples</th><th>Curves</th><th>Values</th></tr></thead>
  <tbody id="rows"></tbody></table>
  <div class="small" style="margin-top:10px">
    <a href="/ui/export.json"><b>&#8681; Download full database (KMDS JSON)</b></a>
    &nbsp;·&nbsp; <a href="/ui/export.csv">&#8681; Dataset as CSV (all curves + values)</a>
    <span class="muted">— the JSON is a complete backup: re-upload each record to restore.</span>
  </div>
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
function unitTag(u){return (u&&u!=='-')?(' ('+u+')'):''}
async function init(){
  PAIRS=(await (await fetch('/ui/pairs')).json()).pairs;
  document.getElementById('pairs').innerHTML = PAIRS.map((p,i)=>
    `<button class="pairbtn" data-i="${i}" onclick="pick(${i})">${esc(p.prop_x)}${esc(unitTag(p.unit_x))} vs ${esc(p.prop_y)}${esc(unitTag(p.unit_y))}`+
    `<span class="n">${p.count}</span>${p.kmds?'':' <span title="not in KMDS vocabulary" style="color:#c0392b">&#9888;</span>'}</button>`).join('');
  if(PAIRS.length) pick(0);
}
async function pick(i){
  document.querySelectorAll('.pairbtn').forEach(b=>b.classList.toggle('on',+b.dataset.i===i));
  const p=PAIRS[i];
  const qs='prop_x='+encodeURIComponent(p.prop_x)+'&prop_y='+encodeURIComponent(p.prop_y)
    +'&unit_x='+encodeURIComponent(p.unit_x||'')+'&unit_y='+encodeURIComponent(p.unit_y||'');
  const d=await (await fetch('/ui/graph?'+qs)).json();
  try{const b=await (await fetch('/ui/band?'+qs)).json();
      d.band=(b.x&&b.x.length>=2)?b:null;}catch(e){d.band=null;}
  CUR=d; draw();
  document.getElementById('ptitle').textContent = p.prop_x+unitTag(p.unit_x)+' vs '+p.prop_y+unitTag(p.unit_y);
  const nc=d.curves.filter(c=>c.type!=='points').length, np=d.curves.length-nc;
  document.getElementById('pmeta').textContent = nc+' curve(s), '+np+' point series across '
    +new Set(d.curves.map(c=>c.sid)).size+' paper(s)';
  document.getElementById('dl').href='/ui/graph.csv?'+qs;
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
  // corpus percentile band (p10–p90 + dashed median) behind everything
  if(CUR.band && document.getElementById('band').checked){
    const b=CUR.band;
    g.beginPath();
    b.x.forEach((x,j)=>{const X=sx(x),Y=sy(b.p90[j]); j?g.lineTo(X,Y):g.moveTo(X,Y);});
    for(let j=b.x.length-1;j>=0;j--) g.lineTo(sx(b.x[j]),sy(b.p10[j]));
    g.closePath(); g.fillStyle='rgba(159,182,207,0.28)'; g.fill();
    g.beginPath(); g.setLineDash([5,4]); g.strokeStyle='#9fb6cf'; g.lineWidth=1.2;
    b.x.forEach((x,j)=>{const X=sx(x),Y=sy(b.p50[j]); j?g.lineTo(X,Y):g.moveTo(X,Y);});
    g.stroke(); g.setLineDash([]);
  }
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
  const cmap={}, csid={}; comps.forEach((c,i)=>cmap[c]=COL[i%COL.length]);
  CUR.curves.forEach(c=>{const k=c.composition||c.sample; if(!(k in csid)) csid[k]=c.sid;});
  const yspan=(ymax-ymin)||1, xspan=(xmax-xmin)||1;
  CUR.curves.forEach(c=>{
    const col=cmap[c.composition||c.sample];
    const sp=[...c.points].sort((a,b)=>a[0]-b[0]);   // draw line in x order (no zig-zag)
    if(c.type==='points'){                            // measured values: markers only
      g.fillStyle=col;
      sp.forEach(p=>{g.beginPath();g.arc(sx(p[0]),sy(p[1]),4,0,7);g.fill();});
      g.strokeStyle='#fff';g.lineWidth=1;
      sp.forEach(p=>{g.beginPath();g.arc(sx(p[0]),sy(p[1]),4,0,7);g.stroke();});
      return;
    }
    // break the line where consecutive points jump abnormally far in x OR y
    // (a straight "bridge" across a broken/merged digitization) — keep real
    // segments connected, skip the bridge.
    const step=j=>Math.hypot((tx(sp[j][0])-tx(sp[j-1][0]))/xspan,(ty(sp[j][1])-ty(sp[j-1][1]))/yspan);
    g.strokeStyle=col;g.lineWidth=1.6;g.beginPath();let pen=false;
    sp.forEach((p,j)=>{const X=sx(p[0]),Y=sy(p[1]);
      if(j>0 && step(j)>0.18) pen=false;
      if(!pen){g.moveTo(X,Y);pen=true;}else g.lineTo(X,Y);});
    g.stroke();
    g.fillStyle=col;
    sp.forEach((p,j)=>{if(j%3)return;g.beginPath();g.arc(sx(p[0]),sy(p[1]),1.6,0,7);g.fill()});
  });
  document.getElementById('legend').innerHTML=comps.map(c=>
    `<a href="/view/${csid[c]}" style="color:inherit"><span><i style="background:${cmap[c]}"></i>${esc(c)}</span></a>`).join('');
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
    <label class="ck"><input type="checkbox" id="band" checked onchange="draw()">corpus band</label>
    <a id="dl" class="small" href="/ui/export.csv">&#8681; CSV of this plot</a>
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



def _svg_plot(curves, band=None):
    pts = [p for c in curves for p in c["points"]]
    if not pts:
        return ""
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    if band:                      # corpus band shares the axes
        xs = xs + band["x"] + band["x"]
        ys = ys + band["p10"] + band["p90"]
    xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
    W, H, P = 620, 280, 48
    sx = lambda v: P + (W - P - 18) * ((v - xmin) / (xmax - xmin) if xmax > xmin else 0.5)
    sy = lambda v: H - P - (H - P - 18) * ((v - ymin) / (ymax - ymin) if ymax > ymin else 0.5)
    marks, leg = [], []
    if band:
        # shaded p10–p90 envelope of the rest of the corpus + dashed median,
        # drawn first so the record's own curves sit on top
        ring = (list(zip(band["x"], band["p90"]))
                + list(zip(band["x"][::-1], band["p10"][::-1])))
        poly = " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in ring)
        med = " ".join(f"{sx(x):.1f},{sy(y):.1f}"
                       for x, y in zip(band["x"], band["p50"]))
        marks.append(f'<polygon points="{poly}" fill="#dbe7f5" opacity="0.55"/>')
        marks.append(f'<polyline points="{med}" fill="none" stroke="#9fb6cf" '
                     f'stroke-width="1.2" stroke-dasharray="5 4"/>')
        leg.append('<span><i style="background:#dbe7f5"></i>corpus p10–p90 '
                   f'({band["n_records"]} other paper(s))</span>')
    for i, c in enumerate(curves):
        col = PALETTE[i % len(PALETTE)]
        sp = sorted(c["points"], key=lambda p: p[0])   # x order, no zig-zag
        xspan = (max(xs) - min(xs)) or 1
        yspan = (max(ys) - min(ys)) or 1
        # split at abnormally large jumps in x OR y (bridges across broken pieces)
        seg, segs = [], []
        for j, p in enumerate(sp):
            if j and ((abs(p[0] - sp[j - 1][0]) / xspan) ** 2
                      + (abs(p[1] - sp[j - 1][1]) / yspan) ** 2) ** 0.5 > 0.18:
                segs.append(seg); seg = []
            seg.append(p)
        segs.append(seg)
        for s in segs:
            if len(s) < 2:
                continue
            poly = " ".join(f"{sx(p[0]):.1f},{sy(p[1]):.1f}" for p in s)
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


def _fmt_num(v):
    try:
        a = abs(float(v))
    except (TypeError, ValueError):
        return esc(v)
    if a and (a < 0.01 or a >= 1e4):
        return f"{v:.3g}"
    return f"{float(v):g}"


def _points_cards(points):
    """Per-property bar charts of a record's measured values (Fig-2a style:
    property across samples/materials)."""
    if not points:
        return ""
    groups = {}
    for p in points:
        g = groups.setdefault((p.prop, p.unit, p.source), {"kmds": p.kmds, "rows": []})
        g["rows"].append(p)
    charts = ""
    for (prop, unit, source), g in sorted(groups.items(), key=lambda kv: (not kv[1]["kmds"], kv[0][0])):
        rows = g["rows"]
        vmax = max(abs(r.value) for r in rows) or 1.0
        bars = ""
        for r in rows:
            envt = (f' <span class="muted">@ {_fmt_num(r.env_temperature)} °C</span>'
                    if r.env_temperature is not None else "")
            bars += (f'<div class="bar-row"><div class="bar-label" title="{esc(r.entity)}">{esc(r.entity)}</div>'
                     f'<div class="bar-track"><div class="bar-fill" style="width:{max(2, abs(r.value) / vmax * 100):.1f}%"></div></div>'
                     f'<div class="bar-val">{_fmt_num(r.value)}{envt}</div></div>')
        badge = "" if g["kmds"] else ' <span class="warn small" title="not in the KMDS vocabulary">&#9888; non-KMDS</span>'
        src = "measured value" if source == "material" else "from table"
        charts += (f'<div class="pv"><div class="pv-head"><b>{esc(prop)}</b> '
                   f'<span class="muted small">{esc(unit) if unit else ""} · {src}</span>{badge}</div>{bars}</div>')
    return (f'<div class="card"><h2>Measured properties ({len(groups)})</h2>'
            f'<p class="muted small" style="margin-top:0">Single measured values from the record&rsquo;s '
            f'material property trees and data tables.</p>{charts}</div>')


def _tables_cards(pub):
    out = ""
    for t in pub.get("tables") or []:
        headers = t.get("headers") or []
        trs = ""
        for row in t.get("rows") or []:
            if not isinstance(row, (list, tuple)):
                continue
            tds = "".join(f"<td>{_fmt_num(v) if isinstance(v, (int, float)) else esc(v)}</td>" for v in row)
            trs += f"<tr>{tds}</tr>"
        if not trs:
            continue
        ths = "".join(f"<th>{esc(h)}</th>" for h in headers)
        out += (f'<div class="card"><h2>{esc(t.get("table name") or t.get("table local id") or "Table")}</h2>'
                f'<div class="muted small">{esc(t.get("caption summary") or "")}</div>'
                f'<div style="overflow-x:auto"><table><thead><tr>{ths}</tr></thead>'
                f'<tbody>{trs}</tbody></table></div></div>')
    return out


def _figures_card(pub):
    from starrydata3.app import sanitize_unit
    figs = pub.get("figures") or []
    rows = ""
    for f in figs:
        chips = ""
        for gr in f.get("graphs") or []:
            axes = {str(a.get("axis") or "").lower(): a for a in (gr.get("axes") or [])}
            xa, ya = axes.get("x", {}), axes.get("y", {})
            xt = (xa.get("quantity") or {}).get("term") or "?"
            yt = (ya.get("quantity") or {}).get("term") or "?"
            xu, yu = sanitize_unit(xa.get("unit")), sanitize_unit(ya.get("unit"))
            xu = f" ({xu})" if xu not in ("", "-") else ""
            yu = f" ({yu})" if yu not in ("", "-") else ""
            chips += (f'<span class="chip"><b>{esc(gr.get("graph name") or gr.get("graph local id") or "")}</b> '
                      f'{esc(yt)}{esc(yu)} vs {esc(xt)}{esc(xu)}</span>')
        rows += (f'<div class="figrow"><b>{esc(f.get("figure name") or "")}</b> '
                 f'<span class="muted small">{esc(f.get("caption summary") or "")}</span>'
                 f'<div style="margin-top:4px">{chips}</div></div>')
    if not rows:
        return ""
    return f'<div class="card"><h2>Figures &amp; graphs ({len(figs)})</h2>{rows}</div>'


_FLAG_STYLE = {"ok":   ("#27ae60", "✓"),
               "warn": ("#e67e22", "⚠"),
               "fail": ("#e74c3c", "✗")}


def _consistency_card(flags):
    """Cross-property consistency check results (PF = S²σ, ZT = S²σT/κ):
    the same-sample curves recomputed against each other."""
    if not flags:
        return ""
    flags = sorted(flags, key=lambda f: ({"fail": 0, "warn": 1, "ok": 2}
                                         .get(f.status, 3), f.sample))
    rows = ""
    for f in flags:
        col, mark = _FLAG_STYLE.get(f.status, ("#5b7590", "?"))
        rows += (f'<div style="margin:6px 0">'
                 f'<b style="color:{col}">{mark} {esc(f.check_name)}</b> '
                 f'<span class="muted small">[{esc(f.sample)}]</span> '
                 f'<span style="color:{col}">{f.status}</span> '
                 f'<span class="muted small">median deviation {f.median_dev:.0%} '
                 f'over {f.n_points} points — {esc(f.detail)}</span></div>')
    n_bad = sum(1 for f in flags if f.status != "ok")
    note = ("all relations hold within digitization error" if n_bad == 0 else
            "deviations like these usually mean a wrong axis unit/scale in the "
            "digitization or in the paper itself — open the figure and check "
            "the axis calibration")
    return (f'<div class="card"><h2>Consistency checks ({len(flags)})</h2>'
            f'<p class="muted small" style="margin-top:0">Thermoelectric relations '
            f'recomputed from this record&rsquo;s own curves. {note}.</p>{rows}</div>')


def _provenance_card(sid, entries):
    """Digitization audit trail: the figure crops + calibration each curve
    came from."""
    if not entries:
        return ""
    tiles = ""
    for e in entries:
        cal = json.loads(e.calibration_json or "{}")
        bits = []
        if cal.get("x1_val") is not None and cal.get("x2_val") is not None:
            log = " (log)" if cal.get("xIsLogScale") else ""
            bits.append(f'x: {_fmt_num(cal["x1_val"])}–{_fmt_num(cal["x2_val"])}{log}')
        if cal.get("y1_val") is not None and cal.get("y2_val") is not None:
            log = " (log)" if cal.get("yIsLogScale") else ""
            bits.append(f'y: {_fmt_num(cal["y1_val"])}–{_fmt_num(cal["y2_val"])}{log}')
        ext = json.loads(e.extractor_json or "{}")
        who = " · ".join(x for x in (
            ext.get("app", ""), e.curator and f"curator: {e.curator}",
            e.created_at[:10]) if x)
        img = (f'<a href="/ui/provenance/{sid}/{e.id}.png" target="_blank">'
               f'<img src="/ui/provenance/{sid}/{e.id}.png" loading="lazy" '
               f'style="max-width:220px;max-height:160px;border:1px solid var(--line);'
               f'border-radius:6px;background:#fff"></a>' if e.image_png else "")
        page_no = f' <span class="muted small">p.{e.page}</span>' if e.page else ""
        tiles += (f'<div style="display:inline-block;vertical-align:top;margin:6px 10px 6px 0;max-width:240px">'
                  f'{img}<div class="small"><b>{esc(e.label)}</b>{page_no}</div>'
                  f'<div class="muted small">{esc("; ".join(bits))}</div>'
                  f'<div class="muted small">{esc(who)}</div></div>')
    return (f'<div class="card"><h2>Digitization provenance ({len(entries)})</h2>'
            f'<p class="muted small" style="margin-top:0">The source figure crops and '
            f'axis calibrations these curves were extracted from — click a crop to '
            f'compare it against the plots above.</p>{tiles}</div>')


def record_page(rec, curves, points, record_json, flags=(), provenance=()):
    """rec: Record row; curves: GraphCurve rows; points: PointValue rows;
    record_json: parsed KMDS dict; flags: QualityFlag rows."""
    pub = (record_json.get("metadata") or {}).get("publication") or {}
    # group curves by property pair + unit for plotting (a Hz sweep and a GHz
    # sweep of the same property are different plots)
    groups = {}
    for c in curves:
        groups.setdefault((c.prop_x, c.prop_y, c.unit_x, c.unit_y), []).append(
            {"sample": c.sample_name, "composition": c.composition,
             "points": json.loads(c.points_json)})
    from starrydata3.app import property_band
    plots = ""
    for (px, py, ux, uy), cs in groups.items():
        band = property_band(px, py, ux, uy, exclude_sid=rec.sid)
        ux = f" ({ux})" if ux not in ("", "-") else ""
        uy = f" ({uy})" if uy not in ("", "-") else ""
        note = ("" if not band else
                " · shaded: where the rest of the database lies")
        plots += (f'<div class="card"><h2>{esc(px)}{esc(ux)} vs {esc(py)}{esc(uy)}</h2>'
                  f'<div class="muted small">{len(cs)} curve(s), {sum(len(c["points"]) for c in cs)} points{note}</div>'
                  f'{_svg_plot(cs, band)}</div>')
    if not plots:
        plots = ('<div class="card muted small">no digitized curves — digitize the '
                 'paper\'s PDF in AutoLineDigitizer and use its '
                 '<i>Upload approved &rarr; Starrydata3</i> button</div>')
    # abstract + keywords
    abstract = ""
    if pub.get("abstract"):
        kws = (pub.get("author keywords") or
               (record_json.get("metadata") or {}).get("keywords") or [])
        chips = "".join(f'<span class="chip">{esc(k)}</span>' for k in kws[:12])
        abstract = (f'<div class="card"><h2>Abstract</h2>'
                    f'<div class="abstract">{esc(pub["abstract"])}</div>'
                    f'<div style="margin-top:8px">{chips}</div></div>')
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
            f'<a href="/kmds/{rec.sid}"><b>full KMDS record</b></a> &nbsp;·&nbsp; '
            f'<a href="/explore">explore curves</a> &nbsp;·&nbsp; '
            f'<a href="/ui/record/{rec.sid}">raw KMDS JSON</a></div>')
    body = (head + _consistency_card(flags) + abstract + _points_cards(points)
            + plots + _provenance_card(rec.sid, provenance) + _tables_cards(pub)
            + samp_card + mat_card + _figures_card(pub))
    return layout(f"SID-{rec.sid}", "home", body)


MATERIALS_JS = """
async function init(){
  const d = await (await fetch('/ui/materials')).json();
  const el = document.getElementById('mats');
  if(!d.count){el.innerHTML='<div class="card muted">No normalized compositions yet — '
    +'materials appear here once curves carry parseable chemical formulas.</div>';return;}
  el.innerHTML = d.materials.map(m=>`<div class="card">
    <h2><a href="/material/${encodeURIComponent(m.comp_key)}">${m.comp_key}</a></h2>
    <div class="muted small">elements: ${m.elements} · ${m.records.length} paper(s) · ${m.n_curves} curve(s)</div>
    <div style="margin:6px 0">${m.properties.map(p=>'<span class="chip">'+p+'</span>').join('')}</div>
    <div class="muted small">as written: ${m.compositions.join(' · ')}</div>
  </div>`).join('');
}
init();
"""


def materials_page():
    body = """
<div class="card"><h1>Materials</h1>
<p class="muted" style="margin-top:0">Every normalized composition in the database —
one dossier per material family, regardless of which paper the curves came from.</p></div>
<div id="mats"></div>"""
    return layout("Materials", "materials", body, MATERIALS_JS)


FIND_JS = """
async function go(){
  const qs = new URLSearchParams();
  qs.set('prop', document.getElementById('fprop').value.trim());
  for (const [id,key] of [['fvmin','vmin'],['fvmax','vmax'],['fxmin','xmin'],['fxmax','xmax']]){
    const v = document.getElementById(id).value.trim(); if(v) qs.set(key, v);
  }
  const el = document.getElementById('felem').value.trim(); if(el) qs.set('element', el);
  const d = await (await fetch('/ui/facet?'+qs)).json();
  const out = document.getElementById('fout');
  if(!d.count){out.innerHTML='<div class="muted">No matching curves.</div>';return;}
  out.innerHTML = '<div class="muted small">'+d.count+' matching curve(s)</div>'
    +'<div style="overflow-x:auto"><table><thead><tr><th>best value</th><th>at</th>'
    +'<th>sample</th><th>material</th><th>paper</th></tr></thead><tbody>'
    +d.hits.map(h=>`<tr><td><b>${(+h.best_value.toPrecision(4))}</b> ${h.unit||''}</td>`
      +`<td>${(+h.best_x.toPrecision(4))}</td>`
      +`<td>${h.sample}</td>`
      +`<td>${h.comp_key?('<a href="/material/'+encodeURIComponent(h.comp_key)+'">'+h.comp_key+'</a>'):'—'}</td>`
      +`<td><a href="/view/${h.sid}">SID-${h.sid}</a></td></tr>`).join('')
    +'</tbody></table></div>';
}
document.getElementById('fgo').onclick = go;
"""


def find_page():
    body = """
<div class="card"><h1>Find</h1>
<p class="muted" style="margin-top:0">Range-at-condition search over every digitized curve:
&ldquo;which samples reach a property value in this window?&rdquo;</p>
<div style="display:flex;gap:10px;flex-wrap:wrap;align-items:end">
  <label class="small">property<br><input class="search" id="fprop" value="ZT" style="width:180px"></label>
  <label class="small">value ≥<br><input class="search" id="fvmin" style="width:90px" placeholder="1"></label>
  <label class="small">value ≤<br><input class="search" id="fvmax" style="width:90px"></label>
  <label class="small">x from<br><input class="search" id="fxmin" style="width:90px" placeholder="500"></label>
  <label class="small">x to<br><input class="search" id="fxmax" style="width:90px" placeholder="700"></label>
  <label class="small">elements<br><input class="search" id="felem" style="width:110px" placeholder="Te, Bi"></label>
  <button id="fgo" class="pairbtn on" style="cursor:pointer">Search</button>
</div>
<p class="muted small">x is the condition axis (temperature by default). Elements must all be present.</p>
</div>
<div class="card" id="fout"><div class="muted">Set a filter and search.</div></div>"""
    return layout("Find", "find", body, FIND_JS)


def material_page(key, curves, titles):
    """Dossier: every curve of one normalized composition, across papers."""
    groups = {}
    for c in curves:
        groups.setdefault((c.prop_x, c.prop_y, c.unit_x, c.unit_y), []).append(
            {"sample": f"{c.sample_name} · SID-{c.record_sid}",
             "composition": c.composition, "points": json.loads(c.points_json)})
    plots = ""
    for (px, py, ux, uy), cs in sorted(groups.items(), key=lambda kv: kv[0][1]):
        uxs = f" ({ux})" if ux not in ("", "-") else ""
        uys = f" ({uy})" if uy not in ("", "-") else ""
        plots += (f'<div class="card"><h2>{esc(py)}{esc(uys)} vs {esc(px)}{esc(uxs)}</h2>'
                  f'<div class="muted small">{len(cs)} curve(s)</div>{_svg_plot(cs)}</div>')
    sids = sorted({c.record_sid for c in curves})
    papers = "".join(f'<li><a href="/view/{sid}">SID-{sid}</a> '
                     f'{esc(titles.get(sid, ""))}</li>' for sid in sids)
    els = curves[0].elements if curves else ""
    head = (f'<div class="card"><div class="muted small">material dossier · elements: {esc(els)}</div>'
            f'<h1>{esc(key)}</h1>'
            f'<a href="/materials">&larr; all materials</a></div>')
    return layout(key, "materials",
                  head + plots + f'<div class="card"><h2>Source papers ({len(sids)})</h2><ul>{papers}</ul></div>')


QUEUE_JS = """
async function load(){
  const d = await (await fetch('/ui/queue')).json();
  document.getElementById('qmeta').textContent =
    d.queued+' queued · '+d.digitized+' digitized';
  const rows = d.papers.map(p=>`<tr>
    <td>${p.status==='queued'
        ? '<span class="chip" style="background:#fdf3e3;color:#9a6a00">queued</span>'
        : '<a href="/view/'+p.sid+'"><span class="chip" style="background:#e3f3ea;color:#17804b">SID-'+p.sid+'</span></a>'}</td>
    <td><a href="https://doi.org/${encodeURIComponent(p.doi)}" target="_blank">${p.doi}</a></td>
    <td>${p.title||'<span class=muted>(bibliography pending)</span>'}
        <div class="muted small">${[p.authors, p.journal, p.year].filter(Boolean).join(' · ')}</div></td>
    <td class="muted small">${p.project}</td></tr>`).join('');
  document.getElementById('qbody').innerHTML = rows ||
    '<tr><td colspan="4" class="muted">Queue is empty — paste DOIs above.</td></tr>';
}
document.getElementById('qadd').onclick = async ()=>{
  const dois = document.getElementById('qdois').value.split(/\\s+/).filter(Boolean);
  const key = document.getElementById('qkey').value.trim();
  const out = document.getElementById('qout');
  if(!dois.length||!key){out.textContent='Paste DOIs and the API key first.';return;}
  out.textContent = 'Registering '+dois.length+' DOI(s)…';
  const r = await fetch('/api/v1/queue', {method:'POST',
    headers:{'Content-Type':'application/json','X-API-Key':key},
    body: JSON.stringify({dois, project: document.getElementById('qproj').value.trim()||'default'})});
  const d = await r.json();
  out.textContent = r.ok ? ('Added '+d.added.length+', skipped '+d.skipped_existing.length+' already queued.')
                         : ('Failed: '+JSON.stringify(d.detail||d));
  if(r.ok){document.getElementById('qdois').value='';load();}
};
load();
"""


def queue_page():
    body = """
<div class="card"><h1>Paper queue</h1>
<p class="muted" style="margin-top:0">Paste DOIs to register papers for digitization —
bibliography is fetched from CrossRef, and each paper flips to its SID automatically
when its record is uploaded.</p>
<textarea id="qdois" class="search" style="width:100%;height:70px" placeholder="one DOI per line"></textarea>
<div style="display:flex;gap:10px;margin-top:8px;flex-wrap:wrap">
  <input class="search" id="qproj" placeholder="project (default)" style="max-width:200px">
  <input class="search" id="qkey" type="password" placeholder="API key (sd3_…)" style="max-width:260px">
  <button id="qadd" class="pairbtn on" style="cursor:pointer">Add to queue</button>
  <span id="qout" class="muted small" style="align-self:center"></span>
</div></div>
<div class="card"><h2>Papers <span class="muted small" id="qmeta"></span></h2>
<div style="overflow-x:auto"><table><thead>
<tr><th>status</th><th>DOI</th><th>paper</th><th>project</th></tr></thead>
<tbody id="qbody"></tbody></table></div></div>"""
    return layout("Queue", "queue", body, QUEUE_JS)


REVIEW_LIST_JS = """
async function load(){
  const d = await (await fetch('/ui/review/sessions')).json();
  const chip = s => ({ready:'#e3ecff', in_review:'#fdf3e3', approved:'#e3f3ea'}[s]||'#eef2f7');
  document.getElementById('rbody').innerHTML = d.sessions.map(s=>`<tr>
    <td><span class="chip" style="background:${chip(s.status)}">${s.status}</span></td>
    <td><a href="/review/${s.id}"><b>${s.title||s.pdf}</b></a>
        <div class="muted small">${s.doi||''} · ${s.n_figures} figure(s), ${s.n_reviewed} reviewed · ${s.created_at}</div></td>
  </tr>`).join('') || '<tr><td colspan="2" class="muted">No batch sessions yet — run batch_run.py --push.</td></tr>';
}
load(); setInterval(load, 5000);
"""


def review_list_page():
    body = """
<div class="card"><h1>Batch review</h1>
<p class="muted" style="margin-top:0">Papers extracted on the GPU land here as the batch runs.
Open one to correct figures and edit its KMDS record, then approve to ingest.</p>
<div style="overflow-x:auto"><table><tbody id="rbody"></tbody></table></div></div>"""
    return layout("Review", "review", body, REVIEW_LIST_JS)


REVIEW_SESSION_JS = r"""
const SID = window.__SID; let S=null, cur=null, sel=0, mode='drag', drag=null;
const $ = id => document.getElementById(id);
const COL = __PALETTE__;
async function api(url, method, body){
  const r = await fetch(url, {method, headers:{'Content-Type':'application/json',
    'X-API-Key': $('rkey').value.trim()}, body: body?JSON.stringify(body):undefined});
  return {ok:r.ok, data: await r.json().catch(()=>({}))};
}
async function load(){
  S = await (await fetch('/ui/review/session/'+SID)).json();
  $('rtitle').textContent = S.title || S.pdf;
  $('rmeta').textContent = (S.doi||'') + ' · ' + S.status;
  $('figs').innerHTML = S.figures.map((f,i)=>
    `<button class="pairbtn ${i===0?'on':''}" data-i="${i}" onclick="pick(${i})">${f.label||('fig '+(f.idx+1))}
     <span class="n">${f.status}</span></button>`).join('');
  $('krec').value = JSON.stringify(S.record, null, 1);
  if (S.figures.length) pick(0);
}
function pick(i){
  document.querySelectorAll('#figs .pairbtn').forEach(b=>b.classList.toggle('on',+b.dataset.i===i));
  cur = S.figures[i]; sel = 0;
  const img = new Image();
  img.onload = () => { cur._img = img; drawFig(); };
  img.src = '/ui/review/figure/'+cur.fid+'.png';
  const st = cur.state;
  $('fx').value = st.x_name||''; $('fy').value = st.y_name||'';
  const c = st.axis_config||{};
  for (const k of ['x1_val','x2_val','y1_val','y2_val'])
    $(k).value = (c[k]===undefined||c[k]===null)?'':c[k];
  $('logx').checked = !!c.xIsLogScale; $('logy').checked = !!c.yIsLogScale;
  seriesList();
}
function seriesList(){
  const st = cur.state;
  $('series').innerHTML = (st.series_px||[]).map((s,i)=>
    `<div style="display:flex;gap:6px;align-items:center;margin:2px 0">
      <i style="width:12px;height:12px;border-radius:3px;background:${COL[i%COL.length]};display:inline-block"></i>
      <input class="search" style="flex:1" value="${(st.series_names||[])[i]||('Line '+(i+1))}"
        onchange="S && (cur.state.series_names[${i}]=this.value)">
      <input type="radio" name="selser" ${i===sel?'checked':''} onclick="sel=${i}">
      <button class="pairbtn" onclick="delSeries(${i})">✕</button></div>`).join('');
}
function delSeries(i){
  cur.state.series_px.splice(i,1);
  (cur.state.series_names||[]).splice(i,1);
  sel = 0; seriesList(); drawFig();
}
function drawFig(){
  if(!cur || !cur._img) return;
  const cv = $('cv'), img = cur._img;
  const scale = Math.min(1, 860/img.width) * (cv._zoom||1);
  cv.width = img.width*scale; cv.height = img.height*scale;
  cv._scale = scale;
  const g = cv.getContext('2d');
  g.drawImage(img, 0, 0, cv.width, cv.height);
  (cur.state.series_px||[]).forEach((s,i)=>{
    const on = i===sel;
    g.fillStyle = COL[i%COL.length];
    g.globalAlpha = on ? 1 : 0.55;
    s.forEach(p=>{ g.beginPath(); g.arc(p[0]*scale, p[1]*scale, on?3.6:2.2, 0, 7); g.fill(); });
    if (on){                       // white halo marks the active curve
      g.strokeStyle='#fff'; g.lineWidth=1;
      s.forEach(p=>{ g.beginPath(); g.arc(p[0]*scale, p[1]*scale, 4.6, 0, 7); g.stroke(); });
    }
  });
  g.globalAlpha = 1;
  if (box && box.active){          // erase rectangle
    g.strokeStyle='#d6455d'; g.setLineDash([6,4]); g.lineWidth=1.5;
    g.strokeRect(Math.min(box.x0,box.x1)*scale, Math.min(box.y0,box.y1)*scale,
                 Math.abs(box.x1-box.x0)*scale, Math.abs(box.y1-box.y0)*scale);
    g.setLineDash([]);
  }
  if (loupe && hover){             // magnifier inset (press M to toggle)
    const L=170, Z=3.5, hx=hover[0], hy=hover[1];
    const lx = (hx*scale < cv.width/2) ? cv.width-L-10 : 10;
    g.save();
    g.beginPath(); g.rect(lx, 10, L, L); g.clip();
    g.fillStyle='#fff'; g.fillRect(lx,10,L,L);
    g.drawImage(img, hx-L/(2*Z), hy-L/(2*Z), L/Z, L/Z, lx, 10, L, L);
    (cur.state.series_px||[]).forEach((s,i)=>{
      g.fillStyle = COL[i%COL.length];
      s.forEach(p=>{
        const px=lx+(p[0]-(hx-L/(2*Z)))*Z, py=10+(p[1]-(hy-L/(2*Z)))*Z;
        if (px>=lx&&px<=lx+L&&py>=10&&py<=10+L){
          g.beginPath(); g.arc(px, py, 3, 0, 7); g.fill(); } });
    });
    g.restore();
    g.strokeStyle='#0d2544'; g.lineWidth=2; g.strokeRect(lx,10,L,L);
    g.beginPath(); g.moveTo(lx+L/2-8,10+L/2); g.lineTo(lx+L/2+8,10+L/2);
    g.moveTo(lx+L/2,10+L/2-8); g.lineTo(lx+L/2,10+L/2+8);
    g.strokeStyle='#d6455d'; g.lineWidth=1; g.stroke();
  }
}
let undoStack=[], box=null, hover=null, loupe=false;
function snapshot(){
  undoStack.push(JSON.stringify(cur.state.series_px));
  if (undoStack.length>60) undoStack.shift();
}
function undo(){
  if (!undoStack.length || !cur) return;
  cur.state.series_px = JSON.parse(undoStack.pop());
  drawFig(); seriesList();
}
function hitR(){ return 14 / ($('cv')._scale||1); }   // constant in screen px
function nearest(x, y){
  let best=null, bd=hitR();
  (cur.state.series_px||[]).forEach((s,i)=>s.forEach((p,j)=>{
    const d = Math.hypot(p[0]-x, p[1]-y);
    if (d<bd){bd=d; best=[i,j];}
  }));
  return best;
}
function insertIntoSeries(s, x, y){
  // insert at the nearest SEGMENT so loops/hysteresis keep their order
  if (s.length < 2){ s.push([x,y]); return; }
  let bi = 0, bd = Infinity;
  for (let j=0; j<s.length-1; j++){
    const [ax,ay]=s[j], [bx,by]=s[j+1];
    const L2=(bx-ax)**2+(by-ay)**2 || 1;
    let t=((x-ax)*(bx-ax)+(y-ay)*(by-ay))/L2;
    t=Math.max(0,Math.min(1,t));
    const d=Math.hypot(x-(ax+t*(bx-ax)), y-(ay+t*(by-ay)));
    if (d<bd){bd=d; bi=j+1;}
  }
  const dEnd=Math.hypot(x-s[s.length-1][0], y-s[s.length-1][1]);
  const dStart=Math.hypot(x-s[0][0], y-s[0][1]);
  if (dEnd<bd) s.push([x,y]);
  else if (dStart<bd) s.unshift([x,y]);
  else s.splice(bi,0,[x,y]);
}
function canvasXY(e){
  const cv=$('cv'), r=cv.getBoundingClientRect();
  return [(e.clientX-r.left)/cv._scale, (e.clientY-r.top)/cv._scale];
}
function bindCanvas(){
  const cv=$('cv');
  cv.onmousedown = e => {
    if (!cur) return;
    const [x,y]=canvasXY(e);
    if (mode==='add'){ snapshot(); insertIntoSeries(cur.state.series_px[sel]||[], x, y); drawFig(); return; }
    if (mode==='del'){ box={x0:x,y0:y,x1:x,y1:y,active:true}; return; }
    const n = nearest(x,y);
    if (n){ if (n[0]!==sel){ sel=n[0]; seriesList(); }   // click selects curve
      snapshot(); drag = n; drawFig(); return; }
    // clicked empty space: select nearest curve
    let bi=null, bd=25/(cv._scale||1);
    (cur.state.series_px||[]).forEach((s,i)=>s.forEach(p=>{
      const d=Math.hypot(p[0]-x,p[1]-y); if(d<bd){bd=d;bi=i;} }));
    if (bi!==null && bi!==sel){ sel=bi; seriesList(); drawFig(); }
  };
  cv.onmousemove = e => {
    const [x,y]=canvasXY(e);
    hover=[x,y];
    if (box && box.active){ box.x1=x; box.y1=y; drawFig(); return; }
    if (drag){ cur.state.series_px[drag[0]][drag[1]]=[x,y]; drawFig(); return; }
    if (loupe) drawFig();
  };
  cv.onmouseup = e => {
    if (box && box.active){
      const [x0,x1]=[Math.min(box.x0,box.x1),Math.max(box.x0,box.x1)];
      const [y0,y1]=[Math.min(box.y0,box.y1),Math.max(box.y0,box.y1)];
      if (x1-x0>3 || y1-y0>3){
        snapshot();
        (cur.state.series_px||[]).forEach((s,i)=>{
          cur.state.series_px[i]=s.filter(p=>!(p[0]>=x0&&p[0]<=x1&&p[1]>=y0&&p[1]<=y1)); });
      } else {              // tiny box = a click: delete single point
        const [x,y]=canvasXY(e); const n=nearest(x,y);
        if (n){ snapshot(); cur.state.series_px[n[0]].splice(n[1],1); }
      }
      box=null; drawFig(); seriesList(); return;
    }
    drag=null;
  };
  cv.onmouseleave = () => { hover=null; if (loupe) drawFig(); };
  document.addEventListener('keydown', e=>{
    if ((e.metaKey||e.ctrlKey) && e.key==='z'){ e.preventDefault(); undo(); }
    if (e.key==='m'){ loupe=!loupe; drawFig(); }
    if (e.key==='1') mode='drag';
    if (e.key==='2') mode='add';
    if (e.key==='3') mode='del';
    document.querySelectorAll('input[name=mode]').forEach(el=>el.checked = el.value===mode);
  });
}
function collectState(){
  const st = cur.state;
  st.x_name = $('fx').value.trim(); st.y_name = $('fy').value.trim();
  st.axis_config = st.axis_config || {};
  for (const k of ['x1_val','x2_val','y1_val','y2_val'])
    if ($(k).value.trim()!=='') st.axis_config[k]=parseFloat($(k).value);
  st.axis_config.xIsLogScale = $('logx').checked;
  st.axis_config.yIsLogScale = $('logy').checked;
  st.n_lines = (st.series_px||[]).length;
  st.n_points = (st.series_px||[]).reduce((a,s)=>a+s.length,0);
  return st;
}
async function saveFig(status){
  const body = {state: collectState()};
  if (status) body.status = status;
  const r = await api('/api/v1/review/figure/'+cur.fid, 'PATCH', body);
  $('fmsg').textContent = r.ok ? ('saved'+(status?' · '+status:'')) : ('failed: '+JSON.stringify(r.data.detail||r.data));
  if (r.ok && status){ cur.status = status; load(); }
}
async function saveRecord(){
  let rec;
  try { rec = JSON.parse($('krec').value); }
  catch(e){ $('kmsg').textContent = 'JSON error: '+e.message; return; }
  const r = await api('/api/v1/review/session/'+SID, 'PATCH', {record: rec});
  $('kmsg').textContent = r.ok ? (r.data.n_violations+' schema violation(s)') : 'save failed';
}
async function approveAll(){
  const r = await api('/api/v1/review/session/'+SID+'/approve', 'POST');
  $('kmsg').innerHTML = r.ok
    ? ('✅ ingested as <a href="/view/'+r.data.sid+'"><b>SID-'+r.data.sid+'</b></a>')
    : ('approve failed: '+JSON.stringify(r.data.detail||r.data));
}
async function assist(op, extra, apply){
  $('amsg').textContent = op+'…';
  const body = Object.assign({fid: cur.fid}, extra||{});
  const r = await api('/api/v1/assist/'+op, 'POST', body);
  if (!r.ok){ $('amsg').textContent = (r.data.detail||'failed'); return; }
  apply(r.data); drawFig(); seriesList();
  $('amsg').textContent = op+' ✓';
}
function doAxes(){ assist('axes', null, d=>{
  if (d.axis_config){ cur.state.axis_config = d.axis_config;
    for (const k of ['x1_val','x2_val','y1_val','y2_val'])
      if (d.axis_config[k]!==undefined) $(k).value = d.axis_config[k];
    $('logx').checked = !!d.axis_config.xIsLogScale;
    $('logy').checked = !!d.axis_config.yIsLogScale; }
  if (d.x_name) $('fx').value = d.x_name;
  if (d.y_name) $('fy').value = d.y_name;
});}
function doScatter(){ assist('scatter', null, d=>{
  (d.series_px||[]).forEach((s,i)=>{
    cur.state.series_px.push(s);
    (cur.state.series_names=cur.state.series_names||[]).push('Scatter '+(i+1)); });
});}
function doLabel(){ assist('label', {series_px: cur.state.series_px}, d=>{
  (d.names||[]).forEach((n,i)=>{ if(n) cur.state.series_names[i]=n; });
});}
function doAiAxes(){ assist('fix_axes', null, d=>{
  const a = d.axes||{};
  if ((a.x_axis||{}).name) $('fx').value = a.x_axis.name +
    ((a.x_axis.unit)?' ('+a.x_axis.unit+')':'');
  if ((a.y_axis||{}).name) $('fy').value = a.y_axis.name +
    ((a.y_axis.unit)?' ('+a.y_axis.unit+')':'');
  cur.state.axis_config = cur.state.axis_config||{};
  cur.state.axis_config.xIsLogScale = !!(a.x_axis||{}).is_log;
  cur.state.axis_config.yIsLogScale = !!(a.y_axis||{}).is_log;
  $('logx').checked = !!(a.x_axis||{}).is_log;
  $('logy').checked = !!(a.y_axis||{}).is_log;
});}
function doVerify(){ assist('verify', {series_px: cur.state.series_px}, d=>{
  if (d.series_px && d.series_px.length) cur.state.series_px = d.series_px;
});}
document.querySelectorAll('input[name=mode]').forEach(el=>
  el.onchange = () => { mode = el.value; });
window.addEventListener('load', ()=>{
  $('rkey').value = localStorage.getItem('sd3key') || '';
  $('rkey').addEventListener('input',
    ()=>localStorage.setItem('sd3key', $('rkey').value.trim()));
  bindCanvas();
  $('cv').addEventListener('wheel', e=>{
    e.preventDefault();
    const cvv=$('cv');
    cvv._zoom = Math.min(4, Math.max(0.5, (cvv._zoom||1) * (e.deltaY<0?1.15:0.87)));
    drawFig();
  }, {passive:false});
  load();
});
"""


def review_session_page(sid: int):
    body = """
<div class="card"><div class="muted small" id="rmeta"></div><h1 id="rtitle">…</h1>
<a href="/review">&larr; all sessions</a> &nbsp;·&nbsp;
<input class="search" id="rkey" type="password" placeholder="API key (sd3_…)" style="max-width:240px">
</div>
<div class="card"><h2>Figures</h2><div id="figs"></div>
<div class="toolbar" style="margin:8px 0">
  <label class="ck"><input type="radio" name="mode" value="drag" checked>drag</label>
  <label class="ck"><input type="radio" name="mode" value="add">add point</label>
  <label class="ck"><input type="radio" name="mode" value="del">delete point</label>
  <button class="pairbtn" onclick="undo()">↩ Undo</button>
  <button class="pairbtn" onclick="window.open('/digitizer/?figure='+cur.fid,'_blank')">⭐ Open in StarryDigitizer</button>
  <span class="muted small">— click a curve to select it · add inserts into the line · drag a box in delete mode to erase a region · keys: 1/2/3 modes, M magnifier, ⌘Z undo · wheel zooms</span>
</div>
<div class="toolbar" style="margin:4px 0 8px">
  <button class="pairbtn" onclick="doAxes()">Detect axes</button>
  <button class="pairbtn" onclick="doAiAxes()">✦ AI axis names</button>
  <button class="pairbtn" onclick="doScatter()">Detect scatter</button>
  <button class="pairbtn" onclick="doLabel()">✦ Label lines</button>
  <button class="pairbtn" onclick="doVerify()">✦ Verify curves</button>
  <span class="muted small" id="amsg"></span>
</div>
<div style="overflow:auto;max-height:640px"><canvas id="cv" class="plot"></canvas></div>
<div style="display:flex;gap:14px;flex-wrap:wrap;margin-top:8px">
  <div style="min-width:260px"><h2>Curves</h2><div id="series"></div></div>
  <div><h2>Axes</h2>
    <div style="display:flex;gap:6px;flex-wrap:wrap;max-width:340px">
      <input class="search" id="fx" placeholder="x property" style="width:160px">
      <input class="search" id="fy" placeholder="y property" style="width:160px">
      <input class="search" id="x1_val" placeholder="x min" style="width:76px">
      <input class="search" id="x2_val" placeholder="x max" style="width:76px">
      <input class="search" id="y1_val" placeholder="y min" style="width:76px">
      <input class="search" id="y2_val" placeholder="y max" style="width:76px">
      <label class="ck"><input type="checkbox" id="logx">log x</label>
      <label class="ck"><input type="checkbox" id="logy">log y</label>
    </div>
    <div style="margin-top:8px;display:flex;gap:8px;flex-wrap:wrap">
      <button class="pairbtn on" onclick="saveFig()">Save figure</button>
      <button class="pairbtn" onclick="saveFig('approved')">Approve ✓</button>
      <button class="pairbtn" onclick="saveFig('rejected')">Reject ✕</button>
      <span class="muted small" id="fmsg"></span>
    </div>
  </div>
</div></div>
<div class="card"><h2>KMDS record (editable)</h2>
<textarea id="krec" class="search" style="width:100%;height:300px;font-family:monospace;font-size:12px"></textarea>
<div style="margin-top:8px;display:flex;gap:8px;align-items:center">
  <button class="pairbtn on" onclick="saveRecord()">Validate &amp; save</button>
  <button class="pairbtn" onclick="approveAll()" style="color:#178a50"><b>Approve session → database</b></button>
  <span class="muted small" id="kmsg"></span>
</div></div>
<script>window.__SID = __SID_VALUE__;</script>"""
    body = body.replace("__SID_VALUE__", str(int(sid)))
    return layout(f"Review #{sid}", "review", body,
                  REVIEW_SESSION_JS.replace("__PALETTE__", json.dumps(PALETTE)))


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

    @app.get("/materials", response_class=HTMLResponse)
    def _materials():
        return materials_page()

    @app.get("/find", response_class=HTMLResponse)
    def _find():
        return find_page()

    @app.get("/queue", response_class=HTMLResponse)
    def _queue():
        return queue_page()

    @app.get("/review", response_class=HTMLResponse)
    def _review():
        return review_list_page()

    @app.get("/review/{sid}", response_class=HTMLResponse)
    def _review_session(sid: int):
        return review_session_page(sid)

    @app.get("/material/{key}", response_class=HTMLResponse)
    def _material(key: str):
        with Session(engine) as s:
            curves = s.exec(select(GraphCurve)
                            .where(GraphCurve.comp_key == key)).all()
            titles = {r.sid: r.title for r in s.exec(select(Record)).all()}
        if not curves:
            return layout("not found", "materials",
                          f'<div class="card">No material {esc(key)}. '
                          f'<a href="/materials">back</a></div>')
        return material_page(key, curves, titles)

    @app.get("/ui/record/{sid}")
    def _raw(sid: int):
        with Session(engine) as s:
            rec = s.exec(select(Record).where(Record.sid == sid)).first()
            if not rec:
                return {"error": f"no record {sid}"}
            return json.loads(rec.record_json)

    @app.get("/view/{sid}", response_class=HTMLResponse)
    def _view(sid: int):
        from starrydata3.app import PointValue, Provenance, QualityFlag
        with Session(engine) as s:
            rec = s.exec(select(Record).where(Record.sid == sid)).first()
            if not rec:
                return layout("not found", "home",
                              f'<div class="card">No record SID-{sid}. <a href="/">back</a></div>')
            curves = s.exec(select(GraphCurve).where(GraphCurve.record_sid == sid)).all()
            points = s.exec(select(PointValue).where(PointValue.record_sid == sid)).all()
            flags = s.exec(select(QualityFlag).where(QualityFlag.record_sid == sid)).all()
            prov = s.exec(select(Provenance).where(Provenance.record_sid == sid)).all()
            record_json = json.loads(rec.record_json)
            return record_page(rec, curves, points, record_json, flags, prov)
 
    @app.get("/kmds/{sid}", response_class=HTMLResponse)
    def _kmds_view(sid: int):
        """The full KMDS paper-record viewer (same renderer as AutoLineDigitizer's
        standalone viewer), auto-loaded with this record."""
        import os
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kmds_viewer.html")
        with open(path, encoding="utf-8") as f:
            html = f.read()
        boot = f"""
<script>
(async () => {{
  const back = document.createElement('a');
  back.href = '/view/{sid}'; back.textContent = '\\u2190 Starrydata3';
  back.style.cssText = 'position:fixed;top:14px;left:14px;z-index:9999;background:#0d2544;' +
    'color:#fff;padding:6px 12px;border-radius:8px;font:12px sans-serif;text-decoration:none;opacity:.9';
  document.body.appendChild(back);
  try {{
    const data = await (await fetch('/ui/record/{sid}')).json();
    validateAndRender(data);
  }} catch (e) {{
    document.getElementById('error-box').textContent = 'Failed to load record {sid}: ' + e.message;
    document.getElementById('error-box').classList.add('show');
  }}
}})();
</script>
"""
        html = html.replace("</body>", boot + "</body>")
        return HTMLResponse(html, headers={"Cache-Control": "no-store, must-revalidate",
                                           "Pragma": "no-cache"})
