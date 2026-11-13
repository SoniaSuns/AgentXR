/* Frozen-frame calibration: a grid is never carried over to a moving camera.
   0.14: the model proposes endpoints on the labelled grid (you accept each one), the
   deterministic circuit check names the single move that closes the loop, and both are drawn
   on the frozen photo. */
(()=>{
 const el=id=>document.getElementById(id);
 let frozen=null,profiles={},spec=null,anchors=[],result=null,pins=[],wires=[],measurements=[],expected=[],revision=0,proposals=[],dropped=[],readJob=null;
 const say=(s,error=false)=>{el('board-status').textContent=s;el('board-status').classList.toggle('error',error);};
 const names=()=>['a1',`a${spec.columns}`,`j${spec.columns}`,'j1','e1',`f${spec.columns}`];
 function invalidate(){revision++;result=null;el('board-submit').disabled=true;el('board-export').disabled=true;el('board-read').disabled=true;}
 function reset(){invalidate();frozen=null;anchors=[];pins=[];wires=[];measurements=[];expected=[];proposals=[];dropped=[];readJob=null;el('board-photo').hidden=true;el('board-warp').hidden=true;render();renderProposals();renderCircuit(null);drawOverlay();say('先冻结高清画面。换帧或移动接线后，必须重新标定与录入。');}
 window.resetBoard=reset;
 function render(){
  el('board-anchors').textContent=anchors.length?anchors.map(a=>a.hole).join(' → '):'尚未标定';
  el('board-pin-list').replaceChildren();
  for(const p of pins){const li=document.createElement('li');li.textContent=`${p.id} → ${p.hole}（${p.source==='model'?'Codex 提议 · 你已采用':'人工确认'}）`;el('board-pin-list').append(li);}
  for(const id of ['board-a','board-b']){const select=el(id),keep=select.value;select.replaceChildren();for(const p of pins){const o=document.createElement('option');o.value=p.id;o.textContent=p.id;select.append(o);}if(pins.some(p=>p.id===keep))select.value=keep;}
  el('board-entries').textContent=[...wires.map(p=>`同根导线：${p.a} ↔ ${p.b}`),...expected.map(p=>`目标：${p.a} 与 ${p.b} ${p.connected?'应相通':'应隔离'}`),...measurements.map(p=>`用户实测：${p.a} ↔ ${p.b} ${p.value}`)].join('\n');
  drawOverlay();
 }
 function request(){
  return {mode:el('board-mode').value,profile:spec,layout_confirmed:el('board-layout').checked,
   marker_layout_confirmed:el('board-markers').checked,anchors,frame_digest:result?.frame_digest,
   endpoints:pins.map(({id,xy})=>({id,xy,confirmed:true})),wires,measurements,expected};
 }
 const holeXY=hole=>result?.grid?.find(g=>g.hole===hole)?.xy||null;
 // ---- overlay on the frozen photo: confirmed pins, proposals, and the suggested move ----
 function drawOverlay(){
  const svg=el('board-overlay');if(!svg)return;svg.replaceChildren();
  if(!result||!frozen){svg.setAttribute('viewBox','0 0 100 100');return;}
  const W=result.width,H=result.height,r=Math.max(6,result.min_pitch_px*.45);
  svg.setAttribute('viewBox',`0 0 ${W} ${H}`);
  const ns='http://www.w3.org/2000/svg';
  const mark=(hole,cls,label)=>{const xy=holeXY(hole);if(!xy)return null;const g=document.createElementNS(ns,'g');g.setAttribute('class',cls);
   const c=document.createElementNS(ns,'circle');c.setAttribute('cx',xy[0]*W);c.setAttribute('cy',xy[1]*H);c.setAttribute('r',r);g.append(c);
   if(label){const t=document.createElementNS(ns,'text');t.setAttribute('x',xy[0]*W+r*1.2);t.setAttribute('y',xy[1]*H-r*.6);t.setAttribute('font-size',Math.max(12,r*1.6));t.textContent=label;g.append(t);}
   svg.append(g);return g;};
  for(const p of proposals)mark(p.hole,'board-mark proposal',`${p.id}?`);
  for(const p of pins)mark(p.hole,'board-mark pin',p.id);
  const move=result.connections?.circuit?.findings?.find(f=>f.move)?.move;
  if(move){const a=holeXY(move.from),b=holeXY(move.to);if(a&&b){
   mark(move.to,'board-mark target',`→ ${move.to}`);
   const line=document.createElementNS(ns,'line');line.setAttribute('class','board-move');line.setAttribute('x1',a[0]*W);line.setAttribute('y1',a[1]*H);line.setAttribute('x2',b[0]*W);line.setAttribute('y2',b[1]*H);line.setAttribute('stroke-width',Math.max(2,r*.35));line.setAttribute('marker-end','url(#board-arrow)');svg.append(line);
   const defs=document.createElementNS(ns,'defs');const m=document.createElementNS(ns,'marker');m.setAttribute('id','board-arrow');m.setAttribute('viewBox','0 0 10 10');m.setAttribute('refX','9');m.setAttribute('refY','5');m.setAttribute('markerWidth','5');m.setAttribute('markerHeight','5');m.setAttribute('orient','auto');
   const path=document.createElementNS(ns,'path');path.setAttribute('d','M0,0 L10,5 L0,10 z');path.setAttribute('fill','#f1ba7b');m.append(path);defs.append(m);svg.append(defs);
  }}
 }
 // ---- deterministic circuit verdict ----
 const SEVERITY={error:'需要改',warning:'注意',uncertain:'不确定',info:'说明'};
 function renderCircuit(c){
  const box=el('board-circuit'),list=el('board-findings');list.replaceChildren();
  if(!c){box.hidden=true;return;}
  box.hidden=false;
  const status={works:'回路闭合',open:'回路未闭合',short:'短路',faulty:'回路有错',incomplete:'连接表不完整',conflict:'几何与实测矛盾'}[c.status]||c.status;
  el('board-circuit-status').textContent=status;el('board-circuit-status').className=`badge circuit-${c.status}`;
  el('board-circuit-summary').textContent=c.summary+(c.missing?.length?` 还缺：${c.missing.join('；')}`:'');
  for(const f of c.findings){
   const li=document.createElement('li');li.className=`finding-${f.severity}`;
   const b=document.createElement('b');b.textContent=SEVERITY[f.severity]||f.severity;
   const span=document.createElement('span');span.textContent=`${f.message} ${f.suggestion||''}${f.verification?` 核验：${f.verification}`:''}`;
   li.append(b,span);
   if(f.alternatives?.length){const small=document.createElement('small');small.textContent='其他可行的单步改动：'+f.alternatives.map(a=>`${a.pin} ${a.from}→${a.to}`).join('；');li.append(small);}
   list.append(li);
  }
 }
 async function check(){
  if(!frozen)throw Error('请先冻结照片');
  const body=request(), rev=++revision;result=null;el('board-submit').disabled=true;el('board-export').disabled=true;el('board-read').disabled=true;say('正在校验孔格与连接…');
  const r=await api('/api/breadboard/rectify',{image:frozen.image,board:body});
  if(rev!==revision)return;
  result=r;el('board-warp').src=r.image;el('board-warp').hidden=false;
  const labels={geometry_match:'按已录入几何关系相通/匹配，尚未实测',geometry_mismatch:'几何关系与目标不符，需实测核验',measured_match:'用户报告的测量与目标相符',measured_mismatch:'用户报告的测量与目标不符',conflict:'测量与几何或其他测量冲突，先核对记录',unknown:'未建立连接证据，不能判为断路'};
  el('board-verdicts').textContent=r.connections.checks.map(c=>`${c.a} ↔ ${c.b}：${labels[c.verdict]}`).join('\n')||'（未添加目标端点关系；回路判断见上方「电路判断」）';
  el('board-result').textContent=JSON.stringify({connections:r.connections,color_candidates:r.candidates},null,2);
  el('board-submit').disabled=!r.ok;el('board-export').disabled=!r.ok;el('board-read').disabled=!r.ok;
  renderCircuit(r.connections.circuit);drawOverlay();
  say(r.ok?`孔格通过：原图 ${r.width}×${r.height}，最小孔距 ${r.min_pitch_px.toFixed(1)}px，孔纹理支持 ${(r.hole_support*100).toFixed(0)}%。可以让 Codex 读端点候选，或输入编号后在原照片点击入孔位置。` : r.reasons.join('；'),!r.ok);
 }
 const attempt=fn=>async()=>{try{await fn();}catch(e){say(e.message,true);}};
 el('board-freeze').onclick=attempt(async()=>{
  if(!stream)throw Error('请先开启摄像头');
  stopAuto();const shot=await captureFrame();reset();
  spec=JSON.parse(el('board-profile-json').value);
  frozen={...shot,image:shot.hires||shot.image,session};
  el('board-photo').src=frozen.image;el('board-photo').hidden=false;
  say(el('board-mode').value==='manual'?`请依次点击孔中心：${names().join(' → ')}。A 行在上，列号从左到右；这不是板子塑料外壳四角。`:'确认四个标记位置后点击“校验孔格”。');
 });
 function snapHole(xy){
  const ds=result.grid.map(g=>({g,d:Math.hypot((g.xy[0]-xy[0])*result.width,(g.xy[1]-xy[1])*result.height)})).sort((a,b)=>a.d-b.d);
  if(ds[0].d>.35*ds[0].g.pitch_px||ds[1].d-ds[0].d<.25*ds[0].g.pitch_px)throw Error('落点离孔过远或位于两孔之间；放大原图后重选');
  return ds[0].g.hole;
 }
 el('board-photo').onclick=async event=>{
  try{
   if(!frozen)return;
   const rect=el('board-photo').getBoundingClientRect();const xy=[(event.clientX-rect.left)/rect.width,(event.clientY-rect.top)/rect.height];
   if(el('board-mode').value==='manual'&&anchors.length<6){
    invalidate();anchors.push({hole:names()[anchors.length],xy});render();
    if(anchors.length===6)await check();else say(`已记录 ${anchors.at(-1).hole}；接下来点击 ${names()[anchors.length]} 孔中心。`);
    return;
   }
   if(!result?.ok)throw Error('须先通过孔格校验');
   const id=el('board-pin-id').value.trim();
   if(!/^[A-Za-z0-9_.+-]{1,40}$/.test(id)||pins.some(p=>p.id===id))throw Error('先填写唯一端点编号，如 W1.1、R1.1、LED1.A、BAT.+');
   const hole=snapHole(xy);
   pins.push({id,xy,hole,source:'user'});el('board-pin-id').value='';render();await check();
  }catch(e){say(e.message,true);}
 };
 // ---- model-proposed endpoints ----
 el('board-read').onclick=attempt(async()=>{
  if(!result?.ok||!frozen)throw Error('须先通过孔格校验');
  if(!ready||!connected)throw Error('先连接分析服务');
  const {id}=await api('/api/breadboard/read',{session,image:frozen.image,board:request(),known_pins:pins.map(p=>({id:p.id,hole:p.hole})),task:currentTask()});
  readJob={id,status:'queued'};say('已把编号图和原照片发给 Codex，正在读端点…');renderProposals();
 });
 window.receiveBoardRead=job=>{
  if(!readJob||job.id!==readJob.id)return;
  readJob=job;
  const state=job.lanes?.brief||job.status;
  if(state==='queued'||state==='running'){say(`Codex 正在读端点…${job.brief_partial?` 已输出 ${job.brief_partial.length} 字`:''}`);renderProposals();return;}
  if(state==='done'&&job.board_read){
   if(job.board_read.frame_digest!==result?.frame_digest){say('读板结果属于另一帧，已忽略；请重新校验后再读。',true);return;}
   proposals=job.board_read.proposals.filter(p=>!pins.some(q=>q.id===p.id));dropped=job.board_read.dropped;
   renderProposals();drawOverlay();
   say(proposals.length?`Codex 提议了 ${proposals.length} 个端点（橙色虚圈）。逐个核对原照片后点「采用」，不确定的忽略。`:'Codex 没有读出可用的端点；靠近俯拍后重新冻结，或手动点击录入。',!proposals.length);
   if(job.board_read.obstructed)say('Codex 认为有手或物体挡住了孔区，结果可能不全。',true);
  }else if(state==='error')say(`读板失败：${job.brief_error||'未知错误'}`,true);
  else if(state==='superseded'||state==='cancelled')say('读板请求已被新的请求取代。');
  renderProposals();
 };
 function renderProposals(){
  const box=el('board-proposals');box.replaceChildren();
  const active=readJob&&(readJob.status==='queued'||readJob.status==='running'||readJob.lanes?.brief==='running'||readJob.lanes?.brief==='queued');
  if(active){const p=document.createElement('p');p.className='muted';p.textContent='Codex 正在读端点…';box.append(p);}
  if(proposals.length){
   const all=document.createElement('button');all.textContent='全部采用';all.onclick=attempt(async()=>{for(const p of [...proposals])accept(p,false);render();await check();});box.append(all);
  }
  for(const p of proposals){
   const row=document.createElement('div');row.className='proposal';
   const text=document.createElement('span');text.textContent=`${p.id} → ${p.hole}（${p.type}${p.color?' · '+p.color:''} · 自评 ${Math.round(p.confidence*100)}%）${p.evidence?'：'+p.evidence:''}`;
   const ok=document.createElement('button');ok.textContent='采用';ok.onclick=attempt(async()=>{accept(p);render();await check();});
   const no=document.createElement('button');no.textContent='忽略';no.onclick=()=>{proposals=proposals.filter(q=>q!==p);renderProposals();drawOverlay();};
   row.append(text,ok,no);box.append(row);
  }
  if(dropped.length){const small=document.createElement('small');small.className='muted';small.textContent='未采用的读数：'+dropped.map(d=>`${d.id} ${d.hole}（${d.reason}）`).join('；');box.append(small);}
 }
 function accept(p){
  const xy=holeXY(p.hole);if(!xy)throw Error(`孔 ${p.hole} 不在当前孔格中`);
  if(pins.some(q=>q.id===p.id))throw Error(`${p.id} 已存在`);
  pins.push({id:p.id,xy,hole:p.hole,source:'model'});proposals=proposals.filter(q=>q!==p);
  renderProposals();
 }
 el('board-check').onclick=attempt(check);
 el('board-undo').onclick=()=>{invalidate();if(pins.length){const p=pins.pop();wires=wires.filter(e=>e.a!==p.id&&e.b!==p.id);measurements=measurements.filter(e=>e.a!==p.id&&e.b!==p.id);expected=expected.filter(e=>e.a!==p.id&&e.b!==p.id);}else anchors.pop();render();say('已撤销；请重新校验。');};
 el('board-clear-links').onclick=()=>{wires=[];measurements=[];expected=[];invalidate();render();say('关系已清空；端点保留，请重新校验。');};
 function pair(){const a=el('board-a').value,b=el('board-b').value;if(!a||!b||a===b)throw Error('选择两个不同端点');return {a,b};}
 el('board-wire').onclick=attempt(async()=>{wires.push({...pair(),confirmed:true});render();await check();});
 el('board-target').onclick=attempt(async()=>{expected.push({...pair(),connected:el('board-target-value').value==='yes'});render();await check();});
 el('board-measure').onclick=attempt(async()=>{
  if(!el('board-power-off').checked)throw Error('先断电，再用通断档测量，并勾选确认');
  const p=pair();measurements=measurements.filter(m=>!([m.a,m.b].includes(p.a)&&[m.a,m.b].includes(p.b)));
  measurements.push({...p,value:el('board-measure-value').value,power_off:true});render();await check();
 });
 el('board-submit').onclick=attempt(async()=>{
  if(!result?.ok||!frozen||session!==frozen.session)throw Error('该标定帧不可用，请重新冻结');
  if(!ready||!connected)throw Error('先连接分析服务');
  stopAuto();const frameId=crypto.randomUUID();
  frames.set(frameId,{...frozen,hires:null,goal:currentTask().goal});
  await api('/api/analyze',{session,frame_id:frameId,...currentTask(),ask_level:el('ask-level').value,
   image:frozen.image,memory_image:frozen.memory_image,captured_at:frozen.captured_at,board:request()});
  say('已发送同一张高清帧、编号图、连接表和回路判断；模型输出显示在当前判断中。');
 });
 el('board-export').onclick=()=>{
  if(!result?.ok)return;
  const url=URL.createObjectURL(new Blob([JSON.stringify({board:request(),quality:{min_pitch_px:result.min_pitch_px,hole_support:result.hole_support},connections:result.connections,color_candidates:result.candidates},null,2)],{type:'application/json'}));
  const a=document.createElement('a');a.href=url;a.download='agentxr-connections.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
 };
 el('board-zoom').onchange=()=>{el('board-photo').style.width=el('board-zoom').value==='native'?'auto':'100%';};
 el('board-profile').onchange=()=>{reset();spec=profiles[el('board-profile').value];el('board-profile-json').value=JSON.stringify(spec,null,2);};
 for(const id of ['board-mode','board-layout','board-markers','board-profile-json'])el(id).onchange=reset;
 api('/api/breadboard/profiles').then(data=>{
  profiles=data.profiles;
  for(const [id,p] of Object.entries(profiles)){const o=document.createElement('option');o.value=id;o.textContent=p.name;el('board-profile').append(o);}
  spec=profiles[el('board-profile').value];el('board-profile-json').value=JSON.stringify(spec,null,2);
  if(!data.vision_available)say('标定功能尚未启用：安装 requirements-vision.txt 后重启服务。',true);
 }).catch(e=>say(e.message,true));
})();
