"use strict";
const $ = id => document.getElementById(id);
const phases={observe:'观察现场',prepare:'准备操作',act:'执行步骤',verify:'确认结果',complete:'任务完成',uncertain:'需要确认'};
const qualities={good:'画面清晰',dark:'光线不足',blurred:'画面模糊',occluded:'存在遮挡',unknown:'画质未知'};
const modes={act:'操作指导',confirm:'确认目标',observe:'补充观察'};
const SESSION_RE=/^[a-zA-Z0-9-]{8,80}$/;
const LS_KEY='agentxr.session';
function storedSession(){try{return localStorage.getItem(LS_KEY);}catch{return null;}}
function storeSession(id){try{localStorage.setItem(LS_KEY,id);}catch{}}
let session=(()=>{const s=storedSession();return s&&SESSION_RE.test(s)?s:crypto.randomUUID();})();
let config={images:{current_max_width:768,memory_max_width:512,detail_max_side:1024,jpeg_quality:.8,detail_quality:.9},visual:{live_cue_fade_seconds:10},lanes:{guide:true}};
let lanes=['full'];
let stream=null,busy=false,auto=false,timer=null,view='snapshot',latest=null,records=[],shot=null,shotTime=0,previousPixels=null,cameraGeneration=0;
let source=null,connected=false,uploading=false,ready=false,received=0,dropped=0,cancelEpoch=0,focusROI=null,selectingFocus=false,focusStart=null;
let connecting=false,backendIssue='正在连接服务…';
let display=null,lastJob=null,sessionDetail=null,domains=[],pendingDelete=null,dialog=null;
const LEVEL_KEY='agentxr.ask_level';
try{const l=localStorage.getItem(LEVEL_KEY);if(['quiet','balanced','careful'].includes(l))document.getElementById('ask-level').value=l;}catch{}
const lastPaint={guide:0,full:0};
let brief=null,briefJob=null,briefTimer=null,lastTaskSent='';
let historyReference=null;
const actionFrames=new Map(),frames=new Map(),jobs=new Map(),applied=new Set();

// SVG elements have no reliable .hidden property; use the attribute (styled by [hidden]).
function hide(node,value){node.toggleAttribute('hidden',!!value);}
function notice(text,error=false){$('notice').textContent=text;$('notice').classList.toggle('error',error);}
function controls(){
  const locked=busy||auto||uploading;
  $('connect-camera').disabled=busy||uploading;
  $('connect-camera').textContent=stream?'重新连接':'开启摄像头';
  $('capture').disabled=!stream||uploading||!connected||!ready;
  $('capture').textContent=auto?'◎ 切换单次拍照':'◎ 拍照并分析';
  $('auto').disabled=!stream||!connected||!ready;
  $('cancel').hidden=!busy&&!uploading;
  $('stop-camera').disabled=!stream;
  $('camera-select').disabled=busy||auto;
  // Task text stays editable while observing: edits are parsed at once and used from the next frame.
  for(const id of ['reset','new-session'])$(id).disabled=locked;
  $('auto').textContent=auto?'Ⅱ 停止流式观察':'▷ 开始流式观察';
  const blockers=[];
  if(!ready)blockers.push(backendIssue);
  else if(!connected)blockers.push('正在连接输出流，请稍候；长时间未连接可点击“重新连接服务”。');
  if(!stream)blockers.push('请先点击“开启摄像头”并允许访问。');
  $('capture-status').textContent=blockers.join(' ')||(auto?'流式观察：每轮完成后再采样；点击拍照可切换为单次。':busy?'正在分析；可拍一张新照片，等待当前分析结束后处理。':'可以拍照或开始流式观察。');
  $('auto').title=blockers.join(' ');
  if(historyReference)$('capture-status').textContent+=' · 已选历史参考照片';
  $('retry-backend').hidden=ready&&connected;
  $('retry-backend').disabled=connecting;
}
async function api(path,body,method){
  const options={signal:AbortSignal.timeout(15000)};
  if(body!==undefined||method){options.method=method||'POST';if(body!==undefined){options.headers={'Content-Type':'application/json'};options.body=JSON.stringify(body);}}
  const response=await fetch(path,options);
  const data=await response.json();if(!response.ok)throw Error(data.error||`HTTP ${response.status}`);return data;
}
function currentTask(){return {goal:$('goal').value,steps:$('steps').value.split('\n').map(s=>s.trim()).filter(Boolean),plan:$('plan').value,domain:$('domain').value||'auto'};}

// ---------------- views ----------------
function setView(mode){
 view=mode;$('snapshot-tab').classList.toggle('selected',mode==='snapshot');$('live-tab').classList.toggle('selected',mode==='live');
 $('video').hidden=mode!=='live'||!stream;$('frame').hidden=mode!=='snapshot'||!shot;
 $('empty').hidden=!!stream||!!shot;
 const overlaysOn=$('show-overlays').checked;
 $('overlays').hidden=mode!=='snapshot'||!overlaysOn;hide($('snapshot-cues'),mode!=='snapshot'||!overlaysOn);
 $('live-boxes').hidden=mode!=='live'||!$('show-live-cues').checked;hide($('live-cues'),mode!=='live'||!$('show-live-cues').checked);
 $('guide-inset').hidden=!display||!display.image||mode!=='live'||selectingFocus;
 $('frame-tag').textContent=mode==='live'?'LIVE · 示意基于最近分析帧':shot?'ANALYZED FRAME':'CAMERA OFF';resizePlane();renderFocus();
}
function planeSize(){const plane=$('image-plane');return {w:plane.clientWidth||800,h:plane.clientHeight||600};}
function resizePlane(){const w=shot?.width||$('video').videoWidth||4,h=shot?.height||$('video').videoHeight||3;const ratio=view==='live'&&stream?$('video').videoWidth/$('video').videoHeight:w/h;const maxW=$('viewport').clientWidth,maxH=$('viewport').clientHeight;const width=Math.min(maxW,maxH*(ratio||4/3));$('image-plane').style.width=`${width}px`;$('image-plane').style.height=`${width/(ratio||4/3)}px`;renderVisualLayers();}

// ---------------- zoomable annotated photo ----------------
let photoData=null,photoScale=1,photoX=0,photoY=0;
const photoPointers=new Map();
function photoGeometry(){
 const box=$('photo-viewport'),img=$('photo-image');
 const ratio=photoData?.width&&photoData?.height?photoData.width/photoData.height:img.naturalWidth&&img.naturalHeight?img.naturalWidth/img.naturalHeight:16/9;
 const w=Math.min(box.clientWidth,box.clientHeight*ratio);
 return {w,h:w/ratio,vw:box.clientWidth,vh:box.clientHeight};
}
function paintPhoto(){
 if(!photoData)return;
 const {w,h,vw,vh}=photoGeometry();
 const limitX=Math.max(0,(w*photoScale-vw)/2),limitY=Math.max(0,(h*photoScale-vh)/2);
 photoX=Math.max(-limitX,Math.min(limitX,photoX));photoY=Math.max(-limitY,Math.min(limitY,photoY));
 Object.assign($('photo-plane').style,{width:`${w}px`,height:`${h}px`,transform:`translate(-50%, -50%) translate(${photoX}px, ${photoY}px) scale(${photoScale})`});
 $('photo-scale').textContent=`${Math.round(photoScale*100)}%`;
 $('photo-minus').disabled=photoScale<=.5;$('photo-plus').disabled=photoScale>=8;
 $('photo-boxes').setAttribute('style',`--annotation-scale:${1/photoScale}`);
 AXR.draw($('photo-cues'),photoData.cues||[],w,h,photoScale);
}
function zoomPhoto(scale,clientX,clientY){
 const rect=$('photo-viewport').getBoundingClientRect();
 const x=clientX==null?0:clientX-rect.left-rect.width/2,y=clientY==null?0:clientY-rect.top-rect.height/2;
 const next=Math.max(.5,Math.min(8,scale)),ratio=next/photoScale;
 photoX=x-(x-photoX)*ratio;photoY=y-(y-photoY)*ratio;photoScale=next;paintPhoto();
}
function resetPhoto(){photoScale=1;photoX=photoY=0;photoPointers.clear();paintPhoto();}
function openPhoto(data){
 if(!data?.image){notice('先拍照分析，再查看对应的操作图。');return;}
 photoData={...data};
 $('photo-title').textContent=data.guidance?.title||'操作标注图';
 $('photo-instruction').textContent=data.guidance?.instruction||'';
 $('photo-image').src=data.image;
 drawActionBoxes($('photo-boxes'),data.targets||[]);
 if(!$('photo-viewer').open)$('photo-viewer').showModal();
 resetPhoto();$('photo-viewport').focus();
}
function setupPhotoViewer(){
 const viewport=$('photo-viewport'),dialog=$('photo-viewer');
 $('photo-plus').onclick=()=>zoomPhoto(photoScale*1.25);
 $('photo-minus').onclick=()=>zoomPhoto(photoScale/1.25);
 $('photo-reset').onclick=resetPhoto;$('photo-close').onclick=()=>dialog.close();
 $('photo-image').onload=()=>paintPhoto();
 viewport.addEventListener('wheel',e=>{e.preventDefault();zoomPhoto(photoScale*Math.exp(-e.deltaY*.002),e.clientX,e.clientY);},{passive:false});
 viewport.addEventListener('dblclick',resetPhoto);
 viewport.addEventListener('pointerdown',e=>{
  if(e.button!=null&&e.button!==0)return;
  e.preventDefault();photoPointers.set(e.pointerId,{x:e.clientX,y:e.clientY});viewport.setPointerCapture(e.pointerId);
 });
 viewport.addEventListener('pointermove',e=>{
  if(!photoPointers.has(e.pointerId))return;
  const before=[...photoPointers.values()],old=photoPointers.get(e.pointerId);
  photoPointers.set(e.pointerId,{x:e.clientX,y:e.clientY});
  const after=[...photoPointers.values()];
  if(after.length===1){photoX+=e.clientX-old.x;photoY+=e.clientY-old.y;paintPhoto();}
  else if(after.length===2){
   const distance=p=>Math.hypot(p[1].x-p[0].x,p[1].y-p[0].y);
   const mid=p=>({x:(p[0].x+p[1].x)/2,y:(p[0].y+p[1].y)/2});
   const a=mid(before),b=mid(after),dist=distance(before);
   if(dist>0)zoomPhoto(photoScale*distance(after)/dist,a.x,a.y);
   photoX+=b.x-a.x;photoY+=b.y-a.y;paintPhoto();
  }
 });
 const release=e=>photoPointers.delete(e.pointerId);
 for(const type of ['pointerup','pointercancel','lostpointercapture'])viewport.addEventListener(type,release);
 dialog.addEventListener('close',()=>{photoPointers.clear();photoData=null;});
 dialog.addEventListener('keydown',e=>{
  if(e.key==='+'||e.key==='='){e.preventDefault();zoomPhoto(photoScale*1.25);}
  else if(e.key==='-'){e.preventDefault();zoomPhoto(photoScale/1.25);}
  else if(e.key==='0'){e.preventDefault();resetPhoto();}
 });
 new ResizeObserver(()=>{if(dialog.open)paintPhoto();}).observe(viewport);
}

// ---------------- camera ----------------
async function startCamera(){
 const generation=++cameraGeneration;
 try{
  if(!navigator.mediaDevices?.getUserMedia)throw Error('请通过 localhost 使用摄像头');
  if(stream)stopCamera(false);
  const id=$('camera-select').value;
  const opened=await navigator.mediaDevices.getUserMedia({audio:false,video:{width:{ideal:1920},height:{ideal:1080},frameRate:{ideal:30},...(id?{deviceId:{exact:id}}:{})}});
  if(generation!==cameraGeneration){opened.getTracks().forEach(t=>t.stop());return;}
  stream=opened;$('video').srcObject=stream;await $('video').play();measurePreview(generation);
  const track=stream.getVideoTracks()[0];track.onended=()=>{stopCamera();notice('摄像头断开',true);};
  const devices=await navigator.mediaDevices.enumerateDevices();$('camera-select').replaceChildren();
  for(const device of devices.filter(d=>d.kind==='videoinput')){const option=document.createElement('option');option.value=device.deviceId;option.textContent=device.label||'摄像头';$('camera-select').append(option);}
  $('camera-select').value=track.getSettings().deviceId||'';
  $('resolution').textContent=`${$('video').videoWidth} × ${$('video').videoHeight}`;
  $('session-label').textContent='摄像头就绪';setView('live');controls();notice(ready&&connected?'已就绪。单次拍照或开始流式观察。':'摄像头已开启；'+$('capture-status').textContent);
 }catch(error){stopCamera(false);notice(error.name==='NotAllowedError'?'请允许浏览器访问摄像头，并检查 macOS 摄像头隐私权限。':error.message,true);}
}
function measurePreview(generation){
 const video=$('video');if(!video.requestVideoFrameCallback){$('preview-stats').textContent='浏览器未提供预览帧率';return;}
 let start=performance.now(),count=0;
 const tick=now=>{if(generation!==cameraGeneration||!stream)return;count++;if(now-start>=1000){$('preview-stats').textContent=`预览 ${(count*1000/(now-start)).toFixed(0)} fps`;count=0;start=now;}video.requestVideoFrameCallback(tick);};
 video.requestVideoFrameCallback(tick);
}
function stopAuto(clearPending=true){
 auto=false;clearTimeout(timer);timer=null;controls();
 if(clearPending&&ready)api('/api/cancel',{session,pending_only:true}).catch(()=>{});
}
function scheduleAuto(){
 if(!auto||!stream||busy||uploading||!connected||!ready||timer!==null)return;
 timer=setTimeout(()=>{timer=null;if(auto)analyze();},Number($('interval').value)*1000);
}
function stopCamera(invalidate=true){
 if(invalidate)cameraGeneration++;stopAuto();
 if(stream)stream.getTracks().forEach(t=>{t.onended=null;t.stop();});
 stream=null;$('video').srcObject=null;$('session-label').textContent='摄像头已关闭';controls();setView('snapshot');
}
function connectEvents(){
 source?.close();connected=false;controls();
 const ownSession=session;
 source=new EventSource(`/api/events?session=${encodeURIComponent(session)}`);
 source.onopen=()=>{if(session!==ownSession)return;connected=true;$('connection').textContent='● SSE 已连接';controls();};
 source.onerror=()=>{if(session!==ownSession)return;connected=false;stopAuto();$('connection').textContent='SSE 重连中';notice('输出连接中断，已停止采样；连接恢复后可重新开始。',true);};
 source.addEventListener('job',event=>{if(session!==ownSession)return;try{receive(JSON.parse(event.data));}catch(error){notice(`流事件处理失败：${error.message}`,true);}});
}

// ---------------- capture (sizes from server config) ----------------
function jpeg(canvas,quality){return new Promise((resolve,reject)=>canvas.toBlob(blob=>{if(!blob){reject(Error('图像编码失败'));return;}const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(Error('图像读取失败'));reader.readAsDataURL(blob);},'image/jpeg',quality));}
function scaled(original,maxWidth){const canvas=document.createElement('canvas'),scale=Math.min(1,maxWidth/original.width);canvas.width=Math.round(original.width*scale);canvas.height=Math.round(original.height*scale);canvas.getContext('2d').drawImage(original,0,0,canvas.width,canvas.height);return canvas;}
async function captureFrame(){
 const video=$('video');if(!video.videoWidth||video.readyState<2)throw Error('摄像头尚未准备好');
 const I=config.images,captured_at=Date.now(),roi=focusROI?{...focusROI}:null;
 // Freeze exactly one source frame so full-view, memory thumbnail and crop describe the same moment.
 const original=document.createElement('canvas');original.width=video.videoWidth;original.height=video.videoHeight;
 original.getContext('2d').drawImage(video,0,0);
 const main=scaled(original,I.current_max_width),memory=scaled(original,I.memory_max_width);
 const small=document.createElement('canvas');small.width=64;small.height=36;
 const sc=small.getContext('2d');sc.drawImage(original,0,0,64,36);
 const pixels=sc.getImageData(0,0,64,36).data;let brightness=0,motion=0;
 const gray=Array.from({length:pixels.length/4},(_,i)=>.2126*pixels[i*4]+.7152*pixels[i*4+1]+.0722*pixels[i*4+2]);
 for(let i=0;i<gray.length;i++){brightness+=gray[i];if(previousPixels)motion+=Math.abs(gray[i]-previousPixels[i]);}
 const difference=previousPixels?+(motion/gray.length/255).toFixed(4):null;previousPixels=gray;
 let detailPromise=Promise.resolve(null);
 if(roi){
  const crop=document.createElement('canvas'),sw=roi.width*original.width,sh=roi.height*original.height,factor=Math.min(1,I.detail_max_side/Math.max(sw,sh));
  crop.width=Math.max(1,Math.round(sw*factor));crop.height=Math.max(1,Math.round(sh*factor));
  crop.getContext('2d').drawImage(original,roi.x*original.width,roi.y*original.height,sw,sh,0,0,crop.width,crop.height);
  detailPromise=jpeg(crop,I.detail_quality).then(image=>({image,roi}));
 }
 // Keep the same high-resolution frame for model analysis and zoomable annotations.
 const hiresW=I.hires_max_width||0,hires=hiresW>main.width?scaled(original,hiresW):null;
 const [image,memory_image,detail,hiresImage]=await Promise.all([jpeg(main,I.jpeg_quality),memory.width<main.width?jpeg(memory,I.jpeg_quality):Promise.resolve(null),detailPromise,hires?jpeg(hires,I.hires_quality||.85):Promise.resolve(null)]);
 return {image,memory_image,detail,hires:hiresImage,width:main.width,height:main.height,captured_at,brightness:Math.round(brightness/gray.length/255*100),frame_difference:difference};
}
async function analyze(){
 if(!stream||uploading||!connected||!ready)return;
 if(auto&&busy)return;
 clearTimeout(timer);timer=null;
 const ownSession=session,wasAuto=auto,epoch=cancelEpoch;
 uploading=true;controls();
 try{
  const task=currentTask();
  if(task.steps.length>15||task.steps.some(s=>s.length>200))throw Error('最多 15 个步骤，每个步骤最多 200 字符');
  const captured=await captureFrame();const frameId=crypto.randomUUID();
  frames.set(frameId,{...captured,image:captured.hires||captured.image,hires:null,goal:task.goal});
  while(frames.size>40){const oldest=frames.keys().next().value;frames.delete(oldest);}
  received++;$('input-stats').textContent=`已发送 ${received} 帧 · 丢弃过时帧 ${dropped}`;
  const body={session:ownSession,frame_id:frameId,...task,ask_level:$('ask-level').value,detail:captured.detail,captured_at:captured.captured_at,image:captured.image};
  if(captured.memory_image)body.memory_image=captured.memory_image;
  if(captured.hires)body.hires=captured.hires;
  if(historyReference)body.history_record_id=historyReference;
  const submitted=await api('/api/analyze',body);
  // Reserve the job immediately: SSE may arrive after the HTTP response.
  if(ownSession===session&&!jobs.has(submitted.id)){
   jobs.set(submitted.id,{id:submitted.id,session:ownSession,version:-1,status:'queued',lanes:Object.fromEntries(lanes.map(name=>[name,'queued']))});
   busy=true;
  }
  if(epoch!==cancelEpoch)await api('/api/cancel',{session:ownSession});
  else if(wasAuto&&!auto)await api('/api/cancel',{session:ownSession,pending_only:true});
 }catch(error){stopAuto();notice(error.message,true);}
 finally{uploading=false;controls();scheduleAuto();}
}
function captureOnce(){
 if(!stream||uploading||!connected||!ready)return;
 stopAuto(false);
 notice(busy?'已切换单次拍照；新照片将在当前分析结束后处理。':'正在拍照并分析一次。');
 return analyze();
}

// ---------------- streaming updates ----------------
const ACTIVE=s=>s==='queued'||s==='running';
function laneStates(job){return job.lanes||{full:job.status};}
function isNewer(job){return !display||display.source==='brief'||job.captured_at*1000>=display.captured_at;}
function receive(job){
 if(job.session!==session)return;
 if(job.kind==='brief'){receiveBrief(job);return;}
 if(job.kind==='board'){window.receiveBoardRead?.(job);return;}
 const prior=jobs.get(job.id);if(prior&&prior.version>=job.version)return;
 jobs.set(job.id,job);while(jobs.size>40)jobs.delete(jobs.keys().next().value);
 if(job.metrics?.network_route)$('session-label').textContent=`会话：${sessionDetail?.title||session.slice(0,8)} · 网络 ${job.metrics.network_route}`;
 lastJob=job;
 if(job.dialog&&isNewer(job)){dialog=job.dialog;renderAsk();}
 const captured=frames.get(job.frame_id),states=laneStates(job),before=prior?laneStates(prior):{};
 if(job.status==='superseded'&&prior?.status!=='superseded')dropped++;
 // Quick lane: stream text into the HUD, then commit validated cues.
 if(states.guide==='running'&&job.guide_partial&&isNewer(job)&&Date.now()-lastPaint.guide>=120){
  lastPaint.guide=Date.now();showStreaming(AXR.parsePartial(job.guide_partial),job,'guide');
 }
 if(job.guide&&!applied.has(job.id+':guide')){
  applied.add(job.id+':guide');
  if(isNewer(job))applyDisplay(fromGuide(job,captured));
  if(job.guide_metrics?.capture_to_result_ms!=null)$('guide-latency').textContent=`${(job.guide_metrics.capture_to_result_ms/1000).toFixed(1)}s`;
 }
 if(states.guide==='error'&&before.guide!=='error')notice(`快速指导失败：${job.guide_error||'未知错误'}；完整分析继续。`,true);
 // Full lane: audit fields + panels.
 if(states.full==='running'&&Date.now()-lastPaint.full>=150){
  lastPaint.full=Date.now();renderTrace(job);
  $('partial-output').textContent=job.partial||'等待首个输出片段…';
  $('partial-state').textContent='完整分析生成中 · 尚未校验';
  if(job.partial){
   const partial=AXR.parsePartial(job.partial);
   if(partial&&!states.guide&&isNewer(job))showStreaming(partial,job,'full');
   if(partial&&!latest){if(partial.activity)$('activity').textContent=partial.activity;if(partial.summary)$('summary').textContent=partial.summary;}
  }
 }
 if(states.full==='done'&&job.result&&!applied.has(job.id+':full')){
  applied.add(job.id+':full');
  const record={...job,capture:captured||{image:null,captured_at:job.captured_at*1000},reference_capture:actionFrames.get(job.temporal_context?.reference_frame_id),goal:captured?.goal};
  if(captured&&job.temporal_memory?.anchor_frame_id===job.frame_id){actionFrames.set(job.frame_id,captured);while(actionFrames.size>2)actionFrames.delete(actionFrames.keys().next().value);}
  lastFullLatency=job.metrics?.capture_to_result_ms||lastFullLatency;
  upsertRecord(record);render(record);renderHistory();
  if(isNewer(job))applyDisplay(fromFull(record));
  $('partial-output').textContent=job.partial||'';$('partial-state').textContent='本帧已校验';
  notice(auto?'完整分析完成，继续分析最新照片。':'分析完成，标注对应本次照片。');
 }else if(states.guide==='done'&&job.guide&&!records.some(r=>r.id===job.id)&&captured){
  upsertRecord({...job,capture:captured});renderHistory();
 }
 if(states.full==='error'&&before.full!=='error'){stopAuto();renderTrace(job);$('partial-state').textContent='失败 · 输出未采用';notice(job.error||'分析失败',true);}
 if(states.full==='cancelled'&&before.full!=='cancelled')$('partial-state').textContent='已取消';
 if(!Object.values(states).some(ACTIVE))frames.delete(job.frame_id);
 busy=[...jobs.values()].some(j=>Object.values(laneStates(j)).some(ACTIVE));
 renderLaneStatus();
 $('input-stats').textContent=`已发送 ${received} 帧 · 丢弃过时帧 ${dropped}`;
 controls();
 scheduleAuto();
}
function upsertRecord(record){const i=records.findIndex(r=>r.id===record.id);if(i>=0)records[i]={...records[i],...record};else records.unshift(record);}
function fromGuide(job,captured){
 const g=job.guide;
 return {source:'guide',captured_at:job.captured_at*1000,guidance:g.guidance,overview:g.task_overview,cues:g.visual_cues,
  targets:g.targets.map(t=>({...t,inspection:g.guidance_owner==='inspection'})),overlays:[],image:captured?.image||null,width:captured?.width,height:captured?.height,
  owner:g.guidance_owner,latency:job.guide_metrics?.capture_to_result_ms,interaction:g.interaction,assumed:g.assumed_guidance,
  backend:job.guide_metrics?.backend,model:job.guide_metrics?.model,observation:g.activity||''};
}
function fromFull(record){
 const r=record.result;
 return {source:'full',captured_at:record.capture.captured_at,guidance:r.guidance,overview:r.task_overview,cues:r.visual_cues||[],targets:visualTargets(record),
  overlays:r.overlays||[],image:record.capture.image,width:record.capture.width,height:record.capture.height,phase:r.phase,latency:record.metrics?.capture_to_result_ms,
  interaction:r.interaction,assumed:r.assumed_guidance,observation:r.summary||''};
}
function showStreaming(partial,job,lane){
 const g=partial?.guidance;if(!g)return;
 if(g.mode)$('hud-mode').textContent=modes[g.mode]||'下一步';
 if(g.title)$('hud-title').textContent=g.title;
 if(g.instruction)$('hud-instruction').textContent=g.instruction;
 if(g.check)$('hud-check').textContent=`完成后：${g.check}`;
 if(partial.summary){$('hud-observation').textContent=partial.summary;hide($('hud-observation'),false);}
 $('hud-meta').textContent=`${lane==='guide'?(job.guide_metrics?.backend==='ollama'?'本地 Qwen':'快速指导'):'完整分析'}生成中 · 尚未校验`;
 $('hud').classList.add('streaming');
 const o=partial.task_overview;if(o?.goal&&!display)$('hud-goal').textContent=o.goal;
}

// ---------------- HUD & visual layers ----------------
function applyDisplay(d){
 display=d;
 if(d.image){shot={image:d.image,captured_at:d.captured_at,width:d.width,height:d.height,brightness:shot?.brightness};shotTime=d.captured_at;$('frame').src=d.image;}
 $('hud').classList.remove('streaming');
 renderHud();renderVisualLayers();
 setView(view);
}
function renderHud(){
 const d=display,o=d?.overview;
 $('hud-goal-source').textContent=!o?'目标':o.goal_source==='user'?'你设定的目标':'推测目标';
 $('hud-goal').textContent=o?.goal||'等待观察（未设置目标时由模型推测）';
 $('hud-goal-confidence').textContent=o&&o.goal_source!=='user'&&o.goal?` · 自评 ${Math.round((o.goal_confidence||0)*100)}%`:'';
 const steps=o?.steps||[],index=o?.current_step_index??-1;
 $('hud-step-label').textContent=o?.plan_source==='user'?'当前步骤（你的步骤）':'当前步骤（推测计划）';
 $('hud-step').textContent=steps.length&&index>=0?`第 ${index+1} / ${steps.length} 步：${steps[index].title}`:steps.length?'尚未定位当前步骤':'尚无步骤';
 $('hud-progress-bar').style.width=`${o?.progress||0}%`;
 $('hud-steps').replaceChildren();
 steps.forEach((s,i)=>{const li=document.createElement('li');li.className=`${s.status}${i===index?' current':''}`;const icon=document.createElement('i');icon.textContent=s.status==='done'?'✓':s.status==='uncertain'?'?':String(i+1);const span=document.createElement('span');span.textContent=s.title;li.append(icon,span);$('hud-steps').append(li);});
 $('hud-mode').textContent=d?modes[d.guidance.mode]:'下一步';
 $('hud-mode').className=`badge mode-${d?.guidance.mode||'observe'}`;
 $('hud-observation').textContent=d?.observation||'';hide($('hud-observation'),!d?.observation);
 $('hud-title').textContent=d?.guidance.title||'让操作区域完整入镜';
 $('hud-instruction').textContent=d?.guidance.instruction||'';
 $('hud-check').textContent=d?.guidance.check?`完成后：${d.guidance.check}`:'';
 updateAges();renderAsk();
}

// ---------------- questions, assumptions, feedback ----------------
function optionButtons(container,options,defaultIndex,onPick,skip){
 container.replaceChildren();
 options.forEach((text,i)=>{if(i===skip)return;const b=document.createElement('button');b.type='button';b.textContent=text;if(i===defaultIndex)b.classList.add('default');b.onclick=()=>onPick(text);container.append(b);});
}
function renderAsk(){
 const open=dialog?.open,assumption=dialog?.assumption;
 hide($('hud-ask'),!open);
 if(open){
  $('ask-text').textContent=open.text;
  optionButtons($('ask-options'),open.options||[],open.default_option,text=>sendFeedback({kind:'answer',answer:text,question_id:open.id}));
  const i=display?.interaction;
  $('ask-reason').textContent=i?.decision==='ask'&&i.topic===open.topic?`为什么问：${i.reason}`:(open.reason?`为什么问：${open.reason}`:'');
  $('ask-badge').textContent=open.optional?'可选回答':'需要你确认';
  $('hud-ask').classList.toggle('optional',!!open.optional);
  if(!open.optional){$('hud-mode').textContent='需要你确认';$('hud-mode').className='badge mode-confirm';}
 }
 const showAssume=!open&&assumption&&assumption.default_option>=0;
 hide($('hud-assume'),!showAssume);
 if(showAssume){
  $('assume-text').textContent=`我按「${assumption.chosen}」理解（${assumption.text}）。不对的话改选：`;
  optionButtons($('assume-options'),assumption.options,-1,text=>sendFeedback({kind:'correct',answer:text,question_id:assumption.id}),assumption.default_option);
 }
}
async function sendFeedback(body){
 try{
  const out=await api(`/api/sessions/${encodeURIComponent(session)}/feedback`,body);
  dialog=out.dialog;
  // Answering with the model's own default releases the step it had prepared.
  if(body.kind==='answer'&&display?.assumed&&display.interaction?.options?.[display.interaction.default_option]===body.answer){
   display={...display,guidance:display.assumed,assumed:null};renderHud();
  }
  renderAsk();
  const labels={answer:'已收到回答',correct:'已改正我的理解',dismiss:'好的，这类问题我会自行判断',feedback:'已收到反馈'};
  notice(`${labels[body.kind]}，${stream&&ready?'正在用最新画面重新分析…':'下次分析时会用上。'}`);
  if(!$('drawer').hidden)refreshMemory();
  // React right away instead of waiting for the next periodic frame.
  if(stream&&ready&&!uploading&&!auto)analyze();
  return true;
 }catch(e){notice(e.message,true);return false;}
}
$('ask-form').onsubmit=e=>{e.preventDefault();const text=$('ask-input').value.trim();if(!text||!dialog?.open)return;sendFeedback({kind:'answer',answer:text,question_id:dialog.open.id}).then(ok=>{if(ok)$('ask-input').value='';});};
$('ask-dismiss').onclick=()=>{if(dialog?.open)sendFeedback({kind:'dismiss',question_id:dialog.open.id});};
for(const reaction of ['done','stuck','wrong']){const b=$(`fb-${reaction}`);b.onclick=()=>{
 sendFeedback({kind:'feedback',reaction,answer:'',guidance_title:display?.guidance?.title||''}).then(ok=>{if(ok){b.classList.add('sent');setTimeout(()=>b.classList.remove('sent'),1500);}});
};}
$('feedback-note-toggle').onclick=()=>{$('feedback-form').hidden=!$('feedback-form').hidden;if(!$('feedback-form').hidden)$('feedback-input').focus();};
$('feedback-form').onsubmit=e=>{e.preventDefault();const text=$('feedback-input').value.trim();if(!text)return;
 sendFeedback({kind:'feedback',reaction:'note',answer:text,guidance_title:display?.guidance?.title||''}).then(ok=>{if(ok){$('feedback-input').value='';$('feedback-form').hidden=true;}});};
$('ask-level').onchange=()=>{try{localStorage.setItem(LEVEL_KEY,$('ask-level').value);}catch{}notice({quiet:'少打扰：只有猜错代价很高时才会问你。',balanced:'平衡：猜错代价高于打扰成本时才问。',careful:'多确认：拿不准就会问你。'}[$('ask-level').value]);};
let lastFullLatency=0;
// Progress while Codex works: stage from the job's events, elapsed since capture, scaled by the last run.
function renderLaneStatus(){
 const job=lastJob,strip=$('hud-progress-strip');
 if(!job){$('hud-lanes').textContent='待机';hide(strip,true);return;}
 const s=laneStates(job),st=s.full||job.status,ev=job.events||[],has=n=>ev.some(e=>e.event===n);
 const started=frames.get(job.frame_id)?.captured_at||job.started_at*1000,elapsed=Math.max(0,(Date.now()-started)/1000);
 let stage,active=true;
 if(st==='queued')stage='已上传 · 等待上一帧完成';
 else if(st==='running')stage=job.partial?`Codex 正在输出 · ${job.partial.length} 字`:has('full.turn.started')?'Codex 正在看图':has('codex.ready')?'正在发送给 Codex':'已上传';
 else{active=false;stage={done:'已校验',error:'失败',cancelled:'已取消',superseded:'已被新帧取代'}[st]||st;}
 if(s.guide&&ACTIVE(s.guide))stage=(st==='running'||st==='queued'?stage+' · ':'')+'快速指导生成中';
 $('hud-lanes').textContent=active?`${stage} · ${elapsed.toFixed(0)} s${lastFullLatency?` / 上次 ${(lastFullLatency/1000).toFixed(0)} s`:''}`:stage;
 hide(strip,!active);
 if(active){const pct=lastFullLatency?Math.min(95,100*elapsed*1000/lastFullLatency):null;strip.classList.toggle('indeterminate',pct==null);$('hud-progress-fill').style.width=pct==null?'30%':`${pct}%`;}
}
function updateAges(){
 if(!display){$('hud-meta').textContent='';return;}
 const age=Math.max(0,(Date.now()-display.captured_at)/1000);
 $('hud-meta').textContent=display.source==='brief'?'根据你填写的任务文字 · 还没看画面':`${display.source==='guide'?(display.backend==='ollama'?'快速指导（本地 Qwen）':'快速指导'):'完整分析（Codex）'} · 基于 ${age.toFixed(0)} 秒前的画面${display.latency!=null?` · 采集到结果 ${(display.latency/1000).toFixed(1)}s`:''}`;
 const fade=config.visual.live_cue_fade_seconds||10;
 const opacity=String(Math.max(.25,1-age/fade).toFixed(2));
 $('live-cues').style.opacity=opacity;$('live-boxes').style.opacity=opacity;
}
function renderVisualLayers(){
 const d=display,{w,h}=planeSize();
 // Snapshot: full-lane overlay boxes + action/inspection boxes + animated cues.
 $('overlays').replaceChildren();
 if(d){
  for(const b of d.overlays||[]){const box=document.createElement('div');box.className=`box ${b.kind}`;Object.assign(box.style,{left:`${b.x*100}%`,top:`${b.y*100}%`,width:`${b.width*100}%`,height:`${b.height*100}%`});const label=document.createElement('span');label.textContent=b.label;if(b.y<.08){label.style.top='0';label.style.bottom='auto';}box.append(label);$('overlays').append(box);}
  const layer=document.createElement('div');layer.className='action-layer';drawActionBoxes(layer,d.targets||[]);$('overlays').append(layer);
 }
 AXR.draw($('snapshot-cues'),d?.cues||[],w,h);
 // Live: the same cues on the moving preview, labelled with their age and fading out.
 const live=d&&$('show-live-cues').checked;
 drawActionBoxes($('live-boxes'),live?d.targets||[]:[]);
 AXR.draw($('live-cues'),live?d.cues||[]:[],w,h);
 renderVisualGuide();
 updateAges();
}
const ROLE_PREFIX={object:'移动：',destination:'放到：',contact_point:'接到：',tool:'工具：'};
function drawActionBoxes(container,targets){
 container.replaceChildren();
 for(const target of targets){const box=document.createElement('div');box.className=`action-target ${target.role}${target.inspection?' inspection':''}`;
 Object.assign(box.style,{left:`${target.x*100}%`,top:`${target.y*100}%`,width:`${target.width*100}%`,height:`${target.height*100}%`});
 const label=document.createElement('span');label.textContent=(target.inspection?'检查：':ROLE_PREFIX[target.role]||'')+target.label;if(target.y<.06)label.style.transform='none';box.append(label);container.append(box);}
}
function visualTargets(record){
 if(!record?.result)return [];
 const r=record.result;
 const precise=r.next_action?.status==='ready'?r.next_action.targets:[];
 const inspection=[...(r.diagnostics?.regions||[]),...(r.inspection?.response.regions||[])].map(t=>({...t,inspection:true}));
 if(precise.length||inspection.length)return [...precise,...inspection];
 return (r.overlays||[]).filter(t=>t.kind==='focus'||t.kind==='warning').map(t=>({...t,role:'object',inspection:true}));
}
function renderVisualGuide(){
 const d=display;
 $('guide-inset').hidden=!d||!d.image||view!=='live'||selectingFocus;
 if(!d||!d.image)return;
 $('guide-inset-image').src=d.image;
 drawActionBoxes($('guide-inset-boxes'),d.targets||[]);
 const iw=$('guide-inset-image').clientWidth||320,ih=d.width&&d.height?iw*d.height/d.width:iw*.5625;
 AXR.draw($('guide-inset-cues'),d.cues||[],iw,ih);
}

// ---------------- full-result panels ----------------
function renderDiagnostics(record){
 const d=record?.result?.diagnostics;
 const statuses={none:'未报告异常',suspected:'发现待排查现象',insufficient_evidence:'观察依据不足'};
 $('diagnostic-status').textContent=d?statuses[d.status]:'等待观察';
 $('diagnostic-panel').classList.toggle('suspected',d?.status==='suspected');
 $('diagnostic-observed').textContent=d?.observed||'等待分析当前画面的实际现象';
 $('diagnostic-expected').textContent=d?.expected?`期望：${d.expected}`:'';
 $('diagnostic-frame').textContent=record?`基于 ${new Date(record.capture.captured_at).toLocaleTimeString()} 的已分析照片；候选原因尚需验证。`:'每行是一项“期望 vs 实际”，尚未判断时显示待核实。';
 $('state-checks').replaceChildren();
 const names={ok:'符合',abnormal:'不符',unknown:'待核实',not_visible:'看不见',not_applicable:'不适用'};
 for(const row of d?.state_checks||[]){
  const li=document.createElement('li');li.className=`state-${row.status}`;
  const tag=document.createElement('b');tag.textContent=names[row.status]||row.status;
  const text=document.createElement('span');text.textContent=`${row.item}：期望 ${row.expected||'—'} / 实际 ${row.observed||'—'}`;
  li.append(tag,text);$('state-checks').append(li);
 }
 $('diagnostic-causes').replaceChildren();
 for(const [i,cause] of (d?.causes||[]).entries()){
  const card=document.createElement('div');card.className='diagnostic-cause';
  const title=document.createElement('strong');title.textContent=`${i+1}. ${cause.cause}`;
  const basis=document.createElement('small');basis.textContent=' · '+({visible_evidence:'有可见线索',needs_measurement:'需测量验证',needs_confirmation:'待确认'})[cause.basis];
  const evidence=document.createElement('ul');for(const fact of cause.evidence){const li=document.createElement('li');li.textContent=fact;evidence.append(li);}
  const check=document.createElement('p');check.textContent=`验证：${cause.verification}`;
  card.append(title,basis,evidence,check);$('diagnostic-causes').append(card);
 }
}
function renderMemory(record){
 const memory=record?.scene_context, facts=record?.inspection_knowledge||[];
 $('scene-memory-status').textContent=memory?`参考帧 ${memory.frame_id} · ${Math.round(memory.age_seconds)} 秒前 · 历史依据，不代表当前状态`:'本轮尚无可用的历史任务视图';
 $('scene-memory-summary').textContent=memory?.summary||'';
 $('known-facts').replaceChildren();
 for(const fact of facts.slice(-8)){
  const li=document.createElement('li');li.textContent=`${fact.target} / ${fact.field}：${fact.value} · ${new Date(fact.captured_at*1000).toLocaleTimeString()} · ${fact.frame_id}`;
  $('known-facts').append(li);
 }
}
function renderInspection(record){
 const state=record?.inspection_state,response=record?.result?.inspection?.response;
 $('inspection-panel').hidden=!state;
 $('diagnostic-panel').hidden=false;
 $('inspection-findings').replaceChildren();
 if(!state){$('inspection-target').textContent='';$('inspection-question').textContent='';$('inspection-missing').textContent='';$('inspection-quality').textContent='';return;}
 $('inspection-target').textContent=`当前检查：${state.target}`;
 $('inspection-status').textContent=({requested:'待补充',missing:'尚未看清',partial:'已取得部分信息',answered:'本项已完成',remembered:'沿用历史记录',abandoned:'暂未确认'})[state.status]||'检查中';
 $('inspection-question').textContent=state.question;
 for(const finding of state.findings){const li=document.createElement('li');li.textContent=`${finding.field}：${finding.value}（${finding.evidence}）`;$('inspection-findings').append(li);}
 $('inspection-missing').textContent=state.missing_information.length?`仍需看清：${state.missing_information.join('、')}`:'所需信息已记录，将结合任务记忆继续判断。';
 $('inspection-quality').textContent=state.status==='requested'?'只需展示当前对象，无需让所有元件同时入镜。':`当前对象清晰度：${({not_visible:'未入镜',...qualities})[response?.quality]||'待确认'} · 与全局画质分开判断`;
}
const effectVerdicts={matched:'匹配',mismatched:'不匹配',pending:'等待动作 / 后续画面',uncertain:'证据不足',not_applicable:'尚无待验证动作'};
function renderEffect(record){
 const effect=record?.result?.action_effect, history=record?.effect_history||[], pending=record?.pending_expectation;
 const status=effectVerdicts[effect?.verdict]||'等待动作';
 $('effect-status').textContent=status;
 $('effect-status').className=`badge effect-${effect?.verdict||'pending'}`;

 $('effect-action').textContent=effect?.action||'尚无待验证动作';
 $('effect-expected').textContent=effect?.expected_change||'提出动作时保存预期，后续画面再核验。';
 $('effect-observed').textContent=effect?.actual_observation||'等待后续观察';
 $('effect-verification').textContent=effect?.verification?`核验：${effect.verification}`:'';
 const origin=effect?.source==='previous_guidance'?'预期已提前保存':effect?.source==='observed_action'?'预期为事后重建的假设':'等待建立动作预期';
 const kind={object_state:'物体状态',spatial_relation:'空间关系',both:'物体状态与空间关系',unknown:'效果类型待确认'}[effect?.effect_type]||'';
 $('effect-origin').textContent=record?`${origin} · ${kind} · 当前观察 ${new Date(record.capture.captured_at).toLocaleTimeString()}`:origin;
 $('effect-pending').textContent=pending?`待验证：${pending.action} → ${pending.expected_change}`:'';
 $('effect-record-count').textContent=`动作效果记录 · ${history.length} 条`;
 $('effect-records').replaceChildren();
 for(const row of [...history].reverse()){
  const tr=document.createElement('tr');
  const values=[`${row.action}\n${row.before_frame_id} → ${row.after_frame_id}\n${new Date(row.captured_at*1000).toLocaleTimeString()}`,
   `${row.expected_change}\n${row.expectation_origin==='before_observation'?'事前保存':'事后重建'}${row.preconditions?.length?'\n前提：'+row.preconditions.join('；'):''}`,
   `${row.actual_observation}\n动作依据：${row.action_evidence.join('；')||'不足'}\n效果依据：${row.outcome_evidence.join('；')||'不足'}`,
   `${effectVerdicts[row.verdict]}\n${row.verification}`];
  values.forEach((value,index)=>{const td=document.createElement('td');td.textContent=value;if(index===3)td.className=`effect-${row.verdict}`;tr.append(td);});
  $('effect-records').append(tr);
 }
}
function renderAction(record){
 const action=record?.result?.next_action;
 $('action-stage').hidden=!record?.capture?.image;$('inspect-action').hidden=!record;
 if(!record){$('action-preconditions').replaceChildren();$('action-location').textContent='';$('action-snapshot-meta').textContent='';return;}
 if(record.capture.image)$('action-image').src=record.capture.image;
 drawActionBoxes($('action-boxes'),visualTargets(record));
 const iw=$('action-image').clientWidth||300,ih=record.capture.width?iw*record.capture.height/record.capture.width:iw*.5625;
 AXR.draw($('action-cues'),record.result.visual_cues||[],iw,ih);
 list('action-preconditions',action?.preconditions||[],'');
 $('action-location').textContent=action?.location_label?`目标位置：${action.location_label}`:'';
 $('action-snapshot-meta').textContent=`依据 ${new Date(record.capture.captured_at).toLocaleTimeString()} 的照片 · 高亮对应此照片，未跟踪实时画面`;
}
function list(id,items,fallback){$(id).replaceChildren();for(const item of items.length?items:[fallback]){const li=document.createElement('li');li.textContent=item;$(id).append(li);}}
function renderTemporal(record,result){
 const ctx=record?.temporal_context, temporal=result?.temporal;
 const names={baseline:'建立基线',changed:'发现动作变化',unchanged:'没有新动作',uncertain:'变化待确认'};
 $('temporal-state').textContent=temporal?names[temporal.state]:ctx?'正在对比':'待观察';
 $('temporal-reference').textContent=ctx?.reference_frame_id?`参考：${ctx.reference_action} · ${ctx.gap_seconds} 秒前 · ${ctx.reference_frame_id.slice(0,8)}`:'尚无可用历史动作帧，当前帧用于建立基线';
 const ref=record?.reference_capture||actionFrames.get(ctx?.reference_frame_id);
 $('reference-frame').hidden=!ref;if(ref)$('reference-frame').src=ref.image;
 $('temporal-action').textContent=temporal?.action||'';
 list('temporal-changes',temporal?.changes||[],temporal?.state==='baseline'?'单帧不足以判断动作变化':'等待可靠的前后变化依据');
 const decisions={baseline:'已保存观察基线',updated:'已更新有意义动作帧',retained:'继续保留原动作帧',unavailable:'画面不足，暂不建立动作记忆'};
 $('temporal-memory').textContent=decisions[record?.temporal_memory?.decision]||'';
}
function renderIntent(intent,guidance){
 const status={probable:'较可能 · 待验证',ambiguous:'有歧义 · 先确认',unknown:'依据不足'};
 $('intent-status').textContent=intent?status[intent.status]:'等待推断';
 $('intent-candidates').replaceChildren();
 for(const candidate of intent?.candidates||[]){
  const card=document.createElement('div');card.className='intent-card';
  const score=document.createElement('span');score.className='intent-score';score.textContent=`${Math.round(candidate.confidence*100)}%`;
  const goal=document.createElement('strong');goal.textContent=candidate.goal;
  const operation=document.createElement('p');operation.textContent=`可能尝试：${candidate.operation}`;
  const evidence=document.createElement('ul');for(const text of candidate.evidence){const li=document.createElement('li');li.textContent=text;evidence.append(li);}
  const check=document.createElement('small');check.textContent=`如何验证：${candidate.confirmation}`;
  card.append(score,goal,operation,evidence,check);$('intent-candidates').append(card);
 }
 $('intent-clarification').textContent=intent?.clarification||'';
 $('guidance-mode').textContent=modes[guidance?.mode]||'下一步指导';
}
function renderSteps(r){
 $('step-list').replaceChildren();
 const o=r.task_overview,user=r.steps.length>0,steps=user?r.steps:(o?.steps||[]);
 for(const [i,s] of steps.entries()){const li=document.createElement('li');li.className=s.status;const icon=document.createElement('i');icon.textContent=s.status==='done'?'✓':s.status==='uncertain'?'?':String(i+1).padStart(2,'0');const div=document.createElement('div');div.textContent=s.title+(user?'':'（推测）');const small=document.createElement('small');small.textContent=s.evidence||'';div.append(small);li.append(icon,div);$('step-list').append(li);}
 if(!steps.length)list('step-list',[],'自由观察 · 尚无步骤');
 const progress=user?r.progress:o?.progress;
 $('progress-label').textContent=progress==null?'自由观察':`${progress}%${user?'':' · 推测计划'}`;$('progress-bar').style.width=`${progress||0}%`;
}
function render(record){
 latest=record;const r=record.result;
 renderDiagnostics(record);renderInspection(record);renderMemory(record);renderEffect(record);renderAction(record);renderTemporal(record,r);renderIntent(r.intent,r.guidance);renderSteps(r);
 $('activity').textContent=r.activity;$('summary').textContent=r.summary;
 $('quality').textContent=record.inspection_state?`检查对象：${({not_visible:'未入镜',...qualities})[r.inspection?.response.quality]||'待展示'}`:qualities[r.quality];
 $('phase').textContent=phases[r.phase];
 $('next-title').textContent=r.guidance.title;$('next-instruction').textContent=r.guidance.instruction;$('check').textContent=r.guidance.check?`完成后：${r.guidance.check}`:'';
 list('evidence',r.evidence,'没有足够可见证据');list('uncertainties',r.uncertainties,'本次未报告额外不确定性');
 $('confidence').textContent=`${Math.round(r.confidence*100)}%`;
 const m=record.metrics||{};$('latency').textContent=m.capture_to_result_ms!=null?`${(m.capture_to_result_ms/1000).toFixed(1)}s`:`${((m.latency_ms||0)/1000).toFixed(1)}s`;
 const usage=m.tokens||{};$('tokens').textContent=`${usage.input_tokens??'—'} / ${usage.output_tokens??'—'}`;
 if(record.events)renderTrace(record);$('export').disabled=false;
}
function laneTiming(name,m){
 if(!m)return `${name} —`;
 const parts=[];if(m.capture_to_result_ms!=null)parts.push('采集至结果 '+(m.capture_to_result_ms/1000).toFixed(1)+'s');
 if(m.first_delta_ms!=null)parts.push('首字 '+(m.first_delta_ms/1000).toFixed(1)+'s');
 if(m.image_count!=null)parts.push(`${m.image_count} 张图`);if(m.tokens?.input_tokens!=null)parts.push(`输入 ${m.tokens.input_tokens} tok`);
 parts.push(`重连 ${m.retry_count??0}`);
 return `${name} ${parts.join(' · ')}`;
}
function renderTrace(job){
 const m=job.metrics||{};
 $('timing-detail').textContent=[job.guide_metrics?laneTiming('快速指导',job.guide_metrics):null,laneTiming('完整分析',m)].filter(Boolean).join('  |  ');
 $('timing-detail').classList.toggle('retrying',Number(m.retry_count||job.guide_metrics?.retry_count||0)>0);
 $('trace').replaceChildren();for(const event of job.events||[]){const row=document.createElement('div');const time=document.createElement('span');time.textContent=event.time.toFixed(2);row.append(time,document.createTextNode(event.event+(event.message?' · '+event.message:'')));$('trace').append(row);}$('trace').scrollTop=$('trace').scrollHeight;
 $('raw').textContent=JSON.stringify({...job,reference_capture:undefined,capture:job.capture?{captured_at:job.capture.captured_at,width:job.capture.width,height:job.capture.height,image:'[JPEG omitted]'}:undefined},null,2);
}
function renderHistory(){
 $('history').replaceChildren();
 if(!records.length){const p=document.createElement('p');p.className='muted';p.textContent='每次观察都保留对应照片，方便比较不同阶段。';$('history').append(p);return;}
 for(const record of records){
  const card=document.createElement('div');card.className='history-card';
  const button=document.createElement('button');button.className='history-open';
  const img=document.createElement('img');if(record.capture?.image)img.src=record.capture.image;img.alt='';
  const title=document.createElement('span');title.textContent=record.result?.activity||record.guide?.guidance?.title||'观察';
  const time=document.createElement('small');time.textContent=`${new Date(record.capture.captured_at).toLocaleString()} · ${record.result?phases[record.result.phase]:'快速指导'}`;
  button.append(img,title,time);
  button.onclick=()=>{if(busy)return;stopAuto();if(record.result)render(record);applyDisplay(record.result?fromFull(record):fromGuide(record,record.capture));setView('snapshot');notice('正在回看历史照片；下一次采集仍基于最新任务状态。');};
  const remove=document.createElement('button');remove.className='history-delete';remove.textContent='×';remove.title='删除这条记录';
  remove.onclick=async()=>{try{await api(`/api/sessions/${session}/records/${encodeURIComponent(record.id)}`,undefined,'DELETE');}catch(e){notice(e.message,true);return;}if(historyReference===record.id)historyReference=null;records=records.filter(r=>r.id!==record.id);renderHistory();controls();};
  const reference=document.createElement('button');reference.textContent=historyReference===record.id?'取消历史参考':'作为历史参考';
  reference.disabled=!record.capture?.image;
  reference.onclick=()=>{historyReference=historyReference===record.id?null:record.id;renderHistory();controls();notice(historyReference?'下一轮会附上这张旧照片，并标明拍摄时间；仍以当前照片判断现状。':'已取消指定历史参考。');};
  card.append(button,reference,remove);$('history').append(card);
 }
}
function clearResult(){
 latest=null;display=null;renderDiagnostics(null);renderInspection(null);renderMemory(null);renderEffect(null);renderAction(null);renderTemporal(null,null);renderIntent(null,null);renderHud();renderVisualLayers();
 $('timing-detail').textContent='快速指导 — · 完整分析 —';$('timing-detail').classList.remove('retrying');
 $('activity').textContent='等待本次分析';$('summary').textContent='将根据新照片更新判断';$('quality').textContent='待观察';$('phase').textContent='待观察';$('progress-label').textContent='—';$('progress-bar').style.width='0%';$('step-list').replaceChildren();
 $('confidence').textContent='—';$('tokens').textContent='— / —';$('latency').textContent='—';$('guide-latency').textContent='—';$('next-title').textContent='等待分析';$('next-instruction').textContent='';$('check').textContent='';
 list('evidence',[],'等待分析');list('uncertainties',[],'等待分析');$('export').disabled=true;
}

// ---------------- focus selection ----------------
function renderFocus(){
 const box=$('focus-box');box.hidden=!focusROI||view!=='live';
 if(focusROI)Object.assign(box.style,{left:`${focusROI.x*100}%`,top:`${focusROI.y*100}%`,width:`${focusROI.width*100}%`,height:`${focusROI.height*100}%`});
 $('clear-focus').hidden=!focusROI;
}
function focusPoint(event){const rect=$('image-plane').getBoundingClientRect();return {x:Math.max(0,Math.min(1,(event.clientX-rect.left)/rect.width)),y:Math.max(0,Math.min(1,(event.clientY-rect.top)/rect.height))};}
$('select-focus').onclick=()=>{if(!stream){notice('请先开启摄像头',true);return;}selectingFocus=true;setView('live');$('viewport').classList.add('selecting');notice('在画面上拖出矩形，圈住需要看清的部位。');};
$('clear-focus').onclick=()=>{focusROI=null;renderFocus();$('focus-hint').textContent='已清除选框，后续仅发送全景与历史动作帧。';};
$('viewport').addEventListener('pointerdown',event=>{if(!selectingFocus)return;focusROI=null;focusStart=focusPoint(event);$('viewport').setPointerCapture(event.pointerId);event.preventDefault();});
$('viewport').addEventListener('pointermove',event=>{if(!selectingFocus||!focusStart)return;const end=focusPoint(event);focusROI={x:Math.min(focusStart.x,end.x),y:Math.min(focusStart.y,end.y),width:Math.abs(end.x-focusStart.x),height:Math.abs(end.y-focusStart.y)};renderFocus();});
function finishFocus(){if(!selectingFocus||!focusStart)return;if(!focusROI||focusROI.width<.02||focusROI.height<.02){focusROI=null;notice('选区太小，请重新框选。',true);}else{$('focus-hint').textContent='已选局部细节：后续附带同帧裁剪；相机或物体移动后请重新框选。';}focusStart=null;selectingFocus=false;$('viewport').classList.remove('selecting');renderFocus();setView(view);}
$('viewport').addEventListener('pointerup',finishFocus);$('viewport').addEventListener('pointercancel',finishFocus);

// ---------------- sessions & memory (persistent) ----------------
function storedRecord(r){
 return {...r,capture:{image:r.image_url,captured_at:r.captured_at*1000,width:null,height:null},metrics:r.metrics||{},events:[],stored:true};
}
async function loadSession(id){
 window.resetBoard?.();
 session=id;storeSession(id);
 jobs.clear();frames.clear();actionFrames.clear();applied.clear();records=[];historyReference=null;lastJob=null;busy=false;clearResult();
 $('partial-output').textContent='等待输出';$('partial-state').textContent='待机';
 let detail=null;
 try{detail=await api(`/api/sessions/${encodeURIComponent(id)}`);}catch{detail=null;}
 sessionDetail=detail;dialog=detail?.memory?.dialog||null;renderAsk();
 if(detail?.task){$('goal').value=detail.task.goal||'';$('steps').value=(detail.task.steps||[]).join('\n');$('plan').value=detail.task.plan||'';$('domain').value=detail.task.domain||'auto';}
 records=(detail?.records||[]).slice().reverse().map(storedRecord);
 renderHistory();
 const last=records[0];
 if(last){if(last.result)render(last);const full=records.find(r=>r.result);applyDisplay(last.result?fromFull(last):fromGuide(last,last.capture));if(!last.result&&full)render(full);}
 renderMemoryItems(detail);
 lastTaskSent=JSON.stringify(currentTask());briefJob=null;
 brief=detail?.memory?.brief||null;renderBrief();
 if(!records.length&&brief)applyDisplay(fromBrief(brief));
 $('session-label').textContent=detail?`会话：${detail.title||id.slice(0,8)}`:'新会话';
 if(ready)connectEvents();
 refreshSessions();controls();
}
async function refreshSessions(){
 let list=[];try{list=(await api('/api/sessions')).sessions;}catch{}
 $('session-list').replaceChildren();
 const select=$('inherit-from');const keep=select.value;select.replaceChildren();
 const none=document.createElement('option');none.value='';none.textContent='不继承（全新）';select.append(none);
 for(const s of list){
  const opt=document.createElement('option');opt.value=s.id;opt.textContent=`${s.title}${s.id===session?'（当前）':''}`;select.append(opt);
  const li=document.createElement('li');li.className=s.id===session?'current':'';
  const title=document.createElement('strong');title.textContent=s.title;
  const meta=document.createElement('small');meta.textContent=`${new Date(s.updated_at*1000).toLocaleString()} · 信息 ${s.counts.facts} · 记录 ${s.counts.records}${s.counts.scene?' · 场景':''}${s.counts.plan?' · 计划':''}`;
  const goal=document.createElement('p');goal.textContent=s.goal||'自由观察';
  const row=document.createElement('div');row.className='row';
  const open=document.createElement('button');open.textContent=s.id===session?'当前':'打开';open.disabled=s.id===session||busy||auto;open.onclick=()=>{stopAuto();loadSession(s.id);};
  const rename=document.createElement('button');rename.textContent='重命名';rename.onclick=()=>{const input=document.createElement('input');input.value=s.title;input.maxLength=80;const save=document.createElement('button');save.textContent='保存';save.onclick=async()=>{try{await api(`/api/sessions/${s.id}`,{title:input.value},'PATCH');}catch(e){notice(e.message,true);}refreshSessions();};title.replaceChildren(input,save);};
  const del=document.createElement('button');del.className='danger';del.textContent=pendingDelete===s.id?'确认删除？':'删除';
  del.onclick=async()=>{if(pendingDelete!==s.id){pendingDelete=s.id;refreshSessions();setTimeout(()=>{if(pendingDelete===s.id){pendingDelete=null;refreshSessions();}},4000);return;}pendingDelete=null;try{await api(`/api/sessions/${s.id}`,undefined,'DELETE');}catch(e){notice(e.message,true);}if(s.id===session)loadSession(crypto.randomUUID());else refreshSessions();};
  row.append(open,rename,del);li.append(title,goal,meta,row);$('session-list').append(li);
 }
 if(!list.length){const li=document.createElement('li');li.className='muted';li.textContent='还没有保存的会话。开始观察后自动保存。';$('session-list').append(li);}
 select.value=[...select.children].some(o=>o.value===keep)?keep:'';
}
function memoryRow(container,text,kind,id,image){
 const row=document.createElement('div');row.className='memory-row';
 if(image){const img=document.createElement('img');img.src=image;img.alt='';row.append(img);}
 const span=document.createElement('span');span.textContent=text;row.append(span);
 const del=document.createElement('button');del.className='text-button danger';del.textContent='删除';
 del.onclick=async()=>{try{await api(`/api/sessions/${session}/memory/${kind}${id?'/'+encodeURIComponent(id):''}`,undefined,'DELETE');notice('已删除该记忆，下一轮不再传给模型。');}catch(e){notice(e.message,true);}refreshMemory();};
 row.append(del);container.append(row);
}
function renderMemoryItems(detail){
 const box=$('memory-items');box.replaceChildren();
 const m=detail?.memory;
 if(!m){const p=document.createElement('p');p.className='muted';p.textContent='本会话尚无记忆。';box.append(p);return;}
 const section=title=>{const h=document.createElement('h4');h.textContent=title;box.append(h);return box;};
 if(detail.inherited_from){const p=document.createElement('p');p.className='muted';p.textContent=`继承自会话 ${detail.inherited_from.slice(0,8)}`;box.append(p);}
 if(m.plan)memoryRow(section('推测的计划'),`${m.plan.goal||'（无目标）'}：${m.plan.steps.map(s=>s.title).join(' → ')}`,'plan');
 if(m.facts.length){section('已读取的信息');for(const f of m.facts)memoryRow(box,`${f.target} / ${f.field}：${f.value} · ${new Date(f.captured_at*1000).toLocaleTimeString()}`,'fact',f.id);}
 if(m.scene)memoryRow(section('任务场景画面'),`${m.scene.summary} · ${Math.round(m.scene.age_seconds)} 秒前`,'scene','',m.scene.image_url);
 if(m.anchor)memoryRow(section('上一动作参考帧'),m.anchor.action||m.anchor.frame_id,'anchor','',m.anchor.image_url);
 if(m.expectation)memoryRow(section('待验证的动作预期'),`${m.expectation.action} → ${m.expectation.expected_change}`,'expectation');
 if(m.inspection)memoryRow(section('进行中的信息检查'),`${m.inspection.target}：${m.inspection.question}`,'inspection');
 if(m.effects.length){section('动作效果记录');for(const e of [...m.effects].reverse())memoryRow(box,`${effectVerdicts[e.verdict]} · ${e.action} → ${e.expected_change}`,'effect',e.id);}
 const dg=m.dialog;
 if(dg&&(dg.answers.length||dg.dismissed.length||dg.stats.right+dg.stats.wrong)){
  section('你的回答与反馈');
  const p=document.createElement('p');p.className='muted';
  const topicNames={goal:'目标',object:'操作对象',location:'位置',method:'做法',parameter:'参数',safety:'安全',step_status:'步骤状态',other:'其他'};
  p.textContent=`我的默认猜测：对 ${dg.stats.right} 次 / 错 ${dg.stats.wrong} 次（估计出错率 ${Math.round(dg.stats.p_history_wrong*100)}%，越高越常问你）${dg.dismissed.length?` · 交给我判断的话题：${dg.dismissed.map(t=>topicNames[t]||t).join('、')}`:''}`;
  box.append(p);
  const reactions={done:'说做完了',stuck:'说做不到',wrong:'说指引不对',note:'补充'};
  for(const a of [...dg.answers].reverse())memoryRow(box,a.kind==='feedback'?`${reactions[a.reaction]||'反馈'}${a.question?`（${a.question}）`:''}${a.answer?`：${a.answer}`:''}`:`${a.question} → ${a.answer}`,'answer',a.id);
 }
 if(!dg?.answers?.length&&!m.plan&&!m.facts.length&&!m.scene&&!m.anchor&&!m.expectation&&!m.inspection&&!m.effects.length){const p=document.createElement('p');p.className='muted';p.textContent='本会话尚无记忆。';box.append(p);}
}
async function refreshMemory(){let detail=null;try{detail=await api(`/api/sessions/${encodeURIComponent(session)}`);}catch{}sessionDetail=detail;renderMemoryItems(detail);refreshSessions();}
function openDrawer(showForm=false){$('drawer').hidden=false;refreshSessions();refreshMemory();if(showForm)openNewSessionForm();}
function openNewSessionForm(inheritCurrent=false){$('new-session-form').hidden=false;$('new-title').value='';refreshSessions().then(()=>{$('inherit-from').value=inheritCurrent?session:'';});}
async function createSession(){
 const sections=[...$('inherit-sections').querySelectorAll('input')].filter(i=>i.checked).map(i=>i.value);
 const from=$('inherit-from').value;
 try{
  const {id}=await api('/api/sessions',{task:currentTask(),inherit_from:from||null,inherit:from?sections:[],title:$('new-title').value});
  $('new-session-form').hidden=true;stopAuto();await loadSession(id);
  notice(from?`已创建新会话，并继承：${sections.join('、')||'无'}。`:'已创建全新会话。');
 }catch(e){notice(e.message,true);}
}
async function taskChanged(){
 clearTimeout(briefTimer);briefTimer=null;
 const task=currentTask(),key=JSON.stringify(task);
 if(key===lastTaskSent)return;
 if(task.steps.length>15||task.steps.some(s=>s.length>200)){notice('最多 15 个步骤，每个步骤最多 200 字符',true);return;}
 lastTaskSent=key;
 try{await api(`/api/sessions/${encodeURIComponent(session)}`,{task},'PATCH');}catch(e){lastTaskSent='';notice(e.message,true);return;}
 if(!task.goal.trim()&&!task.plan.trim()&&!task.steps.length){brief=null;renderBrief();}
 if(records.length){
  $('task-banner-text').textContent='任务已更新，本会话的记忆继续保留（照片、已读信息、计划）。如果是全新的任务，可以开一个新会话。';
  $('task-banner').hidden=false;
 }
 if(!$('drawer').hidden)refreshMemory();
}

// ---------------- task understanding (text only, before any photo) ----------------
function receiveBrief(job){
 if(briefJob&&briefJob.id===job.id&&briefJob.version>=job.version)return;
 if(briefJob&&briefJob.id!==job.id&&job.started_at<briefJob.started_at)return;
 briefJob=job;
 const state=job.lanes?.brief;
 if(state==='queued'||state==='running'){
  const partial=job.brief_partial?AXR.parsePartial(job.brief_partial):null;
  renderBrief('Codex 正在读任务…',partial?.first_guidance?.title);return;
 }
 if(state==='done'&&job.brief){
  brief=job.brief;renderBrief();
  if(!display||display.source==='brief')applyDisplay(fromBrief(brief));
  notice(`已读懂任务。第一步：${brief.first_guidance.title||'见右侧'}${stream?'':'；开启摄像头后会按画面继续指导。'}`);
 }else if(state==='error'){renderBrief('解析失败');notice(`任务文字解析失败：${job.brief_error||'未知错误'}；拍照分析仍会直接读取任务文字。`,true);}
 else renderBrief();
}
function renderBrief(pending,partialTitle){
 const panel=$('brief-panel');
 hide(panel,!brief&&!pending);panel.classList.toggle('loading',!!pending);
 $('brief-status').textContent=pending||(brief?'已更新':'—');
 const b=brief;
 $('brief-goal').textContent=b?.goal?`目标：${b.goal}`:'';
 $('brief-first').replaceChildren();
 const fg=b?.first_guidance,title=pending&&partialTitle?partialTitle:fg?.title;
 if(title){const s=document.createElement('strong');s.textContent=`第一步：${title}`;const p=document.createElement('span');p.textContent=pending?'':[fg?.instruction,fg?.check?`验收：${fg.check}`:''].filter(Boolean).join(' · ');$('brief-first').append(s,p);}
 hide($('brief-first'),!title);
 $('brief-steps').replaceChildren();
 for(const s of b?.steps||[]){const li=document.createElement('li');li.textContent=s.title;if(s.check){const c=document.createElement('small');c.textContent=`完成标志：${s.check}`;li.append(c);}$('brief-steps').append(li);}
 $('brief-requirements').replaceChildren();
 for(const r of b?.requirements||[]){const li=document.createElement('li');const k=document.createElement('b');k.textContent=`${r.item}：`;li.append(k,document.createTextNode(r.value));$('brief-requirements').append(li);}
 $('brief-notes').replaceChildren();
 for(const t of [...(b?.safety||[]).map(s=>`安全：${s}`),...(b?.missing||[]).map(m=>`还缺：${m}`)]){const li=document.createElement('li');li.textContent=t;$('brief-notes').append(li);}
}
function fromBrief(b){
 const fg=b.first_guidance;
 return {source:'brief',captured_at:(b.created_at||Date.now()/1000)*1000,
  guidance:{mode:'act',title:fg.title||'准备开始',instruction:fg.instruction,check:fg.check},
  overview:{goal:b.goal,goal_source:$('goal').value.trim()?'user':'inferred',goal_confidence:b.goal_confidence,
   steps:b.steps.map((s,i)=>({title:s.title,status:i===0?'active':'pending',evidence:''})),current_step_index:b.steps.length?0:-1,
   plan_source:b.steps_source==='user'?'user':'inferred',progress:0},
  cues:[],targets:[],overlays:[],image:null,interaction:null,assumed:null,observation:''};
}

// ---------------- draggable floating panel ----------------
function makeDraggable(box,handle,key){
 const place=(x,y)=>{
  const host=box.parentElement.getBoundingClientRect();
  const maxX=Math.max(0,host.width-box.offsetWidth),maxY=Math.max(0,host.height-box.offsetHeight);
  box.style.left=`${Math.min(maxX,Math.max(0,x))}px`;box.style.top=`${Math.min(maxY,Math.max(0,y))}px`;box.style.right='auto';
 };
 // Stored as fractions of the viewport so a resized window keeps the box in the same corner.
 const restore=()=>{let pos=null;try{pos=JSON.parse(localStorage.getItem(key)||'null');}catch{}
  if(pos&&!box.hidden){const host=box.parentElement.getBoundingClientRect();place(pos.x*host.width,pos.y*host.height);}};
 handle.addEventListener('pointerdown',e=>{
  if(e.button!==0||e.target.closest('button'))return;
  const start=box.getBoundingClientRect(),host=box.parentElement.getBoundingClientRect();
  const dx=e.clientX-start.left,dy=e.clientY-start.top;
  handle.setPointerCapture(e.pointerId);box.classList.add('dragging');e.preventDefault();
  const move=ev=>place(ev.clientX-host.left-dx,ev.clientY-host.top-dy);
  const up=()=>{handle.removeEventListener('pointermove',move);box.classList.remove('dragging');
   const r=box.getBoundingClientRect(),h=box.parentElement.getBoundingClientRect();
   try{localStorage.setItem(key,JSON.stringify({x:(r.left-h.left)/h.width,y:(r.top-h.top)/h.height}));}catch{}};
  handle.addEventListener('pointermove',move);handle.addEventListener('pointerup',up,{once:true});handle.addEventListener('pointercancel',up,{once:true});
 });
 handle.addEventListener('dblclick',e=>{if(e.target.closest('button'))return;box.style.left='';box.style.top='';box.style.right='';try{localStorage.removeItem(key);}catch{}});
 if(typeof MutationObserver==='function')new MutationObserver(restore).observe(box,{attributes:true,attributeFilter:['hidden']});
 new ResizeObserver(restore).observe(box.parentElement);
}

// ---------------- wiring ----------------
new ResizeObserver(resizePlane).observe($('viewport'));
$('connect-camera').onclick=startCamera;$('start-camera').onclick=startCamera;
$('stop-camera').onclick=()=>{stopCamera();notice(busy?'摄像头已关闭，正在分析的一帧可单独取消。':'摄像头已关闭。');};
$('camera-select').onchange=startCamera;$('capture').onclick=captureOnce;
$('auto').onclick=()=>{if(auto){stopAuto();notice('已停止输入，清除待处理帧；当前分析继续。');}else{auto=true;setView('live');controls();analyze();}};
$('cancel').onclick=async()=>{cancelEpoch++;stopAuto(false);try{await api('/api/cancel',{session});notice('正在取消分析…');}catch(e){notice(e.message,true);}};
$('expand-guide').onclick=()=>openPhoto(display);
$('inspect-action').onclick=()=>openPhoto(latest?fromFull(latest):null);
$('action-image').onclick=()=>openPhoto(latest?fromFull(latest):null);
$('zoom-photo').onclick=()=>openPhoto(display);
setupPhotoViewer();
$('snapshot-tab').onclick=()=>setView('snapshot');$('live-tab').onclick=()=>setView('live');
$('show-overlays').onchange=()=>setView(view);$('show-live-cues').onchange=()=>{renderVisualLayers();setView(view);};
$('hud-toggle').onclick=()=>{const collapsed=!$('hud-body').hidden;$('hud-body').hidden=collapsed;$('hud-toggle').textContent=collapsed?'展开':'收起';$('hud-toggle').setAttribute('aria-expanded',String(!collapsed));};
$('open-drawer').onclick=()=>openDrawer();$('close-drawer').onclick=()=>{$('drawer').hidden=true;};
$('new-session').onclick=()=>openNewSessionForm(true);$('cancel-new-session').onclick=()=>{$('new-session-form').hidden=true;};
$('create-session').onclick=createSession;
$('clear-memory').onclick=async()=>{if($('clear-memory').dataset.confirm!=='1'){$('clear-memory').dataset.confirm='1';$('clear-memory').textContent='再点一次确认清空';setTimeout(()=>{$('clear-memory').dataset.confirm='';$('clear-memory').textContent='清空全部记忆';},4000);return;}
 $('clear-memory').dataset.confirm='';$('clear-memory').textContent='清空全部记忆';try{await api(`/api/sessions/${session}/memory/all`,undefined,'DELETE');notice('已清空本会话记忆。');}catch(e){notice(e.message,true);}refreshMemory();};
$('reset').onclick=()=>openDrawer(true);
$('banner-new-session').onclick=()=>{$('task-banner').hidden=true;openDrawer(true);};
$('banner-dismiss').onclick=()=>{$('task-banner').hidden=true;};
for(const id of ['goal','steps','plan','domain'])$(id).onchange=taskChanged;
// Parse while typing: send once the user pauses, so Codex reads the task before any photo.
for(const id of ['goal','steps','plan'])$(id).addEventListener('input',()=>{clearTimeout(briefTimer);briefTimer=setTimeout(taskChanged,1000);});
makeDraggable($('guide-inset'),$('guide-inset-plane'),'agentxr.guide_inset_pos');
$('export').onclick=()=>{if(!latest)return;const blob=new Blob([JSON.stringify({...latest,reference_capture:undefined,capture:{captured_at:latest.capture.captured_at,image:'[omitted]'}},null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=`agentxr-${latest.id}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
function renderDomains(){
 const select=$('domain'),keep=select.value||'auto';
 for(const d of domains){if([...select.children].some(o=>o.value===d.id))continue;const o=document.createElement('option');o.value=d.id;o.textContent=d.label;select.append(o);}
 select.value=keep;
 $('presets').replaceChildren();
 for(const d of domains)for(const p of d.presets||[]){const b=document.createElement('button');b.className='text-button';b.textContent=p.label+' ↗';b.onclick=()=>{if(busy||auto||uploading)return;$('goal').value=p.goal;$('domain').value=d.id;if(p.plan_placeholder)$('plan').placeholder=p.plan_placeholder;taskChanged();notice(`已设置目标并加载「${d.label}」规则；可在操作说明中补充细节。`);};$('presets').append(b);}
}
setInterval(()=>{
 $('frame-age').textContent=view==='live'&&stream?'LIVE':shotTime?`${Math.floor((Date.now()-shotTime)/1000)}s AGO`:'尚未采集';
 updateAges();renderLaneStatus();
},500);
window.addEventListener('pagehide',()=>{cancelEpoch++;source?.close();stopCamera();if(ready)fetch('/api/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session}),keepalive:true}).catch(()=>{});});
async function connectBackend(){
 if(connecting)return;
 connecting=true;ready=false;connected=false;source?.close();stopAuto(false);
 backendIssue='正在连接服务…';controls();
 try{
 const h=await api('/api/health');
 const compatible=h.transport==='sse'&&['session-store','ask-gate','task-brief'].every(c=>h.capabilities?.includes(c));
 ready=compatible&&!!h.cli_available;
 if(!ready){
  backendIssue=compatible?'后端未找到 Codex CLI，请通过 start.command 启动服务。':'后端版本不兼容，请停止旧服务并重新运行 start.command。';
  $('connection').textContent=compatible?'Codex CLI 不可用':'需要重启后端';notice(backendIssue,true);return;
 }
 if(h.config)config={...config,...h.config};lanes=h.lanes||['full'];
 try{domains=(await api('/api/domains')).domains;}catch{domains=[];}
 renderDomains();
 $('connection').textContent='连接输出流…';
 await loadSession(session);
 $('session-label').textContent+=` · 代理 ${h.proxy}`;
 if(h.guide_backend?.reason)notice(h.guide_backend.reason,true);
 }catch(e){
  ready=false;backendIssue=`服务连接失败：${e.message}。请运行 start.command，并通过 http://localhost:8765 打开页面，然后重新连接服务。`;
  $('connection').textContent='服务未连接';notice(backendIssue,true);
 }finally{connecting=false;controls();}
}
$('retry-backend').onclick=connectBackend;
connectBackend();
