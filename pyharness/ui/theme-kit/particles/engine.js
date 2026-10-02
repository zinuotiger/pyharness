import {normalizeConfig} from '../tokens/themes.js';
const INSTANCES=new WeakMap();
export function createParticles(container,options={}) {
  INSTANCES.get(container)?.destroy();
  const doc=container.ownerDocument, win=doc.defaultView;
  const canvas=doc.createElement('canvas');canvas.className='phtheme-canvas';canvas.setAttribute('aria-hidden','true');
  Object.assign(canvas.style,{pointerEvents:'none',position:'absolute',inset:'0',width:'100%',height:'100%'});
  container.append(canvas);const ctx=canvas.getContext('2d');
  let config=normalizeConfig(options),raf=null,dead=false,last=0,width=1,height=1,frames=0;
  let points=[];const reasons=new Set(['stopped']);let frameIntervals=[];
  function resize(){if(dead)return;const r=container.getBoundingClientRect();width=Math.max(1,Math.min(r.width,3840));height=Math.max(1,Math.min(r.height,2160));
    const ratio=Math.min(win.devicePixelRatio||1,config.pixelRatio);canvas.width=Math.round(width*ratio);canvas.height=Math.round(height*ratio);ctx?.setTransform(ratio,0,0,ratio,0,0);
    points=Array.from({length:config.count},()=>({x:Math.random()*width,y:Math.random()*height,vx:Math.random()-.5,vy:Math.random()-.5}));}
  function draw(now){raf=null;if(dead||reasons.size||!ctx)return;
    const dt=last?Math.min(50,now-last):16;if(last){frameIntervals.push(now-last);if(frameIntervals.length>120)frameIntervals.shift();}last=now;frames++;
    ctx.clearRect(0,0,width,height);ctx.fillStyle='#71cfff';ctx.strokeStyle='#71cfff';
    for(const p of points){p.x=(p.x+p.vx*config.speed*dt+width)%width;p.y=(p.y+p.vy*config.speed*dt+height)%height;
      ctx.globalAlpha=config.opacity;ctx.beginPath();ctx.arc(p.x,p.y,1.5,0,Math.PI*2);ctx.fill();}
    if(config.links)for(let i=0;i<points.length;i++)for(let j=i+1;j<points.length;j++){
      const a=points[i],b=points[j],distance=Math.hypot(a.x-b.x,a.y-b.y);if(distance<100){ctx.globalAlpha=config.opacity*.28*(1-distance/100);ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);ctx.stroke();}}
    raf=win.requestAnimationFrame(draw);
  }
  function pause(reason='user'){reasons.add(reason);if(raf!==null)win.cancelAnimationFrame(raf);raf=null;last=0;}
  function resume(reason='user'){reasons.delete(reason);if(!dead&&!reasons.size&&raf===null&&ctx){last=0;raf=win.requestAnimationFrame(draw);}}
  const observer=typeof win.ResizeObserver==='function'?new win.ResizeObserver(resize):null;observer?.observe(container);resize();
  const api={resize,pause,resume,stop(){pause('stopped');ctx?.clearRect(0,0,width,height);},
    updateConfig(value){if(dead)return;config=normalizeConfig({...config,...value});resize();},
    stats(){return {frames,active:raf!==null,reasons:[...reasons],particles:points.length,canvasCount:dead?0:1,frameIntervals:[...frameIntervals]};},
    destroy(){if(dead)return;pause('destroyed');dead=true;observer?.disconnect();canvas.remove();points=[];INSTANCES.delete(container);}};
  INSTANCES.set(container,api);return api;
}
