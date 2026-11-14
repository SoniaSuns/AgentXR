const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync('web/index.html','utf8'),ids=new Set([...html.matchAll(/id="([^"]+)"/g)].map(m=>m[1]));
class El{
 constructor(){this.value='';this.children=[];this.checked=false;this.disabled=false;this.hidden=false;this.style={};this.classList={toggle(){}};}
 append(o){this.children.push(o);if(!this.value)this.value=o.value;}
 replaceChildren(){this.children=[];this.value='';}
 getBoundingClientRect(){return {left:0,top:0,width:1000,height:500};}
 click(){}
 setAttribute(k,v){this[k]=v;}
}
const els=new Map([...ids].map(id=>[id,new El()]));
const spec={columns:30,row_y:[0,1,2,3,4,7,8,9,10,11],rails:[],name:'test'};
const requests=[],frames=new Map();let stopCount=0;
const context=vm.createContext({document:{getElementById:id=>{assert(ids.has(id),`missing HTML id ${id}`);return els.get(id)},createElement:()=>new El(),createElementNS:()=>new El()},
 window:{},console,JSON,Math,Array,Object,crypto:{randomUUID:()=> 'board-frame-1'},Blob,URL:{createObjectURL:()=> 'blob:test',revokeObjectURL(){}},setTimeout(){},
 stream:{},ready:true,connected:true,session:'session-a',frames,stopAuto(){stopCount++},currentTask:()=>({goal:'LED',steps:[],plan:'',domain:'electronics'}),
 captureFrame:async()=>({image:'low',hires:'high',memory_image:'memory',captured_at:1234,width:768,height:432}),
 api:async(path,body)=>{
  requests.push({path,body});
  if(path.endsWith('/profiles'))return {profiles:{test:spec},vision_available:true};
  if(path.endsWith('/rectify'))return {ok:true,reasons:[],image:'warp',frame_digest:'digest',width:1000,height:500,min_pitch_px:28,hole_support:.95,
   grid:[{hole:'a1',xy:[.1,.1],pitch_px:28},{hole:'a2',xy:[.128,.1],pitch_px:28}],candidates:[],
   connections:{checks:[],endpoints:Object.fromEntries(body.board.endpoints.map(e=>[e.id,{hole:'a1'}]))}};
  return {id:'job'};
 }});
const flush=()=>new Promise(r=>setImmediate(r));
(async()=>{
 els.get('board-mode').value='manual';els.get('board-layout').checked=true;
 vm.runInContext(fs.readFileSync('web/board.js','utf8'),context);await flush();
 await els.get('board-freeze').onclick();
 assert.equal(els.get('board-photo').src,'high','calibration uses full resolution');
 assert.equal(stopCount,1,'streaming stops for frozen-frame calibration');
 for(const [x,y] of [[100,50],[900,50],[900,450],[100,450],[100,200],[900,300]])await els.get('board-photo').onclick({clientX:x,clientY:y});
 const sent=requests.filter(r=>r.path.endsWith('/rectify')).at(-1);
 assert.equal(JSON.stringify(sent.body.board.anchors.map(a=>a.hole)),JSON.stringify(['a1','a30','j30','j1','e1','f30']));
 assert.equal(els.get('board-submit').disabled,false);
 els.get('board-pin-id').value='W1.1';await els.get('board-photo').onclick({clientX:100,clientY:50});
 assert.equal(requests.at(-1).body.board.endpoints[0].confirmed,true);
 await els.get('board-submit').onclick();
 const analysis=requests.find(r=>r.path==='/api/analyze');
 assert.equal(analysis.body.image,'high');assert.equal(analysis.body.captured_at,1234);
 assert.equal(analysis.body.board.frame_digest,'digest');assert.equal(analysis.body.board.endpoints[0].id,'W1.1');
 context.session='session-b';await els.get('board-submit').onclick();
 assert.equal(requests.filter(r=>r.path==='/api/analyze').length,1,'old session evidence cannot be submitted');
 context.window.resetBoard();assert.equal(els.get('board-submit').disabled,true);
 assert.equal(els.get('board-photo').hidden,true,'reset removes old frame');
 console.log('Board frontend: HTML bindings, six anchors, high-resolution freeze, explicit endpoint confirmation, same-frame submission, session reset passed.');
})().catch(e=>{console.error(e);process.exitCode=1});
