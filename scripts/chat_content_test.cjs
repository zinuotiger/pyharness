/* Run with node --test scripts/chat_content_test.cjs. No browser or model calls. */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {runInNewContext}=require('node:vm');
const source=readFileSync(require('node:path').join(__dirname,'../pyharness/ui/host-adapters/chat-content.js'),'utf8');
const render=runInNewContext(source+';renderChatMarkdown');
test('untrusted model text and fenced code cannot create active HTML',()=>{
 for(const value of ['<script>alert(1)</script>','<img src=x onerror=alert(1)>','**<svg onload=alert(1)>**','`<img src=x>`','```html\n<script>alert(1)</script>\n```','[open](javascript:alert(1))']){
  const html=render(value);
  assert.doesNotMatch(html,/<(?:script|img|svg|a|iframe|style)\b/i);
  assert.doesNotMatch(html,/<[^>]*\s(?:on\w+|href|src)=/i);
 }
});
test('code stays literal and incomplete streamed fences stay readable',()=>{
 assert.equal(render('```python\nprint("<hello> **world**")'),'<pre><code>print(&quot;&lt;hello&gt; **world**&quot;)</code></pre>');
 assert.equal(render('`**literal**` and **bold**'),'<p><code>**literal**</code> and <strong>bold</strong></p>');
});
test('Chinese paragraphs, headings, lists and code form readable blocks',()=>{
 const html=render('### 第一步\n\n选择 **助手**。\n\n- 只聊天\n- 沙盒\n\n```python\nprint("你好")\n```');
 assert.match(html,/<h3>第一步<\/h3>/);
 assert.match(html,/<p>选择 <strong>助手<\/strong>。<\/p>/);
 assert.match(html,/<ul><li>只聊天<\/li><li>沙盒<\/li><\/ul>/);
 assert.match(html,/<pre><code data-language="python">/);
});
