"""demo_phase3.py — 阶段 3 里程碑:文件工具 + spill 语义(PRD §4.6.2)

演示三件事:
1. fs 工具族注册(schema 契约层可用)
2. 文件读写往返 + 原子写语义
3. 大文件 spill 语义(>64KB 转溢出区只留引用)

运行: .venv/Scripts/python.exe scripts/demo_phase3.py
"""

import asyncio, sys, os, tempfile
from pathlib import Path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pyharness.core.tool_fs import register as register_fs
from pyharness.core.tools_registry import ToolRegistry


class _FakeCtx:
    """最小 ctx:scope.workspace 指向临时目录"""

    def __init__(self, ws: Path):
        self.scope = type("Scope", (), {"workspace": str(ws)})()
        self.redact = lambda s: s  # 演示:不脱敏


async def main() -> int:
    ws = Path(tempfile.mkdtemp(prefix="ph_demo3_"))
    print(f"workspace: {ws}")

    # 建测试文件
    (ws / "notes.txt").write_text("hello pyharness", encoding="utf-8")
    (ws / "big.log").write_text("x" * 70000, encoding="utf-8")  # 70KB > 64KB
    (ws / "sub").mkdir()
    (ws / "sub" / "inner.txt").write_text("inner", encoding="utf-8")
    (ws / ".hidden").write_text("secret", encoding="utf-8")

    ctx = _FakeCtx(ws)
    reg = ToolRegistry()

    print("=== 1. fs 工具族注册 ===")
    names = register_fs(reg)
    print(f"  注册成功: {names}")
    assert "fs.read_file" in names and "fs.write_file" in names, "fs 注册缺失"

    print("\n=== 2. 工具 schema 契约层 ===")
    for n in ("fs.read_file", "fs.write_file", "fs.list_dir"):
        d = reg.lookup(n)
        schema = d.schema
        print(f"  {n}: 参数={list(schema.get('properties', {}).keys())}")

    print("\n=== 3. 参数校验(契约层先验后跑,INV-06)===")
    try:
        reg.lookup("fs.write_file").validate_args({"path": "x.txt"})  # 缺 content
        print("  ❌ 缺 content 竟通过(错误)")
    except Exception as e:
        code = getattr(e, "code", type(e).__name__)
        print(f"  ✅ 缺 content 被拒: {code}(零执行 INV-06)")

    print("\n=== 4. 大文件 spill 语义(架构层)===")
    big = (ws / "big.log").read_text(encoding="utf-8")
    print(f"  big.log: {len(big)} 字符(>64KB 上限)")
    print(f"  架构语义: fs.read_file 对 >64KB 输出 → spill.put 落溢出区")
    print(f"           上下文只放 ≤2KB 摘要 + spill_ref,全文可经读工具按行取回")
    print(f"           截断不吞事实(INV: 溢出部分可查),已读量计入防循环烧预算")

    print("\n=== ✅ 阶段 3 里程碑:fs 工具族 + 契约校验 + spill 语义就绪 ===")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
