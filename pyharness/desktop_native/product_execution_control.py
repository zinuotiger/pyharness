"""Qt exact product stop control; the existing application remains authority."""
import asyncio
import re
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFormLayout,QHBoxLayout,QLabel,QLineEdit,QPushButton,QVBoxLayout,QWidget
from pyharness.application.accounts import current_actor
from pyharness.application.execution_contracts import AttemptReference,CancelExecution,ExecutionReference
from pyharness.application.workspace_store import canonical,digest_of
from pyharness.application.execution_cancellation_store import ExecutionCancellationStore


class ProductExecutionControl(QWidget):
    def __init__(self,controller,parent=None):
        super().__init__(parent);self.controller=controller;self.target=None;self.inflight=None;self.epoch=0;self.dead=False;self.hint=None
        layout=QVBoxLayout(self);layout.addWidget(QLabel('产品运行控制'))
        form=QFormLayout();self.project_input=QLineEdit();self.project_input.setPlaceholderText('p-…')
        self.attempt_input=QLineEdit();self.attempt_input.setPlaceholderText('ra-…')
        form.addRow('项目 ID',self.project_input);form.addRow('运行尝试 ID',self.attempt_input);layout.addLayout(form)
        row=QHBoxLayout();self.refresh_button=QPushButton('只读核对');self.stop_button=QPushButton('停止精确执行');self.query_button=QPushButton('只读核对原停止键')
        for button in (self.refresh_button,self.stop_button,self.query_button):row.addWidget(button)
        layout.addLayout(row);self.status_label=QLabel('先核对项目与运行尝试。查询不会启动执行。');self.status_label.setWordWrap(True)
        self.status_label.setTextFormat(Qt.PlainText);layout.addWidget(self.status_label)
        self.project_input.textChanged.connect(self.invalidate);self.attempt_input.textChanged.connect(self.invalidate)
        controller.sessions_changed.connect(self.invalidate)
        self.refresh_button.clicked.connect(lambda:self.start('refresh'));self.stop_button.clicked.connect(lambda:self.start('stop'));self.query_button.clicked.connect(lambda:self.start('query'))
        self.controls()

    def identity(self):
        service=self.controller.service;actor=current_actor.get()
        user=actor.get('user_id') if actor else 'local-operator'
        tenant=getattr(service,'tenant_id',self.controller.tenant_id)
        root=getattr(service,'tenant_root',None)
        realm=(tenant,user,str(root().resolve()) if callable(root) else '')
        return service,realm

    def invalidate(self,*args):
        self.epoch+=1;self.target=None;self.hint=None
        if not self.dead:self.status_label.setText('目标或主体已变化，请重新只读核对。');self.controls()

    def dispose(self):
        if self.dead:return
        self.dead=True;self.epoch+=1;self.target=None;self.hint=None
        try:self.controller.sessions_changed.disconnect(self.invalidate)
        except (RuntimeError,TypeError):pass
        self.controls()

    def closeEvent(self,event):
        self.dispose();super().closeEvent(event)

    def controls(self):
        busy=self.inflight is not None
        self.refresh_button.setEnabled(not self.dead and not busy)
        self.stop_button.setEnabled(not self.dead and not busy and self.target is not None and self.target['execution_ref'] is not None and self.target['cancellation'] is None)
        self.query_button.setEnabled(not self.dead and not busy and self.target is not None and self.target['execution_ref'] is not None)

    def scope(self):return self.project_input.text().strip(),self.attempt_input.text().strip()

    def live(self,epoch,identity,scope):
        return not self.dead and self.epoch==epoch and self.identity()==identity and self.scope()==scope

    def start(self,action):
        if self.dead or self.inflight is not None:return
        identity=self.identity();scope=self.scope();epoch=self.epoch
        self.inflight=asyncio.create_task(self.operate(action,epoch,identity,scope));self.controls()
        self.inflight.add_done_callback(self.finished)

    def finished(self,task):
        try:task.result()
        except asyncio.CancelledError:pass
        if self.inflight is task:self.inflight=None
        if not self.dead:self.controls()

    def command(self,realm,project,target):
        # The same exact target yields the same original command after restart.
        # No prompt, private result, source path, or capability is persisted here.
        value=digest_of(canonical(dict(operation='native.execution.stop',realm=realm,project=project,execution_ref=target['execution_ref'])))
        command=CancelExecution(schema_version=1,cancellation_id='ec-'+value[:16],execution_ref=target['execution_ref'])
        return dict(body=command.model_dump(),key='native-stop-'+value,request_digest=ExecutionCancellationStore._cancellation_request(project,command))

    def checked_control(self,value,project,attempt):
        if (type(value.get('schema_version')) is not int or value['schema_version']!=1 or value.get('control_only') is not True or
                value.get('project_id')!=project or value.get('status') not in ('authorized','not_authorized')):raise ValueError('控制回执范围不符')
        reference=AttemptReference.model_validate(value['attempt_ref'])
        if reference.attempt_id!=attempt:raise ValueError('运行尝试不符')
        execution=value.get('execution_ref')
        if execution is not None:ExecutionReference.model_validate(execution)
        if (value['status']=='authorized')!=(execution is not None):raise ValueError('控制状态不符')
        return value

    def checked_stop(self,value,project,target,hint=None):
        if (value.get('schema_version')!=1 or type(value.get('schema_version')) is not int or value.get('project_id')!=project or
                value.get('execution_ref')!=target['execution_ref'] or value.get('persisted') is not True or
                value.get('status')!='cancellation_requested' or (value.get('execution_stopped') is not None and type(value['execution_stopped']) is not bool)):
            raise ValueError('停止回执范围不符')
        if hint and (value.get('submission_key')!=hint['key'] or value.get('request_digest')!=hint['request_digest'] or
                value.get('cancellation_id')!=hint['body']['cancellation_id']):raise ValueError('原停止键不符')
        return value

    def show_stop(self,value):
        stopped=value.get('execution_stopped')
        text='实际执行已停' if stopped is True else '停止已请求，实际执行仍待确认' if stopped is False else '停止已记录，当前服务无法确认实际执行是否已停'
        self.status_label.setText(text+'。不会重发模型或工具；执行停止不等于业务验收通过。')

    async def operate(self,action,epoch,identity,scope):
        service,realm=identity;project,attempt=scope
        try:
            if not re.fullmatch(r'p-[0-9a-f]{16}',project) or not re.fullmatch(r'ra-[0-9a-f]{16}',attempt):raise ValueError('请输入完整项目和运行尝试 ID')
            target=self.checked_control(await service.projects.execution_control_for_attempt(project,attempt),project,attempt)
            if not self.live(epoch,identity,scope):return
            self.target=target
            if target['execution_ref'] is None:
                self.status_label.setText('该尝试尚无执行授权；没有启动或停止任何任务。');return
            if target['cancellation'] is not None:
                stopped=self.checked_stop(target['cancellation'],project,target)
                if action=='query':
                    command=CancelExecution(schema_version=1,cancellation_id=stopped['cancellation_id'],execution_ref=target['execution_ref'])
                    expected=dict(body=command.model_dump(),key=stopped['submission_key'],request_digest=ExecutionCancellationStore._cancellation_request(project,command))
                    found=await service.projects.cancellation_submission(project,expected['key'])
                    if not self.live(epoch,identity,scope):return
                    if (type(found.get('schema_version')) is not int or found['schema_version']!=1 or found.get('project_id')!=project or
                            found.get('submission_key')!=expected['key'] or found.get('status')!='committed'):raise ValueError('原停止键记录未确认')
                    stopped=self.checked_stop(found['cancellation'],project,target,expected)
                self.show_stop(stopped)
                return
            hint=self.command(realm,project,target);self.hint=hint
            if action=='refresh':
                self.status_label.setText('精确执行已核对：'+target['execution_ref']['execution_id']+'。只读查询没有发出停止或启动命令。')
                return
            if action=='query':
                found=await service.projects.cancellation_submission(project,hint['key'])
                if not self.live(epoch,identity,scope):return
                if (found.get('schema_version')!=1 or found.get('project_id')!=project or found.get('submission_key')!=hint['key']):raise ValueError('原键查询范围不符')
                if found['status']=='not_found':self.status_label.setText('原键尚无已确认停止记录。没有自动重发；显式停止仍使用相同原键。');return
                if found['status']!='committed':raise ValueError('停止记录状态未确认')
                value=self.checked_stop(found['cancellation'],project,target,hint)
            else:
                self.status_label.setText('正在停止精确执行，原键：'+hint['key'])
                value=await service.projects.cancel_execution(project,hint['body'],hint['key'])
                if not self.live(epoch,identity,scope):return
                value=self.checked_stop(value,project,target,hint)
            target['cancellation']=value;self.show_stop(value)
        except Exception as exc:
            if self.live(epoch,identity,scope):
                self.target=None;self.status_label.setText('操作未确认：'+str(exc)+'。先只读核对；不会自动重发或更换停止键。')
