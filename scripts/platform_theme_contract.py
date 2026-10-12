"""Real browser checks for appearance and attachments; synthetic input only."""
import base64
import asyncio
import json
import struct
import zlib


def synthetic_png():
    def chunk(k,d): return struct.pack('!I',len(d))+k+d+struct.pack('!I',zlib.crc32(k+d))
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',1,1,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'\0\x00\x80\xff'))+chunk(b'IEND',b'')


async def verify_theme_and_attachments(browser, app, url, output):
    context=await browser.new_context(viewport={'width':1440,'height':900},extra_http_headers={'X-PyHarness-Token':app._api_token})
    errors=[];page=await context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
    try:
        await page.goto(url);await page.locator('#compose').wait_for()
        await page.wait_for_function('window.pyharnessAppearance?.stats().frames>=2')
        assert await page.locator('canvas.phtheme-canvas').count()==1
        await page.locator('#messageText').fill('附件与主题切换保留草稿')
        async with page.expect_file_chooser() as chooser:
            await page.locator('[data-action="artifact-upload"]').click()
        await (await chooser.value).set_files({'name':'pixel.png','mimeType':'image/png','buffer':synthetic_png()})
        await page.wait_for_function("composer().attachments.length===1")
        await page.wait_for_function("document.querySelector('[data-attachment-image]')?.naturalWidth===1")
        sid=await page.evaluate('state.sid')
        assert await page.locator('#messageText').input_value()=='附件与主题切换保留草稿'
        for theme in ['light','dark','tech-blue-particle']:
            await page.evaluate('(theme)=>pyharnessAppearance.updateConfig({themeId:theme})',theme)
            assert await page.locator('#messageText').input_value()=='附件与主题切换保留草稿'
            assert await page.evaluate('composer().attachments.length')==1
            await page.screenshot(path=str(output/f'appearance-{theme}.png'),full_page=True)
        await page.reload();await page.locator('#compose').wait_for()
        assert await page.evaluate('state.sid')==sid
        assert await page.locator('#messageText').input_value()=='附件与主题切换保留草稿'
        assert await page.evaluate('composer().attachments.length')==1
        await page.locator('[data-action="attachment-remove"]').click()
        assert await page.evaluate('composer().attachments.length')==0
        upload_started=asyncio.Event();release_upload=asyncio.Event()
        async def delayed_upload(route):
            if route.request.method!='POST':return await route.continue_()
            upload_started.set();await release_upload.wait();await route.continue_()
        await page.route('**/api/v1/artifacts',delayed_upload)
        await page.evaluate("()=>{window.pendingAcceptanceUpload=uploadAttachment(new File(['synthetic'],'delayed.txt',{type:'text/plain'}));}")
        await asyncio.wait_for(upload_started.wait(),10)
        await page.locator('summary[aria-label="输入选项"]').click()
        await page.locator('[data-action="attachments-clear"]').click()
        await page.locator('#messageText').fill('New draft after clear')
        release_upload.set();await page.evaluate('pendingAcceptanceUpload')
        assert await page.evaluate('composer().attachments.length')==0
        assert await page.locator('#messageText').input_value()=='New draft after clear'
        await page.unroute('**/api/v1/artifacts',delayed_upload)
        await page.emulate_media(reduced_motion='reduce')
        await page.wait_for_function("pyharnessAppearance.stats().reasons.includes('reduced')")
        assert not await page.evaluate('pyharnessAppearance.stats().active')
        await page.emulate_media(reduced_motion='no-preference')
        await page.evaluate("pyharnessAppearance.updateConfig({animationMode:'off'})")
        await page.evaluate("pyharnessAppearance.setHostHidden(true);pyharnessAppearance.setHostHidden(false)")
        assert not await page.evaluate('pyharnessAppearance.stats().active')
        await page.evaluate("pyharnessAppearance.updateConfig({animationMode:'normal'});pyharnessAppearance.pause('test');pyharnessAppearance.setHostHidden(true);pyharnessAppearance.setHostHidden(false)")
        assert not await page.evaluate('pyharnessAppearance.stats().active')
        await page.evaluate("pyharnessAppearance.resume('test')")
        await page.wait_for_function('pyharnessAppearance.stats().active')
        # Second minimal host, independent of all application APIs.
        lifecycle=await page.evaluate("""async()=>{const {createParticles}=await import('/assets/theme-kit/particles/engine.js');const host=document.createElement('div');host.style.cssText='width:320px;height:160px';document.body.append(host);for(let i=0;i<12;i++){const a=createParticles(host,{count:80});a.resume('stopped');const b=createParticles(host,{count:8});if(host.querySelectorAll('canvas').length!==1)throw Error('duplicate canvas');a.destroy();b.destroy();b.destroy();if(host.children.length)throw Error('canvas residue');}host.remove();return true;}""")
        assert lifecycle
        await page.evaluate("pyharnessAppearance.stop();pyharnessAppearance.setHostHidden(true);pyharnessAppearance.setHostHidden(false)")
        assert not await page.evaluate('pyharnessAppearance.stats().active')
        await page.screenshot(path=str(output/'appearance-low-motion.png'),full_page=True)
        await page.goto(url+'/classic');await page.wait_for_function('window.pyharnessAppearance')
        await page.screenshot(path=str(output/'appearance-classic.png'),full_page=True)
        # Failed optional particle module must not prevent normal page startup.
        await page.route('**/theme-kit/particles/engine.js',lambda route:route.abort())
        await page.goto(url);await page.locator('#compose').wait_for()
        await page.wait_for_function("pyharnessAppearance.stats().reasons.includes('static-fallback')")
        assert not errors,errors
        report={'status':'passed','synthetic_inputs':True,'real_model':False,'checks':['image preview','draft and attachment reload','individual removal','light/dark/tech-blue','reduced motion','combined pauses','stop persists','second host lifecycle','classic','missing particle fallback'],'errors':errors}
        (output/'theme-attachment-summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    finally:await context.close()
