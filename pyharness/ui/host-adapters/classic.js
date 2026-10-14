// The classic document has different controls but shares the pure appearance kit.
import './platform.js';
const panel=document.createElement('section');panel.className='phtheme-controls';panel.setAttribute('aria-label','外观');
panel.innerHTML='<label>主题 <select data-appearance-field="themeId"><option value="light">原浅色</option><option value="dark">原深色</option><option value="tech-blue-particle">科技蓝粒子</option></select></label><label>动效 <select data-appearance-field="animationMode"><option value="normal">开启</option><option value="low">低动效</option><option value="off">关闭</option></select></label><button type="button" data-appearance-reset>恢复原主题</button>';
document.body.prepend(panel);
