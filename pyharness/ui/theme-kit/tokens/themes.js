export const THEMES = Object.freeze({
  light: {label:'原主题 · 浅色',background:'#f5f8fc',panel:'#ffffff',text:'#101d37',muted:'#59677d',line:'#dce5f0',accent:'#1672ff',danger:'#bd3448',warning:'#936200',success:'#087d49'},
  dark: {label:'原主题 · 深色',background:'#142034',panel:'#1e2b41',text:'#e1e9f6',muted:'#b1bed1',line:'#425570',accent:'#75b3ff',danger:'#ff9aa8',warning:'#f4cc79',success:'#6cdda8'},
  'tech-blue-particle': {label:'科技蓝 · 粒子',background:'#071322',panel:'#10263c',text:'#e7f3ff',muted:'#acc7de',line:'#305873',accent:'#66caff',danger:'#ff9aa8',warning:'#ffda82',success:'#75edbb'},
});
export const DEFAULT_CONFIG = Object.freeze({schemaVersion:1,themeId:'tech-blue-particle',animationMode:'normal',count:48,speed:0.35,links:true,opacity:0.55,pixelRatio:1.5});
export function normalizeConfig(value={}) {
  const v=value && typeof value==='object' && !Array.isArray(value)?value:{};
  const finite=(n,fallback,min,max)=>Number.isFinite(n)?Math.min(max,Math.max(min,n)):fallback;
  return {schemaVersion:1,themeId:Object.hasOwn(THEMES,v.themeId)?v.themeId:DEFAULT_CONFIG.themeId,
    animationMode:['normal','low','off'].includes(v.animationMode)?v.animationMode:'normal',
    count:Math.round(finite(v.count,48,0,80)),speed:finite(v.speed,.35,0,1),links:v.links!==false,
    opacity:finite(v.opacity,.55,0,1),pixelRatio:finite(v.pixelRatio,1.5,1,2)};
}
