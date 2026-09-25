"""Bounded, isolated CI stages; all generated files live outside the checkout.

Dependency installation may use the package index. Test processes receive a
small environment allowlist and a loopback-only socket guard before collection.
Uploaded JUnit deliberately omits captured output, failure bodies and properties.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import time
import venv
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PYTHON_ARGS = ["-B", "-X", "utf8"]


def environment(root: Path) -> dict[str, str]:
    keep = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "SYSTEMDRIVE",
            "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "SSL_CERT_FILE",
            "SSL_CERT_DIR", "LANG", "LC_ALL"}
    env = {k: v for k, v in os.environ.items() if k.upper() in keep}
    # Windows known-folder APIs expand USERPROFILE independently of APPDATA.
    # Chromium refuses DevTools if the synthetic default directory is missing.
    for key, name in (("HOME", "home"), ("USERPROFILE", "home"),
                      ("APPDATA", "home/AppData/Roaming"), ("LOCALAPPDATA", "home/AppData/Local"),
                      ("TMP", "tmp"), ("TEMP", "tmp"), ("TMPDIR", "tmp"),
                      ("XDG_CACHE_HOME", "cache"), ("XDG_CONFIG_HOME", "config")):
        path = root / name
        path.mkdir(parents=True, exist_ok=True)
        env[key] = str(path)
    config = root / "isolated-config.json"
    config.write_text(json.dumps({"llm": {"base_url": "http://127.0.0.1:1",
        "api_key": "env:PYHARNESS_SYNTHETIC_KEY", "fallback_models": []},
        "plugins": {"enabled": [], "mcp_servers": []}}), encoding="utf-8")
    env.update(PH_CFG_PATH=str(config), PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1",
               PYTHONIOENCODING="utf-8", QT_QPA_PLATFORM="offscreen",
               UV_CACHE_DIR=str(root / "uv-cache"), UV_PYTHON_DOWNLOADS="never",
               PIP_CACHE_DIR=str(root / "pip-cache"), PIP_DISABLE_PIP_VERSION_CHECK="1",
               COVERAGE_FILE=str(root / "coverage-data"), HYPOTHESIS_STORAGE_DIRECTORY=str(root / "hypothesis"))
    return env


def python_in(path: Path) -> Path:
    return path / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(args, *, cwd: Path, env: dict[str, str], timeout: int = 900) -> None:
    # New process groups belong only to this command. Timeout cleanup never
    # searches by executable name or terminates another CI/application process.
    started = time.monotonic()
    options = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
               else {"start_new_session": True})
    process = subprocess.Popen([str(a) for a in args], cwd=cwd, env=env, **options)
    def stop_owned_group():
        if os.name == "nt":
            if process.poll() is None:
                try:
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   timeout=15, check=True)
                except (OSError, subprocess.SubprocessError):
                    # Reap our direct child even if the OS tree terminator fails;
                    # report failure rather than claiming descendants are stopped.
                    process.kill()
                    process.wait(timeout=10)
                    raise RuntimeError("Owned process-tree termination failed")
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait(timeout=15)
    try:
        code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        stop_owned_group()
        code = 124
    except BaseException:
        stop_owned_group()
        raise
    print(json.dumps({"command": Path(str(args[0])).name, "exit_code": code,
                      "seconds": round(time.monotonic() - started, 3)}), flush=True)
    if code:
        raise SystemExit(code)


def prepare(out: Path) -> None:
    env = environment(out)
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("uv must be installed by the pinned setup-uv workflow step")
    requirements = out / "requirements.txt"
    run([uv, "export", "--frozen", "--extra", "dev", "--no-emit-project", "--format",
         "requirements-txt", "--output-file", requirements, "--quiet"], cwd=ROOT, env=env)
    venv.EnvBuilder(with_pip=False).create(out / "test-env")
    run([uv, "pip", "install", "--python", python_in(out / "test-env"), "--require-hashes",
         "-r", requirements], cwd=out, env=env)
    optional_check = "import PySide6; print('PySide6', PySide6.__version__)"
    if os.name == "nt":
        optional_check += "; import winpty; print('pywinpty import passed')"
    run([python_in(out / "test-env"), "-I", *PYTHON_ARGS, "-c", optional_check], cwd=out, env=env)
    write_json(out / "artifacts" / "environment.json", {
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "python": platform.python_version(), "os": platform.system(),
        "runner_os": os.environ.get("RUNNER_OS", "local"),
        "lock_sha256": hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest(),
        "synthetic_configuration": True, "real_model_requests": 0})


def install_network_boundary() -> None:
    connect, connect_ex, resolve = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo
    def check(host):
        if isinstance(host, bytes):
            host = host.decode("ascii")
        if host == "localhost":
            return
        try:
            if ipaddress.ip_address(host.split("%")[0]).is_loopback:
                return
        except (ValueError, AttributeError):
            pass
        raise PermissionError("CI boundary: non-loopback network denied")
    def guarded_connect(sock, address):
        if isinstance(address, tuple):
            check(address[0])
        return connect(sock, address)
    def guarded_connect_ex(sock, address):
        if isinstance(address, tuple):
            check(address[0])
        return connect_ex(sock, address)
    def guarded_resolve(host, *args, **kwargs):
        if host is not None:
            check(host)
        return resolve(host, *args, **kwargs)
    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex
    socket.getaddrinfo = guarded_resolve


def safe_junit(raw: Path, target: Path) -> dict:
    root = ET.parse(raw).getroot()
    cases = list(root.iter("testcase"))
    counts = {"collected": len(cases), "passed": 0, "failed": 0, "skipped": 0, "errors": 0}
    safe = ET.Element("testsuites")
    suite = ET.SubElement(safe, "testsuite", name=target.stem)
    for index, case in enumerate(cases):
        attrs = {k: case.attrib[k] for k in ("classname", "time") if k in case.attrib}
        attrs["name"] = case.attrib.get("name", "case").split("[", 1)[0] + f"[case-{index}]"
        new = ET.SubElement(suite, "testcase", attrs)
        status = next((name for name in ("failure", "error", "skipped") if case.find(name) is not None), None)
        counts[{"failure": "failed", "error": "errors", "skipped": "skipped", None: "passed"}[status]] += 1
        if status:
            ET.SubElement(new, status, message="Status only; details remain in the workflow step log")
    suite.attrib.update(tests=str(counts["collected"]), failures=str(counts["failed"]),
                        errors=str(counts["errors"]), skipped=str(counts["skipped"]))
    ET.ElementTree(safe).write(target, encoding="utf-8", xml_declaration=True)
    return counts


def test(out: Path, suite: str, cov_floor: int = 75) -> None:
    env = environment(out / suite)
    artifacts = out / "artifacts"
    artifacts.mkdir(exist_ok=True)
    targets = {
        "full": ["tests"], "isolation": ["tests/unit/test_remediation_isolation.py"],
        "posix": ["tests/unit/test_posix_secret_permissions.py"],
        "platform": ["tests/unit/test_platform_models.py", "tests/unit/test_platform_service.py", "tests/unit/test_platform_safety.py", "tests/unit/test_platform_remote.py"],
        "docker": ["tests/docker_acceptance", "--real-docker"],
        "high-risk": [str(p.relative_to(ROOT)) for p in sorted((ROOT / "tests/unit").glob("test_remediation_*.py"))],
        "security": ["tests/security"], "acceptance": ["tests/acceptance"],
        "e2e": ["tests/e2e"], "invariants": ["tests/invariants"],
        "structural": ["tests/unit/test_governance_policy.py", "tests/integration/test_shell_service_contract.py"],
    }
    if suite == "posix":
        if sys.platform != "linux" or os.geteuid() == 0:
            raise SystemExit("R02 requires Linux with a non-root runner")
    raw = out / suite / "junit-raw.xml"
    cmd = [python_in(out / "test-env"), *PYTHON_ARGS, Path(__file__), "_pytest", "-q", "-p",
           "no:cacheprovider", "-o", "addopts=", "--basetemp", out / suite / "pytest-tmp",
           "--junitxml", raw, *targets[suite]]
    if suite == "full":
        cmd += ["--cov=pyharness", "--cov-report=term", "--cov-report=json:" + str(out / suite / "coverage.json"),
                "--cov-fail-under=" + str(cov_floor)]
    code = 0
    if suite == "isolation":
        run([python_in(out / "test-env"), *PYTHON_ARGS, Path(__file__), "_isolation"],
            cwd=out, env=env, timeout=60)
    try:
        run(cmd, cwd=ROOT, env=env, timeout=1200)
    except SystemExit as exc:
        code = int(exc.code)
    finally:
        if raw.exists():
            counts = safe_junit(raw, artifacts / ("junit-" + suite + ".xml"))
            if suite in {"posix", "docker"} and (counts["collected"] == 0 or counts["skipped"]):
                code = code or 1
            evidence = {"suite": suite, **counts, "exit_code": code,
                        "python": platform.python_version(), "os": platform.system(),
                        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()}
            if suite == "posix":
                evidence.update(euid=os.geteuid(), real_posix_filesystem=True, required_umask="022")
            coverage = out / suite / "coverage.json"
            if coverage.exists():
                evidence["coverage"] = json.loads(coverage.read_text(encoding="utf-8"))["totals"]
            write_json(artifacts / (suite + "-summary.json"), evidence)
            print(json.dumps(evidence), flush=True)
    if code:
        raise SystemExit(code)


def static(out: Path) -> None:
    env = environment(out / "static")
    run([sys.executable, *PYTHON_ARGS, ROOT / "scripts/check_authorization.py"], cwd=ROOT, env=env)
    sources = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "pyharness").rglob("*.py"))
    for symbol in ("governance.audit", "archive_task_evidence", "governance_audit("):
        if symbol not in sources:
            raise SystemExit("Missing production governance read path: " + symbol)
    run(["git", "diff", "--check"], cwd=ROOT, env=env)
    run(["git", "show", "--check", "--oneline", "HEAD"], cwd=ROOT, env=env)
    write_json(out / "artifacts" / "static-summary.json", {"authorization_ast": "passed", "governance_read_paths": "passed", "git_diff_check": "passed"})


def check_archives(paths: list[Path]) -> dict:
    hashes = {}
    for archive in paths:
        if archive.suffix == ".whl":
            with zipfile.ZipFile(archive) as zf:
                entries = [(name, zf.read(name)) for name in zf.namelist() if not name.endswith("/")]
            names={name for name,_ in entries}
            if not {'pyharness/ui/platform.html','pyharness/ui/index.html'}.issubset(names):
                raise SystemExit('Missing platform/classic Web resources')
        else:
            with tarfile.open(archive, "r:gz") as tf:
                entries = [(member.name, tf.extractfile(member).read()) for member in tf.getmembers() if member.isfile()]
        for name, data in entries:
            parts = PurePosixPath(name).parts
            if any(part in {".git", ".work", ".venv", "checkpoints", "__pycache__", ".pytest_cache", "node_modules"} for part in parts):
                raise SystemExit("Forbidden generated/private archive member: " + name)
            if PurePosixPath(name).is_absolute() or ".." in parts:
                raise SystemExit("Unsafe archive member")
            if re.search(rb"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----", data):
                raise SystemExit("Private-key payload in archive")
        if archive.suffix == ".whl":
            metadata = next(data.decode() for name, data in entries if name.endswith(".dist-info/METADATA"))
            entry = next(data.decode() for name, data in entries if name.endswith(".dist-info/entry_points.txt"))
            if "License-Expression: MIT" not in metadata or "Description-Content-Type: text/markdown" not in metadata:
                raise SystemExit("Wheel license/README metadata missing")
            for name in ("pyharness", "pyharness-desktop", "pyharness-native"):
                if name + " = " not in entry:
                    raise SystemExit("Missing console entry point: " + name)
        hashes[archive.name] = hashlib.sha256(archive.read_bytes()).hexdigest()
    return hashes


def package(out: Path) -> None:
    env = environment(out / "package")
    env["UV_CACHE_DIR"] = str(out / "uv-cache")
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("uv is required")
    venv.EnvBuilder(with_pip=False).create(out / "build-env")
    buildpy = python_in(out / "build-env")
    run([uv, "pip", "install", "--python", buildpy, "hatchling==1.32.4"], cwd=out, env=env)
    packages = out / "artifacts" / "packages"
    packages.mkdir(parents=True, exist_ok=True)
    run([buildpy, *PYTHON_ARGS, "-m", "hatchling", "build", "-t", "wheel", "-t", "sdist", "-d", packages], cwd=ROOT, env=env)
    wheels, sdists = list(packages.glob("*.whl")), list(packages.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise SystemExit("Expected exactly one wheel and one sdist")
    hashes = check_archives(wheels + sdists)
    (packages / "SHA256SUMS.txt").write_text("".join(f"{value}  {name}\n" for name, value in sorted(hashes.items())), encoding="utf-8")
    modes = ("base", "native", "desktop") if os.name == "nt" else ("base",)
    results = {}
    for mode in modes:
        location = out / ("installed-" + mode)
        venv.EnvBuilder(with_pip=False).create(location)
        installedpy = python_in(location)
        wheel = str(wheels[0]) + ("[" + mode + "]" if mode != "base" else "")
        run([uv, "pip", "install", "--python", installedpy, "--reinstall-package", "pyharness", "--constraint", out / "requirements.txt", wheel], cwd=out, env=env)
        outside = out / ("outside source 中文 " + mode)
        outside.mkdir(exist_ok=True)
        check = "from pathlib import Path; import sys,pyharness,importlib.metadata as m; assert Path(pyharness.__file__).is_relative_to(Path(sys.prefix)); assert m.version('pyharness')=='0.1.0'; print('installed import verified')"
        run([installedpy, "-I", *PYTHON_ARGS, "-c", check], cwd=outside, env=env)
        cli = location / ("Scripts/pyharness.exe" if os.name == "nt" else "bin/pyharness")
        run([cli, "--help"], cwd=outside, env=env)
        if mode != "base":
            run([installedpy, "-I", *PYTHON_ARGS, Path(__file__), "_entry", mode], cwd=outside, env=env, timeout=60)
        if mode == 'desktop':
            run([installedpy, "-I", *PYTHON_ARGS, Path(__file__), "_entry", 'webview'], cwd=outside, env=env, timeout=60)
        results[mode] = {"install": "passed", "isolated_import": "passed", "cli_help": "passed",
                         "precheck": "not_applicable" if mode == "base" else "passed"}
    write_json(out / "artifacts" / "package-summary.json", {"sha256": hashes, "modes": results,
        "full_manual_gui": "not_run", "real_model": "not_run"})



def isolation_probe() -> None:
    """Real assembly with synthetic host configuration that must remain unused."""
    import asyncio
    install_network_boundary()
    sys.path.insert(0, str(ROOT))
    from pyharness.config import load_settings
    from pyharness.engine import assemble_real_engine
    home = Path.home()
    host = home / ".pyharness"
    host.mkdir(parents=True, exist_ok=True)
    plugin_marker, mcp_marker = host / "plugin-started", host / "mcp-started"
    plugin = host / "plugins" / "must-not-load" / "plugin.py"
    plugin.parent.mkdir(parents=True, exist_ok=True)
    plugin.write_text("from pathlib import Path\nPath(" + repr(str(plugin_marker)) + ").touch()\n", encoding="utf-8")
    hostile = {"llm": {"model": "must-not-inherit"}, "plugins": {
        "enabled": ["must-not-load"], "dir": str(plugin.parent.parent),
        "mcp_servers": [{"name": "must-not-start", "command": [sys.executable, "-c",
            "from pathlib import Path; Path(" + repr(str(mcp_marker)) + ").touch()"]}]}}
    (host / "config.yaml").write_text(json.dumps(hostile), encoding="utf-8")
    cfg = load_settings()
    assert cfg.llm.model != "must-not-inherit"
    assert cfg.plugins.enabled == [] and cfg.plugins.mcp_servers == []
    for value in (cfg.storage.root, cfg.storage.sessions_dir, cfg.storage.workspaces_dir,
                  cfg.storage.spill_dir, cfg.storage.db_path, cfg.security.credentials.file):
        assert Path(value).expanduser().resolve().is_relative_to(home.resolve())
    async def exercise():
        from pyharness.core import llm
        from tests.conftest import ScriptedAdapter
        adapter = ScriptedAdapter(cfg.llm.model)
        saved = dict(llm.adapters)
        llm.adapters[cfg.llm.model] = adapter
        ctx = None
        try:
            ctx = await assemble_real_engine(cfg, sid="ci-isolation-sentinel",
                sessions_dir=Path(cfg.storage.sessions_dir).expanduser(), channel="cli")
            await ctx.session.append("session.created", {"title": "synthetic isolation", "model": cfg.llm.model}, actor="system", sync=True)
            await ctx.session.append("user.message", {"content": "synthetic isolation sentinel"}, actor="user", sync=True)
            task_id = await ctx.task_queue.submit("synthetic isolation sentinel")
            result = await asyncio.wait_for(ctx.task_queue.wait_for(task_id), timeout=20)
            assert result.ok and adapter.n > 0
            assert ctx.engine_spine._plugins_ready
        finally:
            try:
                if ctx is not None:
                    try:
                        await ctx.engine_spine.close()
                    finally:
                        ctx.scope.release()
            finally:
                llm.adapters.clear()
                llm.adapters.update(saved)
        records = list(Path(cfg.storage.sessions_dir).expanduser().rglob("*.jsonl"))
        assert records and all(path.resolve().is_relative_to(home.resolve()) for path in records)
    asyncio.run(exercise())
    assert not plugin_marker.exists() and not mcp_marker.exists()
    with socket.socket() as sock:
        try:
            sock.connect(("192.0.2.1", 443))
        except PermissionError:
            pass
        else:
            raise AssertionError("Non-loopback sentinel was not rejected")
    print(json.dumps({"synthetic_home_configuration": "ignored", "plugin_starts": 0,
                      "mcp_starts": 0, "storage": "isolated_home", "non_loopback": "rejected",
                      "task": "completed", "model": "deterministic_adapter", "lazy_preload": "executed"}))


def entry(mode: str) -> None:
    install_network_boundary()
    import pyharness
    if not Path(pyharness.__file__).is_relative_to(Path(sys.prefix)):
        raise SystemExit("Installation check imported the source tree")
    if mode == "native":
        import asyncio
        from PySide6.QtCore import QTimer
        from pyharness.desktop_native import app
        original = app.MainWindow
        class Window(original):
            def show(self):
                super().show()
                QTimer.singleShot(300, asyncio.get_event_loop().stop)
        app.MainWindow = Window
        if app.main(["pyharness-native"]) != 0:
            raise SystemExit("Native offscreen entry failed")
        from pyharness import persistence
        if persistence._LOCKS:
            raise SystemExit("Native shutdown retained persistence locks")
    elif mode in {"desktop","webview"}:
        import threading
        import httpx
        from pyharness.application.bootstrap import assemble_desktop_ctx
        from pyharness.desktop.app import DesktopApp
        from pyharness.desktop import net
        ctx = assemble_desktop_ctx()
        ctx.settings.shell.web.host = "127.0.0.1"
        ctx.settings.shell.web.port = 0
        host, port = net.resolve_bind(ctx.settings)
        app = DesktopApp(ctx)
        worker = threading.Thread(target=net.run_uvicorn, args=(app, port, host), name="ci-owned-desktop")
        worker.start()
        try:
            if not net.wait_until_listening(port, timeout=8, host=host, app=app):
                raise RuntimeError("Desktop service did not become ready")
            with httpx.Client(trust_env=False, timeout=5) as client:
                response = client.get(f"http://127.0.0.1:{port}/api/sessions", headers={"X-PyHarness-Token": app._api_token})
            if response.status_code != 200:
                raise RuntimeError("Desktop HTTP precheck failed")
            if mode=='webview':
                import webview
                window=webview.create_window('PyHarness isolated WebView smoke',app.bootstrap_url(host,port),hidden=True)
                verdict=[]
                def inspect_page():
                    try:
                        if not window.events.loaded.wait(15):
                            raise RuntimeError('WebView page did not load')
                        deadline=time.monotonic()+15
                        while time.monotonic()<deadline:
                            text=window.evaluate_js('document.querySelector("main")?.innerText || ""')
                            if text and '你好' in text:
                                verdict.append('passed');return
                            time.sleep(.2)
                        raise RuntimeError('Platform dashboard did not render in WebView')
                    except Exception as exc:
                        verdict.append(type(exc).__name__)
                    finally:
                        window.destroy()
                webview.start(inspect_page,debug=False,private_mode=True,storage_path=os.environ['LOCALAPPDATA'])
                if verdict!=['passed']:
                    raise RuntimeError('WebView smoke failed: '+str(verdict))
        finally:
            app.shutdown_gracefully()
            worker.join(timeout=10)
            if worker.is_alive():
                raise RuntimeError("Desktop worker did not stop")
        if net._probe_port(port, host=host):
            raise RuntimeError("Desktop port remains in use")
    print(json.dumps({"mode": mode, "entry_precheck": "passed", "model_requests": 0}))


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "_pytest":
        install_network_boundary()
        import pytest
        return pytest.main(args[1:])
    if args and args[0] == "_isolation":
        isolation_probe()
        return 0
    if args and args[0] == "_entry":
        entry(args[1])
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("prepare", "test", "static", "package"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cov-fail-under", type=int, default=75)
    parser.add_argument("--suite", default="full", choices=("full", "isolation", "posix", "high-risk", "security", "acceptance", "e2e", "invariants", "structural", "platform", "docker"))
    parsed = parser.parse_args(args)
    if parsed.cov_fail_under < 75 or parsed.cov_fail_under > 100:
        parser.error("coverage gate must be between 75 and 100 percent")
    out = parsed.output_dir.resolve()
    if out == ROOT or out.is_relative_to(ROOT):
        parser.error("--output-dir must be outside the checkout")
    out.mkdir(parents=True, exist_ok=True)
    if parsed.stage == "test":
        test(out, parsed.suite, parsed.cov_fail_under)
    else:
        {"prepare": prepare, "static": static, "package": package}[parsed.stage](out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
