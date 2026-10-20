"""Given corrupt own Git metadata, no claimed HEAD identity may be accepted."""
import zlib
import pytest
from tests.unit.test_source_grants import grant_app
from tests.unit.test_workspace_git_preview import git_repo,git
from tests.unit.test_workspace_snapshots import setup,selection,PREVIEW


@pytest.mark.controlled_process
@pytest.mark.parametrize('kind',['commit','tree'])
async def test_git_object_under_wrong_oid_never_authorizes_capture(git_repo,kind):
    app,client,root=git_repo
    oid=git(root,'rev-parse','HEAD' if kind=='commit' else 'HEAD^{tree}').strip().decode()
    raw=git(root,'cat-file',kind,oid)
    if kind=='commit':
        modified=raw.replace(b'synthetic fixture seed',b'synthetic fixture evil')
    else:
        (root/'forged.txt').write_bytes(b'synthetic forged HEAD bytes\r\n')
        forged=git(root,'hash-object','-w','--','forged.txt').strip().decode()
        previous=git(root,'rev-parse','HEAD:unchanged.txt').strip().decode()
        modified=raw.replace(bytes.fromhex(previous),bytes.fromhex(forged))
    assert modified!=raw
    obj=root/'.git/objects'/oid[:2]/oid[2:]
    obj.chmod(0o600)  # only this fresh synthetic loose object
    obj.write_bytes(zlib.compress(kind.encode()+b' '+str(len(modified)).encode()+b'\0'+modified))
    await setup(client,root)
    before=app.service.projects.store.log_path.read_bytes()
    response=await client.post(PREVIEW,json=selection())
    assert response.status_code in (403,409)
    assert app.service.projects.store.log_path.read_bytes()==before
