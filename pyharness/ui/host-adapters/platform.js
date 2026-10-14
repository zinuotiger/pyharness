import {createAppearance} from '../theme-kit/appearance/controller.js';
import {preferenceStore} from '../theme-kit/contracts/preferences.js';
import {THEMES,SCENE_IDS} from '../theme-kit/tokens/themes.js';
import {PARTICLE_BACKGROUNDS,particleBackground} from '../theme-kit/tokens/backgrounds.js';
import {createBackgroundLibrary} from '../theme-kit/appearance/background-library.js';
let particleFactory;try{particleFactory=(await import('../theme-kit/particles/engine.js')).createParticles;}catch{/* Optional effect, not the application. */}
let storage;try{storage=window.localStorage;}catch{storage=null;}
const store=preferenceStore(storage);
const controller=await createAppearance(document.body,{store,particleFactory});
window.pyharnessAppearance=controller;
const library=createBackgroundLibrary(controller);
function controls(){
  for(const select of document.querySelectorAll('[data-appearance-field="themeId"]')){
    if(!select.dataset.scenesReady){
      const options=[...['light','dark',...SCENE_IDS].map(id=>({id,label:THEMES[id].label})),
        ...PARTICLE_BACKGROUNDS.filter(item=>item.ready).map(item=>({id:item.id,label:item.label+' · 浅色背景'}))];
      select.replaceChildren(...options.map(({id,label})=>{const o=document.createElement('option');o.value=id;o.textContent=label;return o;}));select.dataset.scenesReady='true';}
    select.disabled=false;
    const panel=select.closest('.phtheme-controls');
    if(panel&&!panel.querySelector('.phtheme-gallery')){const gallery=document.createElement('div');gallery.className='phtheme-gallery';gallery.setAttribute('aria-label','场景皮肤');
      for(const id of SCENE_IDS){const t=THEMES[id],button=document.createElement('button');button.type='button';button.dataset.sceneChoice=id;button.className='phtheme-scene-choice';
        const img=document.createElement('img');img.src=new URL('../theme-kit/scenes/thumbnails/'+t.scene+'.png',import.meta.url).href;img.alt='';img.loading='lazy';
        const label=document.createElement('strong');label.textContent=t.label;const detail=document.createElement('small');detail.textContent=t.description;button.append(img,label,detail);gallery.append(button);}
      panel.append(gallery);}
    if(panel&&!panel.querySelector('[data-more-backgrounds]')){const more=document.createElement('button');more.type='button';more.dataset.moreBackgrounds='';more.textContent='更多背景';more.setAttribute('aria-haspopup','dialog');panel.append(more);}
  }
  const config=controller.config;
  for(const el of document.querySelectorAll('[data-appearance-field]'))el.value=el.dataset.appearanceField==='themeId'?(config.backgroundId||config.themeId):config[el.dataset.appearanceField];
  for(const el of document.querySelectorAll('[data-scene-choice]'))el.setAttribute('aria-pressed',String(!controller.config.backgroundId&&el.dataset.sceneChoice===controller.config.themeId));
}
function changed(event){const field=event.target.dataset?.appearanceField,value=event.target.value;
  if(field==='themeId')controller.updateConfig(particleBackground(value)?{backgroundId:value}:{themeId:value,backgroundId:null});
  else if(field==='animationMode')controller.updateConfig({animationMode:value});}
function clicked(event){const more=event.target.closest('[data-more-backgrounds]');if(more)return library.open(more);
  const choice=event.target.closest('[data-scene-choice]');if(choice)controller.updateConfig({themeId:choice.dataset.sceneChoice,backgroundId:null});else if(event.target.closest('[data-appearance-reset]'))controller.reset();}
document.addEventListener('change',changed);document.addEventListener('click',clicked);document.body.addEventListener('appearance-change',controls);
const observer=new MutationObserver(controls);observer.observe(document.querySelector('#main')||document.body,{childList:true,subtree:true});controls();
function hostEvent(event){controller.setHostHidden(event.detail?.hidden===true);}
window.addEventListener('pyharness-host-visibility',hostEvent);
function close(){observer.disconnect();library.destroy();document.removeEventListener('change',changed);document.removeEventListener('click',clicked);document.body.removeEventListener('appearance-change',controls);window.removeEventListener('pyharness-host-visibility',hostEvent);controller.destroy();}
window.addEventListener('pagehide',event=>{if(event.persisted)controller.pause('page-cache');else close();});
window.addEventListener('pageshow',()=>controller.resume('page-cache'));
async function hostPreferences(){
  const bridge=window.pywebview?.api;if(!bridge?.appearance_load)return;
  try{const preferences=await bridge.appearance_load();const hostStore={load:async()=>preferences,save:async value=>bridge.appearance_save(value),reset:async()=>bridge.appearance_reset()};
    // Keep the single controller; install the non-sensitive host preference store.
    const saved=preferences&&Object.keys(preferences).length?preferences:controller.config;
    store.save=hostStore.save;store.reset=hostStore.reset;await controller.updateConfig(saved);
  }catch{/* Browser store remains usable if a host bridge is unavailable. */}
}
window.addEventListener('pywebviewready',hostPreferences);
if(window.pywebview?.api)hostPreferences();
