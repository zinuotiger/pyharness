"""Actual Qt buttons, original product service, queue and control journal."""
import asyncio
import os
import pytest
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from tests.unit.test_product_execution_start import running_product,wait_terminal
from tests.unit.test_execution_liveness import held
from tests.unit.test_source_grants import PID,GID


async def settled(widget):
    for _ in range(400):
        QApplication.processEvents()
        if widget.inflight is None:return
        await asyncio.sleep(.01)
    raise AssertionError('native control operation never settled')


async def test_native_exact_control_queries_without_dispatch_and_stops_shared_root(running_product):
    from pyharness.desktop_native.product_execution_control import ProductExecutionControl
    from pyharness.desktop_native.controller import NativeController
    app=QApplication.instance() or QApplication([])
    env=running_product;started,release=await held(env)
    controller=NativeController(env.app.service.ctx,service=env.app.service)
    widget=ProductExecutionControl(controller);widget.show()
    try:
        widget.project_input.setText(PID);widget.attempt_input.setText(env.selection['attempt_ref']['attempt_id'])
        before=env.app.service.projects.store.log_path.read_bytes();calls=env.adapter.n
        widget.refresh_button.click();await settled(widget)
        assert widget.target['execution_ref']['execution_id']==started['execution_id'] and widget.stop_button.isEnabled()
        assert env.app.service.projects.store.log_path.read_bytes()==before and env.adapter.n==calls
        queue=env.app.service._queues[started['session_id']];cancel=queue.cancel;signals=[]
        async def counted(task,*args,**kwargs):signals.append(task);return await cancel(task,*args,**kwargs)
        queue.cancel=counted
        widget.stop_button.click();widget.stop_button.click();await settled(widget)
        result=await wait_terminal(env.app.service.projects,started['execution_id'])
        assert result['status']=='cancelled' and signals==[started['task_id']] and env.adapter.n==calls
        before=env.app.service.projects.store.log_path.read_bytes()
        widget.query_button.click();await settled(widget)
        assert '实际执行已停' in widget.status_label.text() and len(signals)==1
        assert env.app.service.projects.store.log_path.read_bytes()==before
    finally:
        release.set();widget.dispose();widget.close();await controller.close()


async def test_native_source_withdrawal_still_allows_exact_minimum_stop(running_product,monkeypatch):
    from pyharness.desktop_native.product_execution_control import ProductExecutionControl
    from pyharness.desktop_native.controller import NativeController
    app=QApplication.instance() or QApplication([])
    env=running_product;started,release=await held(env)
    r=await env.client.post(f'/api/v1/source-grants/{GID}/revoke',json=dict(expected_revision=1),headers={'Idempotency-Key':'native-withdraw'})
    assert r.status_code==200
    def forbidden(*args,**kwargs):raise AssertionError('native minimum stop tried to read source body')
    monkeypatch.setattr(env.app.service.projects.store,'_verify_snapshot_content',forbidden)
    monkeypatch.setattr(env.app.service.projects.store,'_verify_workspace_content',forbidden)
    controller=NativeController(env.app.service.ctx,service=env.app.service);widget=ProductExecutionControl(controller);widget.show()
    try:
        widget.project_input.setText(PID);widget.attempt_input.setText(env.selection['attempt_ref']['attempt_id'])
        widget.refresh_button.click();await settled(widget);assert widget.target is not None
        widget.stop_button.click();await settled(widget)
        result=await queue_terminal(env,started)
        assert result=='cancelled' and '原资料' not in widget.status_label.text()
    finally:
        release.set();widget.dispose();widget.close();await controller.close()


@pytest.fixture
def native_widget(tmp_path):
    import copy
    from types import SimpleNamespace
    from pyharness.application.execution_cancellation_store import ExecutionCancellationStore
    from pyharness.application.execution_contracts import CancelExecution
    from pyharness.desktop_native.product_execution_control import ProductExecutionControl
    from pyharness.desktop_native.controller import NativeController
    app=QApplication.instance() or QApplication([])
    class Control:
        def __init__(self):self.gate=None;self.entered=asyncio.Event();self.posts=[];self.lost=False;self.denied=False;self.cancellation=None
        async def execution_control_for_attempt(self,project,attempt):
            self.entered.set()
            if self.gate is not None:await self.gate.wait()
            if self.denied:raise ValueError('GRD-403')
            return dict(schema_version=1,control_only=True,project_id=project,status='authorized',
                attempt_ref=dict(attempt_id=attempt,revision=1,digest='a'*64,source_seq=2),
                execution_ref=dict(execution_id='ex-'+'a'*16,revision=1,digest='b'*64,source_seq=3),cancellation=self.cancellation)
        async def cancel_execution(self,project,body,key):
            self.posts.append((project,copy.deepcopy(body),key));command=CancelExecution.model_validate(body)
            self.cancellation=dict(body,project_id=project,submission_key=key,request_digest=ExecutionCancellationStore._cancellation_request(project,command),
                persisted=True,status='cancellation_requested',execution_stopped=True)
            if self.lost:raise OSError('reply lost after committed stop')
            return self.cancellation
        async def cancellation_submission(self,project,key):
            return dict(schema_version=1,project_id=project,submission_key=key,status='committed' if self.cancellation else 'not_found',cancellation=self.cancellation)
    control=Control();service=SimpleNamespace(tenant_id='default',tenant_root=lambda:tmp_path,projects=control)
    controller=NativeController(service=service);widget=ProductExecutionControl(controller);widget.show()
    widget.project_input.setText(PID);widget.attempt_input.setText('ra-'+'a'*16)
    yield widget,control,controller
    widget.dispose();widget.close()


async def test_native_late_read_cannot_replace_changed_target(native_widget):
    widget,control,_=native_widget;control.gate=asyncio.Event();widget.refresh_button.click()
    await control.entered.wait();widget.project_input.setText('p-'+'f'*16);control.gate.set();await settled(widget)
    assert widget.target is None and not widget.stop_button.isEnabled() and not control.posts


async def test_native_tenant_switch_discards_late_old_receipt(native_widget):
    widget,control,controller=native_widget;control.gate=asyncio.Event();widget.refresh_button.click()
    await control.entered.wait();controller.switch_tenant('other');control.gate.set();await settled(widget)
    assert widget.target is None and not widget.stop_button.isEnabled() and 'ex-' not in widget.status_label.text()


async def test_native_lost_reply_and_reopened_widget_never_reposts(native_widget):
    from pyharness.desktop_native.product_execution_control import ProductExecutionControl
    widget,control,controller=native_widget;widget.refresh_button.click();await settled(widget)
    control.lost=True;widget.stop_button.click();widget.stop_button.click();await settled(widget)
    assert len(control.posts)==1 and '操作未确认' in widget.status_label.text()
    original=control.posts[0];widget.dispose();widget.close()
    new=ProductExecutionControl(controller);new.show()
    try:
        new.project_input.setText(PID);new.attempt_input.setText('ra-'+'a'*16)
        assert len(control.posts)==1
        new.refresh_button.click();await settled(new)
        assert '实际执行已停' in new.status_label.text() and not new.stop_button.isEnabled() and len(control.posts)==1
        assert new.command(new.identity()[1],PID,new.target)['key']==original[2]
    finally:new.dispose();new.close()


async def test_native_role_revoked_between_read_and_stop_creates_no_command(native_widget):
    widget,control,_=native_widget;widget.refresh_button.click();await settled(widget)
    control.denied=True;widget.stop_button.click();await settled(widget)
    assert not control.posts and widget.target is None and 'GRD-403' in widget.status_label.text()


async def queue_terminal(env,started):
    q=env.app.service._queues[started['session_id']]
    result=await q.wait_for(started['task_id'],timeout=5)
    return result.code
