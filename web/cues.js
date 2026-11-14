"use strict";
// Animated guidance cues (SVG) and a tolerant parser for streaming JSON.
const AXR=(()=>{
 const NS='http://www.w3.org/2000/svg';
 let uid=0;
 function el(name,attrs={},parent){const node=document.createElementNS(NS,name);for(const [k,v] of Object.entries(attrs))node.setAttribute(k,String(v));if(parent)parent.append(node);return node;}

 // ---------- streaming JSON ----------
 function scan(s){
  const stack=[],cuts=[];let inStr=false,esc=false;
  for(let i=0;i<s.length;i++){
   const c=s[i];
   if(inStr){if(esc)esc=false;else if(c==='\\')esc=true;else if(c==='"')inStr=false;continue;}
   if(c==='"')inStr=true;
   else if(c==='{'||c==='['){stack.push(c);cuts.push(i+1);}
   else if(c==='}'||c===']')stack.pop();
   else if(c===',')cuts.push(i);
  }
  return {stack,inStr,esc,cuts};
 }
 function close(s){
  const r=scan(s);let t=s;
  if(r.inStr){if(r.esc)t=t.slice(0,-1);t+='"';}
  t=t.replace(/[\s,:]+$/,'');
  return t+scan(t).stack.reverse().map(c=>c==='{'?'}':']').join('');
 }
 function parsePartial(text){
  let s=String(text||'').trimStart();
  for(let k=0;k<60&&s;k++){
   try{return JSON.parse(close(s));}catch{}
   const cut=scan(s).cuts.filter(p=>p<s.length).pop();
   if(cut===undefined)return null;
   s=s.slice(0,cut);
  }
  return null;
 }

 // ---------- tool icons (drawn around 0,0, ~28px) ----------
 const TOOLS={
  hand:g=>{el('path',{d:'M-8 10 L-8 -2 Q-8 -6 -5 -6 L-5 -12 Q-5 -15 -2 -15 Q1 -15 1 -12 L1 -6 L4 -10 Q6 -13 8 -11 Q10 -9 8 -6 L4 2 L4 10 Z',class:'tool-fill'},g);},
  finger:g=>{el('rect',{x:-4,y:-14,width:8,height:22,rx:4,class:'tool-fill'},g);},
  screwdriver:g=>{el('rect',{x:-4,y:2,width:8,height:12,rx:2,class:'tool-fill'},g);el('line',{x1:0,y1:2,x2:0,y2:-14,class:'tool-line'},g);},
  wrench:g=>{el('path',{d:'M-2 14 L-2 -6 A6 6 0 1 1 2 -6 L2 14 Z',class:'tool-fill'},g);},
  pliers:g=>{el('path',{d:'M-2 -14 L-6 4 L-8 14 M2 -14 L6 4 L8 14',class:'tool-line'},g);el('circle',{cx:0,cy:-2,r:2,class:'tool-fill'},g);},
  scissors:g=>{el('circle',{cx:-5,cy:8,r:4,class:'tool-line'},g);el('circle',{cx:5,cy:8,r:4,class:'tool-line'},g);el('path',{d:'M-3 5 L6 -14 M3 5 L-6 -14',class:'tool-line'},g);},
  knife:g=>{el('path',{d:'M-3 14 L-3 2 L3 2 L3 14 Z',class:'tool-fill'},g);el('path',{d:'M-3 2 L-3 -14 Q4 -8 3 2 Z',class:'tool-fill ghost-blade'},g);},
  wire:g=>{el('path',{d:'M0 14 C 0 4, -8 0, 0 -6 L0 -14',class:'tool-line'},g);el('rect',{x:-2,y:-16,width:4,height:5,class:'tool-fill'},g);},
  probe:g=>{el('rect',{x:-3,y:-2,width:6,height:14,rx:2,class:'tool-fill'},g);el('line',{x1:0,y1:-2,x2:0,y2:-15,class:'tool-line'},g);},
  pen:g=>{el('path',{d:'M-3 12 L-3 -8 L0 -14 L3 -8 L3 12 Z',class:'tool-fill'},g);},
  spoon:g=>{el('ellipse',{cx:0,cy:-8,rx:5,ry:7,class:'tool-fill'},g);el('line',{x1:0,y1:-1,x2:0,y2:14,class:'tool-line'},g);},
  cloth:g=>{el('path',{d:'M-10 -8 Q0 -14 10 -8 L8 10 Q0 6 -8 10 Z',class:'tool-fill'},g);},
  other:g=>{el('circle',{cx:0,cy:0,r:8,class:'tool-fill'},g);},
 };
 function tool(name,parent,scale=1){const g=el('g',{class:'cue-tool'},parent);const icon=el('g',{transform:`scale(${.55*scale})`},g);(TOOLS[name]||TOOLS.hand)(icon);return g;}

 const TURN_DEG={quarter:90,half:180,full:360,none:270};
 const TURN_TEXT={quarter:'1/4 圈',half:'半圈',full:'一圈',none:''};

 function label(svg,text,x,y,W,H){
  if(!text)return;
  const tx=Math.min(Math.max(x,6),W-6),ty=Math.min(Math.max(y,16),H-6);
  el('text',{x:tx,y:ty,class:'cue-label','text-anchor':tx>W*.75?'end':'start'},svg).textContent=text;
 }

 function draw(svg,cues,width,height,zoom=1){
  const size=1/Math.max(.5,zoom),px=v=>v*size;
  svg.replaceChildren();
  const W=Math.max(1,Math.round(width)),H=Math.max(1,Math.round(height));
  svg.setAttribute('viewBox',`0 0 ${W} ${H}`);svg.setAttribute('width',W);svg.setAttribute('height',H);
  const id='ah'+(++uid);
  const defs=el('defs',{},svg);
  const marker=el('marker',{id,viewBox:'0 0 10 10',refX:10,refY:5,markerUnits:'userSpaceOnUse',markerWidth:px(6),markerHeight:px(6),orient:'auto-start-reverse'},defs);
  el('path',{d:'M0 0 L10 5 L0 10 z',class:'cue-head'},marker);
  for(const cue of cues||[]){
   const x1=cue.x1*W,y1=cue.y1*H,x2=cue.x2*W,y2=cue.y2*H;
   const g=el('g',{class:`cue cue-${cue.type}`,style:`--cue-scale:${size}`},svg);
   const d=`M${x1.toFixed(1)} ${y1.toFixed(1)} L${x2.toFixed(1)} ${y2.toFixed(1)}`;
   if(cue.type==='arrow'||cue.type==='insert'||cue.type==='pull'){
    el('path',{d,class:'cue-shadow'},g);
    el('path',{d,class:'cue-flow','marker-end':`url(#${id})`},g);
    if(cue.type==='insert')el('circle',{cx:x2,cy:y2,r:px(4),class:'cue-ring'},g);
    if(cue.tool&&cue.tool!=='none'){const t=tool(cue.tool,g,size);t.setAttribute('class','cue-tool ghost');el('animateMotion',{dur:'1.8s',repeatCount:'indefinite',path:d},t);}
    label(g,cue.label||({insert:'插入',pull:'拔出'})[cue.type]||'',(x1+x2)/2+px(6),(y1+y2)/2-px(7),W,H);
   }else if(cue.type==='rotate'){
    const r=Math.max(px(6),cue.radius*Math.min(W,H)),deg=TURN_DEG[cue.turns]||270,sweep=Math.min(deg,330)*Math.PI/180,cw=cue.direction!=='ccw';
    const sx=x1+r,sy=y1,ex=x1+r*Math.cos(cw?sweep:-sweep),ey=y1+r*Math.sin(cw?sweep:-sweep);
    const arc=`M${sx.toFixed(1)} ${sy.toFixed(1)} A${r.toFixed(1)} ${r.toFixed(1)} 0 ${sweep>Math.PI?1:0} ${cw?1:0} ${ex.toFixed(1)} ${ey.toFixed(1)}`;
    const spin=el('g',{},g);
    el('path',{d:arc,class:'cue-shadow'},spin);el('path',{d:arc,class:'cue-flow','marker-end':`url(#${id})`},spin);
    el('animateTransform',{attributeName:'transform',type:'rotate',from:`0 ${x1} ${y1}`,to:`${cw?360:-360} ${x1} ${y1}`,dur:'3s',repeatCount:'indefinite'},spin);
    el('circle',{cx:x1,cy:y1,r:px(2),class:'cue-head'},g);
    label(g,(cue.label||'旋转')+` · ${cw?'顺时针':'逆时针'}${TURN_TEXT[cue.turns]?' '+TURN_TEXT[cue.turns]:''}`,x1+r+px(6),y1-r,W,H);
   }else if(cue.type==='press'){
    el('circle',{cx:x1,cy:y1,r:px(2.5),class:'cue-head'},g);
    const ring=el('circle',{cx:x1,cy:y1,r:px(4),class:'cue-ring'},g);
    el('animate',{attributeName:'r',values:`${px(4)};${px(9)};${px(4)}`,dur:'1.4s',repeatCount:'indefinite'},ring);
    el('animate',{attributeName:'opacity',values:'1;0;1',dur:'1.4s',repeatCount:'indefinite'},ring);
    const down=`M${x1} ${y1-px(22)} L${x1} ${y1-px(7)}`;el('path',{d:down,class:'cue-flow','marker-end':`url(#${id})`},g);
    label(g,cue.label||'按压',x1+px(8),y1-px(12),W,H);
   }else if(cue.type==='cut'){
    el('path',{d,class:'cue-cut'},g);
    const t=tool(cue.tool&&cue.tool!=='none'?cue.tool:'scissors',g,size);t.setAttribute('class','cue-tool ghost');
    el('animateMotion',{dur:'2s',repeatCount:'indefinite',path:d,rotate:'auto'},t);
    label(g,cue.label||'沿虚线切',(x1+x2)/2+px(6),(y1+y2)/2-px(7),W,H);
   }else if(cue.type==='tool_ghost'){
    el('path',{d,class:'cue-trail'},g);
    const start=tool(cue.tool,g,size);start.setAttribute('transform',`translate(${x1} ${y1})`);start.setAttribute('class','cue-tool faint');
    const moving=tool(cue.tool,g,size);moving.setAttribute('class','cue-tool ghost');
    el('animateMotion',{dur:'2.2s',repeatCount:'indefinite',path:d},moving);
    el('circle',{cx:x2,cy:y2,r:px(4),class:'cue-ring'},g);
    label(g,cue.label||'',x2+px(8),y2-px(7),W,H);
   }
  }
 }
 return {parsePartial,draw,tools:Object.keys(TOOLS)};
})();
