"""Exact ordinary-file copies into an already owned empty workspace; no host fallback."""
import os,stat
from pathlib import Path
from pyharness.core.sandbox import safe_path
from pyharness.application.source_snapshot import read_selected
from pyharness.errors import raise_code


def _directories(files):
    return _directories_bounded(files,10000,2*1024*1024)


def _directories_bounded(files,max_dirs,max_bytes):
    dirs=set()
    for item in files:
        parts=item['path'].split('/')
        for count in range(1,len(parts)):dirs.add('/'.join(parts[:count]))
    if len(dirs)>max_dirs or sum(len(p.encode('utf-8')) for p in dirs)>max_bytes:
        raise_code('EVT-100',reason='workspace_input_directory_limit')
    return dirs


def verify_inputs(root,files,gate):
    return _verify_tree(root,files,gate,max_dirs=10000,max_dir_bytes=2*1024*1024,max_entries=10200)


def verify_attempt_inputs(root,files,gate):
    return _verify_tree(root,files,gate,max_dirs=10001,max_dir_bytes=2*1024*1024+1024,max_entries=15000)


def _verify_tree(root,files,gate,*,max_dirs,max_dir_bytes,max_entries):
    gate();root=Path(root);expected={i['path']:i for i in files};dirs=_directories_bounded(files,max_dirs,max_dir_bytes)
    # safe_path rejects a changed root or any component link before walking.
    safe_path(root,'owned-check')
    observed=set();observed_dirs=set();count=0
    for parent,names,entries in os.walk(root,followlinks=False):
        for name in [*names,*entries]:
            count+=1
            if count>max_entries:raise_code('PERS-202',reason='workspace_input_tree_limit')
            path=Path(parent)/name;relative=path.relative_to(root).as_posix();safe_path(root,relative)
            info=path.lstat()
            if stat.S_ISDIR(info.st_mode):observed_dirs.add(relative)
            elif stat.S_ISREG(info.st_mode) and info.st_nlink==1:observed.add(relative)
            else:raise_code('PERS-202',reason='workspace_input_tree_changed')
    if observed!=set(expected) or observed_dirs!=dirs:raise_code('PERS-202',reason='workspace_input_set_changed')
    for name,item in expected.items():
        metadata,raw=read_selected(root,name,before_read=gate)
        if metadata.sha256!=item['content_digest'] or len(raw)!=item['size_bytes']:
            raise_code('PERS-202',reason='workspace_input_content_changed')
        if os.name!='nt' and metadata.mode!=item['applied_mode']:
            raise_code('PERS-202',reason='workspace_input_mode_changed')
    gate()


def copy_inputs(root,files,reader,gate):
    try:_copy_inputs(root,files,reader,gate)
    except OSError:raise_code('PERS-202',reason='workspace_input_copy_unavailable')


def _copy_inputs(root,files,reader,gate):
    return _copy_tree(root,files,reader,gate,attempt=False)


def copy_attempt_inputs(root,files,reader,gate):
    try:return _copy_tree(root,files,reader,gate,attempt=True)
    except OSError:raise_code('PERS-202',reason='workspace_input_copy_unavailable')


def _copy_tree(root,files,reader,gate,*,attempt):
    root=Path(root);gate();safe_path(root,'owned-check')
    if not root.is_dir() or any(root.iterdir()):raise_code('GRD-403',reason='workspace_input_destination_not_empty')
    _directories_bounded(files,10001,2*1024*1024+1024) if attempt else _directories(files)
    identity=(root.stat().st_dev,root.stat().st_ino)
    for item in files:
        gate();raw=reader(item);gate()
        if identity!=(root.stat().st_dev,root.stat().st_ino):raise_code('PERS-202',reason='workspace_input_root_changed')
        target=safe_path(root,item['path']);target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        target=safe_path(root,item['path'])
        descriptor=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_BINARY',0),0o600)
        with os.fdopen(descriptor,'wb') as stream:
            opened=os.fstat(stream.fileno());current=safe_path(root,item['path']).lstat()
            if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink!=1
                or (opened.st_dev,opened.st_ino)!=(current.st_dev,current.st_ino)):
                raise_code('PERS-202',reason='workspace_input_destination_changed')
            gate()
            if hasattr(os,'fchmod'):os.fchmod(stream.fileno(),item['applied_mode'])
            if stream.write(raw)!=len(raw):raise_code('PERS-202',reason='workspace_input_short_write')
            stream.flush();os.fsync(stream.fileno())
    (verify_attempt_inputs if attempt else verify_inputs)(root,files,gate)
