"""tests/invariants/test_inv_core.py — 核心不变量编号化用例:INV-01 / INV-02 / INV-03 / INV-04。

唯一语义来源 = `docs/INVARIANT_REGISTRY.md`(Canonical Invariant Registry):
  · **INV-02** 无绕过 agent-loop 直调 llm —— 冻结依据 `PRD-Core.md:838,634` ·
    `CONSTRAINTS-06-Testing.md:63` · `DIS-CORE.md:186` · `PRD-Core.md:1251`(F042 指定出口 `ctx.llm.mini`)。
  · **INV-01** 日志只追加 / 历史必由日志派生 —— 冻结依据 `CONSTRAINTS-06-Testing.md:62` · `PRD-Core.md:360`。
  · **INV-03** rebuild 与缓存一致 —— 冻结依据 `CONSTRAINTS-06-Testing.md:64` · `EVENT-SCHEMA.md:503,526`。
  · **INV-04** 无 guard 事件即非法执行(含单调拒绝 / 无 bypass)—— 冻结依据 `CONSTRAINTS-06-Testing.md:65` ·
    `SECURITY.md:199,212` · `DIS-SEAM.md:582,640`;`tool.error` 两类区分见 **F-1 裁定**(2026-09-15)。

来源:本文件由 S6-2a-P0-F(KF-A 修复)建立,S6-2b-1/2/3 在**同一文件**逐条扩展
(不另建第二套编号化用例)。

边界:静态扫描对象 = 运行时包 `pyharness/`。`scripts/` 下的人工探针(e2e / probe)
不参与装配、不被 `pyharness` import,不在这些不变量的运行时边界内。

**类别式边界**(非例外名单):`chat`/`chat_stream` = Agent Loop **对话出口**(唯一合法
调用方 = `agent_loop`);`mini`/`summarize`/`json_chat` = **System Tool LLM 出口**,
不受 INV-02 的调用方约束,但其成员由 `LLMClient` 公开面显式枚举。
"""
from __future__ import annotations

import ast
import hashlib
import pathlib
import types
from typing import Optional

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


# ================================================================ INV-03 · rebuild 与缓存一致
# 本段只补 S6-2b-2 审计确认的 3 条 partial 性质(session 侧),**不重复**以下已充分覆盖者:
#   二次 rebuild 无重复      -> tests/unit/test_session.py::test_rebuild_from_log_invariant_inv03
#                              tests/unit/test_session_query.py::test_rebuild_then_incremental_no_dup
#   全量 <-> 增量一致        -> tests/unit/test_session.py::test_events_after_incremental_gwt_s3_04
#                              tests/unit/test_session_query.py::test_rebuild_full_matches_incremental
#   编辑重放(query 侧)      -> test_session_query.py::test_rebuild_full_matches_incremental
#   cache invalidation       -> tests/unit/test_session.py::test_rebuild_derived_cache_invalidation_on_append
#   重启恢复 == 崩溃前      -> tests/unit/test_session.py::test_open_session_recovers_state


def _wired_log(sid: str, tmp_path):
    """真实装配:`SessionLog` + `EventBus` → **真实 `SessionStore`(文件真源)**。

    与生产同一条落盘路径(镜像 `engine._record_to` 的订阅语义);词表全量订阅,
    故 `store.replay()` 即真源读取入口 —— rebuild 走真实 replay 路径。
    """
    from pyharness.bus import EventBus
    from pyharness.core.session import SessionLog
    from pyharness.events import EVENT_TYPES
    from pyharness.persistence import open_store

    store = open_store(sid, dir=tmp_path)
    bus = EventBus()
    rec = _record_to_store(store)
    for t in EVENT_TYPES:
        bus.subscribe(t, rec)
    return SessionLog(sid=sid, persistence=store, bus=bus), store


async def _seed_rebuild_fixture(log) -> None:
    """固定语料(多类型事件 + 一条编辑)——**不含时间/随机量**,供确定性用例复用。"""
    await log.append("session.created", {"title": "", "model": "m"}, actor="system")
    await log.append("user.message", {"content": "把 D:/杂乱 按主题归类"}, actor="user")
    await log.append("user.message_edited",
                     {"target_seq": 2, "new_content": "把 D:/work 归档"}, actor="user")
    await log.append("llm.response", {"model": "m", "finish_reason": "stop",
                                      "content": "好的"}, actor="llm")
    await log.append("tool.result", {"name": "list_dir", "call_id": "call_1", "ok": True,
                                     "summary": "42 项:3 文件夹,39 文件",
                                     "truncated": False}, actor="tool")
    await log.append("guard.rejected", {"tool": "shell_exec", "guard_id": "g-danger-cmd",
                                        "reason": "rm -rf 高危"}, actor="tool")


# ---------------------------------------------------------------- T-1(INV-03 · 编辑重放 · session 侧)
async def test_inv03_rebuild_preserves_edit_override(tmp_path):
    """INV-03(T-1):含 `user.message_edited` 的日志经 **session 侧** `rebuild_from_log()`
    后,`derive_messages()` 仍取**编辑后的新版**内容,且日志**原文两行都在**。

    子性质:**编辑重放(edited 覆盖目标行)** 在**会话内存缓存**这一侧成立 ——
    此前只有 query 投影侧(`test_rebuild_full_matches_incremental`)有证据。
    前置:`store.flush()` 把非强同步事件落盘,保证 rebuild 的 replay 输入完整。
    """
    log, store = _wired_log("s-inv03-edit", tmp_path)
    await log.append("session.created", {"title": "", "model": "m"}, actor="system")
    await log.append("user.message", {"content": "把 D:/杂乱 按主题归类"}, actor="user")
    await log.append("user.message_edited",
                     {"target_seq": 2, "new_content": "把 D:/work 归档"}, actor="user")
    await store.flush()                     # 前置:replay 输入完整

    before_msgs = log.derive_messages()
    assert before_msgs == [{"role": "user", "content": "把 D:/work 归档"}], \
        "前置条件:rebuild 前派生应已取新版(否则本用例无鉴别力)"

    log.rebuild_from_log()                  # ★ session 侧 rebuild(真实 replay 路径)

    assert log.derive_messages() == before_msgs, \
        "INV-03 违约:rebuild 后未取编辑后的新版(edited 覆盖丢失)"
    evs = list(log.events_after())
    assert [e.type for e in evs] == ["session.created", "user.message",
                                     "user.message_edited"]
    assert evs[1].payload["content"] == "把 D:/杂乱 按主题归类", \
        "INV-03 违约:rebuild 改写了日志原文(取新版须留旧痕)"
    assert evs[2].payload["new_content"] == "把 D:/work 归档"
    store.close()


# ---------------------------------------------------------------- T-2(INV-03 · 真源零变化)
async def test_inv03_rebuild_does_not_touch_truth_source(tmp_path):
    """INV-03(T-2):`rebuild_from_log()` **只读**真源 —— 事件日志 bytes / sha256 /
    事件序列在 rebuild 前后**完全一致**(可变更的只有状态派生)。

    子性质:**rebuild 无错误副作用**(rebuild 是纯派生,绝不回写事件日志)。
    """
    log, store = _wired_log("s-inv03-notouch", tmp_path)
    await _seed_rebuild_fixture(log)
    await store.flush()

    before_bytes = store.path.read_bytes()
    before_sha = hashlib.sha256(before_bytes).hexdigest()
    before_seqs = [(e.seq, e.type) for e in store.replay()]
    assert before_bytes and before_seqs, "前置条件:真源应已落盘且非空"

    log.rebuild_from_log()

    assert store.path.read_bytes() == before_bytes, \
        "INV-03 违约:rebuild 改动了真源字节(事件日志必须零变化)"
    assert hashlib.sha256(store.path.read_bytes()).hexdigest() == before_sha, \
        "INV-03 违约:真源 sha256 变化"
    assert [(e.seq, e.type) for e in store.replay()] == before_seqs, \
        "INV-03 违约:rebuild 后真源事件序列变化"
    store.close()


# ---------------------------------------------------------------- T-3(INV-03 · 确定性)
async def test_inv03_rebuild_is_deterministic(tmp_path):
    """INV-03(T-3):**同一日志、输入不变**,连续 `rebuild_from_log()` 多次 ⇒
    结果(A/B/C 三次快照)**完全一致**。

    子性质:**rebuild 的确定性**。快照只取**稳定量**(事件 seq/type 序列 · 派生消息 ·
    `stats()` 字典),**不含时间 / 随机 ID / 调用次数**等不稳定因素。
    """
    log, store = _wired_log("s-inv03-det", tmp_path)
    await _seed_rebuild_fixture(log)
    await store.flush()

    def snapshot():
        return ([(e.seq, e.type) for e in log.events_after()],
                log.derive_messages(),
                log.stats())

    log.rebuild_from_log()
    a = snapshot()
    log.rebuild_from_log()                  # 输入未变
    b = snapshot()
    log.rebuild_from_log()                  # 第三次:加强验证
    c = snapshot()

    assert a == b, "INV-03 违约:连续两次 rebuild 结果不一致(非确定性)"
    assert b == c, "INV-03 违约:第三次 rebuild 结果不一致(非确定性)"
    store.close()


# ================================================================ INV-04 · guard 事件与单调拒绝
# 语义来源:docs/INVARIANT_REGISTRY.md(Canonical INV-04)——(a) 执行前必有求值事实 +
#          (b) 结构单调性。
#
# **F-1 裁定(2026-09-15,设计理解更新;未改 Registry)**:
#   INV-04(a)「执行前必有 guard.evaluated」的适用范围 = **真实工具执行路径**——
#     · ``tool.result``            ⇒ 必须有同 ``call_id`` 的 ``guard.evaluated``;
#     · ``tool.error`` 执行前失败  ⇒ **不要求** ``guard.evaluated``,但**必须证明
#                                     Provider 未调用**(unknown tool / invalid args /
#                                     wiring failure —— 三者都在关 2 之前返回);
#     · ``tool.error`` 执行后失败  ⇒ **必须**有 ``guard.evaluated``,且**先于** ``tool.error``
#                                     (Provider 已进入执行路径)。
#
# 本段只补审计确认的缺口(A4 / A5 / G-1 / G-2 + F-1 裁定新增的"两类 tool.error"),
# **不重复**已 covered 的 8 条(A1/A2/A3/B1~B6 —— 既有断言见 §"无重复建设"注释)。

# ---------------------------------------------------------------- INV-04 本地夹具
_READ_SCHEMA_INV04 = {"type": "object",
                      "properties": {"path": {"type": "string"}},
                      "required": ["path"]}


class _Sess:
    """最小异步事件落点(SessionLog 同型:append → 信封;记录强同步/trace)。"""

    def __init__(self, sid: str = "s-inv04") -> None:
        self.sid = sid
        self.events: list[dict] = []
        self._seq = 0

    async def append(self, type_, payload, *, actor, **kw):
        self._seq += 1
        self.events.append({"type": type_, "payload": dict(payload), "actor": actor,
                            "seq": self._seq, "sync": kw.get("sync", False),
                            "trace": kw.get("trace")})
        return types.SimpleNamespace(seq=self._seq, type=type_,
                                     payload=dict(payload))

    def types(self) -> list[str]:
        return [e["type"] for e in self.events]

    def of(self, t: str) -> list[dict]:
        return [e for e in self.events if e["type"] == t]


class _Prov:
    """记录型 Provider:计数 + 可选抛错(制造"执行后失败"路径)。"""

    def __init__(self, *, err: Optional[Exception] = None) -> None:
        self.calls = 0
        self.err = err

    def handle(self, args, ctx):
        self.calls += 1
        if self.err is not None:
            raise self.err
        return {"ok": True}


def _inv04_registry(prov):
    from pyharness.core.tools_registry import ToolDefinition, ToolRegistry
    reg = ToolRegistry()
    reg.register_tool(ToolDefinition(name="fs.read_file", description="inv04 工具",
                                     schema=_READ_SCHEMA_INV04, danger="none",
                                     owner="builtin"))
    reg.bind_provider("fs.read_file", prov)
    return reg


def _inv04_scope(root):
    return types.SimpleNamespace(
        policy=types.SimpleNamespace(workspace_root=str(root), allowed_domains=set(),
                                     sandbox_level="basic"),
        can_use=lambda name: True)


def _inv04_gov(chain):
    from pyharness.governance import (DecisionEngine, GovernanceContext, Policy,
                                      PolicyEngine, PolicyRegistry)
    eng = PolicyEngine(policy=Policy("test:v1", "1.0"), registry=PolicyRegistry(),
                       chain=chain)
    return GovernanceContext(policy=eng, decisions=DecisionEngine(), receipts=None)


def _inv04_ctx(sess, root, *, chain=None, gov=None,
               with_session=True, with_scope=True,
               with_guard=True, with_governance=True):
    """可逐件置 None 的 ctx(缺件矩阵用);缺省四件齐备。"""
    return types.SimpleNamespace(
        session=sess if with_session else None,
        scope=_inv04_scope(root) if with_scope else None,
        guard=chain if with_guard else None,
        governance=gov if with_governance else None,
        approval=None, storage=None, channel="cli", headless=False,
        session_id=getattr(sess, "sid", "s-inv04"))


def _inv04_call(name="fs.read_file", raw=None, call_id="c-inv04"):
    from pyharness.core.tools_guard import ToolCall
    return ToolCall(name=name, raw_args=dict(raw or {"path": "a.txt"}),
                    call_id=call_id)


# ---------------------------------------------------------------- T-1(INV-04 · A4 缺件 fail-closed)
@pytest.mark.parametrize("missing", ["session", "scope", "guard", "governance"])
async def test_inv04_require_wiring_fail_closed_all_parts(tmp_path, missing):
    """INV-04(A4):执行前置装配**缺任一件** ⇒ fail-closed(``CYC-999``),
    且 **Provider 零调用**、**无 ``tool.result``**(绝不带缺件执行)。

    子性质:**缺 guard.evaluated 的执行非法**的结构保证 —— 四件(session/scope/
    guard/governance)任一缺失都在关 1 之前上抛,调用方拿不到任何执行结果。
    既有用例只覆盖 guard / governance 两分支(缺口 G-3),本用例补齐四分支。
    """
    from pyharness.core.tools_executor import ToolExecutor
    from pyharness.core.tools_guard import GuardChain
    from pyharness.errors import PyHError

    sess, prov = _Sess(), _Prov()
    reg = _inv04_registry(prov)
    chain = GuardChain(session=sess)
    ctx = _inv04_ctx(sess, tmp_path, chain=chain, gov=_inv04_gov(chain),
                     with_session=(missing != "session"),
                     with_scope=(missing != "scope"),
                     with_guard=(missing != "guard"),
                     with_governance=(missing != "governance"))

    with pytest.raises(PyHError) as ei:
        await ToolExecutor(reg).execute(_inv04_call(), ctx)

    assert ei.value.code == "CYC-999", f"缺 {missing} 应 fail-closed(CYC-999)"
    assert prov.calls == 0, f"缺 {missing} 时 Provider 不得被调用"
    assert not sess.of("tool.result"), f"缺 {missing} 时不得产生 tool.result"


# ---------------------------------------------------------------- T-2(INV-04 · A5 无绕过路径)
# Provider 的两道闸:①**获取** = `registry.lookup_provider()`;②**调用** = `.handle(...)`。
# 二者各自白名单化:任何新增旁路都会在对应闸门 RED。
_INV04_ACQUIRE_ALLOWLIST = {
    "core/tools_executor.py": "唯一 Provider **获取**入口 —— `_provider_handle()` 经 "
                              "`registry.lookup_provider()` 取回可调用面",
}
_INV04_HANDLE_ALLOWLIST = {
    "core/tool_skill.py": "skill Provider 调用其**私有** `_SkillHandle` 对象的同名方法"
                          "(内层委派),非 registry 提供的 Provider",
}


def _inv04_attr_calls(root: pathlib.Path, attr: str) -> list:
    """[(相对路径, 行号, 所在函数名)] —— 全包 `.<attr>(` 属性调用点。

    语法不可解析的文件**不静默跳过**(跳过会让扫描不完整),直接失败。
    """
    sites = []
    for p in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError as e:                 # 扫描不完整 ⇒ 响亮失败
            raise AssertionError(
                f"扫描无法覆盖 {p.relative_to(root).as_posix()}(语法不可解析): {e}") from e
        funcs = [(n.lineno, n.name) for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == attr):
                enclosing = max((f for f in funcs if f[0] <= n.lineno),
                                default=(0, "?"), key=lambda x: x[0])[1]
                sites.append((p.relative_to(root).as_posix(), n.lineno, enclosing))
    return sites


def _inv04_gate(attr: str, allow: dict, what: str) -> list:
    """通用闸门断言:调用点 ⊆ 白名单(双向校验)+ 返回调用点。"""
    sites = _inv04_attr_calls(_PKG, attr)
    files = {f for f, _, _ in sites}
    unlisted = sorted(files - set(allow))
    assert not unlisted, (
        f"INV-04 违约:出现未登记的 Provider {what}(疑绕过 tools_executor) -> "
        + ", ".join(f"{f}:{[ln for ff, ln, _ in sites if ff == f]}" for f in unlisted))
    stale = sorted(set(allow) - files)
    assert not stale, (
        f"白名单过期:{what}侧下列文件已无 `.{attr}(` 调用点,须同步收缩 -> "
        + ", ".join(stale))
    return sites


def test_inv04_provider_acquisition_surface_is_executor_only():
    """INV-04(A5 · 闸①):`registry.lookup_provider()` 调用点 ⊆ `{tools_executor}`,
    且归属 `_provider_handle` —— **任何模块想拿到已注册 Provider 都必须过此门**,
    故旁路获取在执行面上不可能。正向控制防止"删掉调用即变绿"。
    """
    sites = _inv04_gate("lookup_provider", _INV04_ACQUIRE_ALLOWLIST, "获取面")
    assert sites and all(fn == "_provider_handle" for _, _, fn in sites), (
        f"Provider 获取点应归属 _provider_handle -> {sites}")


def test_inv04_provider_handle_call_surface_is_allowlisted():
    """INV-04(A5 · 闸②):`.handle(` **调用点** ⊆ 白名单(逐条写明理由)。

    子性质:**无绕过路径** —— 任何新增的 Provider 对象直调都会在此 RED。
    """
    _inv04_gate("handle", _INV04_HANDLE_ALLOWLIST, "调用面")


# ---------------------------------------------------------------- T-3(INV-04 · G-1 call_id 配对)
async def test_inv04_guard_evaluated_and_tool_result_share_call_id(tmp_path):
    """INV-04(A1/A3):同一次调用的 `guard.evaluated` 与 `tool.result` 的
    **`call_id` 逐字段一致**(仅断言"事件序"不足以发现 call_id 被写成常量/异值)。

    子性质:`call_id` 是规则级留痕与执行结果的**配对锚点**;配对断裂 ⇒ 审计
    无法证明"这次执行过了 guard"。
    """
    from pyharness.core.tools_executor import ToolExecutor
    from pyharness.core.tools_guard import GuardChain

    sess, prov = _Sess(), _Prov()
    chain = GuardChain(session=sess)
    ctx = _inv04_ctx(sess, tmp_path, chain=chain, gov=_inv04_gov(chain))
    await ToolExecutor(_inv04_registry(prov)).execute(
        _inv04_call(call_id="c-pair"), ctx)

    ge = sess.of("guard.evaluated")
    tr = sess.of("tool.result")
    assert len(ge) == 1 and len(tr) == 1
    assert (ge[0]["trace"] or {}).get("call_id") == "c-pair", \
        f"guard.evaluated 的 call_id 应经 trace 携带 -> {ge[0]['trace']}"
    assert tr[0]["payload"]["call_id"] == "c-pair"
    assert (ge[0]["trace"] or {}).get("call_id") == tr[0]["payload"]["call_id"], \
        "INV-04 违约:guard.evaluated 与 tool.result 的 call_id 不一致(配对断裂)"


async def test_inv04_reject_path_call_id_pairing(tmp_path):
    """INV-04(A3 · 拒绝路径):`guard.evaluated` / `guard.rejected` / `tool.call`
    三者的 `call_id` 一致,且**无 `tool.result`**(拦了且没执行,INV-05)。"""
    from pyharness.core.tools_executor import ToolExecutor
    from pyharness.core.tools_guard import GuardChain

    sess, prov = _Sess(), _Prov()
    chain = GuardChain(session=sess)
    ctx = _inv04_ctx(sess, tmp_path, chain=chain, gov=_inv04_gov(chain))
    # danger=critical ⇒ g-danger 直接 reject(不可审批)
    reg = _inv04_registry(prov)
    from pyharness.core.tools_registry import ToolDefinition
    reg.register_tool(ToolDefinition(name="fs.delete_file", description="危险工具",
                                     schema=_READ_SCHEMA_INV04, danger="critical",
                                     owner="builtin"))
    reg.bind_provider("fs.delete_file", prov)
    await ToolExecutor(reg).execute(
        _inv04_call(name="fs.delete_file", call_id="c-rej"), ctx)

    ge = sess.of("guard.evaluated")
    gr = sess.of("guard.rejected")
    tc = sess.of("tool.call")
    assert len(ge) == 1 and len(gr) == 1 and len(tc) == 1
    ids = {(ge[0]["trace"] or {}).get("call_id"),
           (gr[0]["trace"] or {}).get("call_id"),
           tc[0]["payload"]["call_id"]}
    assert ids == {"c-rej"}, f"拒绝路径 call_id 应三处一致 -> {ids}"
    assert not sess.of("tool.result"), "reject 后不得产生 tool.result(INV-05)"
    assert prov.calls == 0


# ---------------------------------------------------------------- T-4(INV-04 · G-2 挂载恒链尾)
def test_inv04_plugin_guard_appends_to_tail_only():
    """INV-04(B5):`register_plugin_guard` **恒在链尾**,既有 id 序列**前缀恒等**,
    装配版本号单调 +1(绝无插队/重排)。

    子性质:"只增拒绝面"的**顺序面**保证 —— 插件 guard 只能追加,不能插到既有
    guard 之前(那会改变求值序)。既有用例只断言"不可翻回",未断言链尾位置。
    """
    from pyharness.core.tools_guard import Guard, GuardChain

    class _AllowAll(Guard):
        id = "g-plugin-inv04"
        def match(self, call): return True
        def check(self, call, scope): return ("allow", None)

    chain = GuardChain()
    before = [g.id for g in chain.chain]
    version0 = chain.chain_version()

    chain.register_plugin_guard(_AllowAll())

    after = [g.id for g in chain.chain]
    assert after[:len(before)] == before, "既有 guard 序列被改动(前缀恒等被破)"
    assert after[-1] == "g-plugin-inv04", "新挂 guard 未落在链尾"
    assert len(after) == len(before) + 1
    assert chain.chain_version() == version0 + 1, "装配版本号应单调 +1"


# ---------------------------------------------------------------- T-5(INV-04 · F-1 两类 tool.error)
async def test_inv04_tool_error_two_classes(tmp_path):
    """INV-04(F-1 裁定):`tool.error` 分**两类**,审计要求不同。

    **① 执行前失败**(unknown tool / invalid args)⇒ **不要求** `guard.evaluated`,
    但**必须证明 Provider 未调用**;
    **② 执行后失败**(Provider 已进入执行路径)⇒ **必须**有 `guard.evaluated`,
    且**先于** `tool.error`,且两者 `call_id` 一致。
    """
    from pyharness.core.tools_executor import ToolExecutor
    from pyharness.core.tools_guard import GuardChain

    # ---- ①a 执行前失败:unknown tool(TLB-802)
    sess, prov = _Sess(), _Prov()
    chain = GuardChain(session=sess)
    ctx = _inv04_ctx(sess, tmp_path, chain=chain, gov=_inv04_gov(chain))
    await ToolExecutor(_inv04_registry(prov)).execute(
        _inv04_call(name="no.such_tool", raw={}, call_id="c-1"), ctx)
    assert sess.types() == ["tool.error"], f"①a 应只发 tool.error -> {sess.types()}"
    assert not sess.of("guard.evaluated"), "①a 执行前失败**不要求** guard.evaluated"
    assert prov.calls == 0, "①a 必须证明 Provider 未调用"

    # ---- ①b 执行前失败:invalid args(TLB-803)
    sess, prov = _Sess(), _Prov()
    chain = GuardChain(session=sess)
    ctx = _inv04_ctx(sess, tmp_path, chain=chain, gov=_inv04_gov(chain))
    await ToolExecutor(_inv04_registry(prov)).execute(
        _inv04_call(raw={"path": 123}, call_id="c-2"), ctx)
    assert sess.types() == ["tool.error"], f"①b 应只发 tool.error -> {sess.types()}"
    assert not sess.of("guard.evaluated"), "①b 执行前失败**不要求** guard.evaluated"
    assert prov.calls == 0, "①b 必须证明 Provider 未调用"

    # ---- ② 执行后失败:Provider 抛错(已进入执行路径)
    sess, prov = _Sess(), _Prov(err=RuntimeError("boom"))
    chain = GuardChain(session=sess)
    ctx = _inv04_ctx(sess, tmp_path, chain=chain, gov=_inv04_gov(chain))
    await ToolExecutor(_inv04_registry(prov)).execute(
        _inv04_call(call_id="c-3"), ctx)
    types_ = sess.types()
    assert "tool.error" in types_, f"② 应发 tool.error -> {types_}"
    ge, te = sess.of("guard.evaluated"), sess.of("tool.error")
    assert len(ge) == 1, f"② 执行后失败**必须**有 guard.evaluated -> {types_}"
    assert types_.index("guard.evaluated") < types_.index("tool.error"), \
        f"② guard.evaluated 必须先于 tool.error -> {types_}"
    assert (ge[0]["trace"] or {}).get("call_id") == te[0]["payload"]["call_id"] == "c-3"
    assert prov.calls == 1, "② 已进入执行路径(Provider 恰 1 次)"
