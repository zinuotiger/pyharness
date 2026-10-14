const fs=require('node:fs');
const path=require('node:path');
const read=name=>fs.readFileSync(path.join(__dirname,name),'utf8');
let html=read('index.html');
html=html.replace('<link rel="stylesheet" href="style.css">',()=>'<style>'+read('style.css')+'</style>');
for(const name of ['history.js','app.js'])html=html.replace(`<script src="${name}"></script>`,()=>'<script>'+read(name).replace(/<\/script/gi,'<\\/script')+'</script>');
fs.writeFileSync(path.join(__dirname,'PyHarness-Trace-Lab.html'),html);
console.log('Portable offline prototype written: '+Buffer.byteLength(html)+' bytes');
