"""Real PTY probe: 证明 exec.pty 走的是真伪终端(ConPTY),不是行式管道。

判定标准(硬证据):子进程自报 sys.stdout.isatty() 为 True——管道下必为 False。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pyharness.core import pty as pty_mod          # noqa: E402


def main() -> int:
    print("platform :", sys.platform)
    print("supported:", pty_mod.pty_supported())
    cwd = ROOT / ".probe_pty_tmp"
    cwd.mkdir(exist_ok=True)

    code = ("import sys, os;"
            "print('PROBE_ISATTY', sys.stdout.isatty());"
            "print('PROBE_ENV_LEAK', any("
            "k in os.environ for k in ('HTTP_PROXY','DEEPSEEK_API_KEY')))")
    r = pty_mod.run_in_pty([sys.executable, "-c", code],
                           cwd=cwd, env=None, timeout_s=30)
    out = r["output"]
    print("exit_code:", r["exit_code"], "timed_out:", r["timed_out"],
          "elapsed_ms:", r["elapsed_ms"])
    print("--- output ---")
    print(out.strip())
    print("--------------")

    ok_isatty = "PROBE_ISATTY True" in out
    ok_leak = "PROBE_ENV_LEAK False" in out

    # 交互式 stdin:PTY 下写入的输入应被真正读到
    r2 = pty_mod.run_in_pty([sys.executable, "-c", "print(input().upper())"],
                            cwd=cwd, env=None, input_text="hello-pty\n",
                            timeout_s=30)
    ok_input = "HELLO-PTY" in r2["output"]

    # 超时路径:长命令必须被墙钟掐断并标 timed_out
    r3 = pty_mod.run_in_pty([sys.executable, "-c", "import time; time.sleep(30)"],
                            cwd=cwd, env=None, timeout_s=3)
    ok_timeout = bool(r3["timed_out"])

    print("RESULT isatty=%s env_sandboxed=%s stdin=%s timeout=%s"
          % (ok_isatty, ok_leak, ok_input, ok_timeout))
    ok = ok_isatty and ok_leak and ok_input and ok_timeout
    print("PROBE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
