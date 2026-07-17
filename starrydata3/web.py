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
                                  ("digitize", "/digitize", "Digitize PDF"),
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


DIGITIZE_JS = """
const f=document.getElementById('pdf'), keyIn=document.getElementById('key');
const out=document.getElementById('out'), go=document.getElementById('go');
const COL=__PALETTE__;
let RID=null, FIGS=[];
function esc(s){return (s+'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
function splitNU(name){ // "Temperature (K)" -> {name:"Temperature", unit:"K"}
  const m=(name||'').match(/^(.*?)\\s*\\(([^()]+)\\)\\s*$/);
  return m?{name:m[1].trim(),unit:m[2].trim()}:{name:(name||'').trim(),unit:''};
}
go.addEventListener('click', async ()=>{
  const key=keyIn.value.trim();
  if(!f.files[0]){alert('Choose a PDF');return;}
  if(!key){alert('Enter your API key');return;}
  const review=document.getElementById('review').checked;
  go.disabled=true; out.innerHTML='Uploading PDF…';
  const fd=new FormData(); fd.append('file', f.files[0]);
  const url=review?'/api/v1/review/start':'/api/v1/process_pdf';
  let r;
  try{ r=await fetch(url,{method:'POST',headers:{'X-API-Key':key},body:fd}); }
  catch(e){ out.innerHTML='<span style=color:#c0392b>network error</span>'; go.disabled=false; return; }
  const d=await r.json();
  if(!r.ok){ out.innerHTML='<span style=color:#c0392b>HTTP '+r.status+': '+esc(JSON.stringify(d.detail||d))+'</span>'; go.disabled=false; return; }
  const job=d.job_id;
  out.innerHTML='Digitizing… <span class=muted id=stg></span>';
  const poll=setInterval(async ()=>{
    const jr=await fetch('/api/v1/jobs/'+job,{headers:{'X-API-Key':key}});
    const j=await jr.json();
    document.getElementById('stg') && (document.getElementById('stg').textContent=j.stage||'');
    if(j.status==='done'){ clearInterval(poll); go.disabled=false;
      if(review){ out.textContent=''; openReview(j.review_id); }
      else{ const res=j.result||{};
        out.innerHTML='<div style=color:#27ae60>✓ Digitized → <a href="/view/'+j.sid+'">SID-'+j.sid+'</a> — '+
          (res.curves_indexed||0)+' curves, '+(res.kmds_curves||0)+' KMDS-conformant.</div>'; } }
    else if(j.status==='error'){ clearInterval(poll); go.disabled=false;
      out.innerHTML='<span style=color:#c0392b>Failed: '+esc(j.error||'')+'</span>'; }
  }, 1500);
});

async function openReview(rid){
  RID=rid;
  const d=await (await fetch('/api/v1/review/'+rid)).json();
  FIGS=d.figures;
  document.getElementById('rdoi').value=d.doi||'';
  document.getElementById('board').style.display='block';
  document.getElementById('figs').innerHTML=FIGS.map((fg,i)=>{
    const xu=splitNU(fg.x_name), yu=splitNU(fg.y_name);
    const fmtR=v=>{const a=Math.abs(v);return (a&&(a<0.01||a>=1e4))?v.toExponential(1):(+v.toPrecision(3))};
    const ser=fg.series.map((s,j)=>`
      <div style="display:flex;gap:8px;align-items:center;margin:4px 0">
        <i style="width:14px;height:14px;border-radius:3px;background:${COL[j%COL.length]};flex:none"></i>
        <input class="search" style="padding:5px 9px;font-size:13px" id="lab_${i}_${j}" value="${esc(s.label)}">
        <span class="muted small" style="white-space:nowrap" title="digitized data range — if this doesn't match the axis ticks, re-read the axes">${s.n_points} pts · x ${fmtR(s.range.x_min)}–${fmtR(s.range.x_max)} · y ${fmtR(s.range.y_min)}–${fmtR(s.range.y_max)}</span>
        <label class="ck"><input type="checkbox" id="sinc_${i}_${j}" checked>keep</label>
      </div>`).join('');
    const controls = fg.calibrated ? `
      <div style="display:grid;grid-template-columns:1fr 90px;gap:8px;margin-bottom:6px">
        <input class="search" style="padding:5px 9px;font-size:13px" id="xn_${i}" value="${esc(xu.name)}" placeholder="x property">
        <input class="search" style="padding:5px 9px;font-size:13px" id="xu_${i}" value="${esc(xu.unit)}" placeholder="unit">
        <input class="search" style="padding:5px 9px;font-size:13px" id="yn_${i}" value="${esc(yu.name)}" placeholder="y property">
        <input class="search" style="padding:5px 9px;font-size:13px" id="yu_${i}" value="${esc(yu.unit)}" placeholder="unit">
      </div>
      <div class="muted small" style="margin-bottom:4px">${fg.x_log?'log x · ':''}${fg.y_log?'log y · ':''}${fg.series.length} curve(s)${fg.calibration==='claude'?' · <span style="color:#8e44ad">✦ axes read by Claude — double-check ranges</span>':''}</div>
      ${ser}
      <div style="margin-top:8px"><button class="pairbtn" style="cursor:pointer" onclick="recal(${i})">✦ Re-read axes with Claude</button>
      <span class="muted small" id="rc_${i}"></span></div>` : `<div class="warn">✗ ${esc(fg.error||'not digitizable')} — excluded</div>`;
    return `<div class="card" id="fig_${i}">
      <div style="display:flex;justify-content:space-between;gap:10px;align-items:baseline">
        <h2 style="margin:0">${esc(fg.caption)} ${fg.detector?`<span class="muted small" style="font-weight:400">· located by ${fg.detector==='mineru'?'MinerU PP-DocLayoutV2':esc(fg.detector)}</span>`:''}</h2>
        <label class="ck" style="flex:none"><input type="checkbox" id="finc_${i}" ${fg.calibrated?'checked':''} ${fg.calibrated?'':'disabled'}>include</label>
      </div>
      <div style="display:grid;grid-template-columns:minmax(280px,1.2fr) 1fr;gap:16px;margin-top:10px">
        <div class="figwrap" style="position:relative">
          <img src="${fg.image_url}" style="width:100%;display:block;border:1px solid var(--line);border-radius:6px" id="img_${i}">
          <canvas id="ov_${i}" width="${fg.width}" height="${fg.height}"
            style="position:absolute;inset:0;width:100%;height:100%;pointer-events:none"></canvas>
        </div>
        <div>${controls}</div>
      </div></div>`;
  }).join('');
  FIGS.forEach((fg,i)=>{
    const cv=document.getElementById('ov_'+i); if(!cv) return;
    const g=cv.getContext('2d');
    fg.series.forEach((s,j)=>{
      g.strokeStyle=COL[j%COL.length]; g.lineWidth=Math.max(2,fg.width/400); g.beginPath();
      (s.points_px||[]).forEach((p,k)=>k?g.lineTo(p[0],p[1]):g.moveTo(p[0],p[1]));
      g.stroke();
    });
  });
  window.scrollTo({top:document.getElementById('board').offsetTop-70,behavior:'smooth'});
}

async function recal(i){
  const key=keyIn.value.trim();
  if(!key){alert('Enter your API key');return;}
  const el=document.getElementById('rc_'+i); el.textContent='Asking Claude to read the axes…';
  const r=await fetch('/api/v1/review/'+RID+'/recalibrate/'+FIGS[i].index,
    {method:'POST',headers:{'X-API-Key':key}});
  const d=await r.json();
  if(!r.ok){el.textContent='failed: '+JSON.stringify(d.detail||d);return;}
  const edits={};   // keep the reviewer's typed labels/axis names across the refresh
  FIGS.forEach((fg,k)=>{edits[k]={xn:document.getElementById('xn_'+k)?.value,xu:document.getElementById('xu_'+k)?.value,
    yn:document.getElementById('yn_'+k)?.value,yu:document.getElementById('yu_'+k)?.value,
    labs:fg.series.map((s,j)=>document.getElementById('lab_'+k+'_'+j)?.value)}});
  await openReview(RID);
  FIGS.forEach((fg,k)=>{const e=edits[k]; if(!e) return;
    if(e.xn!=null&&document.getElementById('xn_'+k)) document.getElementById('xn_'+k).value=e.xn;
    if(e.xu!=null&&document.getElementById('xu_'+k)) document.getElementById('xu_'+k).value=e.xu;
    if(e.yn!=null&&document.getElementById('yn_'+k)) document.getElementById('yn_'+k).value=e.yn;
    if(e.yu!=null&&document.getElementById('yu_'+k)) document.getElementById('yu_'+k).value=e.yu;
    (e.labs||[]).forEach((v,j)=>{if(v!=null&&document.getElementById('lab_'+k+'_'+j)) document.getElementById('lab_'+k+'_'+j).value=v;});});
}

document.getElementById('commit').addEventListener('click', async ()=>{
  const key=keyIn.value.trim();
  if(!key){alert('Enter your API key');return;}
  const body={doi:document.getElementById('rdoi').value.trim(),
              title:document.getElementById('rtitle').value.trim(),
              figures:FIGS.map((fg,i)=>({index:fg.index,
                include:!!document.getElementById('finc_'+i)?.checked,
                x_name:document.getElementById('xn_'+i)?.value||'',
                x_unit:document.getElementById('xu_'+i)?.value||'',
                y_name:document.getElementById('yn_'+i)?.value||'',
                y_unit:document.getElementById('yu_'+i)?.value||'',
                x_log:fg.x_log, y_log:fg.y_log,
                series:fg.series.map((s,j)=>({label:document.getElementById('lab_'+i+'_'+j)?.value||s.label,
                  include:!!document.getElementById('sinc_'+i+'_'+j)?.checked}))}))};
  const co=document.getElementById('cout'); co.textContent='Storing…';
  const r=await fetch('/api/v1/review/'+RID+'/commit',{method:'POST',
    headers:{'Content-Type':'application/json','X-API-Key':key},body:JSON.stringify(body)});
  const d=await r.json();
  if(r.ok){ co.innerHTML='<div style=color:#27ae60>✓ Stored as <a href="/view/'+d.sid+'">SID-'+d.sid+'</a> — '+
    (d.curves_indexed||0)+' curves indexed ('+(d.kmds_curves||0)+' KMDS-conformant).</div>';
    document.getElementById('figs').style.opacity=.5; }
  else co.innerHTML='<span style=color:#c0392b>HTTP '+r.status+': '+esc(JSON.stringify(d.detail||d))+'</span>';
});
document.getElementById('discard').addEventListener('click', async ()=>{
  const key=keyIn.value.trim();
  if(RID) await fetch('/api/v1/review/'+RID,{method:'DELETE',headers:{'X-API-Key':key}});
  document.getElementById('board').style.display='none';
  document.getElementById('figs').innerHTML=''; RID=null;
});
""".replace("__PALETTE__", json.dumps(PALETTE))


def digitize_page():
    body = """
<div class="card">
  <h1>Digitize a paper</h1>
  <p class="muted" style="margin-top:0">Upload a PDF — every figure is extracted,
  axis-calibrated, and its curves digitized and named from the legend, then
  <b>you verify each figure</b> before anything is stored. The whole pipeline runs
  locally: the ML weights (LineFormer + ChartDete) live in your AutoLineDigitizer
  install — nothing leaves this machine. First PDF loads the models, so allow a minute.</p>
  <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:12px 0">
    <input type="file" id="pdf" accept="application/pdf">
    <input class="search" id="key" placeholder="API key (sd3_…)" style="max-width:360px" type="password">
    <label class="ck"><input type="checkbox" id="review" checked>verify each figure first</label>
    <button id="go" class="pairbtn on" style="cursor:pointer">Digitize</button>
  </div>
  <div id="out"></div>
</div>
<div id="board" style="display:none">
  <div class="card">
    <h2>Verify the digitization</h2>
    <p class="muted small" style="margin-top:0">Check every figure: fix axis property
    names/units, rename or drop curves, exclude bad figures. Only what you approve is stored.</p>
    <div style="display:flex;gap:10px;flex-wrap:wrap">
      <input class="search" id="rdoi" placeholder="DOI (10.xxxx/…)" style="max-width:320px">
      <input class="search" id="rtitle" placeholder="Paper title (optional)" style="flex:1;min-width:260px">
    </div>
  </div>
  <div id="figs"></div>
  <div class="card">
    <button id="commit" class="pairbtn on" style="cursor:pointer;font-size:14px">✓ Store approved figures</button>
    <button id="discard" class="pairbtn" style="cursor:pointer">Discard session</button>
    <div id="cout" style="margin-top:12px"></div>
  </div>
</div>
<div class="card muted small">Every stored curve is validated against the KMDS
vocabulary and unit-normalized on ingest, same as a direct upload.</div>"""
    return layout("Digitize", "digitize", body, DIGITIZE_JS)


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


def record_page(rec, curves, points, record_json):
    """rec: Record row; curves: GraphCurve rows; points: PointValue rows;
    record_json: parsed KMDS dict."""
    pub = (record_json.get("metadata") or {}).get("publication") or {}
    # group curves by property pair + unit for plotting (a Hz sweep and a GHz
    # sweep of the same property are different plots)
    groups = {}
    for c in curves:
        groups.setdefault((c.prop_x, c.prop_y, c.unit_x, c.unit_y), []).append(
            {"sample": c.sample_name, "composition": c.composition,
             "points": json.loads(c.points_json)})
    plots = ""
    for (px, py, ux, uy), cs in groups.items():
        ux = f" ({ux})" if ux not in ("", "-") else ""
        uy = f" ({uy})" if uy not in ("", "-") else ""
        plots += (f'<div class="card"><h2>{esc(px)}{esc(ux)} vs {esc(py)}{esc(uy)}</h2>'
                  f'<div class="muted small">{len(cs)} curve(s), {sum(len(c["points"]) for c in cs)} points</div>'
                  f'{_svg_plot(cs)}</div>')
    if not plots:
        plots = '<div class="card muted small">no digitized curves — digitize the paper\'s PDF on the <a href="/digitize">Digitize</a> tab to add some</div>'
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
    body = (head + abstract + _points_cards(points) + plots + _tables_cards(pub)
            + samp_card + mat_card + _figures_card(pub))
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

    @app.get("/digitize", response_class=HTMLResponse)
    def _digitize():
        return digitize_page()

    @app.get("/ui/record/{sid}")
    def _raw(sid: int):
        with Session(engine) as s:
            rec = s.exec(select(Record).where(Record.sid == sid)).first()
            if not rec:
                return {"error": f"no record {sid}"}
            return json.loads(rec.record_json)

    @app.get("/view/{sid}", response_class=HTMLResponse)
    def _view(sid: int):
        from starrydata3.app import PointValue
        with Session(engine) as s:
            rec = s.exec(select(Record).where(Record.sid == sid)).first()
            if not rec:
                return layout("not found", "home",
                              f'<div class="card">No record SID-{sid}. <a href="/">back</a></div>')
            curves = s.exec(select(GraphCurve).where(GraphCurve.record_sid == sid)).all()
            points = s.exec(select(PointValue).where(PointValue.record_sid == sid)).all()
            record_json = json.loads(rec.record_json)
            return record_page(rec, curves, points, record_json)

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
