"""runtime_bootmark.py — 打包诊断:exe bootloader 启动即写标记文件。
放 packaging/ 下,打包时 --runtime-hook 注入。验证 exe 是否真的执行到 Python 层。
"""
import os, sys

_MARK = os.path.join(os.path.expanduser("~"), "ph_exe_bootmark.txt")
try:
    with open(_MARK, "w") as f:
        f.write(f"bootmark: argv={sys.argv!r} prefix={sys.prefix!r}\n")
        f.write(f"frozen={getattr(sys, 'frozen', False)} meipass={getattr(sys, '_MEIPASS', 'N/A')}\n")
except Exception:
    pass
