// Bounded procedural foregrounds over offline artwork; no application APIs.
const TAU=Math.PI*2;
const wrap=(v,max)=>((v%max)+max)%max;
function dot(c,x,y,r,color,alpha){c.globalAlpha=alpha;c.fillStyle=color;c.beginPath();c.arc(x,y,r,0,TAU);c.fill();}
export function drawScene(c,scene,points,w,h,time,config){
 const t=time*(.25+config.speed*1.8),opacity=config.opacity;
 c.save();c.globalCompositeOperation='source-over';
 if(scene==='starfield'){
  for(const p of points){
   const x=wrap(p.x*w+t*(2+p.z*5),w),y=p.y*h;
   dot(c,x,y,.6+p.z*1.8,'#cfe1ff',opacity*(.3+.7*(.5+.5*Math.sin(t*.8+p.phase))));
   if(p.z>.88){c.strokeStyle='#b8cdff';c.lineWidth=.6;c.beginPath();c.moveTo(x-5,y);c.lineTo(x+5,y);c.moveTo(x,y-5);c.lineTo(x,y+5);c.stroke();}
   const a=p.phase+t*.035,r=(.05+p.z*.22)*w;
   dot(c,w*.79+Math.cos(a)*r,h*.26+Math.sin(a)*r*.45,.7+p.z,'#a9aeff',opacity*.45);
  }
 }else if(scene==='sakura'){
  for(const p of points){
   const x=wrap(p.x*w-t*(8+p.z*14)+Math.sin(t*.45+p.phase)*28,w+40)-20;
   const y=wrap(p.y*h+t*(12+p.z*24),h+50)-25,s=5+p.z*10;
   c.save();c.translate(x,y);c.rotate(p.phase+t*(.2+p.z*.3));c.scale(.45+Math.abs(Math.sin(t*.6+p.phase))*.55,1);
   c.globalAlpha=opacity*(.55+p.z*.45);c.fillStyle=p.z>.5?'#ffd5e4':'#ef9abc';
   c.beginPath();c.moveTo(0,-s);c.bezierCurveTo(s*1.15,-s*.6,s*.75,s*.55,0,s);c.bezierCurveTo(-s*.8,s*.3,-s*.9,-s*.6,0,-s);c.fill();c.restore();
  }
 }else if(scene==='aurora'){
  for(let band=0;band<3;band++){
   const g=c.createLinearGradient(0,0,0,h*.7);g.addColorStop(0,'#67ffc900');g.addColorStop(.35,band===1?'#8c72ef':'#4eedc0');g.addColorStop(1,'#39baeb00');
   c.fillStyle=g;c.globalAlpha=opacity*.19;c.beginPath();
   for(let i=0;i<=60;i++){const x=w*i/60,y=h*(.22+band*.07+Math.sin(i*.07+t*.25+band)*.16+Math.sin(i*.19-t*.16)*.04);if(!i)c.moveTo(x,y);else c.lineTo(x,y);}
   for(let i=60;i>=0;i--){const x=w*i/60,y=h*(.52+band*.02+Math.sin(i*.07+t*.25+band)*.13);c.lineTo(x,y);}c.closePath();c.fill();
  }
  for(const p of points)dot(c,p.x*w,p.y*h*.7,.5+p.z,'#cafff2',opacity*(.2+.3*Math.sin(t*.7+p.phase)**2));
 }else if(scene==='gold'){
  for(let band=0;band<3;band++){
   c.beginPath();c.strokeStyle='#e8ae50';c.lineWidth=.7;c.globalAlpha=opacity*.3;
   for(let i=0;i<=80;i++){const x=w*i/80,y=h*(.64+band*.06+Math.sin(i*.06+t*.4+band*.5)*.2);if(!i)c.moveTo(x,y);else c.lineTo(x,y);}c.stroke();
   for(const p of points){const x=wrap(p.x*w+t*(8+band*5),w),y=h*(.64+band*.06+Math.sin(x/w*4.8+t*.4+band*.5)*.2)+(p.z-.5)*44;dot(c,x,y,.7+p.z*1.9,'#ffda88',opacity*(.45+.55*Math.sin(p.phase+t)**2));}
  }
 }else if(scene==='ocean'){
  for(const p of points){const x=wrap(p.x*w+Math.sin(t*.4+p.phase)*18,w),y=wrap(p.y*h-t*(10+p.z*22),h);c.globalAlpha=opacity*(.2+p.z*.4);c.strokeStyle='#b9f3ff';c.lineWidth=.7;c.beginPath();c.arc(x,y,1+p.z*5,0,TAU);c.stroke();if(p.z>.8)dot(c,x-1,y-2,1,'#e4ffff',opacity*.4);}
  for(let j=0;j<3;j++){
   const x=w*[.10,.89,.76][j]+Math.sin(t*.17+j)*18,y=h*[.62,.43,.78][j]+Math.sin(t*.3+j)*22,r=[30,42,22][j],pulse=1+Math.sin(t*1.4+j)*.12;
   c.save();c.translate(x,y);c.scale(pulse,1/pulse);const g=c.createRadialGradient(0,-r*.3,1,0,0,r*1.5);g.addColorStop(0,'#b6f1ff');g.addColorStop(1,'#2da9f500');
   c.globalAlpha=opacity*.65;c.fillStyle=g;c.strokeStyle='#8edcff';c.lineWidth=.8;c.beginPath();c.ellipse(0,0,r,r*.7,Math.PI,0,Math.PI);c.quadraticCurveTo(0,r*.32,-r,0);c.fill();c.stroke();
   for(let k=0;k<7;k++){const dx=(k-3)*r*.23;c.beginPath();c.moveTo(dx,0);c.bezierCurveTo(dx+Math.sin(t+k)*15,r*.7,dx-12,r*1.3,dx+Math.sin(t*.8+k)*12,r*2.2);c.stroke();}c.restore();
  }
 }else{
  const r=Math.min(w*.29,h*.44),cx=w*.83,cy=h*.48;
  const nodes=points.map((p,i)=>{const lat=Math.acos(1-2*(i+.5)/Math.max(1,points.length)),a=i*2.39996+t*.08;return {x:cx+r*Math.sin(lat)*Math.cos(a),y:cy+r*Math.cos(lat),z:Math.sin(lat)*Math.sin(a)};});
  for(let i=0;i<nodes.length;i++){
   const a=nodes[i];dot(c,a.x,a.y,1.2+(a.z+1)*.9,'#75dfff',opacity*(.25+(a.z+1)*.35));
   if(config.links){let links=0;for(let j=i+1;j<nodes.length&&links<4;j++){const b=nodes[j],d=Math.hypot(a.x-b.x,a.y-b.y);if(d<r*.48){c.globalAlpha=opacity*.22*(1-d/(r*.48));c.strokeStyle='#52c5ff';c.lineWidth=.8;c.beginPath();c.moveTo(a.x,a.y);c.lineTo(b.x,b.y);c.stroke();links++;}}}
  }
 }
 c.restore();
}
