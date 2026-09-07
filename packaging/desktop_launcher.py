"""desktop_launcher.py — PyInstaller 打包专用入口(desktop.py 无 __main__ 块)。

PyInstaller 需要脚本顶层有 `if __name__ == "__main__"` 才会真正调用 main();
直接打 desktop.py 会得到"启动即退"的空转 exe(实测 exit 0 零输出)。
本入口 = 薄壳:import 真实实现 → 调 main → 退出码透传。
打包诊断:main 返回码与异常写 ~/ph_desktop_exit.txt(仅打包版生效,源码入口不受影响)。
"""
import os
import sys
import threading
import traceback

_TRACE = os.path.expanduser("~/ph_desktop_exit.txt")


def _log(msg: str) -> None:
    try:
        with open(_TRACE, "a", encoding="utf-8") as f:
            f.write(f"{msg}\n")
    except Exception:
        pass


def _hook(exc_type, exc, tb):
    _log("UNCAUGHT: " + "".join(traceback.format_exception(exc_type, exc, tb)))


sys.excepthook = _hook
threading.excepthook = lambda args: _log(
    "THREAD-UNCAUGHT: " + "".join(traceback.format_exception(
        args.exc_type, args.exc_value, args.exc_traceback)))


if __name__ == "__main__":
    _log(f"=== boot pid={os.getpid()} frozen={getattr(sys, 'frozen', False)} ===")
    try:
        from pyharness.desktop import main
        _log("imported main")
        rc = main()
        _log(f"main returned: {rc}")
        sys.exit(rc)
    except SystemExit as e:
        _log(f"SystemExit: {e.code}")
        raise
    except BaseException:
        _log("LAUNCHER-UNCAUGHT:\n" + traceback.format_exc())
        raise
