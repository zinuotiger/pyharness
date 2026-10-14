import {normalizeConfig,THEMES} from '../tokens/themes.js';
import {drawScene} from './scenes.js';
const INSTANCES=new WeakMap();
const MAX_PIXELS=2400000;
export function createParticles(container,options={}) {
 INSTANCES.get(container)?.destroy();
 const doc=container.ownerDocument,win=doc.defaultView;
 const constrained=(win.navigator?.hardwareConcurrency>0&&win.navigator.hardwareConcurrency<=4)||(win.navigator?.deviceMemory>0&&win.navigator.deviceMemory<=4);
 const targetFps=constrained?20:30,frameMs=1000/targetFps,pixelLimit=constrained?1200000:MAX_PIXELS;
 const canvas=doc.createElement('canvas');canvas.className='phtheme-canvas';canvas.setAttribute('aria-hidden','true');
 Object.assign(canvas.style,{pointerEvents:'none',position:'absolute',inset:'0',width:'100%',height:'100%'});container.append(canvas);
 let ctx;try{ctx=canvas.getContext('2d');}catch{canvas.remove();return null;}
 if(!ctx){canvas.remove();return null;}
 let config=normalizeConfig(options),raf=null,dead=false,last=0,time=0,width=1,height=1,frames=0;
 let points=[],frameIntervals=[];const reasons=new Set(['stopped']);
 function populate(){points=Array.from({length:Math.min(config.count,constrained?24:80)},(_,i)=>({x:((i+.5)*.61803398875)%1,y:((i+.5)*.754877666)%1,z:((i+.5)*.41421356)%1,phase:i*2.39996}));}
 function resize(){if(dead)return;const r=container.getBoundingClientRect();width=Math.max(1,Math.min(r.width,3840));height=Math.max(1,Math.min(r.height,2160));
  const ratio=Math.min(win.devicePixelRatio||1,config.pixelRatio,constrained?1:2,Math.sqrt(pixelLimit/(width*height)));
  try{canvas.width=Math.max(1,Math.floor(width*ratio));canvas.height=Math.max(1,Math.floor(height*ratio));ctx.setTransform(canvas.width/width,0,0,canvas.height/height,0,0);}catch{pause('render-failure');}}
 function clear(){if(dead)return;try{ctx.clearRect(0,0,width,height);}catch{pause('render-failure');}}
 function draw(now){raf=null;if(dead||reasons.size)return;
  if(!last||now-last>=frameMs-1){const dt=last?Math.min(80,now-last):frameMs;if(last){frameIntervals.push(now-last);if(frameIntervals.length>120)frameIntervals.shift();}last=now;time+=dt/1000;frames++;
   try{ctx.clearRect(0,0,width,height);drawScene(ctx,THEMES[config.themeId].scene,points,width,height,time,config);}
   catch{pause('render-failure');return;}}
  raf=win.requestAnimationFrame(draw);}
 function pause(reason='user'){reasons.add(reason);if(raf!==null)win.cancelAnimationFrame(raf);raf=null;last=0;}
 function resume(reason='user'){reasons.delete(reason);if(!dead&&!reasons.size&&raf===null)raf=win.requestAnimationFrame(draw);}
 // A lost graphics context must leave the offline backdrop and controls usable.
 // Do not recreate canvases or enter an automatic retry loop.
 function contextLost(event){event.preventDefault();pause('render-failure');}
 canvas.addEventListener('contextlost',contextLost);
 const observer=typeof win.ResizeObserver==='function'?new win.ResizeObserver(resize):null;observer?.observe(container);populate();resize();
 const api={resize,pause,resume,stop(){pause('stopped');clear();},
  updateConfig(value){if(dead)return;const old=config;config=normalizeConfig({...config,...value});if(old.count!==config.count)populate();if(old.pixelRatio!==config.pixelRatio)resize();if(old.themeId!==config.themeId){time=0;clear();}},
  stats(){return {frames,active:raf!==null,reasons:[...reasons],particles:points.length,canvasCount:dead?0:1,frameIntervals:[...frameIntervals],scene:THEMES[config.themeId].scene||null,pixelCount:canvas.width*canvas.height,pixelLimit,targetFps,deviceProfile:constrained?'constrained':'standard'};},
  destroy(){if(dead)return;pause('destroyed');dead=true;observer?.disconnect();canvas.removeEventListener('contextlost',contextLost);canvas.remove();points=[];if(INSTANCES.get(container)===api)INSTANCES.delete(container);}};
 INSTANCES.set(container,api);return api;
}
