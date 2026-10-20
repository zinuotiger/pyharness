"""Derive new read-only attempt references while retaining every captured output."""
import base64,os
from pathlib import PurePosixPath
from typing import Literal
from pydantic import Field,model_validator
from pyharness.application.document_contracts import VersionOne
from pyharness.application.state_snapshot_contracts import StateFile,StateDirectory,MAX_STATE_TOTAL_FILE_BYTES
from pyharness.application.workspace_contracts import Digest
from pyharness.application.execution_workspace import workspace_plan
from pyharness.application.workspace_store import canonical,digest_of
from pyharness.errors import raise_code


class ContinuationWorkspace(VersionOne):
    capture_scope:Literal['derived_continuation_workspace']='derived_continuation_workspace'
    origin_state_digest:Digest
    materials_digest:Digest
    workspace_backend:Literal['host_approved','isolated']
    filesystem_capture:Literal['files_and_directories']='files_and_directories'
    files:list[StateFile]=Field(max_length=4096)
    directories:list[StateDirectory]=Field(max_length=4096)
    digest:Digest

    @model_validator(mode='after')
    def exact_tree(self):
        files={f.path.casefold() for f in self.files};dirs={d.path.casefold() for d in self.directories}
        if len(files)!=len(self.files) or len(dirs)!=len(self.directories) or files&dirs or sum(f.size for f in self.files)>MAX_STATE_TOTAL_FILE_BYTES:
            raise ValueError('derived filesystem collision or limit')
        for item in [*self.files,*self.directories]:
            if any(p.as_posix().casefold() not in dirs for p in PurePosixPath(item.path).parents if p.as_posix()!='.'):
                raise ValueError('derived filesystem parent missing')
        if digest_of(canonical(self.model_dump(mode='json',exclude={'digest'})))!=self.digest:raise ValueError('derived filesystem digest')
        return self


def validate_reference_workspace(body,materials):
    if body.materials_digest!=materials['materials_digest']:raise_code('PERS-202',reason='continuation_materials_binding')
    if body.workspace_backend=='disabled':
        if body.filesystem_capture!='no_workspace' or body.files or body.directories:raise_code('PERS-202',reason='continuation_workspace_state_invalid')
        return None
    if body.filesystem_capture!='files_and_directories' or body.workspace_backend not in ('host_approved','isolated'):
        raise_code('GRD-403',reason='continuation_complete_filesystem_required')
    old=workspace_plan(materials);prefix=old['context_directory']
    inside=lambda name:name==prefix or name.startswith(prefix+'/')
    captured={f.path:f for f in body.files};captured_dirs={d.path:d for d in body.directories}
    expected={path:raw for path,raw in old['contents'].items() if inside(path)}
    if set(expected)!={name for name in captured if inside(name)}:raise_code('PERS-202',reason='continuation_reference_set_changed')
    for name,raw in expected.items():
        f=captured[name]
        if f.sha256!=digest_of(raw) or base64.b64decode(f.data_b64,validate=True)!=raw:
            raise_code('PERS-202',reason='continuation_reference_bytes_changed')
    expected_dirs={p.as_posix() for name in expected for p in PurePosixPath(name).parents if p.as_posix()!='.'}
    if {name for name in captured_dirs if inside(name)}!=expected_dirs:raise_code('PERS-202',reason='continuation_reference_directories_changed')
    return old,captured,captured_dirs


def derive_continuation_workspace(body,old_materials,new_materials,state_digest):
    before={k:v for k,v in old_materials['bundle'].items() if k!='attempt_ref'}
    after={k:v for k,v in new_materials['bundle'].items() if k!='attempt_ref'}
    if before!=after:raise_code('GRD-403',reason='continuation_materials_changed')
    checked=validate_reference_workspace(body,old_materials)
    if checked is None:return None
    old,captured,captured_dirs=checked;new=workspace_plan(new_materials);prefix=old['context_directory']
    if prefix!=new['context_directory']:raise_code('PERS-202',reason='continuation_context_scope_changed')
    inside=lambda name:name==prefix or name.startswith(prefix+'/')
    files=[f.model_dump() for f in body.files if not inside(f.path)]
    dirs={d.path:d.model_dump() for d in body.directories if not inside(d.path)}
    for name,raw in new['contents'].items():
        if not inside(name):continue
        mode=captured[name].mode if name in captured else 0o666 if os.name=='nt' else 0o600
        files.append(dict(path=name,mode=mode,size=len(raw),sha256=digest_of(raw),data_b64=base64.b64encode(raw).decode('ascii')))
        for parent in PurePosixPath(name).parents:
            relative=parent.as_posix()
            if relative=='.':continue
            mode=captured_dirs[relative].mode if relative in captured_dirs else 0o777 if os.name=='nt' else 0o700
            dirs[relative]=dict(path=relative,mode=mode)
    value=dict(schema_version=1,capture_scope='derived_continuation_workspace',origin_state_digest=state_digest,
        materials_digest=new_materials['materials_digest'],workspace_backend=body.workspace_backend,filesystem_capture='files_and_directories',
        files=sorted(files,key=lambda f:f['path']),directories=sorted(dirs.values(),key=lambda d:d['path']))
    return ContinuationWorkspace.model_validate(dict(value,digest=digest_of(canonical(value))))
