#!/usr/bin/env python3
"""patch_web.py - richer signal cards + live chart. Run once from ~/goldlab."""
import sys

p = "web/index.html"
s = open(p).read()
if "sigCard" in s:
    print("already patched"); sys.exit(0)

# ---------- styles ----------
s = s.replace(".rrbar{height:4px;", """
.sig{margin:0 var(--pad) 10px;border:1px solid var(--line);border-radius:14px;
 background:var(--surf);overflow:hidden}
.sig .top{padding:14px 15px;cursor:pointer}
.sig .body{display:none;border-top:1px solid var(--line);padding:14px 15px;
 background:rgba(27,39,67,.35)}
.sig.open .body{display:block}
.sig .caret{transition:.2s;color:var(--muted);font-size:11px}
.sig.open .caret{transform:rotate(180deg)}
.mini{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:10px 0}
.mini div{background:var(--surf2);border-radius:10px;padding:9px 11px}
.mini i{font-style:normal;font-size:8.5px;letter-spacing:.14em;text-transform:uppercase;
 color:var(--muted);font-weight:700;display:block}
.mini b{font-family:'IBM Plex Mono',monospace;font-size:15px;display:block;margin-top:3px}
.slice{display:flex;justify-content:space-between;align-items:center;
 padding:8px 0;border-bottom:1px solid var(--line);font-size:12.5px}
.slice:last-of-type{border-bottom:0}
.slice span:last-child{font-family:'IBM Plex Mono',monospace}
.thin{color:var(--amber)}
.why{margin-top:12px}
.why div{display:flex;gap:8px;font-size:12.5px;line-height:1.5;margin-bottom:7px}
.why .ok::before{content:'+';color:var(--green);font-weight:800;flex:none}
.why .no::before{content:'!';color:var(--amber);font-weight:800;flex:none}
.livebar{margin:0 var(--pad) 12px;border-radius:14px;padding:13px 15px;
 border:1px solid var(--line);background:var(--surf)}
.livebar.hot{border-color:var(--gold);background:rgba(255,201,60,.09)}
.livedot{width:8px;height:8px;border-radius:50%;background:var(--green);
 display:inline-block;margin-right:7px;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
.rrbar{height:4px;""")

# ---------- live panel markup ----------
s = s.replace('<h2>Auto signals · not for trading</h2>',
 '''<h2>Right now</h2>
 <div class="livebar" id="live"><span class="livedot"></span>Checking the market…</div>
 <h2>Auto signals · not for trading</h2>''')

# ---------- signal rendering ----------
old_start = s.index("  const sl=$('#signals');sl.innerHTML='';")
old_end = s.index("  const obs=await(await fetch(API+'/observations')).json();")
s = s[:old_start] + """  renderSignals();
""" + s[old_end:]

s = s.replace("  SETUPS=await(await fetch(API+'/setups')).json();\n  $('#hSet').textContent=SETUPS.length;",
              "  SETUPS=await(await fetch(API+'/setups')).json();\n  $('#hSet').textContent=SETUPS.length;\n  SIGNALS=await(await fetch(API+'/signals')).json();")

s = s.replace("let CFG={},SETUPS=[],BARS=[],STATS=null;",
              "let CFG={},SETUPS=[],BARS=[],STATS=null,SIGNALS=[];")

s = s.replace("/* ---------- charts ---------- */", """
function sigCard(x){
 const a=x.analysis||{},c=el('div','sig');
 const top=el('div','top'),r=el('div','row'),L=el('div');
 L.append(el('b',null,(x.ts||'').replace('T',' ').slice(0,16)));
 L.append(el('div','sub mono',
  `${f2(x.entry)} → ${f2(x.target)} · stop ${f2(x.stop)} · ${f2(x.rr)}R`));
 r.append(L);
 const right=el('div');right.style.textAlign='right';
 right.append(el('span','pill p-'+x.direction,x.direction));
 right.append(Object.assign(el('div','caret'),{textContent:'▾',style:'margin-top:6px'}));
 r.append(right);top.append(r);
 const b=el('div','rrbar'),f=el('i');f.style.width=Math.min(100,x.rr/3*100)+'%';
 b.append(f);top.append(b);
 top.onclick=()=>c.classList.toggle('open');
 c.append(top);

 const body=el('div','body');
 const m=a.money||{};
 const grid=el('div','mini');
 [['Risk','$'+f2(m.risk||0)],['If it wins','$'+f2(m.reward||0)],
  ['Position',(m.lots||0).toFixed(4)+' lots'],['Stop is','$'+f2(m.stop_dist||0)+'/oz']]
  .forEach(([k,v])=>{const d=el('div');d.append(el('i',null,k));d.append(el('b',null,v));grid.append(d)});
 body.append(grid);
 if(m.tradeable===false){const w=el('div','sub');w.style.color='var(--amber)';
  w.textContent='Your account cannot size this correctly.';body.append(w)}

 if((a.slices||[]).length){
  body.append(Object.assign(el('div','label'),{textContent:'How setups like this have gone',
   style:'margin:14px 0 4px'}));
  a.slices.forEach(sl=>{const d=el('div','slice');
   d.append(el('span',null,sl.label));
   const v=el('span',sl.n<30?'thin':null,
    `${(sl.win*100).toFixed(0)}% · ${sgn(sl.exp)}R · n=${sl.n}`);
   if(sl.n>=30)v.style.color=sl.exp>0?'var(--green)':'var(--red)';
   d.append(v);body.append(d)});
  body.append(Object.assign(el('div','sub'),
   {textContent:'Amber means too few past trades to trust the number.'}))}

 const why=el('div','why');
 (a.reasons||[]).forEach(t=>{const d=el('div','ok');d.append(el('span',null,t));why.append(d)});
 (a.cautions||[]).forEach(t=>{const d=el('div','no');d.append(el('span',null,t));why.append(d)});
 if(why.children.length)body.append(why);
 c.append(body);
 return c}

function renderSignals(){
 const sl=$('#signals');if(!sl)return;sl.innerHTML='';
 if(!SIGNALS.length){sl.append(el('div','empty','No setups detected yet.'));return}
 SIGNALS.forEach(x=>sl.append(sigCard(x)))}

async function live(){
 try{const d=await(await fetch(API+'/live')).json();const box=$('#live');if(!box)return;
  const hot=d.state==='raid_low'||d.state==='raid_high';
  box.className='livebar'+(hot?' hot':'');
  box.innerHTML='';
  const h=el('div','row');
  const L=el('div');
  L.append(el('b',null,(hot?'Setup condition met':'Watching')+' · '+(d.hour||'')));
  L.append(el('div','sub',d.note||''));
  h.append(L);
  if(d.last)h.append(el('span','pill '+(hot?'p-yellow':'p-green'),f2(d.last)));
  box.append(h);
  if(d.high)box.append(Object.assign(el('div','sub mono'),
   {textContent:`hour range ${f2(d.low)} – ${f2(d.high)} · ${d.bars_in} of 4 candles in`}));
 }catch(e){}}

/* ---------- charts ---------- */""")

# ---------- live refresh loop ----------
s = s.replace("load();\n</script>", """load();live();
setInterval(async()=>{
 try{
  const d=await(await fetch(API+'/bars')).json();BARS=d.bars||[];
  SETUPS=await(await fetch(API+'/setups')).json();
  if($('#v-charts').classList.contains('on'))allCharts();
 }catch(e){}
 live();
},60000);
setInterval(()=>{if($('#v-now').classList.contains('on'))load()},300000);
</script>""")

open(p, "w").write(s)
print("web patched")
