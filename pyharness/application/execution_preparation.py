"""Single-use captured input plus derived context preparation on the sole owner."""
import time
from pathlib import Path
from contextlib import asynccontextmanager
from pyharness.application.workspace_materialization import copy_attempt_inputs,verify_attempt_inputs
from pyharness.core.sandbox import WorkspaceInputPreparation,safe_path
from pyharness.errors import raise_code


class ExecutionPreparationStore:
    @asynccontextmanager
    async def execution_preparation(self, project, ident, materials, *, authorize, describe, expected_digest):
        from pyharness.application.execution_workspace import workspace_plan
        started=time.monotonic()
        async with self._workspace_capture_lock(started):
            row,attempt,resources,actor,_=self.checked_execution(project,ident,authorize=authorize,
                describe=describe,expected_digest=expected_digest)
            wi=self._task_bound_input(resources,resources[attempt['run_attempt']['prerequisites']['task_ref']['product_task_id']]['product_task'],actor)['workspace_input']
            plan=workspace_plan(materials)
        # The single writer owns capture, not the asynchronous backend lifetime.
        # A slow backend must not prevent a durable revocation/cancellation.
        active=True;bound=None;identity=None
        def gate():
            if not active:raise_code('GRD-403',reason='execution_preparation_closed')
            self._input_gate(resources,wi,actor,authorize,started)
            for ref in attempt['run_attempt']['context']['document_refs']:
                doc=self._s05_exact(resources,project,'document',ref,'document_id',actor)
                self._document_gate(resources,doc,actor,authorize,started)
        def destination(target):
            gate();self._input_destination(target,resources,wi)
        def verify():
            nonlocal resources
            # Refresh before each no-await copy/verify segment, including after
            # waiting for the sandbox lock and after actual backend creation.
            _,_,resources,_,_=self.checked_execution(project,ident,authorize=authorize,
                describe=describe,expected_digest=expected_digest)
            gate()
            if bound is not None:
                safe_path(bound,'owned-check');info=bound.stat()
                if (info.st_dev,info.st_ino)!=identity:raise_code('PERS-202',reason='execution_workspace_root_changed')
                verify_attempt_inputs(bound,plan['files'],gate)
            self.checked_execution(project,ident,authorize=authorize,describe=describe,expected_digest=expected_digest)
        def populate(target):
            nonlocal bound,identity
            if bound is not None:raise_code('GRD-403',reason='execution_preparation_already_used')
            destination(target);bound=Path(target);info=bound.stat();identity=(info.st_dev,info.st_ino)
            copy_attempt_inputs(bound,plan['files'],lambda item:plan['contents'][item['path']],gate)
            verify()
        verify()
        try:yield WorkspaceInputPreparation(materials['materials_digest'],populate,verify,destination)
        finally:active=False
