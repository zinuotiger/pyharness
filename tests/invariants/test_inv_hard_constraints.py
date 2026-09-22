r"""不变量:硬约束（语言/依赖/架构）—— CONSTRAINTS-01 验收的可执行化（R26）。

**性质**:`CONSTRAINTS-01-Hard.md` 的 §5 验收是**可 grep 的硬事实**，本文件把它固化为
自动闸，防止"某个 PR 悄悄引入 TS 残留 / 被禁框架 / 第二份历史"。

覆盖（逐条对应文档）：
  - **H-01/H-08**：`pyharness/` 无 TS 语法残留；无被禁 Agent 框架依赖；
  - **H-02**：依赖以 `pyproject.toml` 为唯一真源，且**不含**禁用包；
  - **H-03**：锁工件是 **`uv.lock`**（文档原名写 `requirements.lock`，与实现不符，R26 已更正）；
  - **H-04/H-10**：无多进程/容器/跨进程通信残留（工具层 subprocess 属受 guard 管辖的**能力**，不在此列）；
  - **H-05/S-02**：会话模块无第二份**历史真源**（UI 渲染缓冲不算：它由服务端 API 填充）。

**负向**:新增任一被禁依赖/TS 残留/多进程调用 ⇒ 本文件必须 RED。
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
PKG = ROOT / "pyharness"

_FORBIDDEN_PKGS = ("langchain", "langgraph", "crewai", "docker", "redis",
                   "psycopg", "sqlalchemy", "kubernetes")
# TS 残留判据：接口声明 / 类型别名 / 箭头函数
_TS_RESIDUE = (
    re.compile(r"^\s*interface\s+[A-Z]", re.M),
    re.compile(r"^\s*type\s+[A-Za-z_]\w*\s*=", re.M),
    re.compile(r"=>\s*\{"),
)


def _py_files() -> list[pathlib.Path]:
    return sorted(p for p in PKG.rglob("*.py") if "__pycache__" not in p.parts)


def test_no_typescript_residue_in_package():
    """H-01：包内不得出现 TS 语法残留（`interface X` / `type X =` / `=> {`）。"""
    hits: list[str] = []
    for f in _py_files():
        src = f.read_text(encoding="utf-8")
        for pat in _TS_RESIDUE:
            for m in pat.finditer(src):
                hits.append(f"{f.relative_to(ROOT).as_posix()}:"
                            f"{src[:m.start()].count(chr(10)) + 1}")
    assert hits == [], f"发现 TS 语法残留:{hits}"


def test_no_forbidden_dependencies():
    """H-02/H-08/H-10：禁用依赖不得出现在 `pyproject.toml` 或 `uv.lock`。"""
    offenders: list[tuple[str, str]] = []
    for rel in ("pyproject.toml", "uv.lock"):
        f = ROOT / rel
        if not f.exists():
            continue
        low = f.read_text(encoding="utf-8").lower()
        for pkg in _FORBIDDEN_PKGS:
            if pkg in low:
                offenders.append((rel, pkg))
    assert offenders == [], (
        "被禁依赖出现（H-08 禁框架 / H-10 禁容器与中间件）:"
        f"{offenders}")


def test_lock_artifact_is_uv_lock():
    """H-03：锁工件是 **`uv.lock`**（文档原写 `requirements.lock` 与实现不符）。

    R26 实测：仓库根有 `uv.lock`（960 行 / 44 包），无 `requirements.lock` ——
    约束文档曾要求后者。**以实现为准**（uv 原生锁），文档已同步。
    """
    assert (ROOT / "uv.lock").is_file(), "H-03：uv.lock 缺失（uv 原生锁工件）"
    doc = (ROOT / "docs" / "CONSTRAINTS-01-Hard.md").read_text(encoding="utf-8")
    assert "uv.lock" in doc, "CONSTRAINTS-01 的 H-03 必须写明真实锁工件名"


def test_no_multiprocess_architecture_residue():
    """H-04：包内无多进程/跨进程通信框架残留（工具层 subprocess 属受管能力）。"""
    bad = ("multiprocessing", "grpc", "pyro", "xmlrpc")
    hits: list[str] = []
    for f in _py_files():
        src = f.read_text(encoding="utf-8")
        for token in bad:
            if token in src:
                hits.append(f"{f.relative_to(ROOT).as_posix()}:{token}")
    assert hits == [], f"发现多进程/跨进程通信残留:{hits}"


def test_no_second_history_store_in_session_modules():
    """H-05/S-02：会话/引擎模块不得有第二份**历史真源**（列表态消息存储）。

    判据：会话相关模块里不得出现 `self.messages = [...]` / `self.history = [...]`
    这类**真源形态**赋值（`history_cache` 是**可弃派生缓存**，由 `derive_messages`
    唯一填充，属允许面；UI 渲染缓冲由服务端 API 填充，不在此列）。
    """
    pat = re.compile(r"self\.(messages|history)\s*(?::[^=]+)?=\s*(\[|\{|\w)")
    hits: list[str] = []
    for f in _py_files():
        if f.name not in ("session.py", "agent.py", "agent_loop.py",
                          "session_query.py", "compaction.py"):
            continue
        src = f.read_text(encoding="utf-8")
        for m in pat.finditer(src):
            hits.append(f"{f.relative_to(ROOT).as_posix()}:"
                        f"{src[:m.start()].count(chr(10)) + 1}")
    assert hits == [], (
        "会话模块出现疑似第二份历史真源（H-05/S-02）:"
        f"{hits}")


# ---------------------------------------------------------------- CONSTRAINTS-06
def test_every_registered_error_code_has_a_test():
    """**TS-06**：每个已登记错误码至少有 1 条测试引用（验证其**可触发**）。

    R27 实测：43 个已登记码中 **`CRED-702`** 全库无任何测试引用 —— 追查发现它
    **已登记但全库从不抛出**（文档承诺的「凭据文件权限过宽(>600) → 启动拒载」在
    实现里不存在）。已接线（POSIX 查 mode 的 group/other 位）并补用例，故本闸现在
    为绿；将来新增码若不带测试，本条立即变红。
    """

    from pyharness.errors import ERRORS

    tests_text = "\n".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in (ROOT / "tests").rglob("*.py"))
    missing = sorted(c for c in ERRORS if c not in tests_text)
    assert missing == [], f"以下错误码无任何测试引用（TS-06）:{missing}"


# ---------------------------------------------------------------- CI 步骤一致性
def test_ci_workflow_steps_reference_existing_paths_and_deps():
    """**CI 步骤与仓库实际一致**（L-8 的静态可验证面；运行时需 push 才可见）。

    覆盖三类易漂移点：① CI 里点名的 `tests/...` 路径**必须存在**（否则 CI 一跑就红，
    而 L-8 记为"未验证"）；② CI 的"环境自检"`import` 的包**必须在 `.[dev]` 里**
    （否则自检步必红）；③ 覆盖率下限 `COV_FLOOR` 必须是**正整数**且出现在覆盖率步骤里。
    """
    import re as _re

    ci_path = ROOT / ".github" / "workflows" / "ci.yml"
    assert ci_path.is_file(), "L-8：CI 工作流缺失"
    ci = ci_path.read_text(encoding="utf-8")

    # ① tests/... 路径存在性
    missing: list[str] = []
    for m in _re.finditer(r"tests/[A-Za-z_][\w/]*\.py", ci):
        rel = m.group(0)
        if not (ROOT / rel).is_file():
            missing.append(rel)
    for m in _re.finditer(r"^\s*(tests/[a-z_]+)\s*$", ci, _re.M):
        rel = m.group(1)
        if not (ROOT / rel).is_dir():
            missing.append(rel)
    assert missing == [], f"CI 引用了不存在的路径（一跑即红）:{sorted(set(missing))}"

    # ② 自检 import 的包必须在 .[dev]
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    dev_block = pyproject.split("dev = [", 1)[1].split("]", 1)[0] \
        if "dev = [" in pyproject else ""
    for m in _re.finditer(r'python -c "import (\w+)', ci):
        pkg = m.group(1)
        assert pkg.lower() in dev_block.lower(), (
            f"CI 自检 import {pkg}，但 .[dev] 未声明它（自检步必红）")

    # ③ 覆盖率下限存在且为正整数，且被真正用作门禁
    flo = _re.search(r'COV_FLOOR:\s*"(\d+)"', ci)
    assert flo and int(flo.group(1)) > 0, "COV_FLOOR 缺失或非正整数"
    assert "--cov-fail-under" in ci, "覆盖率下限未接到 --cov-fail-under（形同虚设）"
