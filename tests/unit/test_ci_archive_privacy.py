"""Published source archives must exclude local coding state and test caches."""
import io
import tarfile

import pytest

from scripts.ci_verify import check_archives


def source_archive(tmp_path, member):
    path = tmp_path / 'pyharness-0.1.0.tar.gz'
    body = b'Synthetic archive boundary test; no real private data.'
    with tarfile.open(path, 'w:gz') as archive:
        info = tarfile.TarInfo('pyharness-0.1.0/' + member)
        info.size = len(body)
        archive.addfile(info, io.BytesIO(body))
    return path


@pytest.mark.parametrize('member', [
    '.ai-coding/TASK-private.md',
    '.ai-coding/evidence/private-snapshot.json',
    '.hypothesis/constants/private-cache',
])
def test_public_archive_rejects_private_coding_state_and_cache(tmp_path, member):
    with pytest.raises(SystemExit, match='Private coding state|Forbidden generated'):
        check_archives([source_archive(tmp_path, member)])


@pytest.mark.parametrize('member', [
    '.ai-coding/PROTOCOL.md', '.ai-coding/TASK-TEMPLATE.md',
])
def test_public_coding_protocol_and_template_remain_distributable(tmp_path, member):
    path = source_archive(tmp_path, member)
    result = check_archives([path])
    assert set(result) == {path.name} and len(result[path.name]) == 64
