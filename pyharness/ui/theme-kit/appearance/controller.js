import {normalizeConfig,THEMES} from '../tokens/themes.js';
import {particleBackground} from '../tokens/backgrounds.js';
const INSTANCES=new WeakMap();
export async function createAppearance(root,{store,particleFactory}={}) {
  const doc=root.ownerDocument,win=doc.defaultView;
  let initial;try{initial=await store?.load();}catch{/* Preferences never block the host. */}
  INSTANCES.get(root)?.destroy();
  let config=normalizeConfig(initial),committed={...config},dead=false,engine=null,hostHidden=false,stopped=false;
  let request=0,saveQueue=Promise.resolve(),imageSource=null;
  const loaded=new Set();
  const layer=doc.createElement('div');layer.className='phtheme-layer';layer.setAttribute('aria-hidden','true');root.prepend(layer);
  const backdrop=doc.createElement('img');backdrop.className='phtheme-backdrop';backdrop.alt='';backdrop.decoding='async';layer.append(backdrop);
  backdrop.onerror=()=>{backdrop.hidden=true;};
  function source(value){return particleBackground(value.backgroundId)?.image||
    (THEMES[value.themeId].scene?new URL('../scenes/'+THEMES[value.themeId].scene+'.png',import.meta.url).href:null);}
  function prepare(src){
    if(!src||loaded.has(src))return Promise.resolve();
    return new Promise((resolve,reject)=>{
      const img=new win.Image();let timer;
      function finish(ok){win.clearTimeout(timer);img.onload=img.onerror=null;
        if(ok){loaded.add(src);resolve();}else reject(new Error('背景图片加载失败，请重试。'));}
      img.onload=()=>finish(true);img.onerror=()=>finish(false);
      timer=win.setTimeout(()=>finish(false),12000);img.src=src;
    });
  }
  try{engine=particleFactory?.(layer,config);}catch{/* Static theme remains usable. */}
  const reduced=win.matchMedia?.('(prefers-reduced-motion: reduce)');
  function sync(){if(dead)return;
    const theme=THEMES[config.themeId],custom=particleBackground(config.backgroundId);
    // A legacy scene bundles its bitmap and colored overlay. Replacing that
    // scene must remove both; explicit light/dark choices remain independent.
    // Keep the saved scene ID so cancelling restores the original skin exactly.
    const visualTheme=custom&&theme.scene?'light':config.themeId,scene=custom?null:theme.scene;
    root.dataset.pyhTheme=visualTheme;root.classList.toggle('dark',visualTheme==='dark');
    if(scene){root.dataset.pyhScene=scene;root.style.setProperty('--scene-rgb',theme.rgb);root.style.setProperty('--scene-accent-rgb',theme.accentRgb);
    }
    else{delete root.dataset.pyhScene;root.style.removeProperty('--scene-rgb');root.style.removeProperty('--scene-accent-rgb');}
    root.toggleAttribute('data-pyh-static-background',!!custom);
    backdrop.style.objectPosition=custom?.position||'center';
    if(imageSource&&backdrop.src!==imageSource)backdrop.src=imageSource;
    backdrop.hidden=!imageSource;layer.hidden=!scene&&!custom;
    const pauses={theme:!scene,static:!!custom,user:config.animationMode==='off',low:config.animationMode==='low',reduced:!!reduced?.matches,hidden:doc.hidden,host:hostHidden,stopped};
    engine?.updateConfig(config);
    for(const [reason,paused] of Object.entries(pauses))paused?engine?.pause(reason):engine?.resume(reason);
    root.dispatchEvent(new win.CustomEvent('appearance-change',{detail:{...config}}));
  }
  function visibility(){sync();}doc.addEventListener('visibilitychange',visibility);reduced?.addEventListener('change',visibility);
  // Every preview/update owns a generation. Late loads cannot replace the last
  // selection or restore a preview after its dialog has closed.
  async function change(value,persist){
    if(dead)return false;
    const generation=++request;
    const next=normalizeConfig({...config,...value});
    const src=source(next);
    // Legacy shortcuts change their theme controls immediately; the last
    // usable bitmap stays visible until the requested image has loaded.
    if(persist){config=next;sync();}
    try{await prepare(src);}catch{if(generation===request){if(persist){config={...committed};imageSource=loaded.has(source(committed))?source(committed):null;sync();}throw new Error('背景图片加载失败，已保留可用背景。');}return false;}
    if(dead||generation!==request)return false;
    config=next;imageSource=src;sync();
    if(!persist)return true;
    // Serialize asynchronous host saves as well as synchronous localStorage.
    const snapshot={...next};
    const saving=saveQueue.then(async()=>{try{return (await store?.save(snapshot))!==false;}catch{return false;}});
    saveQueue=saving.then(()=>{});
    const ok=await saving;
    if(ok)committed=snapshot;
    else if(generation===request){config={...committed};imageSource=loaded.has(source(committed))?source(committed):null;sync();}
    return ok;
  }
  const api={get config(){return {...config};},get savedConfig(){return {...committed};},
    updateConfig(value){return change(value,true).catch(()=>false);},
    previewConfig(value){return change(value,false);},
    cancelPreview(){++request;config={...committed};imageSource=loaded.has(source(config))?source(config):null;sync();},
    resize(){engine?.resize();},pause(reason='host'){engine?.pause('external:'+reason);},resume(reason='host'){if(dead)return;if(reason==='stopped'){stopped=false;sync();}else{engine?.resize();engine?.resume('external:'+reason);}},
    setHostHidden(value){hostHidden=!!value;sync();},stop(){stopped=true;engine?.stop();},
    reset(){return api.updateConfig({themeId:'light',backgroundId:null});},
    stats(){return engine?.stats()||{active:false,canvasCount:0,reasons:['static-fallback']};},
    destroy(){if(dead)return;dead=true;++request;engine?.destroy();backdrop.onerror=null;backdrop.removeAttribute('src');layer.remove();doc.removeEventListener('visibilitychange',visibility);reduced?.removeEventListener('change',visibility);if(INSTANCES.get(root)===api)INSTANCES.delete(root);}};
  INSTANCES.set(root,api);sync();
  const generation=request,src=source(config);
  prepare(src).then(()=>{if(!dead&&generation===request){imageSource=src;sync();}}).catch(()=>{/* Existing theme color is the safe startup fallback. */});
  return api;
}
