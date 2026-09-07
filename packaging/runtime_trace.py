"""runtime_trace.py — windowed exe 诊断:记录 main 调用前后与退出码。
用法: 打包 --runtime-hook packaging/runtime_trace.py
"""
import os, sys, traceback, threading

_TRACE = os.path.join(os.path.expanduser("~"), "ph_exe_trace.txt")

def _log(msg: str) -> None:
    try:
        with open(_TRACE, "a", encoding="utf-8") as f:
            f.write(f"{msg}\n")
    except Exception:
        pass

def _hook(exc_type, exc, tb):
    _log("UNCAUGHT: " + "".join(traceback.format_exception(exc_type, exc, tb)))

sys.excepthook = _hook
_log(f"=== boot {os.getpid()} frozen={getattr(sys,'frozen',False)} argv={sys.argv!r}")
threading.excepthook = lambda args: _log(
    "THREAD-UNCAUGHT: " + "".join(traceback.format_exception(
        args.exc_type, args.exc_value, args.exc_traceback)))
