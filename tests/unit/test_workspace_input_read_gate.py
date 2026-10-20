"""Given a grant changes after open, when first bytes would read, then reject first."""
import os
from datetime import datetime,timezone
from pathlib import Path
import pytest
from tests.unit.test_source_grants import grant_app,PID,GID,URL,issue
from tests.unit.test_workspace_git_preview import git_repo
from tests.unit.test_workspace_snapshots import PREVIEW,selection,setup


@pytest.mark.controlled_process
@pytest.mark.parametrize('boundary',['expiry','role','role-during-root'])
@pytest.mark.parametrize('route',['workspace','legacy','git-preview'])
async def test_workspace_first_source_byte_rechecks_authority(git_repo,monkeypatch,boundary,route):
    app,client,root=git_repo;await setup(client,root)
    from pyharness.application import snapshot_store
    from pyharness.errors import raise_code
    source=root/'unchanged.txt';reads=[];crossed=False
    original_open=os.open;original_fdopen=os.fdopen;original_auth=app.service.projects._authorize
    original_root=snapshot_store.check_root_metadata;revoked=False
    class Future(datetime):
        @classmethod
        def now(cls,tz=None):return datetime(2099,1,1,tzinfo=timezone.utc)
    def opened(name,*args,**kwargs):
        nonlocal crossed
        fd=original_open(name,*args,**kwargs)
        if Path(name)==source:
            crossed=True
            if boundary=='expiry':monkeypatch.setattr(snapshot_store,'datetime',Future)
        return fd
    def authorize(*args,**kwargs):
        if (crossed and boundary=='role') or revoked:raise_code('GRD-403',reason='project_forbidden')
        return original_auth(*args,**kwargs)
    def root_check(*args,**kwargs):
        nonlocal revoked
        result=original_root(*args,**kwargs)
        if crossed and boundary=='role-during-root':revoked=True
        return result
    class Stream:
        def __init__(self,value):self.value=value
        def __getattr__(self,name):return getattr(self.value,name)
        def __enter__(self):self.value.__enter__();return self
        def __exit__(self,*args):return self.value.__exit__(*args)
        def read(self,*args):
            if Path(self.value.name) == source if isinstance(self.value.name,(str,bytes)) else os.fstat(self.value.fileno()).st_ino==source.stat().st_ino:reads.append(True)
            return self.value.read(*args)
    monkeypatch.setattr(os,'open',opened)
    monkeypatch.setattr(os,'fdopen',lambda *args,**kwargs:Stream(original_fdopen(*args,**kwargs)))
    monkeypatch.setattr(app.service.projects,'_authorize',authorize)
    monkeypatch.setattr(snapshot_store,'check_root_metadata',root_check)
    before=app.service.projects.store.log_path.read_bytes()
    url=PREVIEW if route=='workspace' else f'{URL}/{GID}/'+('git-source-previews' if route=='git-preview' else 'snapshot-previews')
    body=selection() if route=='workspace' else {'schema_version':1,'expected_revision':1,'paths':['unchanged.txt','delete.txt']}
    response=await client.post(url,json=body)
    assert response.status_code==403 and crossed,response.text
    assert reads==[],'authority changed before first source byte, but stream.read still ran'
    assert app.service.projects.store.log_path.read_bytes()==before


def test_source_descriptor_second_link_rejected_before_first_byte(tmp_path,monkeypatch):
    from pyharness.application.source_snapshot import read_selected
    from pyharness.errors import PyHError
    root=tmp_path/'synthetic-input';root.mkdir();source=root/'file.txt';source.write_bytes(b'synthetic bytes')
    outside=tmp_path/'synthetic-external-link';reads=[];original_open=os.open;original_fdopen=os.fdopen
    def opened(name,*args,**kwargs):
        if Path(name)==source:os.link(source,outside)
        return original_open(name,*args,**kwargs)
    class Stream:
        def __init__(self,value):self.value=value
        def __getattr__(self,name):return getattr(self.value,name)
        def __enter__(self):self.value.__enter__();return self
        def __exit__(self,*args):return self.value.__exit__(*args)
        def read(self,*args):reads.append(True);return self.value.read(*args)
    monkeypatch.setattr(os,'open',opened);monkeypatch.setattr(os,'fdopen',lambda *a,**kw:Stream(original_fdopen(*a,**kw)))
    error=None
    try:read_selected(root,'file.txt')
    except PyHError as exc:error=exc
    assert reads==[],'same-inode second-link metadata change was read before rejection'
    assert error is not None and error.code=='EVT-101'


def test_trusted_before_read_preserves_budget_error(tmp_path):
    from pyharness.application.source_snapshot import read_selected
    from pyharness.errors import PyHError,raise_code
    root=tmp_path/'synthetic-read';root.mkdir();(root/'file.txt').write_bytes(b'synthetic bytes')
    def gate():raise_code('EVT-104',reason='workspace_capture_time_limit')
    with pytest.raises(PyHError) as exc:read_selected(root,'file.txt',before_read=gate)
    assert exc.value.code=='EVT-104' and exc.value.ctx['reason']=='workspace_capture_time_limit'


def test_descriptor_is_rechecked_after_blocking_authority_callback(tmp_path,monkeypatch):
    from pyharness.application.source_snapshot import read_selected
    from pyharness.errors import PyHError
    root=tmp_path/'synthetic-source';root.mkdir();source=root/'file.txt';source.write_bytes(b'synthetic bytes')
    outside=tmp_path/'synthetic-second-link';original=os.fdopen;reads=[]
    class Stream:
        def __init__(self,value):self.value=value
        def __getattr__(self,name):return getattr(self.value,name)
        def __enter__(self):self.value.__enter__();return self
        def __exit__(self,*args):return self.value.__exit__(*args)
        def read(self,*args):reads.append(True);return self.value.read(*args)
    monkeypatch.setattr(os,'fdopen',lambda *a,**kw:Stream(original(*a,**kw)))
    with pytest.raises(PyHError):read_selected(root,'file.txt',before_read=lambda:os.link(source,outside))
    assert reads==[],'descriptor changed during authority metadata I/O and was read before recheck'


@pytest.mark.controlled_process
@pytest.mark.parametrize('route',['workspace','git-preview'])
async def test_git_blob_process_never_starts_after_authority_loss(git_repo,monkeypatch,route):
    from pyharness.application import workspace_git
    from pyharness.errors import raise_code
    from tests.unit.test_workspace_git_preview import PREVIEW as GIT_PREVIEW,body
    app,client,root=git_repo;await setup(client,root);original=workspace_git._git
    original_auth=app.service.projects._authorize;revoked=False;blobs=[]
    async def read(*args,**kwargs):
        nonlocal revoked
        argv=args[3]
        if argv[0]=='cat-file':blobs.append(argv)
        result=await original(*args,**kwargs)
        if argv[0]=='ls-files':revoked=True
        return result
    def authorize(*args,**kwargs):
        if revoked:raise_code('GRD-403',reason='project_forbidden')
        return original_auth(*args,**kwargs)
    monkeypatch.setattr(workspace_git,'_git',read);monkeypatch.setattr(app.service.projects,'_authorize',authorize)
    before=app.service.projects.store.log_path.read_bytes()
    response=await client.post(PREVIEW if route=='workspace' else GIT_PREVIEW,json=selection() if route=='workspace' else body(paths=['unchanged.txt','delete.txt']))
    assert response.status_code==403 and revoked and blobs==[],response.text
    assert app.service.projects.store.log_path.read_bytes()==before
