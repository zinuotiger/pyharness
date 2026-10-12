"""Synthetic fixtures, actual schedule/Agent/Patch UI and application owners."""
import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace
from scripts.platform_browser_smoke import navigate


async def verify_lifecycle(page,app,output):
    p=app.service.platform
    from urllib.parse import urlsplit
    address=urlsplit(page.url);base=address.scheme+'://'+address.netloc
    async def until(predicate):
        async with asyncio.timeout(15):
            while not await predicate():await asyncio.sleep(.05)
    await navigate(page,'schedules')
    await page.get_by_role('button',name='创建计划任务',exact=True).click()
    await page.locator('#scheduleName').fill('audit_schedule')
    await page.locator('#scheduleExpr').fill('86400')
    await page.locator('#scheduleIntent').fill('synthetic scheduled task')
    await page.locator('#scheduleForm button').click()
    await page.locator('tr').filter(has_text='audit_schedule').wait_for()
    schedule_data=await (await page.request.get(base+'/api/v1/schedules')).json()
    schedule_sid=next(j['session_id'] for j in schedule_data['schedules'] if j['name']=='audit_schedule')
    await page.get_by_role('button',name='禁用',exact=True).click()
    await page.get_by_role('button',name='启用',exact=True).wait_for()
    await page.get_by_role('button',name='编辑',exact=True).click()
    await page.locator('#editIntent').fill('synthetic edited schedule')
    await page.locator('#editSchedule button').click()
    await page.get_by_role('button',name='启用',exact=True).click()
    await page.get_by_role('button',name='禁用',exact=True).wait_for()
    await page.get_by_role('button',name='立即运行',exact=True).click()
    async def fired():
        return any(r['session_id']==schedule_sid and r['status']=='completed' for r in (await p.runs())['runs'])
    await until(fired)
    await navigate(page,'schedules')
    await page.get_by_text('schedule.trigger',exact=True).wait_for()
    await page.screenshot(path=str(output/'schedule-executed.png'),full_page=True)
    await page.get_by_role('button',name='删除',exact=True).click()
    await page.locator('#confirmProceed').click()
    await page.get_by_text('暂无计划任务',exact=True).wait_for()
    assert not (await (await page.request.get(base+'/api/v1/schedules')).json())['schedules']

    await navigate(page,'agents')
    await page.get_by_role('button',name='创建 Agent',exact=True).click()
    import json
    await page.locator('#agentJson').fill(json.dumps({'name':'Lifecycle agent','permission_policy':'standard'}))
    await page.get_by_role('button',name='保存草稿',exact=True).click()
    card=page.locator('article.card').filter(has=page.get_by_role('heading',name='Lifecycle agent',exact=True))
    await card.get_by_role('button',name='发布',exact=True).click()
    await card.get_by_text('已发布',exact=True).wait_for()
    ident=next(a['agent_id'] for a in p.agents()['agents'] if a['name']=='Lifecycle agent')
    first=p.new_binding_version(ident,1)
    await card.get_by_role('button',name='编辑草稿',exact=True).click()
    edited=json.loads(await page.locator('#agentJson').input_value());edited['description']='second version'
    await page.locator('#agentJson').fill(json.dumps(edited))
    await page.get_by_role('button',name='保存草稿',exact=True).click()
    await card.get_by_role('button',name='发布',exact=True).click()
    await card.get_by_text('已发布',exact=True).wait_for()
    assert p.new_binding_version(ident,1)==first
    assert p.new_binding_version(ident,2)['snapshot']['description']=='second version'
    await card.get_by_role('button',name='版本',exact=True).click()
    await page.get_by_role('button',name='与上一版本比较',exact=True).click()
    await page.locator('.settings-content pre').filter(has_text='second version').wait_for()
    await page.screenshot(path=str(output/'agent-immutable-version-diff.png'),full_page=True)
    await page.locator('[data-action="panel-back"]').click()
    await page.locator('[data-action="panel-back"]').click()
    await card.get_by_role('button',name='测试运行',exact=True).click()
    await page.locator('#messageText').fill('synthetic patch ownership task')
    await page.locator('#messageText').press('Enter')
    await page.locator('.chat-top .badge').filter(has_text='已完成').wait_for()
    active_sid=await page.evaluate('state.sid')
    run=await p.run(next(r['run_id'] for r in (await p.runs())['runs'] if r['session_id']==active_sid))
    sid=run['session_id'];log=await app.service.require_session(sid)
    workspace=Path(app.service._session_workspace(sid));workspace.mkdir(parents=True,exist_ok=True)
    before=b'def add(a, b):\n    return a - b\n';after='def add(a, b):\n    return a + b\n'
    target=workspace/'calculator.py';target.write_bytes(before)
    # Explicit fixture, not represented as model- or Sandbox-generated patch.
    raw=b'--- a/calculator.py\n+++ b/calculator.py\n@@ -1,2 +1,2 @@\n def add(a, b):\n-    return a - b\n+    return a + b\n'
    artifact=await p.record_generated(SimpleNamespace(session=log,task_id=run['task_id']),
        'lifecycle.patch',raw,manifest=[{'path':'calculator.py','before_sha256':hashlib.sha256(before).hexdigest(),'after':after}])
    await navigate(page,'artifacts')
    patch_row=page.locator('tr').filter(has_text='lifecycle.patch')
    await patch_row.get_by_role('button',name='预览',exact=True).click()
    await page.locator('.settings-content pre').filter(has_text='return a + b').wait_for()
    await page.locator('[data-action="panel-back"]').click()
    await patch_row.get_by_role('button',name='应用 Patch',exact=True).click()
    await page.locator('#confirmProceed').click()
    await page.get_by_role('button',name='批准一次',exact=True).wait_for()
    assert target.read_bytes()==before
    await page.screenshot(path=str(output/'patch-awaiting-separate-approval.png'),full_page=True)
    await page.get_by_role('button',name='批准一次',exact=True).click()
    await page.locator('#confirmProceed').click()
    result=await asyncio.wait_for(p.actions[artifact['artifact_id']+':apply'],15)
    assert result['ok'] and target.read_text()==after
    assert (await p.artifact(artifact['artifact_id']))['sha256']==hashlib.sha256(raw).hexdigest()
    await page.screenshot(path=str(output/'patch-approved.png'),full_page=True)
    # Reapplication must reject the stale baseline and leave the target intact.
    await p.request_artifact_action(artifact['artifact_id'],'apply')
    await navigate(page,'approvals');await page.get_by_role('button',name='批准一次',exact=True).wait_for()
    await page.get_by_role('button',name='批准一次',exact=True).click()
    await page.locator('#confirmProceed').click()
    repeated=await asyncio.wait_for(p.actions[artifact['artifact_id']+':apply'],15)
    assert not repeated['ok'] and target.read_text()==after
    await navigate(page,'agents')
    await card.get_by_role('button',name='废弃',exact=True).click()
    await page.locator('#confirmProceed').click()
    await card.get_by_text('已废弃',exact=True).wait_for()
    assert (await p.run(run['run_id']))['status']=='completed'
    return {'area':'schedule/Agent/Patch UI lifecycle','result':'passed',
        'checks':['schedule add/edit/pause/resume/run-now/history/remove','immutable Agent versions/diff/deprecate/history',
                  'explicit fixture Patch preview','separate apply approval','unchanged target before approval','minimal target change','stale baseline rejected'],
        'patch_origin':'synthetic fixture, NOT model/Docker output','patch_sha256':artifact['sha256'],
        'sandbox_mode':'disabled','real_provider_requests':0}
