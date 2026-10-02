import {normalizeConfig} from '../tokens/themes.js';
const INSTANCES=new WeakMap();
export async function createAppearance(root,{store,particleFactory}={}) {
  const doc=root.ownerDocument,win=doc.defaultView;
  let initial;try{initial=await store?.load();}catch{/* Preferences never block the host. */}
  INSTANCES.get(root)?.destroy();
  let config=normalizeConfig(initial),dead=false,engine=null,hostHidden=false,stopped=false;
  const layer=doc.createElement('div');layer.className='phtheme-layer';layer.setAttribute('aria-hidden','true');root.prepend(layer);
  try{engine=particleFactory?.(layer,config);}catch{/* Static theme remains usable. */}
  const reduced=win.matchMedia?.('(prefers-reduced-motion: reduce)');
  function sync(){if(dead)return;
    root.dataset.pyhTheme=config.themeId;root.classList.toggle('dark',config.themeId==='dark');
    layer.hidden=config.themeId!=='tech-blue-particle';
    const pauses={theme:config.themeId!=='tech-blue-particle',user:config.animationMode==='off',low:config.animationMode==='low',reduced:!!reduced?.matches,hidden:doc.hidden,host:hostHidden,stopped};
    engine?.updateConfig(config);
    for(const [reason,paused] of Object.entries(pauses))paused?engine?.pause(reason):engine?.resume(reason);
    root.dispatchEvent(new win.CustomEvent('appearance-change',{detail:{...config}}));
  }
  function visibility(){sync();}doc.addEventListener('visibilitychange',visibility);reduced?.addEventListener('change',visibility);
  const api={get config(){return {...config};},async updateConfig(value){if(dead)return;config=normalizeConfig({...config,...value});sync();try{return (await store?.save(config))!==false;}catch{return false;}},
    resize(){engine?.resize();},pause(reason='host'){engine?.pause('external:'+reason);},resume(reason='host'){if(dead)return;if(reason==='stopped'){stopped=false;sync();}else{engine?.resize();engine?.resume('external:'+reason);}},
    setHostHidden(value){hostHidden=!!value;sync();},stop(){stopped=true;engine?.stop();},
    async reset(){try{await store?.reset();}catch{}config=normalizeConfig({themeId:'light'});sync();try{await store?.save(config);}catch{}},
    stats(){return engine?.stats()||{active:false,canvasCount:0,reasons:['static-fallback']};},
    destroy(){if(dead)return;dead=true;engine?.destroy();layer.remove();doc.removeEventListener('visibilitychange',visibility);reduced?.removeEventListener('change',visibility);INSTANCES.delete(root);}};
  INSTANCES.set(root,api);sync();return api;
}
