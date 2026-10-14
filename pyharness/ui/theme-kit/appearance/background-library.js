import {THEMES,DEFAULT_CONFIG} from '../tokens/themes.js';
import {PARTICLE_BACKGROUNDS,originalBackgrounds} from '../tokens/backgrounds.js';

export function createBackgroundLibrary(controller,doc=document){
  const entries=[...originalBackgrounds(THEMES),...PARTICLE_BACKGROUNDS.map(item=>({...item,config:{backgroundId:item.id}}))];
  const dialog=doc.createElement('dialog');dialog.className='phtheme-library';
  dialog.setAttribute('aria-labelledby','phtheme-library-title');
  dialog.innerHTML=`<div class="phtheme-library-heading"><h2 id="phtheme-library-title">背景库</h2><button type="button" data-library-close aria-label="关闭背景库">×</button></div>
    <p class="muted">选择缩略图临时预览，应用后保存。取消或 Esc 恢复原背景。</p>
    <div class="phtheme-library-filters" role="group" aria-label="背景分类">
      <button type="button" data-background-filter="all" aria-pressed="true">全部</button>
      <button type="button" data-background-filter="original" aria-pressed="false">原有背景</button>
      <button type="button" data-background-filter="particle" aria-pressed="false">浅色粒子</button></div>
    <div class="phtheme-library-grid"></div><p class="phtheme-library-status" role="status" aria-live="polite"></p>
    <div class="phtheme-library-footer"><button type="button" data-background-default>恢复默认背景</button><button type="button" data-library-close>取消</button><button type="button" class="primary" data-background-apply disabled>应用背景</button></div>`;
  doc.body.append(dialog);
  const grid=dialog.querySelector('.phtheme-library-grid'),status=dialog.querySelector('[role=status]'),apply=dialog.querySelector('[data-background-apply]');
  let opener,selected=null,generation=0,saving=false,handledCloses=0;
  const identity=value=>value.backgroundId||value.themeId;
  for(const entry of entries){
    const card=doc.createElement(entry.ready?'button':'div');
    card.className='phtheme-library-choice';card.dataset.backgroundId=entry.id;card.dataset.group=entry.group;
    if(entry.ready){
      card.type='button';card.setAttribute('aria-pressed','false');
      const img=doc.createElement('img');img.alt='';img.src=entry.thumbnail;img.loading='lazy';img.width=320;img.height=180;
      img.onerror=()=>{img.hidden=true;card.querySelector('small').textContent='缩略图加载失败，可重试预览';};
      card.append(img);
    }else{card.setAttribute('aria-disabled','true');}
    const title=doc.createElement('strong');title.textContent=entry.label;
    const badge=doc.createElement('small');badge.textContent=entry.ready?'点击预览':'待素材 · 暂不可用';
    card.append(title,badge);grid.append(card);
  }
  function mark(){for(const card of grid.children){const current=card.dataset.backgroundId===identity(controller.savedConfig);
    if(card.tagName==='BUTTON')card.setAttribute('aria-pressed',String(card.dataset.backgroundId===selected));
    card.toggleAttribute('data-applied',current);
    if(card.tagName==='BUTTON')card.querySelector('small').textContent=current?'当前应用':card.dataset.backgroundId===selected?'预览中':'点击预览';}}
  function filter(group){for(const button of dialog.querySelectorAll('[data-background-filter]'))button.setAttribute('aria-pressed',String(button.dataset.backgroundFilter===group));
    for(const card of grid.children)card.hidden=group!=='all'&&card.dataset.group!==group;}
  async function preview(entry){
    const token=++generation;selected=entry.id;apply.disabled=true;status.textContent='正在加载背景…';mark();
    try{const ok=await controller.previewConfig({...entry.config,...(entry.group==='particle'?{themeId:controller.savedConfig.themeId}:{})});
      if(token!==generation||!dialog.open)return;
      apply.disabled=!ok;status.textContent=ok?'正在预览，尚未保存。':'选择已更新，请重试。';
    }catch(error){if(token!==generation||!dialog.open)return;status.textContent=error.message;selected=null;mark();}
  }
  function close(){if(saving||!dialog.open)return;++generation;controller.cancelPreview();
    ++handledCloses;dialog.close();opener?.focus();}
  dialog.addEventListener('cancel',event=>{event.preventDefault();close();});
  // Handles programmatic close as well as the explicit close buttons.
  dialog.addEventListener('close',()=>{
    // close() has already restored the preview synchronously. Its queued
    // native event must not undo a later selection or a reopened dialog.
    if(handledCloses){--handledCloses;return;}
    if(dialog.open)return;
    ++generation;controller.cancelPreview();opener?.focus();});
  dialog.addEventListener('click',async event=>{
    if(saving)return;
    if(event.target.closest('[data-library-close]'))return close();
    const filterButton=event.target.closest('[data-background-filter]');if(filterButton)return filter(filterButton.dataset.backgroundFilter);
    if(event.target.closest('[data-background-default]'))return preview(entries.find(item=>item.id===DEFAULT_CONFIG.themeId));
    const card=event.target.closest('button[data-background-id]');if(card)return preview(entries.find(item=>item.id===card.dataset.backgroundId));
    if(!event.target.closest('[data-background-apply]')||apply.disabled)return;
    const entry=entries.find(item=>item.id===selected);if(!entry)return;
    saving=true;apply.disabled=true;status.textContent='正在保存…';
    for(const button of dialog.querySelectorAll('button'))button.disabled=true;
    const ok=await controller.updateConfig(entry.config);
    saving=false;for(const button of dialog.querySelectorAll('button'))button.disabled=false;
    if(ok){close();}else{selected=null;apply.disabled=true;mark();status.textContent='保存失败，已恢复原背景。请检查存储权限后重试。';}
  });
  return {open(button){if(dialog.open)return;opener=button;selected=identity(controller.savedConfig);filter('all');mark();apply.disabled=true;
      status.textContent=PARTICLE_BACKGROUNDS.some(item=>!item.ready)?'六款浅色粒子缺少纯背景素材，暂不可用。原有背景可正常选择。':'';dialog.showModal();},
    destroy(){++generation;controller.cancelPreview();dialog.remove();}};
}
