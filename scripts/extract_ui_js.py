"""抽 <script> 内容到临时 js 供 node --check 语法校验(前端唯一可自动验证手段)。"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
html = (ROOT / "pyharness" / "ui" / "index.html").read_text(encoding="utf-8")
blocks = re.findall(r"<script[^>]*>(.*?)</script>", html, re.S)
js = max(blocks, key=len)
out = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / ".ui_check.js"
out.write_text(js, encoding="utf-8")
print(f"extracted {len(js)} chars -> {out}")
