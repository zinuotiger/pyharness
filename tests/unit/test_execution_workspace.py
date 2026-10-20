"""Complete workspace plans preserve captured bytes and bounded readable references."""
import hashlib,json
from types import SimpleNamespace
import pytest
from pyharness.application.workspace_store import canonical, digest_of


def materials(raw=None, path='input.txt'):
    raw = raw or ('中文🙂\r\n' * 10000).encode('utf-8')
    bundle=dict(schema_version=1, attempt_ref={'attempt_id':'ra-'+'1'*16},
        task=dict(title='task', objective='Read the complete captured material.', acceptance_criteria=['cite the source']),
        input_binding={}, files=[dict(path=path, content_digest=digest_of(raw), size_bytes=len(raw), applied_mode=384,
        text=raw.decode('utf-8'))], deletions=[], documents=[], citations=[], delivery=None)
    return dict(bundle=bundle, materials_digest=digest_of(canonical(bundle)), layout='workspace', prompt=None)


def decode_section(plan, section):
    parts=[plan['contents'][section['prefix']+f'{n:04d}.txt'] for n in range(section['count'])]
    assert all(len(part)<=32768 for part in parts)
    for part in parts:part.decode('utf-8')
    body=b''.join(parts)
    assert hashlib.sha256(body).hexdigest()==section['sha256']
    return json.loads(body)


def test_complete_bundle_and_original_native_bytes_are_materialized_without_truncation():
    from pyharness.application.execution_workspace import workspace_plan, execution_prompt
    value=materials();plan=workspace_plan(value)
    assert plan['contents']['input.txt']==value['bundle']['files'][0]['text'].encode('utf-8')
    index=json.loads(plan['contents'][plan['index_path']])
    assert decode_section(plan,index['bundle'])==value['bundle']
    assert decode_section(plan,index['task'])==value['bundle']['task']
    assert len(execution_prompt(value))<32000
    assert plan['index_path'] in execution_prompt(value)
    assert plan['total_bytes']==sum(len(b) for b in plan['contents'].values())


def test_context_namespace_avoids_explicit_input_directory_case_insensitively():
    from pyharness.application.execution_workspace import workspace_plan
    value=materials(path='_PYHARNESS_CONTEXT/original.txt')
    plan=workspace_plan(value)
    assert plan['context_directory'].casefold()!='_pyharness_context'
    assert plan['contents']['_PYHARNESS_CONTEXT/original.txt']==value['bundle']['files'][0]['text'].encode()
    assert len({p.casefold() for p in plan['contents']})==len(plan['contents'])


def test_exact_tree_primitives_admit_declared_context_files_but_reject_extras(tmp_path):
    from pyharness.application.execution_workspace import workspace_plan
    from pyharness.application.workspace_materialization import copy_attempt_inputs,verify_attempt_inputs
    from pyharness.errors import PyHError
    plan=workspace_plan(materials(raw=b'\xef\xbb\xbfcaptured\r\n'))
    copy_attempt_inputs(tmp_path,plan['files'],lambda i:plan['contents'][i['path']],lambda:None)
    assert (tmp_path/'input.txt').read_bytes()==b'\xef\xbb\xbfcaptured\r\n'
    verify_attempt_inputs(tmp_path,plan['files'],lambda:None)
    (tmp_path/'unbound.txt').write_text('extra',encoding='utf-8')
    with pytest.raises(PyHError):verify_attempt_inputs(tmp_path,plan['files'],lambda:None)


def test_prepared_permission_step_keeps_checked_file_modes_and_normal_copy_keeps_old_behavior():
    from pyharness.core.sandbox_creation import prepare_permissions
    class Node:
        def __init__(self,directory,mode):self.directory=directory;self.mode=mode;self.children=[]
        def is_dir(self):return self.directory
        def chmod(self,mode):self.mode=mode
        def rglob(self,_):return iter(self.children)
    root=Node(True,0o755);file=Node(False,0o755);root.children=[file]
    api=SimpleNamespace(os=SimpleNamespace(name='posix',getuid=lambda:1000))
    prepare_permissions(api,root,prepared=True)
    assert root.mode==0o700 and file.mode==0o755
    prepare_permissions(api,root,prepared=False)
    assert file.mode==0o600
