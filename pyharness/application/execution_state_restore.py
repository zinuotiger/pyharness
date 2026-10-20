"""Internal fresh private-state preparation; never old journal or host writeback."""
import base64,os,stat
from pathlib import Path
from pydantic import TypeAdapter
from pyharness.application.execution_contracts import ExecutionSessionId
from pyharness.application.execution_state_capture import workspace_state,state_regular,StateUnavailable
from pyharness.application.workspace_store import digest_of
from pyharness.core.sandbox import safe_path
from pyharness.errors import raise_code


def root_identity(root):
    safe_path(root,'owned-state-check');info=root.lstat()
    if not stat.S_ISDIR(info.st_mode):raise_code('PERS-202',reason='continuation_destination_not_directory')
    return info.st_dev,info.st_ino


def write_new_state_file(root,name,raw,mode,gate):
    gate();target=safe_path(root,name)
    fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_BINARY',0),0o600)
    with os.fdopen(fd,'wb') as stream:
        opened=os.fstat(stream.fileno());current=safe_path(root,name).lstat()
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink!=1 or
                (opened.st_dev,opened.st_ino)!=(current.st_dev,current.st_ino)):
            raise_code('PERS-202',reason='continuation_destination_changed')
        gate()
        if stream.write(raw)!=len(raw):raise_code('PERS-202',reason='continuation_short_write')
        stream.flush();os.fsync(stream.fileno())
        if hasattr(os,'fchmod'):os.fchmod(stream.fileno(),mode)
        elif os.chmod in os.supports_fd:os.chmod(stream.fileno(),mode)
        elif stat.S_IMODE(os.fstat(stream.fileno()).st_mode)!=mode:
            raise_code('GRD-403',reason='continuation_file_mode_unsupported')
    # A path can now name a different inode or an outside hardlink. Never use
    # it for chmod after closing the exclusively created, bound descriptor.
    gate()
    actual,_=state_regular(safe_path(root,name),len(raw))
    if actual!=raw:raise_code('PERS-202',reason='continuation_state_write_unconfirmed')


def apply_directory_mode(root,name,mode,gate):
    gate();path=safe_path(root,name);before=path.lstat()
    if not stat.S_ISDIR(before.st_mode):raise_code('PERS-202',reason='continuation_directory_changed')
    if stat.S_IMODE(before.st_mode)==mode:return
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_DIRECTORY',0)|getattr(os,'O_NOFOLLOW',0))
    try:
        opened=os.fstat(fd);current=safe_path(root,name).lstat()
        if not stat.S_ISDIR(opened.st_mode) or (opened.st_dev,opened.st_ino)!=(current.st_dev,current.st_ino):
            raise_code('PERS-202',reason='continuation_directory_changed')
        gate()
        if hasattr(os,'fchmod'):os.fchmod(fd,mode)
        elif os.chmod in os.supports_fd:os.chmod(fd,mode)
        else:raise_code('GRD-403',reason='continuation_directory_mode_unsupported')
    finally:os.close(fd)
    gate()


def verify_restored_workspace(root,body,gate):
    gate();root=Path(root);identity=root_identity(root)
    if body.filesystem_capture!='files_and_directories':raise_code('GRD-403',reason='continuation_complete_filesystem_required')
    try:files,dirs=workspace_state(root)
    except (StateUnavailable,OSError):raise_code('PERS-202',reason='continuation_workspace_unconfirmed')
    expected={f.path:f.model_dump() for f in body.files};expected_dirs={d.path:d.model_dump() for d in body.directories}
    if ({f['path']:f for f in files}!=expected or {d['path']:d for d in dirs}!=expected_dirs or root_identity(root)!=identity):
        raise_code('PERS-202',reason='continuation_workspace_changed')
    gate()


def restore_state_workspace(root,body,gate):
    gate();root=Path(root);identity=root_identity(root)
    if body.filesystem_capture!='files_and_directories':raise_code('GRD-403',reason='continuation_complete_filesystem_required')
    if any(root.iterdir()):raise_code('GRD-403',reason='continuation_destination_not_empty')
    def checked():
        gate()
        if root_identity(root)!=identity:raise_code('PERS-202',reason='continuation_workspace_root_changed')
    try:
        for directory in sorted(body.directories,key=lambda d:(len(d.path.split('/')),d.path)):
            checked();safe_path(root,directory.path).mkdir(mode=0o700)
        for file in body.files:
            checked();raw=base64.b64decode(file.data_b64,validate=True)
            if digest_of(raw)!=file.sha256 or len(raw)!=file.size:raise_code('PERS-202',reason='continuation_state_bytes_changed')
            write_new_state_file(root,file.path,raw,file.mode,checked)
        for directory in sorted(body.directories,key=lambda d:(-len(d.path.split('/')),d.path)):
            apply_directory_mode(root,directory.path,directory.mode,checked)
        checked();verify_restored_workspace(root,body,checked)
    except (StateUnavailable,OSError):raise_code('PERS-202',reason='continuation_workspace_restore_failed')


def restore_state_kv(directory,sid,body,gate):
    gate();TypeAdapter(ExecutionSessionId).validate_python(sid,strict=True)
    if sid==body.session_id:raise_code('GRD-403',reason='continuation_requires_new_session')
    directory=Path(directory);safe_path(directory,sid+'.kv.json');identity=root_identity(directory)
    def checked():
        gate()
        if root_identity(directory)!=identity:raise_code('PERS-202',reason='continuation_kv_root_changed')
    path=safe_path(directory,sid+'.kv.json')
    if path.exists():raise_code('GRD-403',reason='continuation_kv_namespace_exists')
    if body.kv_present:
        try:write_new_state_file(directory,path.name,base64.b64decode(body.kv_data_b64,validate=True),0o600,checked)
        except (StateUnavailable,OSError):raise_code('PERS-202',reason='continuation_kv_restore_failed')
    checked()
