const fs=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const {webcrypto}=require('node:crypto');
class Element {
 constructor(tag='div'){this.tagName=tag;this.attrs={};this.style={};this.dataset={};this.classList={toggle(){},remove(){},add(){}};this.children=[];this.value='';this.textContent='';this.checked=true;this.clientWidth=800;this.clientHeight=600;this.videoWidth=1280;this.videoHeight=720;this.readyState=4;}
 addEventListener(){}
 setPointerCapture(){}
 setAttribute(k,v){this.attrs[k]=String(v);}
 getAttribute(k){return this.attrs[k];}
 toggleAttribute(k,v){if(v)this.attrs[k]='';else delete this.attrs[k];}
 querySelectorAll(){return this.children.filter(c=>c.tagName==='input');}
 getBoundingClientRect(){return {left:0,top:0,width:800,height:600};}
 toBlob(callback){callback({});}
 append(...nodes){this.children.push(...nodes);}
 replaceChildren(...nodes){this.children=nodes;}
 getContext(){return {drawImage(){},getImageData(){return {data:new Uint8ClampedArray(64*36*4)}}};}
}
const elements=new Map();
const requests=[];
const all=node=>[node,...node.children.filter(c=>c instanceof Element).flatMap(all)];
const responses={
 '/api/health':{transport:'sse',capabilities:['session-store','guide-lane','visual-cues','ask-gate','task-brief'],cli_available:true,proxy:'local',lanes:['full','guide'],config:{visual:{live_cue_fade_seconds:10}}},
 '/api/domains':{domains:[{id:'electronics',label:'电子电路',presets:[{label:'LED 排查',goal:'点亮 LED'}]}]},
 '/api/sessions':{sessions:[]},
};
const context=vm.createContext({
 document:{getElementById(id){if(!elements.has(id))elements.set(id,new Element());return elements.get(id);},createElement(tag){return new Element(tag);},createElementNS(ns,tag){return new Element(tag);},createTextNode(s){return s;}},
 FileReader:class{readAsDataURL(){this.result='data:image/jpeg;base64,fixture';this.onload();}},
 crypto:webcrypto,console,assert,Date,Map,Set,JSON,Number,Math,Array,String,Object,Uint8ClampedArray,Promise,
 ResizeObserver:class{observe(){}},EventSource:class{close(){}addEventListener(){}},
 AbortSignal:{timeout(){}},setTimeout(){return 1;},clearTimeout(){},setInterval(){},
 window:{addEventListener(){}},navigator:{},localStorage:{getItem(){return null;},setItem(){}},
 fetch:async(path,options)=>{requests.push({path,options});const body=responses[path]??(path.startsWith('/api/sessions/')?{id:'x',memory:null,records:[]}:{id:'test'});return {ok:true,json:async()=>body};},
});
context.all=all;
vm.runInContext(fs.readFileSync('web/cues.js','utf8')+'\n;globalThis.AXR=AXR;',context);
vm.runInContext(fs.readFileSync('web/app.js','utf8'),context);
const run=code=>vm.runInContext(code,context);
(async()=>{
 for(let i=0;i<5;i++)await new Promise(r=>setImmediate(r));
 // ---- tolerant streaming JSON parser ----
 const p=run(`AXR.parsePartial('{"guidance":{"mode":"act","title":"翻开 笔记本","instruction":"把封面向右')`);
 assert.equal(p.guidance.title,'翻开 笔记本');
 assert.equal(p.guidance.instruction,'把封面向右');
 assert.equal(run(`AXR.parsePartial('{"guidance":{"mode":"ac')`).guidance.mode,'ac');
 assert.deepEqual(run(`AXR.parsePartial('{"a":[1,2,{"b":tr')`),{a:[1,2,{}]});
 assert.equal(run(`AXR.parsePartial('{"a":"x\\\\')`).a,'x');
 assert.equal(run(`AXR.parsePartial('')`),null);
 run(`
 assert.equal($('domain').children.length,1,'domain options loaded from plug-ins');
 assert.equal($('presets').children.length,1,'presets come from domain files, not code');
 const capture={image:'data:image/jpeg;base64,test',width:1280,height:720,captured_at:Date.now(),brightness:50};
 frames.set('frame-1',capture);
 stream={};auto=true;setView('live');
 const base={id:'job-1',frame_id:'frame-1',session,captured_at:Date.now()/1000,started_at:Date.now()/1000,events:[],metrics:{delta_count:1,retry_count:0},guide_metrics:{delta_count:1,retry_count:0},partial:'',guide_partial:''};
 // 1) quick lane streams guidance text into the fixed HUD before validation
 receive({...base,version:1,status:'running',lanes:{guide:'running',full:'queued'},guide_partial:'{"guidance":{"mode":"act","title":"打开 笔记本","instruction":"把封面向'});
 assert.equal($('hud-title').textContent,'打开 笔记本');
 assert.equal($('hud-instruction').textContent,'把封面向');
 assert.ok($('hud-meta').textContent.includes('尚未校验'));
 assert.equal(display,null);
 assert.equal(busy,true);
 // 2) validated quick guidance: overview + animated cues on the live view
 const guide={guidance:{mode:'act',title:'打开 笔记本',instruction:'把封面向右翻开',check:'可见内页',precision:'object',location_basis:'visual_location'},
   task_overview:{goal:'准备记录笔记',goal_source:'inferred',goal_confidence:.8,plan_source:'inferred',progress:33,current_step_index:1,plan_revision_reason:'',
     steps:[{title:'找到笔记本',status:'done',evidence:'x'},{title:'打开笔记本',status:'active',evidence:''},{title:'开始记录',status:'pending',evidence:''}]},
   visual_cues:[{type:'arrow',label:'翻开',tool:'hand',coordinate_space:'full_frame',x1:.2,y1:.3,x2:.5,y2:.3,radius:0,direction:'none',turns:'none',confidence:.9},
                {type:'rotate',label:'拧',tool:'none',coordinate_space:'full_frame',x1:.5,y1:.5,x2:0,y2:0,radius:.05,direction:'ccw',turns:'quarter',confidence:.9}],
   targets:[{role:'object',label:'笔记本',coordinate_space:'full_frame',x:.1,y:.1,width:.3,height:.4,confidence:.9}],activity:'',quality:'good',confidence:.9,guidance_owner:'guide',usable:true};
 receive({...base,version:2,status:'running',lanes:{guide:'done',full:'running'},guide,guide_metrics:{capture_to_result_ms:4200,delta_count:9,retry_count:0}});
 assert.equal(display.source,'guide');
 assert.equal($('hud-goal').textContent,'准备记录笔记');
 assert.equal($('hud-goal-source').textContent,'推测目标');
 assert.equal($('hud-step').textContent,'第 2 / 3 步：打开笔记本');
 assert.equal($('hud-steps').children.length,3);
 assert.ok($('hud-steps').children[1].className.includes('current'));
 assert.equal($('guide-latency').textContent,'4.2s');
 const liveCues=$('live-cues').children.filter(c=>c.tagName==='g');
 assert.equal(liveCues.length,2,'arrow + rotate drawn on live preview');
 assert.ok(liveCues[0].attrs.class.includes('cue-arrow'));
 const rotate=liveCues[1];
 assert.ok(all(rotate).some(n=>n.tagName==='animateTransform'&&n.attrs.to.startsWith('-360')),'ccw rotation animates counter-clockwise');
 assert.ok(all(liveCues[0]).some(n=>n.tagName==='animateMotion'),'tool ghost moves along the arrow');
 assert.equal($('live-boxes').children.length,1);
 assert.equal($('guide-inset').hidden,false);
 assert.ok($('guide-inset-cues').children.length>1);
 // 3) full lane result: panels + user-facing fields
 const result={guidance:{mode:'act',title:'打开 笔记本',instruction:'打开框中的笔记本',check:'笔记本翻开'},task_overview:guide.task_overview,visual_cues:[guide.visual_cues[0]],
   next_action:{status:'ready',targets:[{role:'destination',label:'A12',x:.2,y:.3,width:.02,height:.02}],preconditions:[],location_label:'A12'},overlays:[],
   phase:'act',steps:[],activity:'拿起杯子',summary:'杯子在手中',quality:'good',confidence:.9,progress:null,scene_scope:'task_view',evidence:['可见杯子'],uncertainties:[],
   intent:{status:'probable',candidates:[{goal:'喝水',operation:'将杯子移向嘴边',confidence:.8,evidence:['手持杯子'],confirmation:'观察杯子后续移动'}],clarification:''},
   temporal:{state:'baseline',action:'建立观察基线',changes:[],confidence:0},
   diagnostics:{status:'suspected',expected:'LED 发光',observed:'未见 LED 发光',state_checks:[{item:'LED',expected:'发光',observed:'未亮',status:'abnormal'},{item:'供电',expected:'开启',observed:'看不清',status:'unknown'}],regions:[{role:'object',label:'LED',x:.1,y:.2,width:.2,height:.2}],causes:[{cause:'供电未确认',basis:'needs_measurement',evidence:[],verification:'读取电源输出电压'}]}};
 receive({...base,version:3,status:'done',lanes:{guide:'done',full:'done'},guide,result,temporal_context:{image_count:1,reference_frame_id:null},temporal_memory:{decision:'baseline',anchor_frame_id:'frame-1'},metrics:{capture_to_result_ms:12000,delta_count:30,retry_count:0}});
 assert.equal(records.length,1);
 assert.equal(display.source,'full');
 assert.equal($('activity').textContent,'拿起杯子');
 assert.equal($('latency').textContent,'12.0s');
 assert.equal($('diagnostic-status').textContent,'发现待排查现象');
 assert.equal($('state-checks').children.length,2,'generic expected-vs-observed rows');
 assert.ok($('state-checks').children[0].className.includes('abnormal'));
 assert.equal($('diagnostic-causes').children.length,1);
 assert.ok($('step-list').children[0].children[1].textContent.includes('（推测）'),'inferred plan labelled');
 assert.ok($('progress-label').textContent.includes('推测计划'));
 assert.equal($('action-boxes').children.length,2);
 assert.equal($('intent-status').textContent,'较可能 · 待验证');
 assert.equal($('guidance-mode').textContent,'操作指导');
 assert.equal(busy,false);
 // 4) an older full result never replaces newer quick guidance
 frames.set('frame-new',capture);
 const newer={...base,id:'job-2',frame_id:'frame-new',captured_at:Date.now()/1000+5};
 receive({...newer,version:2,status:'running',lanes:{guide:'done',full:'queued'},guide:{...guide,guidance:{...guide.guidance,title:'开始 记录'}}});
 assert.equal($('hud-title').textContent,'开始 记录');
 receive({...base,id:'job-old',frame_id:'frame-old',captured_at:Date.now()/1000-20,version:3,status:'done',lanes:{guide:'superseded',full:'done'},result,metrics:{}});
 assert.equal($('hud-title').textContent,'开始 记录','stale full result kept off the HUD');
 // 5) inspection, memory and effect panels
 const confirmed=latest;
 const focused={...confirmed,inspection_state:{target:'电池',question:'标称电压是多少',status:'partial',missing_information:['极性标记'],findings:[{field:'标称电压',value:'1.5 V',evidence:'可见标签'}]},result:{...confirmed.result,inspection:{response:{quality:'good',regions:[{role:'object',label:'电池标签',x:.2,y:.2,width:.2,height:.2}]}}}};
 renderInspection(focused);
 assert.equal($('inspection-target').textContent,'当前检查：电池');
 assert.equal($('diagnostic-panel').hidden,true);
 assert.equal(visualTargets(focused)[0].label,'电池标签');
 assert.equal(visualTargets({...focused,result:{...focused.result,inspection:{response:{regions:[]}}}}).length,0,'No fallback boxes during inspection');
 renderInspection(null);assert.equal($('diagnostic-panel').hidden,false);
 renderMemory({...confirmed,scene_context:{frame_id:'old-task-view',age_seconds:601,summary:'此前已看到电池和面包板'},inspection_knowledge:[{target:'电池',field:'标称电压',value:'1.5 V',captured_at:Date.now()/1000,frame_id:'closeup'}]});
 assert.ok($('scene-memory-status').textContent.includes('601 秒前'));
 assert.equal($('known-facts').children.length,1);
 const effect={source:'previous_guidance',action:'开启输出',effect_type:'object_state',expected_change:'LED 发光',actual_observation:'未见发光',verdict:'mismatched',verification:'读取电源电压',action_evidence:['开关改变'],outcome_evidence:['LED 未亮']};
 renderEffect({...confirmed,result:{...confirmed.result,action_effect:effect},effect_history:[{...effect,before_frame_id:'before',after_frame_id:'after',captured_at:Date.now()/1000,expectation_origin:'before_observation',preconditions:['回路闭合']}],pending_expectation:{action:'读取电压',expected_change:'读数可见'}});
 assert.equal($('effect-status').textContent,'不匹配');
 assert.equal($('effect-records').children.length,1);
 // 6) memory manager: every item is individually deletable
 renderMemoryItems({inherited_from:'abcdef123456',memory:{plan:{goal:'做饭',steps:[{title:'洗菜'}]},facts:[{id:'r|f',target:'电池',field:'电压',value:'1.5 V',captured_at:1}],scene:{summary:'台面',age_seconds:3,image_url:'/x.jpg'},anchor:null,expectation:{action:'翻开',expected_change:'可见内页'},inspection:null,effects:[{id:'e1',verdict:'matched',action:'a',expected_change:'b'}]}});
 const rows=$('memory-items').children.filter(c=>c.className==='memory-row');
 assert.equal(rows.length,5);
 assert.ok(rows.every(r=>r.children.some(c=>c.textContent==='删除')));
 // 7) superseded frames are counted as dropped
 frames.set('frame-2',capture);
 receive({...base,id:'job-3',frame_id:'frame-2',version:1,status:'queued',lanes:{guide:'queued',full:'queued'}});
 receive({...base,id:'job-3',frame_id:'frame-2',version:2,status:'superseded',lanes:{guide:'superseded',full:'superseded'}});
 assert.equal(frames.has('frame-2'),false);
 assert.equal(dropped,1);
 ready=true;connected=true;stream={};busy=true;auto=true;$('interval').value='1';$('domain').value='electronics';
 `);
 await run('analyze()');
 const sent=requests.find(r=>r.path==='/api/analyze');
 assert.ok(sent,'Must keep submitting frames while inference is busy');
 const body=JSON.parse(sent.options.body);
 assert.equal(body.domain,'electronics');
 assert.ok('memory_image' in body||body.image,'sends downscaled frame (+ memory thumbnail when smaller)');
 assert.ok('ask_level' in body,'interruption level travels with each frame');
 // ---- interactive questions, assumptions and feedback ----
 responses['/api/sessions/'+run('session')+'/feedback']={entry:{},dialog:{answers:[{id:'a1',kind:'answer',topic:'object',question:'要打开哪本？',answer:'右边那本',at:1}],open:null,assumption:null,dismissed:[],stats:{right:0,wrong:1,asked:1,assumed:0,last_ask_at:1,p_history_wrong:.33}}};
 run(`
 auto=false;uploading=true;
 const q={id:'q1',topic:'object',topic_label:'操作对象',text:'要打开哪本？',options:['左边那本','右边那本'],default_option:0,reason:'猜错代价较高'};
 dialog={answers:[],open:q,assumption:null,dismissed:[],stats:{right:0,wrong:0}};
 display={...display,guidance:{mode:'confirm',title:'想先确认操作对象',instruction:'要打开哪本？',check:''},assumed:{mode:'act',title:'打开 左边笔记本',instruction:'翻开',check:'可见内页'},
  interaction:{decision:'ask',topic:'object',reason:'猜错代价较高',options:q.options,default_option:0}};
 renderHud();
 assert.equal($('hud-ask').attrs.hidden,undefined,'question card visible');
 assert.equal($('ask-text').textContent,'要打开哪本？');
 assert.equal($('ask-options').children.length,2,'every option is a clickable button');
 assert.ok($('ask-reason').textContent.includes('猜错代价较高'),'explains why it interrupts');
 assert.equal($('hud-assume').attrs.hidden,'');
 `);
 await run(`$('ask-options').children[1].onclick()`);
 for(let i=0;i<5;i++)await new Promise(r=>setImmediate(r));
 const fb=requests.filter(r=>r.path.endsWith('/feedback')).at(-1);
 assert.deepEqual(JSON.parse(fb.options.body),{kind:'answer',answer:'右边那本',question_id:'q1'});
 run(`
 assert.equal($('hud-ask').attrs.hidden,'','answered question disappears');
 dialog={answers:[],open:null,assumption:{id:'s1',topic:'location',text:'插到哪一列？',options:['A 列','B 列','C 列'],default_option:0,chosen:'A 列'},dismissed:[],stats:{right:0,wrong:0}};
 renderAsk();
 assert.equal($('hud-assume').attrs.hidden,undefined,'non-blocking assumption chip');
 assert.ok($('assume-text').textContent.includes('A 列'));
 assert.equal($('assume-options').children.length,2,'only the alternatives are offered');
 `);
 await run(`$('assume-options').children[0].onclick()`);
 await run(`$('fb-stuck').onclick()`);
 for(let i=0;i<5;i++)await new Promise(r=>setImmediate(r));
 const bodies=requests.filter(r=>r.path.endsWith('/feedback')).map(r=>JSON.parse(r.options.body));
 assert.deepEqual(bodies.at(-2),{kind:'correct',answer:'B 列',question_id:'s1'});
 assert.equal(bodies.at(-1).reaction,'stuck');
 // ---- task understanding: typed text is parsed before any photo; fields stay editable while observing ----
 run(`
 auto=true;busy=true;controls();
 assert.notEqual($('goal').disabled,true,'task text stays editable while streaming');
 display=null;
 const b={goal:'点亮 LED',goal_confidence:1,steps:[{title:'插入 LED',check:'LED 在面包板上'},{title:'接电源',check:'LED 发光'}],steps_source:'parsed',
  requirements:[{item:'电阻',value:'220 Ω'}],first_guidance:{title:'准备 元件',instruction:'把元件放进画面',check:'元件入镜'},missing:[],safety:['接线前断电'],created_at:Date.now()/1000};
 receive({id:'brief-1',kind:'brief',session,version:1,started_at:Date.now()/1000,lanes:{brief:'running'},brief_partial:'{"goal":"点亮'});
 assert.equal($('brief-status').textContent,'Codex 正在读任务…');
 receive({id:'brief-1',kind:'brief',session,version:2,started_at:Date.now()/1000,lanes:{brief:'done'},brief:b});
 assert.equal($('hud-title').textContent,'准备 元件','first step shown before any photo');
 assert.equal($('brief-steps').children.length,2);
 assert.equal($('brief-requirements').children.length,1);
 assert.equal(display.source,'brief');
 assert.ok(isNewer({captured_at:0}),'a photo result always replaces text-only guidance');
 assert.equal(jobs.has('brief-1'),false,'brief jobs never count as frame work');
 auto=false;busy=false;
 `);
 // Startup failures must explain the disabled button and allow recovery without a reload.
 const healthy=responses['/api/health'];
 responses['/api/health']={...healthy,cli_available:false};
 await run('connectBackend()');
 run(`
 assert.equal($('auto').disabled,true);
 assert.ok($('capture-status').textContent.includes('Codex CLI'));
 assert.equal($('retry-backend').hidden,false);
 assert.equal($('retry-backend').disabled,false);
 `);
 responses['/api/health']={...healthy,capabilities:[]};
 await run('connectBackend()');
 assert.ok(run(`$('capture-status').textContent.includes('版本不兼容')`));
 const fetchOK=context.fetch;
 context.fetch=async()=>{throw Error('Failed to fetch');};
 await run('connectBackend()');
 assert.ok(run(`$('capture-status').textContent.includes('localhost:8765')`));
 context.fetch=fetchOK;responses['/api/health']=healthy;
 await run(`$('retry-backend').onclick()`);
 run(`
 stream=null;source.onopen();
 assert.equal($('auto').disabled,true,'camera is still required after backend recovery');
 assert.ok($('capture-status').textContent.includes('开启摄像头'));
 stream={};controls();
 assert.equal($('auto').disabled,false,'retry restores observation once camera and SSE are ready');
 assert.equal($('retry-backend').hidden,true);
 source.onerror();
 assert.equal($('auto').disabled,true);
 assert.equal($('retry-backend').hidden,false);
 source.onopen();
 assert.equal($('auto').disabled,false,'SSE reconnect restores button availability');
 `);
 // ---- 0.14 board reader: proposals are listed for the user to accept, never auto-confirmed ----
 responses['/api/breadboard/profiles']={profiles:{'terminal-63':{name:'63 列',columns:63,row_y:[0,1,2,3,4,7,8,9,10,11],rails:[]}},vision_available:true};
 context.window.receiveBoardRead=null;
 vm.runInContext(fs.readFileSync('web/board.js','utf8'),context);
 for(let i=0;i<5;i++)await new Promise(r=>setImmediate(r));
 run(`
 assert.equal(typeof window.receiveBoardRead,'function');
 receive({id:'board-1',kind:'board',session,status:'running',version:1,lanes:{brief:'running'},brief_partial:'{"parts":[',captured_at:Date.now()/1000,started_at:Date.now()/1000,events:[],metrics:{}});
 assert.ok(!$('board-status').textContent.includes('正在读端点'),'a job this page did not request is ignored');
 assert.equal($('board-proposals').children.length,0);
 `);
 console.log('Frontend: startup recovery, observation readiness, streaming JSON parser, HUD overview, animated cues, lane ordering, generic diagnostics, memory manager, questions/assumptions/feedback, frame replacement and board-reader routing passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
