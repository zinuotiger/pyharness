"""Three-way merge preserves user edits and never executes validators."""
import pytest


def merge(base,current,delivery):
    from pyharness.application.writeback_merge import merge_bytes
    return merge_bytes(base,current,delivery)


@pytest.mark.parametrize('base,current,delivery,expected',[
    (b'old\r\n',b'old\r\n',b'new\r\n',b'new\r\n'),
    (b'old\n',b'user\n',b'old\n',b'user\n'),
    (b'old',b'same',b'same',b'same'),
    (None,None,b'created',b'created'),
    (b'deleted',b'deleted',None,None),
    (b'deleted',None,None,None),
    (None,b'user created',None,b'user created'),
    (b'\xef\xbb\xbfa\r\nb\r\nc\r\n',b'\xef\xbb\xbfA\r\nb\r\nc\r\n',b'\xef\xbb\xbfa\r\nb\r\nC\r\n',b'\xef\xbb\xbfA\r\nb\r\nC\r\n'),
    (b'a\nb\nc\n',b'A\nb\nc\n',b'a\nB\nc\n',b'A\nB\nc\n'),
    (b'a\nb\n',b'insert\na\nb\n',b'insert\na\nb\n',b'insert\na\nb\n'),
])
def test_merge_exact_versions_without_discarding_current_edits(base,current,delivery,expected):
    result=merge(base,current,delivery)
    assert result['status']=='mergeable' and result['merged']==expected and not result['conflicts']


@pytest.mark.parametrize('base,current,delivery',[
    (b'old',b'user',b'agent'),
    (b'a\nb\n',b'x\na\nb\n',b'y\na\nb\n'),
    (b'old',b'user',None),
    (None,b'user created',b'agent created'),
    (b'\xff\x00',b'\xfe\x00',b'\xfd\x00'),
])
def test_ambiguous_changes_are_conflicts_without_a_writable_result(base,current,delivery):
    result=merge(base,current,delivery)
    assert result['status']=='conflict' and result['merged'] is None and result['conflicts']


def test_fixed_validator_does_not_execute_python(tmp_path):
    from pyharness.application.writeback_merge import validate_result
    marker=tmp_path/'validator-must-not-execute'
    raw=('from pathlib import Path\nPath('+repr(str(marker))+').write_text("executed")\n').encode()
    assert validate_result('result.py',raw)['status']=='passed'
    assert not marker.exists()
    assert validate_result('result.py',b'def broken(:\n')['status']=='failed'
    assert validate_result('result.json',b'{broken}')['status']=='failed'
    assert validate_result('result.json',b'{"value":1}')['status']=='passed'


def test_fixed_validator_rejects_secret_material_without_returning_it():
    from pyharness.application.writeback_merge import validate_result
    raw=b'password = synthetic-private-value\n'
    result=validate_result('notes.txt',raw)
    assert result['status']=='failed' and 'synthetic-private-value' not in str(result)


def test_large_python_is_refused_before_parser_allocation(monkeypatch):
    import pyharness.application.writeback_merge as module
    from types import SimpleNamespace
    def must_not_parse(*args,**kwargs):raise AssertionError('oversized syntax input reached AST parser')
    monkeypatch.setattr(module,'ast',SimpleNamespace(parse=must_not_parse))
    result=module.validate_result('large.py',b'x=1\n'*33000)
    assert result['status']=='failed'


def test_large_json_is_refused_before_building_an_unbounded_value_tree():
    from pyharness.application.writeback_merge import validate_result
    result=validate_result('large.json',b'['+b'0,'*66000+b'0]')
    assert result['status']=='failed'
