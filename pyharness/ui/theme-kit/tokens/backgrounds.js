// References are design inputs, never runtime images. Enable an entry only
// after its independent, UI-free full image and thumbnail have been verified.
export const PARTICLE_BACKGROUNDS = Object.freeze([
  ['particle-nebula-light','星云粒子','01-nebula-reference.png'],
  ['particle-ice-aurora-light','冰湖极光粒子','02-ice-aurora-reference.png'],
  ['particle-ocean-blue-light','海蓝粒子','03-ocean-blue-reference.png'],
  ['particle-starry-sky-light','星空粒子','04-starry-sky-reference.png'],
  ['particle-aurora-light','极光粒子','05-aurora-reference.png'],
  ['particle-ocean-light','海洋粒子','06-ocean-reference.png'],
].map(([id,label,reference])=>Object.freeze({id,label,reference,group:'particle',ready:true,
  image:new URL('../scenes/'+id+'.png',import.meta.url).href,
  thumbnail:new URL('../scenes/thumbnails/'+id+'.png',import.meta.url).href,
  position:'center'})));

export function particleBackground(id){return PARTICLE_BACKGROUNDS.find(item=>item.id===id&&item.ready);}

export function originalBackgrounds(themes){return Object.entries(themes).map(([id,theme])=>({
  id,label:theme.label,group:'original',ready:true,
  thumbnail:new URL('../scenes/thumbnails/'+(theme.scene||id)+'.png',import.meta.url).href,
  config:{themeId:id,backgroundId:null},
}));}
