/* Text-only Markdown subset. Model output never becomes raw HTML. */
function renderChatMarkdown(value) {
 const escape=text=>String(text).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 function inline(text) {
  return text.split(/(`[^`\n]+`)/g).map(part=>part.startsWith('`')&&part.endsWith('`')?`<code>${escape(part.slice(1,-1))}</code>`:escape(part).replace(/\*\*([^*\n]+)\*\*/g,'<strong>$1</strong>')).join('');
 }
 const lines=String(value).replace(/\r\n?/g,'\n').split('\n'),html=[];
 let paragraph=[],list=null,code=null,language='';
 function flush(){if(paragraph.length){html.push('<p>'+paragraph.map(inline).join('<br>')+'</p>');paragraph=[]}if(list){html.push('</'+list+'>');list=null}}
 for(const line of lines){
  const fence=line.match(/^\s*```\s*([\w+-]*)\s*$/);
  if(code!==null){if(fence){html.push(`<pre><code${language?` data-language="${escape(language)}"`:''}>${escape(code.join('\n'))}</code></pre>`);code=null}else code.push(line);continue}
  if(fence){flush();code=[];language=fence[1];continue}
  if(!line.trim()){flush();continue}
  const heading=line.match(/^#{1,6}\s+(.+)$/);
  if(heading){flush();html.push('<h3>'+inline(heading[1])+'</h3>');continue}
  const item=line.match(/^\s*(?:([-*+])|\d+\.)\s+(.+)$/);
  if(item){const type=item[1]?'ul':'ol';if(list!==type){flush();html.push('<'+type+'>');list=type}html.push('<li>'+inline(item[2])+'</li>');continue}
  if(list)flush();paragraph.push(line);
 }
 flush();if(code!==null)html.push('<pre><code>'+escape(code.join('\n'))+'</code></pre>');
 return html.join('');
}
