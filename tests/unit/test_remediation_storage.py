import json
import os

import pytest

from pyharness import persistence as p
from pyharness.events import Envelope
from pyharness.errors import PyHError

SID = 's-remediation'


async def test_repair_close_failure_never_starts_rewrite(tmp_path, monkeypatch):
    from pyharness import repair
    store = p.open_store(SID, dir=tmp_path)
    real = store._fh
    called = []
    class BrokenClose:
        def __getattr__(self, name): return getattr(real, name)
        def close(self): raise OSError('primary close fault')
    async def rewrite(*a,**kw):
        called.append(True)
        return type('Report',(),{'fixed': [], 'quarantined':[], 'backup_path':None})()
    monkeypatch.setattr(repair, 'repair_session', rewrite)
    store._fh = BrokenClose()
    try:
        with pytest.raises(OSError, match='primary close fault'): await store.repair()
        assert called == []
        assert not real.closed
    finally:
        store._fh = real
        store.close()


def test_rewrite_preserves_primary_when_temporary_cleanup_fails(tmp_path, monkeypatch):
    from pathlib import Path
    store = p.open_store(SID, dir=tmp_path)
    replace, unlink = os.replace, Path.unlink
    def fail_replace(*a): raise OSError('primary replace fault')
    def fail_unlink(self,*a,**kw): raise OSError('secondary unlink fault')
    monkeypatch.setattr(os, 'replace', fail_replace)
    monkeypatch.setattr(Path, 'unlink', fail_unlink)
    try:
        with pytest.raises(PyHError) as error: store._rewrite_without_tail(0)
        assert 'primary replace fault' in str(error.value.ctx)
        assert any('cleanup' in n for n in error.value.__notes__)
    finally:
        monkeypatch.setattr(os, 'replace', replace)
        monkeypatch.setattr(Path, 'unlink', unlink)
        store.close()


def event(seq):
    return Envelope(seq=seq, ts='2026-09-24T00:00:00.000000Z', session_id=SID,
                    type='agent.message', actor='agent', payload={'content': f'中文 {seq}'})


def test_F03_close_consumes_only_own_lease(tmp_path):
    a = p.open_store(SID, dir=tmp_path)
    b = p.open_store(SID, dir=tmp_path)
    key = str(b._lock_path)
    try:
        a.close()
        a.close()
        assert p._LOCKS[key][1] == 1
        assert not b._fh.closed
    finally:
        a.close()
        b.close()


@pytest.mark.parametrize('failure_at', [1, 2])
def test_F10_factory_failure_releases_lease(tmp_path, monkeypatch, failure_at):
    original = p.detect_truncation
    calls = 0
    failure = OSError('synthetic tail read failure')
    def fail(path):
        nonlocal calls
        calls += 1
        if calls == failure_at:
            raise failure
        return original(path)
    before = dict(p._LOCKS)
    monkeypatch.setattr(p, 'detect_truncation', fail)
    with pytest.raises((OSError, PyHError)):
        p.open_store(SID, dir=tmp_path)
    assert p._LOCKS == before


class FaultFile:
    def __init__(self, fh, prefix, mode):
        self.fh, self.prefix, self.mode = fh, prefix, mode
        self.count = 0
        self.fired = False
    def __getattr__(self, name):
        return getattr(self.fh, name)
    def write(self, line):
        if not self.fired and self.count == self.prefix and self.mode in ('write', 'partial', 'newline'):
            self.fired = True
            if self.mode == 'partial':
                self.fh.write(line[:len(line)//2])
                self.fh.flush()
            if self.mode == 'newline':
                self.fh.write(line[:-1])
                self.fh.flush()
            raise OSError('injected write')
        self.count += 1
        return self.fh.write(line)
    def flush(self):
        if not self.fired and self.count and self.mode in ('flush-before', 'flush-after'):
            self.fired = True
            if self.mode == 'flush-after': self.fh.flush()
            raise OSError('injected flush')
        return self.fh.flush()


@pytest.mark.parametrize('prefix', [0, 1, 2])
@pytest.mark.parametrize('mode', ['write', 'partial', 'newline', 'flush-before', 'flush-after', 'fsync'])
async def test_F02_retry_preserves_exact_physical_events(tmp_path, monkeypatch, prefix, mode):
    store = p.open_store(SID, dir=tmp_path)
    real = store._fh
    fault = FaultFile(real, prefix, mode)
    store._fh = fault
    sync = os.fsync
    fired = []
    def fail_sync(fd):
        if mode == 'fsync' and not fired:
            fired.append(True)
            raise OSError('injected fsync')
        return sync(fd)
    monkeypatch.setattr(p.os, 'fsync', fail_sync)
    try:
        for seq in range(1, 4): await store.append(event(seq))
        with pytest.raises((OSError, PyHError)):
            await store.flush()
        await store.flush()
        first = store.path.read_bytes()
        await store.flush()
        assert store.path.read_bytes() == first
        complete = []
        for line in first.splitlines():
            try: complete.append(json.loads(line))
            except ValueError: pass  # damaged evidence stays; complete events never duplicate
        assert [e['seq'] for e in complete] == [1, 2, 3]
        assert [e['payload']['content'] for e in complete] == ['中文 1', '中文 2', '中文 3']
        if mode == 'partial': assert b'"seq":' in first
        if mode == 'fsync': assert fired == [True]
    finally:
        store._fh = real
        monkeypatch.setattr(p.os, 'fsync', sync)
        store.close()
    reopened = p.open_store(SID, dir=tmp_path)
    try:
        assert [e.seq for e in reopened.replay()] == [1, 2, 3]
    finally:
        reopened.close()


async def test_F02_close_commits_pending(tmp_path):
    store = p.open_store(SID, dir=tmp_path)
    await store.append(event(1))
    store.close()
    assert json.loads(store.path.read_text(encoding='utf-8'))['seq'] == 1


@pytest.mark.parametrize('entry', ['flush', '_flush_pending_all', '_flush_batch'])
async def test_F02_conflict_retains_pending(tmp_path, entry):
    store = p.open_store(SID, dir=tmp_path)
    try:
        await store.append(event(1), sync=True)
        other = event(1).model_copy(update={'payload': {'content':'conflict'}})
        await store.append(other)
        with pytest.raises(PyHError): await getattr(store, entry)()
        assert list(store._pending) + list(store._retry_q)
    finally:
        with pytest.raises(PyHError): store.close()


async def test_F02_invalid_envelope_does_not_block_valid_append(tmp_path):
    store = p.open_store(SID, dir=tmp_path)
    try:
        store._fh.write('{"seq":999}\n')
        store._fh.flush()
        await store.append(event(1), sync=True)
        assert [e.seq for e in store.replay()] == [1]
    finally: store.close()


async def test_F02_rewrite_invalidates_scan_cache(tmp_path):
    store = p.open_store(SID, dir=tmp_path)
    try:
        await store.append(event(1), sync=True)
        end = store.path.stat().st_size
        await store.append(event(2), sync=True)
        store._rewrite_without_tail(end)
        await store.append(event(2), sync=True)
        assert [e.seq for e in store.replay()] == [1, 2]
    finally: store.close()


async def test_F02_fsync_after_visible_batch_is_not_success_or_duplicate(tmp_path, monkeypatch):
    store = p.open_store(SID, dir=tmp_path)
    original = os.fsync
    fired = []
    def fault(fd):
        if fd == store._fh.fileno() and store.path.stat().st_size and not fired:
            fired.append(True)
            raise OSError('after-visible-write')
        return original(fd)
    try:
        for seq in range(1,4): await store.append(event(seq))
        monkeypatch.setattr(os, 'fsync', fault)
        with pytest.raises(PyHError): await store.flush()
        assert fired
        await store.flush()
        assert [json.loads(line)['seq'] for line in store.path.read_text(encoding='utf-8').splitlines()] == [1,2,3]
    finally:
        monkeypatch.setattr(os, 'fsync', original)
        store.close()
