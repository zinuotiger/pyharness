"""Loaded view identity, explicit freeze, and actual filesystem escape checks."""
import hashlib
import os
import subprocess
from pathlib import Path
import pytest
from pyharness.core.skill import SkillManager


def write_skill(root,body='original'):
    target=root/'sample'/'SKILL.md'
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text('---\nname: sample\ndescription: sample skill\n---\n'+body,encoding='utf-8')
    return target


def test_fixed_snapshot_and_classic_explicit_reload(tmp_path):
    path=write_skill(tmp_path,'x'*7000)
    classic=SkillManager([tmp_path])
    frozen=classic.frozen_copy(['sample'])
    before=frozen.snapshot('sample')
    assert before['source_sha256']==hashlib.sha256(path.read_bytes()).hexdigest()
    assert before['truncated'] is True and before['body']==classic.load('sample')['body']
    write_skill(tmp_path,'changed')
    assert frozen.scan()==1 and frozen.snapshot('sample')==before
    classic.scan()
    assert classic.load('sample')['body']=='changed'
    assert classic.snapshot('sample')['loaded_sha256']!=before['loaded_sha256']
    assert frozen.list()==[{'name':'sample','description':'sample skill'}]


@pytest.mark.controlled_process
def test_real_directory_link_escape_is_not_read(tmp_path):
    inside=tmp_path/'inside';inside.mkdir()
    outside=tmp_path/'outside';write_skill(outside,'private outside content')
    link=inside/'escape'
    if os.name=='nt':
        result=subprocess.run(['cmd.exe','/d','/c','mklink','/J',str(link),str(outside/'sample')],
            capture_output=True,creationflags=0x08000000,timeout=10)
        assert result.returncode==0,result.stderr
    else:
        link.symlink_to(outside/'sample',target_is_directory=True)
    assert link.resolve()==(outside/'sample').resolve()
    assert SkillManager([inside]).list()==[]
    assert (outside/'sample'/'SKILL.md').read_text().endswith('private outside content')
    # Remove the link only, with a single native API. Never recurse its target.
    if os.name=='nt':os.rmdir(link)
    else:link.unlink()


def test_malformed_utf8_does_not_break_other_skills(tmp_path):
    write_skill(tmp_path)
    bad=tmp_path/'bad'/'SKILL.md';bad.parent.mkdir();bad.write_bytes(b'\xff\xfe')
    assert [s['name'] for s in SkillManager([tmp_path]).list()]==['sample']


def test_physical_crlf_identity_preserves_original_normalized_load_view(tmp_path):
    path=write_skill(tmp_path)
    raw=b'---\r\nname: sample\r\ndescription: sample\r\n---\r\nline one\r\nline two\r\n'
    path.write_bytes(raw)
    mgr=SkillManager([tmp_path])
    assert mgr.load('sample')['body']=='line one\nline two'
    assert mgr.snapshot('sample')['source_sha256']==hashlib.sha256(raw).hexdigest()
