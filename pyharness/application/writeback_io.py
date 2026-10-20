"""Exclusive Windows file handles; no unsafe application backend fallback."""
import os
import stat
from contextlib import ExitStack
from functools import lru_cache
from pathlib import Path
from pyharness.application.source_snapshot import LIMIT,SnapshotFile,identity,read_selected
from pyharness.core.sandbox import safe_path
from pyharness.errors import PyHError,raise_code


def observe_target(root,name,gate):
    path=safe_path(root,name)
    if not path.parent.is_dir():raise_code('GRD-403',reason='writeback_parent_unavailable')
    try:path.lstat()
    except FileNotFoundError:
        gate()
        try:safe_path(root,name).lstat()
        except FileNotFoundError:return None,None
        raise_code('EVT-101',reason='writeback_target_changed')
    return read_selected(root,name,before_read=gate)


def target_metadata(value):
    item=value.model_dump() if hasattr(value,'model_dump') else dict(value)
    return dict(item,device=str(item['device']),inode=str(item['inode']))


@lru_cache(maxsize=1)
def _api():
    import ctypes
    from ctypes import wintypes as t
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[t.LPCWSTR,t.DWORD,t.DWORD,t.LPVOID,t.DWORD,t.DWORD,t.HANDLE]
    kernel.CreateFileW.restype=t.HANDLE
    kernel.CloseHandle.argtypes=[t.HANDLE];kernel.CloseHandle.restype=t.BOOL
    kernel.SetFileInformationByHandle.argtypes=[t.HANDLE,ctypes.c_int,t.LPVOID,t.DWORD]
    kernel.SetFileInformationByHandle.restype=t.BOOL
    class Information(ctypes.Structure):
        _fields_=[('attributes',t.DWORD),('created',t.FILETIME),('accessed',t.FILETIME),('written',t.FILETIME),
            ('volume',t.DWORD),('size_hi',t.DWORD),('size_lo',t.DWORD),('links',t.DWORD),('index_hi',t.DWORD),('index_lo',t.DWORD)]
    kernel.GetFileInformationByHandle.argtypes=[t.HANDLE,ctypes.POINTER(Information)]
    kernel.GetFileInformationByHandle.restype=t.BOOL
    return kernel,Information


def _open_handle(path,*,directory=False,create=False):
    import ctypes
    kernel,Info=_api()
    access=0x80 if directory else 0x80000000|0x40000000|0x10000
    flags=0x00200000|(0x02000000 if directory else 0x80)
    handle=kernel.CreateFileW(str(path),access,3 if directory else 0,None,1 if create else 3,flags,None)
    if handle==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
    try:
        info=Info()
        if not kernel.GetFileInformationByHandle(handle,ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.attributes&0x400 or not directory and (info.attributes&0x10 or info.links!=1):
            raise_code('GRD-403',reason='writeback_link_or_nonregular_target')
    except BaseException:kernel.CloseHandle(handle);raise
    return handle


def _open_stream(path,*,create=False):
    import msvcrt
    handle=_open_handle(path,create=create)
    try:fd=msvcrt.open_osfhandle(handle,os.O_RDWR|os.O_BINARY)
    except BaseException:_api()[0].CloseHandle(handle);raise
    try:
        os.set_inheritable(fd,False)
        return os.fdopen(fd,'r+b',buffering=0)
    except BaseException:os.close(fd);raise


def _metadata(name,info,raw):
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or not info.st_ino or len(raw)>LIMIT:
        raise_code('GRD-403',reason='writeback_nonregular_target')
    import hashlib
    return SnapshotFile(path=name,size_bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),
        mtime_ns=str(info.st_mtime_ns),mode=stat.S_IMODE(info.st_mode),device=info.st_dev,inode=info.st_ino)


class LockedTargets:
    """All existing targets checked before intent; fresh names use CREATE_NEW only."""
    def __init__(self,root,files,gate):
        self.root=Path(root);self.files=files;self.gate=gate;self.streams={};self.deleted=set();self.stack=ExitStack()

    def __enter__(self):
        if os.name!='nt':raise_code('GRD-403',reason='writeback_application_backend_unavailable')
        try:
            parents={self.root}
            for item in self.files:
                path=safe_path(self.root,item['target_path'])
                if not path.parent.is_dir():raise_code('GRD-403',reason='writeback_parent_unavailable')
                parent=path.parent
                while parent!=self.root:parents.add(parent);parent=parent.parent
            for parent in sorted(parents,key=lambda p:str(p).casefold()):
                self.gate();handle=_open_handle(parent,directory=True)
                self.stack.callback(_api()[0].CloseHandle,handle)
            for item in self.files:
                name=item['target_path'];self.gate();path=safe_path(self.root,name)
                if item['target'] is None:
                    try:path.lstat()
                    except FileNotFoundError:continue
                    raise_code('EVT-101',reason='writeback_target_changed')
                try:stream=self.stack.enter_context(_open_stream(path))
                except OSError:raise_code('EVT-101',reason='writeback_target_changed_or_busy')
                self.streams[name]=stream
                observed=self.read(name,metadata=True)
                if target_metadata(observed)!=target_metadata(item['target']):
                    raise_code('EVT-101',reason='writeback_target_changed')
            self.gate();return self
        except BaseException:self.stack.close();raise

    def __exit__(self,*args):return self.stack.__exit__(*args)

    def read(self,name,*,metadata=False):
        stream=self.streams.get(name);path=safe_path(self.root,name)
        if stream is None or stream.closed:
            if name not in self.deleted:raise_code('PERS-202',reason='writeback_handle_unavailable')
            try:path.lstat()
            except FileNotFoundError:return None
            raise_code('EVT-101',reason='writeback_deleted_target_reappeared')
        before=os.fstat(stream.fileno());current=path.lstat()
        if identity(before)!=identity(current):raise_code('EVT-101',reason='writeback_target_identity_changed')
        stream.seek(0);raw=stream.read(LIMIT+1);after=os.fstat(stream.fileno())
        if identity(before)!=identity(after) or len(raw)!=after.st_size:
            raise_code('EVT-101',reason='writeback_target_changed')
        info=_metadata(name,after,raw)
        return info if metadata else raw


def apply_one(locked,item,raw):
    """Called only after a strongly confirmed application intent."""
    import ctypes,msvcrt
    name=item['target_path'];action=item['action'];locked.gate()
    if action=='none':return locked.read(name) if item['target'] is not None else None
    path=safe_path(locked.root,name)
    if action=='create':
        try:stream=locked.stack.enter_context(_open_stream(path,create=True))
        except OSError:raise_code('EVT-101',reason='writeback_create_target_changed_or_busy')
        locked.streams[name]=stream
    else:stream=locked.streams[name]
    locked.gate()
    if action=='delete':
        flag=ctypes.c_int(1)
        if not _api()[0].SetFileInformationByHandle(msvcrt.get_osfhandle(stream.fileno()),4,ctypes.byref(flag),ctypes.sizeof(flag)):
            raise ctypes.WinError(ctypes.get_last_error())
        stream.close();locked.deleted.add(name)
        return locked.read(name)
    if raw is None:raise_code('PERS-202',reason='writeback_result_bytes_missing')
    stream.seek(0);view=memoryview(raw)
    while view:
        count=stream.write(view)
        if not count:raise OSError('writeback short write')
        view=view[count:]
    stream.truncate(len(raw));stream.flush();os.fsync(stream.fileno());locked.gate()
    return locked.read(name)
