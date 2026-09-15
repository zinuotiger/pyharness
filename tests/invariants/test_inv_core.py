"""tests/invariants/test_inv_core.py — 核心不变量:INV-02(无绕过 agent-loop 直调 llm)。

冻结依据:`docs/INVARIANT_REGISTRY.md`(Canonical INV-02)· `docs/PRD-Core.md:838,634`
· `docs/CONSTRAINTS-06-Testing.md:63` · `docs/DIS-CORE.md:186`
· `docs/PRD-Core.md:1251`(F042 指定出口 = `ctx.llm.mini`)。

来源:本文件由 S6-2a-P0-F(KF-A 修复)建立,是 INV-02 的**正式不变量测试资产**;
S6-2b 将在**同一文件**扩展 INV-01 / INV-03~09 的编号化用例(不另建第二套)。

边界:静态扫描对象 = 运行时包 `pyharness/`。`scripts/` 下的人工探针(e2e / probe)
不参与装配、不被 `pyharness` import,不在 INV-02 的运行时边界内。

**类别式边界**(非例外名单):`chat`/`chat_stream` = Agent Loop **对话出口**(唯一合法
调用方 = `agent_loop`);`mini`/`summarize`/`json_chat` = **System Tool LLM 出口**,
不受 INV-02 的调用方约束,但其成员由 `LLMClient` 公开面显式枚举。
"""
from __future__ import annotations

import ast
import hashlib
import pathlib
import types

import pytest

pytestmark = pytest.mark.invariant

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_PKG = _ROOT / "pyharness"

# Agent Loop 对话出口 —— INV-02 点名的两个方法
_CONVERSATION_ENTRIES = ("chat", "chat_stream")


def _iter_sources():
    for p in sorted(_PKG.rglob("*.py")):
        yield p, p.read_text(encoding="utf-8")


# ================================================================ INV-02 · 静态面
def test_inv02_no_direct_conversation_entry_call():
    """INV-02(静态):包内**无任何**直接 `.chat(` / `.chat_stream(` 属性调用。

    agent-loop 经 `getattr(ctx.llm, chat_fn)` 动态派发(见下一条),故任何**直接**
    属性调用都等价于"非循环调用方" —— 正是 KF-A 的违约形态
    (修复前 `auto_title.py:36` 直调 `ctx.llm.chat`;见 `S6-2a-P0_CHANGE_REPORT.md`)。
    """
    offenders = []
    for p, src in _iter_sources():
        for n in ast.walk(ast.parse(src)):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr in _CONVERSATION_ENTRIES):
                offenders.append(f"{p.relative_to(_ROOT).as_posix()}:{n.lineno}"
                                 f" .{n.func.attr}()")
    assert not offenders, (
        "INV-02 违约:出现绕过 agent-loop 的对话出口直调 -> " + ", ".join(offenders))


def test_inv02_loop_is_the_only_dynamic_entry_dispatcher():
    """INV-02(静态):`getattr(ctx.llm, <method>)` 动态派发**仅**允许在 agent_loop 内。

    这是 agent-loop 调用对话出口的真实形态(`agent_loop.py:240-241`),也是
    "唯一合法调用方 = agent-loop"这一表述的**可验证实现形式**。
    """
    sites = []
    for p, src in _iter_sources():
        for n in ast.walk(ast.parse(src)):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id == "getattr" and n.args
                    and isinstance(n.args[0], ast.Attribute)
                    and n.args[0].attr == "llm"):
                sites.append(f"{p.relative_to(_ROOT).as_posix()}:{n.lineno}")
    assert sites == ["pyharness/core/agent_loop.py:241"], (
        "INV-02 违约:llm 门面的动态派发点不在 agent_loop 唯一位置 -> " + str(sites))


def test_inv02_mini_is_system_tool_entry_not_chat_wrapper():
    """INV-02(类别边界):`mini` 属 System Tool 出口,不得是 `self.chat` 的换名包装。

    静态核验 `LLMClient.mini` 的函数体:必须经 `_chat_any` 复用既有链路(与
    `summarize`/`json_chat` 同类,单一真源),**不得**调用 `chat`/`chat_stream`。
    """
    tree = ast.parse((_PKG / "core" / "llm.py").read_text(encoding="utf-8"))
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name == "mini"), None)
    assert fn is not None, "LLMClient.mini 缺失(System Tool LLM 出口未实现)"
    called = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            if isinstance(n.func, ast.Attribute):
                called.add(n.func.attr)
            elif isinstance(n.func, ast.Name):
                called.add(n.func.id)
    assert "_chat_any" in called, "mini 必须经 _chat_any 复用既有链路(单一真源)"
    overlap = {"chat", "chat_stream"} & called
    assert not overlap, f"mini 被实现为 chat 的换名包装 -> {sorted(overlap)}"


# ================================================================ INV-02 · 行为面
class _RecordingLLM:
    """记录型门面替身:统计各出口被调次数;对话出口被调即断言失败。"""

    def __init__(self, text: str = "会话标题"):
        self.calls = {"mini": 0, "chat": 0, "chat_stream": 0}
        self.prompts: list[str] = []
        self._text = text

    async def mini(self, prompt, *, ctx):
        self.calls["mini"] += 1
        self.prompts.append(prompt)
        return self._text

    async def chat(self, messages, tools=None, *, ctx):
        self.calls["chat"] += 1
        raise AssertionError("auto_title 不得调用 chat(INV-02:仅 agent-loop 可调)")

    async def chat_stream(self, messages, tools=None, *, ctx):
        self.calls["chat_stream"] += 1
        raise AssertionError("auto_title 不得调用 chat_stream(INV-02)")


class _FakeSession:
    """最小会话替身:`events_after` / `append` 足以驱动 auto_title。"""

    def __init__(self, first: str = "帮我整理这个文件夹"):
        self.events = [types.SimpleNamespace(type="user.message",
                                             payload={"content": first}),
                       types.SimpleNamespace(type="agent.message",
                                             payload={"content": "好"})]
        self.appended: list[tuple] = []

    def events_after(self, seq):
        return list(self.events) if seq == 0 else []

    async def append(self, type_, payload, actor=None, **kw):
        self.appended.append((type_, payload, actor))
        self.events.append(types.SimpleNamespace(type=type_, payload=payload))
        return types.SimpleNamespace(seq=len(self.events))


async def test_inv02_auto_title_uses_mini_and_never_chat():
    """INV-02(行为):auto_title 走 System Tool 出口 ⇒ `mini` 恰 1 次、`chat` 0 次。"""
    from pyharness.core.auto_title import auto_title

    llm = _RecordingLLM("整理文件夹笔记")
    sess = _FakeSession()
    ctx = types.SimpleNamespace(llm=llm, session=sess)

    title = await auto_title(ctx)

    assert title == "整理文件夹笔记"
    assert llm.calls["mini"] == 1, f"mini 调用次数应为 1 -> {llm.calls}"
    assert llm.calls["chat"] == 0, f"chat 调用次数应为 0 -> {llm.calls}"
    assert llm.calls["chat_stream"] == 0
    assert sess.appended == [("session.renamed",
                              {"new_title": "整理文件夹笔记", "by": "auto"},
                              "system")]
    assert isinstance(llm.prompts[0], str)   # 单 prompt 契约(非 messages 列表)


async def test_inv02_auto_title_idempotent_skips_llm():
    """INV-02 / F042:日志已有 `session.renamed` ⇒ 幂等返回 None 且**不再调 LLM**。"""
    from pyharness.core.auto_title import auto_title

    llm = _RecordingLLM()
    sess = _FakeSession()
    sess.events.append(types.SimpleNamespace(type="session.renamed",
                                             payload={"new_title": "旧标题"}))
    ctx = types.SimpleNamespace(llm=llm, session=sess)

    assert await auto_title(ctx) is None
    assert llm.calls["mini"] == 0 and llm.calls["chat"] == 0
    assert sess.appended == []


# ================================================================ INV-01 · 只追加(物理层)
# 证明的子性质 = canonical INV-01 的**第一分句**(「日志只追加」)的**物理面**:
# 真实文件在 append 后旧字节逐字不变(⇒ 是 append 不是 rewrite)。
# 第二分句(「历史必由日志派生」)与"无改写 API"已由下列既有用例覆盖,**本文件不重复建设**:
#   tests/unit/test_session.py::test_method_surface_append_only_inv01      (无 update/delete API)
#   tests/unit/test_session.py::test_append_only_no_second_history_store_inv02 (无第二份历史存储)
#   tests/unit/test_session.py::test_derive_only_changes_via_append_inv02  (历史只随 append 变化)


def _record_to_store(store):
    """总线→存储订阅(镜像 `engine._record_to` 的语义:强同步即写即刷)。

    强同步清单**唯一真源** = `events.vocab.SYNC_TYPES`(ADR-019 P-3 收敛),本处不复制清单。
    """
    from pyharness.events.vocab import SYNC_TYPES

    async def _record(type_, payload):
        if not hasattr(payload, "model_dump_json"):     # 瞬时类型不进 JSONL
            return
        await store.append(payload, sync=type_ in SYNC_TYPES)
    return _record


async def test_inv01_append_is_byte_level_append_only(tmp_path):
    """INV-01(分句① · 物理层):真实日志文件 append 后**旧字节逐字不变**。

    子性质:操作是 **append 而非 rewrite** —— 用真实 `SessionStore`(tmp_path)+
    真实 `SessionLog` + 真实 `EventBus` 组装,走与生产同一条落盘路径。
    断言:① 文件前缀逐字节相同;② 前缀 sha256 不变;③ 文件只增长;
         ④ 既有事件序列仍完整可回放。
    """
    from pyharness.bus import EventBus
    from pyharness.core.session import SessionLog
    from pyharness.persistence import open_store

    sid = "s-inv01-bytes"
    store = open_store(sid, dir=tmp_path)
    bus = EventBus()
    for t in ("session.created", "user.message"):
        bus.subscribe(t, _record_to_store(store))
    log = SessionLog(sid, persistence=store, bus=bus)

    await log.append("session.created", {"title": "", "model": "m"}, actor="system")
    await store.flush()          # session.created 非强同步 ⇒ 显式落盘以取得字节基线
    before = store.path.read_bytes()
    assert before, "前置条件:首事件应已落盘(否则本用例无鉴别力)"
    h_before = hashlib.sha256(before).hexdigest()

    await log.append("user.message", {"content": "你好"}, actor="user")  # 强同步:即写即刷
    after = store.path.read_bytes()

    assert after[: len(before)] == before, "旧字节被改动 ⇒ 非 append(INV-01 违约)"
    assert hashlib.sha256(after[: len(before)]).hexdigest() == h_before, \
        "旧前缀 hash 变化 ⇒ 发生了 rewrite"
    assert len(after) > len(before), "文件未增长 ⇒ 本次写入不是 append"
    assert [e.type for e in store.replay()] == ["session.created", "user.message"], \
        "既有事件序列不完整 ⇒ append 破坏了历史"
    store.close()


def test_inv01_append_only_predicate_detects_rewrite(tmp_path):
    """自检:上条的字节判据**真的能**识别 rewrite(否则判据是空转)。

    在 tmp 内构造两种情形,断言判据在"合法 append"通过、在"原地改写"失败。
    本用例只操作 tmp 文件,**不触碰生产代码**。
    """
    p = tmp_path / "log.jsonl"
    p.write_bytes(b'{"seq":1}\n')
    before = p.read_bytes()

    with open(p, "ab") as fh:                       # 合法:纯追加
        fh.write(b'{"seq":2}\n')
    assert p.read_bytes()[: len(before)] == before, "判据漏判合法 append(假阳性)"

    p.write_bytes(b'{"seq":9}\n{"seq":2}\n')        # 违约:原地改写首行
    assert p.read_bytes()[: len(before)] != before, "判据未能识别 rewrite(假阴性)"


# ---------------------------------------------------------------- INV-01 · 无第二写入口(静态)
# 会话日志/消息历史的**物理写入面**白名单:每个写站点必须显式登记"为何不是第二份消息历史"。
# 白名单为**文件级**(不锁行号 ⇒ 不因文件上方增删而误报),且**双向校验**:
# 既查"有写站点但未登记",也查"已登记但已无写站点"(防白名单过期)。
_WRITE_ALLOWLIST = {
    "persistence.py": "SessionStore 追加句柄(open 'a')+ repair 专用原子截断重写"
                      "(_rewrite_without_tail:临时文件+fsync+rename,逐字节保留全部完整行)",
    "repair.py": "隔离坏行(quarantine:坏行副本追加/重写),不改主日志的完好行",
    "cli.py": "CLI 配置导出/初始化(写 YAML 配置文件,非会话日志)",
    "core/spill.py": "工具大结果 spill 落盘(非会话消息历史)",
    "core/tool_fs.py": "文件工具 Provider(受 guard;非会话日志)",
    "core/skill_registry.py": "技能包缓存元数据(非会话日志)",
    "core/tenant_settings.py": "租户设置原子写(非会话日志)",
}

_WRITE_MODES = {"w", "a", "wb", "ab", "w+", "a+", "x", "xb", "r+", "r+b", "w+b"}


def _write_sites(root: pathlib.Path) -> dict:
    """{包内相对路径: [行号…]} —— "内容写盘"站点(写模式 open / write_text / write_bytes)。

    范围说明:只看**内容写盘**;`unlink`/`replace` 等元操作不在内(由 INV-01 的
    方法面断言与 INV-04/05 的执行面用例覆盖)。`os.open(...)` 一律计入(保守)。

    语法不可解析的文件**不静默跳过** —— 跳过会让扫描不完整并可能掩盖写站点,
    故直接失败(区分"实现缺陷"与"扫描无法覆盖";后者属测试自身问题,须响亮暴露)。
    """
    out: dict = {}
    for p in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError as e:                     # 扫描不完整 ⇒ 响亮失败,不静默
            raise AssertionError(
                f"扫描无法覆盖 {p.relative_to(root).as_posix()}(语法不可解析): {e}") from e
        hits = []
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            f = n.func
            if not isinstance(f, (ast.Name, ast.Attribute)):
                continue
            name = f.attr if isinstance(f, ast.Attribute) else f.id
            if name in ("write_text", "write_bytes"):
                hits.append(n.lineno)
                continue
            if name != "open":
                continue
            # os.open(...):保守计为写站点(其 mode 为整数 flag,无法静态判定)
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) \
                    and f.value.id == "os":
                hits.append(n.lineno)
                continue
            # open(path, mode) / open(path, mode=...):仅字面量写模式计入
            cand = [a for a in n.args[1:2]]
            cand += [kw.value for kw in n.keywords if kw.arg == "mode"]
            if any(isinstance(c, ast.Constant) and c.value in _WRITE_MODES
                   for c in cand):
                hits.append(n.lineno)
        if hits:
            out[p.relative_to(root).as_posix()] = sorted(hits)
    return out


def test_inv01_no_second_message_history_write_path():
    """INV-01(分句①「唯一真源」· 静态):包内**每个**写盘站点都必须显式登记理由。

    子性质:不存在**未登记的**第二条消息历史持久化/append 路径 —— 新增任何写路径
    都必须在此白名单中声明"为何不是第二份会话日志",否则本用例 RED。
    白名单双向校验(防漏登记 + 防过期条目)。
    """
    sites = _write_sites(_PKG)
    unlisted = sorted(set(sites) - set(_WRITE_ALLOWLIST))
    assert not unlisted, (
        "INV-01 违约:出现未登记的写盘路径(疑第二份消息历史持久化)-> "
        + ", ".join(f"{f}:{sites[f]}" for f in unlisted))
    stale = sorted(set(_WRITE_ALLOWLIST) - set(sites))
    assert not stale, (
        "白名单过期:下列文件已无写站点,须同步收缩以免掩盖未来回归 -> " + ", ".join(stale))


def test_inv01_write_scan_detects_rogue_writer(tmp_path):
    """自检:扫描器**真的能**捕获"新增的第二条持久化路径",且不误报只读打开。

    只在 tmp 合成树上运行 ⇒ **不触碰生产代码**,且该自检**永久可复现**
    (等价于一次 mutation check:注入违规写路径 → 扫描器必须报出)。
    """
    pkg = tmp_path / "pyharness"
    (pkg / "core").mkdir(parents=True)
    (pkg / "core" / "rogue.py").write_text(
        "from pathlib import Path\n\n\ndef persist(msg):\n"
        "    Path('history.jsonl').write_text(msg)\n", encoding="utf-8")
    (pkg / "core" / "reader.py").write_text(
        "def load(p):\n"
        "    with open(p, 'r', encoding='utf-8') as fh:\n"
        "        return fh.read()\n", encoding="utf-8")

    sites = _write_sites(pkg)
    assert "core/rogue.py" in sites, "扫描器漏检第二条持久化写路径(假阴性)"
    assert "core/reader.py" not in sites, "只读打开被误判为写站点(假阳性;防简单 grep 误报)"
