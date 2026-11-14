const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync('web/index.html','utf8');
const ids=new Set([...html.matchAll(/id="([^"]+)"/g)].map(m=>m[1]));
const source=fs.readFileSync('web/app.js','utf8').split('// ---------------- zoomable annotated photo ----------------')[1].split('// ---------------- camera ----------------')[0];
function harness(){
 const nodes=new Map();let redraw=0;
 const $=id=>{assert(ids.has(id),`HTML contains ${id}`);if(!nodes.has(id))nodes.set(id,{
  style:{},clientWidth:800,clientHeight:600,naturalWidth:1920,naturalHeight:1080,events:{},
  addEventListener(type,fn){this.events[type]=fn;},setPointerCapture(){},focus(){},
  setAttribute(key,value){this[key]=value;},
  showModal(){this.open=true;},close(){this.open=false;this.events.close?.();},
  getBoundingClientRect(){return {left:0,top:0,width:this.clientWidth,height:this.clientHeight};}
 });return nodes.get(id);};
 const ctx=vm.createContext({$,Math,Map,ResizeObserver:class{observe(){}},notice(){},
  AXR:{draw(node,cues,w,h){node.cues=cues;node.dimensions=[w,h];redraw++;}},
  drawActionBoxes(node,targets){node.targets=targets;}});
 const run=s=>vm.runInContext(s,ctx);
 run(source);run('setupPhotoViewer()');
 const data={image:'high-resolution-frame',width:1920,height:1080,guidance:{title:'移动黄线',instruction:'先断电，把黄线插到短跳线旁。'},
  targets:[{role:'object',x:.1,y:.1,width:.02,height:.02},{role:'destination',x:.1,y:.2,width:.02,height:.02}],
  cues:[{type:'insert',x1:.11,y1:.11,x2:.11,y2:.21}]};
 ctx.data=data;run('openPhoto(data)');
 const event=(type,more={})=>$('photo-viewport').events[type]({preventDefault(){},pointerId:1,button:0,clientX:400,clientY:300,...more});
 return {$,run,event,data,redraw:()=>redraw};
}
test('opens the selected full-resolution photo with its own instruction, boxes and arrows',()=>{
 const h=harness();
 assert(h.$('photo-viewer').open);
 assert.equal(h.$('photo-image').src,h.data.image);
 assert.equal(h.$('photo-instruction').textContent,h.data.guidance.instruction);
 assert.equal(h.$('photo-boxes').targets,h.data.targets);
 assert.equal(h.$('photo-cues').cues,h.data.cues);
 assert.match(html,/<div id="photo-plane"><img id="photo-image"[^>]*><div id="photo-boxes"[^>]*><\/div><svg id="photo-cues"/);
});
test('zoom controls scale the shared plane, clamp bounds and reset pan',()=>{
 const h=harness();h.$('photo-plus').onclick();
 assert.equal(h.$('photo-scale').textContent,'125%');
 assert.match(h.$('photo-plane').style.transform,/scale\(1.25\)/);
 h.run('zoomPhoto(100)');assert.equal(h.$('photo-scale').textContent,'800%');
 h.run('zoomPhoto(.01)');assert.equal(h.$('photo-scale').textContent,'50%');
 h.$('photo-reset').onclick();assert.equal(h.$('photo-scale').textContent,'100%');
 assert.equal(h.run('photoX+photoY'),0);
});
test('wheel and drag move the photo and overlays together; pointer cancellation releases drag',()=>{
 const h=harness();h.event('wheel',{deltaY:-500});
 assert(h.run('photoScale')>1);
 h.event('pointerdown');h.event('pointermove',{clientX:470});
 assert.equal(h.run('photoX'),70);
 assert.match(h.$('photo-plane').style.transform,/translate\(70px, 0px\)/);
 h.event('pointercancel');h.event('pointermove',{clientX:600});
 assert.equal(h.run('photoX'),70);
});
test('two fingers pinch, closing and switching photos reset the view',()=>{
 const h=harness();h.event('pointerdown',{pointerId:1,clientX:300});
 h.event('pointerdown',{pointerId:2,clientX:500});
 h.event('pointermove',{pointerId:2,clientX:700});
 assert.equal(h.run('photoScale'),2);
 h.$('photo-close').onclick();assert.equal(h.run('photoPointers.size'),0);
 h.run("openPhoto({...data,image:'another-frame'})");
 assert.equal(h.$('photo-image').src,'another-frame');assert.equal(h.run('photoScale'),1);
});
