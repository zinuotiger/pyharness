"""R02 acceptance on a real, non-root POSIX filesystem.

Only the injected failure is synthetic: file creation, writes, stat, replace,
locks, and permission checks use the operating system.  Windows deliberately
skips this module; its syscall-order regression remains in
test_remediation_credentials.py.  No model or network request is made here.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import stat
from pathlib import Path

import pytest

from pyharness.core import tenant_settings as ts
from pyharness.errors import PyHError


pytestmark = pytest.mark.skipif(os.name != "posix", reason="requires real POSIX permission bits")


@pytest.fixture(autouse=True)
def non_root_posix_umask(record_property):
    """Fail, rather than pass misleading permission tests, when run as root."""
    assert os.geteuid() != 0, "R02 acceptance requires a non-root POSIX process"
    previous = os.umask(0o022)
    try:
        record_property("r02_euid", os.geteuid())
        record_property("r02_umask", "022")
        record_property("r02_stat", "real POSIX filesystem")
        yield
    finally:
        os.umask(previous)


def _body(secret: str) -> dict:
    return {
        "id": "synthetic-profile",
        "model": "synthetic-not-called",
        "base_url": "http://127.0.0.1:9",
        "api_key": secret,
    }


def _same_secret(actual: str, expected: str) -> None:
    # A failed assertion must not render either secret in pytest/JUnit output.
    matches = hmac.compare_digest(actual, expected)
    assert matches, "secret round-trip mismatch"


def _no_secret_output(caplog, capsys, *values: str) -> None:
    captured = capsys.readouterr()
    output = caplog.text + captured.out + captured.err
    leaked = any(value in output for value in values)
    assert not leaked, "synthetic secret appeared in output"


def _generation(store: ts.TenantSettingsStore) -> Path:
    return store._secret_path("tenant", store._load_doc("tenant"))


def _assert_no_private_temps(root: Path) -> None:
    leftovers = list(root.rglob(".model-secrets-*.bin-*")) + list(root.rglob(".models.json-*"))
    assert not leftovers, "private temporary files remain"


def _assert_old_commit(store, metadata_before: bytes, old: Path, old_digest: bytes) -> None:
    metadata = store.tenant_dir("tenant") / "models.json"
    assert metadata.read_bytes() == metadata_before
    assert _generation(store) == old
    assert hashlib.sha256(old.read_bytes()).digest() == old_digest
    assert list(old.parent.glob("model-secrets-*.bin")) == [old]
    assert stat.S_IMODE(old.stat().st_mode) == 0o600
    _assert_no_private_temps(store.root)


def _seed(tmp_path):
    store = ts.TenantSettingsStore(tmp_path / "tenants")
    store.upsert_profile("tenant", _body("synthetic-r02-original"))
    old = _generation(store)
    metadata_before = (old.parent / "models.json").read_bytes()
    return store, metadata_before, old, hashlib.sha256(old.read_bytes()).digest()


def test_creation_and_every_write_are_private_before_atomic_replace(tmp_path, monkeypatch, caplog, capsys):
    """Given umask 022, every observed descriptor is 0600 before its first byte."""
    store = ts.TenantSettingsStore(tmp_path / "tenants")
    opened = []
    live = {}
    writes = []
    replacements = []
    real_open, real_write, real_replace = os.open, os.write, os.replace

    def observe_open(path, flags, mode=0o777, *, dir_fd=None):
        fd = real_open(path, flags, mode, dir_fd=dir_fd)
        name = Path(path)
        if name.name.startswith((".model-secrets-", ".models.json-")):
            initial = os.fstat(fd)
            assert flags & os.O_CREAT and flags & os.O_EXCL
            assert mode == 0o600
            assert stat.S_IMODE(initial.st_mode) == 0o600
            assert initial.st_size == 0
            opened.append(name)
            live[fd] = name
        return fd

    def observe_short_write(fd, data):
        if fd not in live:
            return real_write(fd, data)
        assert stat.S_IMODE(os.fstat(fd).st_mode) == 0o600
        count = real_write(fd, data[:7])  # Exercise multiple real writes.
        assert stat.S_IMODE(os.fstat(fd).st_mode) == 0o600
        writes.append((live[fd], count))
        return count

    def observe_replace(source, target):
        assert stat.S_IMODE(Path(source).stat().st_mode) == 0o600
        result = real_replace(source, target)
        assert stat.S_IMODE(Path(target).stat().st_mode) == 0o600
        replacements.append(Path(target))
        return result

    monkeypatch.setattr(os, "open", observe_open)
    monkeypatch.setattr(os, "write", observe_short_write)
    monkeypatch.setattr(os, "replace", observe_replace)
    secret = "synthetic-r02-round-trip"
    assert store.upsert_profile("tenant", _body(secret))["ok"]
    assert len(opened) == len(replacements) == 2
    assert all(sum(path == name for path, _ in writes) > 1 for name in opened)
    assert all(not path.exists() for path in opened)
    _same_secret(ts.TenantSettingsStore(store.root).resolve_ref("tenant:tenant:synthetic-profile"), secret)
    _assert_no_private_temps(store.root)
    _no_secret_output(caplog, capsys, secret)


@pytest.mark.parametrize("stage", ["secret", "metadata"])
def test_replace_failure_keeps_previous_committed_configuration(tmp_path, monkeypatch, stage, caplog, capsys):
    store, before, old, digest = _seed(tmp_path)
    real_replace = os.replace
    observed = []

    def fail_replace(source, target):
        is_metadata = Path(target).name == "models.json"
        if is_metadata == (stage == "metadata"):
            assert stat.S_IMODE(Path(source).stat().st_mode) == 0o600
            observed.append(Path(source))
            raise OSError("injected replacement failure")
        return real_replace(source, target)

    monkeypatch.setattr(os, "replace", fail_replace)
    secret = "synthetic-r02-uncommitted"
    with pytest.raises(OSError, match="injected replacement failure"):
        store.upsert_profile("tenant", _body(secret))
    assert len(observed) == 1 and not observed[0].exists()
    _assert_old_commit(store, before, old, digest)
    _same_secret(ts.TenantSettingsStore(store.root).resolve_ref("tenant:tenant:synthetic-profile"),
                 "synthetic-r02-original")
    _no_secret_output(caplog, capsys, secret, "synthetic-r02-original")


def test_replace_and_cleanup_failure_leave_only_private_residue(tmp_path, monkeypatch, caplog, capsys):
    """A denied unlink may leave a file; its mode and primary error remain safe."""
    target = tmp_path / "model-secrets-test.bin"
    secret = "synthetic-r02-cleanup-fault"
    leftovers = []
    real_unlink = Path.unlink

    def fail_replace(source, destination):
        residue = Path(source)
        assert stat.S_IMODE(residue.stat().st_mode) == 0o600
        leftovers.append(residue)
        raise OSError("injected replacement failure")

    def fail_cleanup(path, *args, **kwargs):
        if path in leftovers:
            raise PermissionError("injected cleanup denial")
        return real_unlink(path, *args, **kwargs)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(os, "replace", fail_replace)
            patch.setattr(Path, "unlink", fail_cleanup)
            with pytest.raises(OSError, match="injected replacement failure") as error:
                store_write = ts.TenantSettingsStore._atomic_write(target, secret.encode())
            assert len(leftovers) == 1
            residue = leftovers[0]
            assert stat.S_IMODE(residue.stat().st_mode) == 0o600
            assert residue.stat().st_size > 0
            assert not target.exists()
            assert any("private temporary cleanup failed" in note for note in error.value.__notes__)
            exception_leaked = secret in str(error.value) + " ".join(error.value.__notes__)
            assert not exception_leaked
            _no_secret_output(caplog, capsys, secret)
    finally:
        for residue in leftovers:
            real_unlink(residue, missing_ok=True)
    assert not list(tmp_path.iterdir()), "test must remove its deliberately retained synthetic residue"


@pytest.mark.parametrize("stage", ["secret", "metadata"])
def test_permission_setting_failure_aborts_before_bytes_and_preserves_commit(tmp_path, monkeypatch, stage):
    store, before, old, digest = _seed(tmp_path)
    real_open, real_fchmod = os.open, os.fchmod
    names = {}
    denied = []

    def observe_open(path, flags, mode=0o777, *, dir_fd=None):
        fd = real_open(path, flags, mode, dir_fd=dir_fd)
        names[fd] = Path(path)
        return fd

    def fail_mode(fd, mode):
        name = names[fd]
        is_metadata = name.name.startswith(".models.json-")
        if is_metadata == (stage == "metadata"):
            assert os.fstat(fd).st_size == 0
            assert stat.S_IMODE(os.fstat(fd).st_mode) == 0o600
            denied.append(name)
            raise PermissionError("injected private mode failure")
        return real_fchmod(fd, mode)

    monkeypatch.setattr(os, "open", observe_open)
    monkeypatch.setattr(os, "fchmod", fail_mode)
    with pytest.raises(PermissionError, match="injected private mode failure"):
        store.upsert_profile("tenant", _body("synthetic-r02-never-committed"))
    assert len(denied) == 1 and not denied[0].exists()
    _assert_old_commit(store, before, old, digest)


def test_actual_mode_mismatch_is_rejected_before_any_secret_write(tmp_path, monkeypatch):
    """Inject failed permission establishment, but inspect the real changed mode."""
    real_fchmod = os.fchmod
    observations = []

    def wrong_mode(fd, requested):
        real_fchmod(fd, 0o640)
        observations.append((stat.S_IMODE(os.fstat(fd).st_mode), os.fstat(fd).st_size))

    monkeypatch.setattr(os, "fchmod", wrong_mode)
    with pytest.raises(PermissionError, match="private file permissions unavailable"):
        ts.TenantSettingsStore._atomic_write(tmp_path / "secret", b"synthetic-r02-not-written")
    assert observations == [(0o640, 0)]
    assert not list(tmp_path.iterdir())


def test_real_directory_permission_denial_preserves_configuration(tmp_path):
    store, before, old, digest = _seed(tmp_path)
    directory = old.parent
    previous = stat.S_IMODE(directory.stat().st_mode)
    try:
        directory.chmod(0o500)
        with pytest.raises(PermissionError):
            store.upsert_profile("tenant", _body("synthetic-r02-denied-write"))
    finally:
        directory.chmod(previous)
    _assert_old_commit(store, before, old, digest)


def test_retirement_failure_has_private_residue_without_secret_logs_and_recovers(tmp_path, monkeypatch, caplog, capsys):
    store, _, old, _ = _seed(tmp_path)
    real_unlink = Path.unlink
    secret = "synthetic-r02-new-committed"

    def fail_retirement(path, *args, **kwargs):
        if path == old:
            raise PermissionError("injected retired generation cleanup failure")
        return real_unlink(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail_retirement)
        with caplog.at_level(logging.WARNING, logger="pyharness.tenant_settings"):
            result = store.upsert_profile("tenant", _body(secret))
        assert result["ok"] and result["cleanup_pending"]
        assert stat.S_IMODE(old.stat().st_mode) == 0o600
        assert stat.S_IMODE(_generation(store).stat().st_mode) == 0o600
        _same_secret(store.resolve_ref("tenant:tenant:synthetic-profile"), secret)
        assert "retired secret generation cleanup pending" in caplog.text
        _no_secret_output(caplog, capsys, secret, "synthetic-r02-original")
    assert not store.upsert_profile("tenant", _body(secret))["cleanup_pending"]
    assert not old.exists()
    assert list(old.parent.glob("model-secrets-*.bin")) == [_generation(store)]
    _assert_no_private_temps(store.root)


@pytest.mark.parametrize("tenant", ["..", "../outside", "a/b", "a\\b", "/outside"])
def test_tenant_input_cannot_choose_a_path_outside_root(tmp_path, tenant):
    store = ts.TenantSettingsStore(tmp_path / "tenants")
    with pytest.raises(PyHError):
        store.upsert_profile(tenant, _body("synthetic-r02-path-denied"))
    assert not list(store.root.iterdir())
    assert list(tmp_path.iterdir()) == [store.root]


@pytest.mark.parametrize("generation", ["../outside.bin", "/outside.bin", "model-secrets-invalid.bin"])
def test_metadata_cannot_choose_secret_generation_path(tmp_path, generation):
    store = ts.TenantSettingsStore(tmp_path / "tenants")
    with pytest.raises(PyHError):
        store._secret_path("tenant", {"secret_generation": generation})
    assert not list((store.root / "tenant").iterdir())


def test_symlink_tenant_directory_is_rejected_before_write(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "marker"
    marker.write_bytes(b"unchanged synthetic marker")
    store = ts.TenantSettingsStore(tmp_path / "tenants")
    (store.root / "tenant").symlink_to(outside, target_is_directory=True)
    with pytest.raises(PyHError):
        store.upsert_profile("tenant", _body("synthetic-r02-symlink-denied"))
    assert list(outside.iterdir()) == [marker]
    assert marker.read_bytes() == b"unchanged synthetic marker"
    _assert_no_private_temps(store.root)


def test_symlink_secret_generation_is_rejected_without_following_it(tmp_path):
    store, _, old, _ = _seed(tmp_path)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"unchanged synthetic marker")
    old.unlink()
    old.symlink_to(outside)
    with pytest.raises(PyHError):
        store.upsert_profile("tenant", _body("synthetic-r02-generation-denied"))
    assert old.is_symlink()
    assert outside.read_bytes() == b"unchanged synthetic marker"
    _assert_no_private_temps(store.root)


def test_atomic_replace_replaces_symlink_instead_of_writing_through_it(tmp_path):
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"unchanged synthetic marker")
    directory = tmp_path / "tenant"
    directory.mkdir()
    target = directory / "secret.bin"
    target.symlink_to(outside)
    ts.TenantSettingsStore._atomic_write(target, b"synthetic-r02-new-value")
    assert not target.is_symlink()
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert outside.read_bytes() == b"unchanged synthetic marker"
    assert list(directory.iterdir()) == [target]
