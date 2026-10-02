import {createAppearance} from '../theme-kit/appearance/controller.js';
import {preferenceStore} from '../theme-kit/contracts/preferences.js';
let particleFactory;try{particleFactory=(await import('../theme-kit/particles/engine.js')).createParticles;}catch{/* Optional effect, not the application. */}
let storage;try{storage=window.localStorage;}catch{storage=null;}
const store=preferenceStore(storage);
const controller=await createAppearance(document.body,{store,particleFactory});
window.pyharnessAppearance=controller;
function controls(){for(const el of document.querySelectorAll('[data-appearance-field]'))el.value=controller.config[el.dataset.appearanceField];}
function changed(event){const field=event.target.dataset?.appearanceField;if(['themeId','animationMode'].includes(field))controller.updateConfig({[field]:event.target.value});}
function clicked(event){if(event.target.closest('[data-appearance-reset]'))controller.reset();}
document.addEventListener('change',changed);document.addEventListener('click',clicked);document.body.addEventListener('appearance-change',controls);
const observer=new MutationObserver(controls);observer.observe(document.querySelector('#main')||document.body,{childList:true,subtree:true});controls();
function hostEvent(event){controller.setHostHidden(event.detail?.hidden===true);}
window.addEventListener('pyharness-host-visibility',hostEvent);
function close(){observer.disconnect();document.removeEventListener('change',changed);document.removeEventListener('click',clicked);document.body.removeEventListener('appearance-change',controls);window.removeEventListener('pyharness-host-visibility',hostEvent);controller.destroy();}
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
