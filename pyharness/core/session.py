"""pyharness/core/session.py — 会话事件日志门面(specs/session.py.md 契约;阶段 1 核心)

SessionLog = 会话唯一写入口(append)+ 派生视图工厂(derive_messages/events_after/
events_between/get)+ 恢复重建(open_session/rebuild_from_log)。append-only 是铁律
(INV-01):本模块不提供任何 update/delete/原地改写方法——"修正" = 追加修正事件
(user.message_edited / context.compacted / session.recovered),原文永留日志,reducer
取新版留旧痕。消息历史 = 日志派生(INV-02):不存在第二份内存历史,derive_messages 是
唯一权威 reducer,UI/FTS/计量一切视图都经它投影。

事件模型(Envelope/词表/SeqState/五步校验链)来自 pyharness.events,不在本模块重复
定义;错误全走 errors.raise_code(EVT-1xx/PERS-2xx),禁裸 raise str。

持久化边界:物理落盘归 core/persistence(SessionStore,阶段后续模块)。本模块经注入的
persistence(Protocol,鸭子类型)只调 replay()(只读回放)与 flush(seq)(强同步落盘);
写路径事件由总线日志订阅者负责持久化(与 DIS-CORE §8 一致)。
"""
from __future__ import annotations

import bisect
import logging
import re
from typing import Iterator, Optional, Protocol, TYPE_CHECKING

from pyharness.errors import raise_code
from pyharness.events import (DECLARE_TYPES, Envelope, SeqState, SYNC_TYPES,
                              call_id_of, declared_ranges, is_transient,
                              make_envelope)

if TYPE_CHECKING:  # 总线仅作类型标注;构造未接线时跳过分发(不硬依赖装配)
    from pyharness.bus import EventBus

log = logging.getLogger("pyharness.session")


# ------------------------------------------------------------------ 注入协议
class SessionStoreProtocol(Protocol):
    """persistence.SessionStore 最小契约(注入用;core/persistence 后续实现)。

    session → persistence 单向依赖(INV-08):只读 replay + 强同步 flush;
    不反向 import persistence 模块,避免阶段装配期硬依赖。
    """

    def replay(self) -> Iterator[Envelope]:
        """严格行解析回放,坏行记 PERS-201 跳隔离,不中断(§3.8/§5.3)。"""
        ...

    async def flush(self, seq: int) -> None:
        """强同步落盘:立即 write+flush,成功才返回;失败抛 PERS-202。"""
        ...


# 估 token 启发式(无权威 est_tokens 实现;仅窗口截断用,不影响 reducer 映射):
# CJK 逐字 1 token;ASCII 词按 4 字符 ≈ 1 token;每条消息 +4 role 开销常量。
_ASCII_WORD = re.compile(r"[A-Za-z0-9_./\\@:+-]+")


def _estimate_tokens(content: str) -> int:
    """消息文本 token 粗估(中文逐字,西文按词;截断阈值仅需量级近似)。"""
    cjk = sum(1 for ch in content if "\u4e00" <= ch <= "\u9fff")
    words = sum(max(1, (len(w) + 3) // 4) for w in _ASCII_WORD.findall(content))
    return cjk + words + 4  # +4: role/content 等结构开销(启发式常量)


# ---------------------------------------------------- 拒绝反馈文本(ADR-022 D-6)
# 拒绝类事件投影为配对 tool 消息时的 content。**脱敏**:只含工具名与规则/策略引用,
# **不含参数原文与凭据**(SECURITY §6.4 既有纪律)。
_APPROVAL_REJECT_NOTES: dict = {
    "approval.denied": "调用被拒绝:人工审批未通过",
    "approval.timeout": "调用被拒绝:人工审批超时",
}


def _guard_reject_note(payload: dict) -> str:
    """guard 链拒绝的反馈文本(工具名 + guard_id/policy_ref,脱敏)。"""
    tool = str(payload.get("tool") or "工具")
    ref = str(payload.get("guard_id") or payload.get("policy_ref") or "")
    return f"{tool} 调用被拒绝:{ref}" if ref else f"{tool} 调用被拒绝"


# ------------------------------------------------------------------ SessionLog
class SessionLog:
    """会话事件日志门面(F009):append-only 唯一写入口 + 派生视图工厂。

    私有字段(可弃重建,spec 清单):
        sid            会话 id(不可变)
        _seq           已分配最大 seq(从日志 max 续写,空洞不回填)
        _cache         内存事件缓存(seq 升序;append-only,可整体重建)
        history_cache  派生消息缓存:仅日志尾部未变时有效,append 即失效(INV-03)
        _folded        context.compacted 声明区间索引(空洞合法化口径)
        _closed        终态拒写置位(finished 后任何 append → EVT-104)
        _persistence   注入的 SessionStore(仅 replay/flush)
    附加实现字段:_seqs(与 _cache 平行的 seq 索引,二分定位)、_seq_state(events 层
    seq 分配器,单真源:next = _seq+1;open = 已 created 且未 finished)、_bus(可选
    注入,分发事件;未接线 = 纯内存模式)、_holes_warned(回放未声明空洞告警清单)。
    """

    # 无 update/delete/remove/clear 等改写 API——append-only(INV-01),方法面由测试钉死。
    def __init__(self, sid: str, *,
                 persistence: Optional[SessionStoreProtocol] = None,
                 _persistence: Optional[SessionStoreProtocol] = None,
                 bus: Optional["EventBus"] = None,
                 tenant_id: Optional[str] = None) -> None:
        """构造空会话日志。

        参数: sid = 会话 id;persistence = SessionStore 注入(新会话/恢复都建议经
        open_session 走重放路径);bus = EventBus 注入(总线日志订阅者负责物理落盘;
        None = 纯内存模式,单测/未装配阶段用)。_persistence 为 DIS-CORE §3.3.4
        旧写法的兼容别名(两份规格调用点写法不一致,见偏离说明)。
        tenant_id(GAP-10):本会话的租户归属,由**框架侧**装配时注入,经 append
        盖到每条 Envelope 上(不由各调用点逐个传)。None = 未声明(单租户)。
        """
        if _persistence is not None:  # DIS 兼容:两处规格对构造参数命名不一致
            persistence = _persistence
        self.sid: str = sid
        self.tenant_id: Optional[str] = tenant_id   # GAP-10:框架侧租户归属
        self._persistence = persistence
        self._bus = bus
        self._seq: int = 0
        self._cache: list[Envelope] = []
        self._seqs: list[int] = []            # 与 _cache 平行的 seq 升序索引(二分用)
        self._folded: list[list[int]] = []    # compacted 声明区间 [lo, hi] 列表
        self._closed: bool = False
        self.history_cache: Optional[list[dict]] = None
        self._holes_warned: list[int] = []    # warn_hole 告警的 seq(F031)
        # 空洞**合法化**声明区间(compacted 折叠 **与** recovered 修复声明)。与 _folded
        # 分开存:_folded 是"已折叠"语义(compaction 判重折用),二者不可混用(R14-9)。
        self._holes_declared: list[list[int]] = []
        self._seq_state = SeqState()          # 框架 seq 分配单真源(events 层)
        # 存在持久化真源但尚未回放:派生读先惰性 rebuild(events_after 语义)
        self._needs_rebuild: bool = persistence is not None

    # ============================================================ 内部辅助
    def _absorb(self, env: Envelope) -> None:
        """吸收一条事件入缓存(append/回放共用):seq 推进 + 声明索引同步。"""
        self._cache.append(env)
        self._seqs.append(env.seq)
        self._seq = env.seq
        if env.type == "context.compacted":
            # 折叠声明入索引(compaction 的"已折叠"语义;区间合法性由 payload 模型保证)
            for pair in env.payload["ranges"]:
                self._folded.append(list(pair))
            self._folded.sort(key=lambda r: r[0])
        if env.type in DECLARE_TYPES:
            # 空洞合法化声明(**唯一判据** `declared_ranges`):含 compacted.ranges 与
            # recovered.lost / "seq-holes:[…]"。R14-9 前此处只认 compacted ⇒ 已声明的
            # 修复空洞在下次回放被重报为"未声明空洞(疑丢事件)"。
            for lo, hi in declared_ranges(env):
                self._holes_declared.append([lo, hi])
            self._holes_declared.sort(key=lambda r: r[0])

    def _seq_index(self) -> list[int]:
        """seq 升序索引(与缓存平行;append-only 保证与 _cache 严格对齐)。"""
        return self._seqs

    def _ensure_rebuilt(self) -> None:
        """惰性重建:缓存不可用(构造后未回放)时先从磁盘真源补齐。"""
        if self._needs_rebuild:
            self.rebuild_from_log()

    def _gap_declared(self, gap_lo: int, gap_hi: int) -> bool:
        """空洞区间 [gap_lo, gap_hi] 是否被**声明**完全覆盖(合法空洞不回填)。

        判据源 = ``_holes_declared``(compacted + recovered),**不是** ``_folded``
        (后者只是"已折叠",不含修复声明;R14-9)。
        """
        return all(any(lo <= s <= hi for lo, hi in self._holes_declared)
                   for s in range(gap_lo, gap_hi + 1))

    def warn_hole(self, seq: int) -> None:
        """未声明空洞告警(F031):记录 + 日志;不中断回放、不回填。"""
        log.warning("session=%s 回放遇未声明 seq 空洞: %s(疑丢事件;跑 repair 后用 "
                    "session.recovered/context.compacted 声明)", self.sid, seq)
        self._holes_warned.append(seq)

    def _recompute_terminal(self) -> None:
        """按缓存尾部重算终态:以日志事实为准(可弃重建后保持 INV-03 一致)。"""
        self._closed = bool(self._cache) and self._cache[-1].type == "session.finished"

    async def _dispatch(self, env: Envelope) -> None:
        """总线分发(日志订阅者负责物理落盘);未接线 = 跳过,纯内存模式。"""
        if self._bus is not None:
            await self._bus.emit(env.type, env, mode="sequential")

    async def _flush(self, env: Envelope) -> None:
        """强同步落盘(成功才返回;失败由存储层抛 PERS-202,append 上抛)。"""
        if self._persistence is None:
            return  # 无注入存储:纯内存/单测模式(真装配必注 SessionStore)
        await self._persistence.flush(env.seq)

    def _ref_exists(self, target_seq: int, expect_type: str) -> bool:
        """引用锚点存在性/类型校验:target 必须存在且为期望类型(否则 BadTarget)。"""
        env = self.get(target_seq)
        return env is not None and env.type == expect_type

    @staticmethod
    def _replace_at(msgs: list[dict], sources: list[Optional[int]],
                    target_seq: int, new_content: str) -> None:
        """修正事件落地:把 target_seq 产生的消息内容替换为新版(取新版留旧痕)。

        target 已不在派生流(被压缩/修复声明)时静默跳过——日志原文仍在,投影
        不做历史外替换(§4.2 修正语义)。目标定位 = 源 seq 平行表,保证只命中
        该事件产出的消息,不误伤同内容后续消息。
        """
        for i in range(len(sources) - 1, -1, -1):
            if sources[i] == target_seq:
                msgs[i] = {**msgs[i], "content": new_content}
                return

    def _fold_history(self) -> list[dict]:
        """日志 → 消息历史纯折叠(§3.5 reducer 唯一权威实现,INV-02)。"""
        msgs: list[dict] = []
        sources: list[Optional[int]] = []     # 与 msgs 平行的源 seq(修正定位用)
        paired: set[str] = set()              # 已产出 tool 消息的 call_id(ADR-022 终局兜底用)
        req_index: dict = {}                  # approval.requested: seq → (call_id, tool)
        for ev in self._cache:                # seq 升序遍历
            t = ev.type
            p = ev.payload
            if t == "user.message":
                msgs.append({"role": "user", "content": p["content"]})
                sources.append(ev.seq)
            elif t == "user.message_edited":
                # 修正 = 追加事件:取新版留旧痕(F063);原文仍在日志
                self._replace_at(msgs, sources, p["target_seq"], p["new_content"])
            elif t == "context.compacted":
                # 摘要代折叠区间(空洞合法化锚点,§3.4/§3.5.5)
                msgs.append({"role": "system",
                             "content": f"[已压缩 {p['ranges']}] {p['summary']}"})
                sources.append(ev.seq)
            elif t == "llm.response":
                c = p.get("content") or ""
                calls = p.get("tool_calls") or []
                if c or calls:
                    # 工具轮必须重建 assistant.tool_calls(OpenAI/DeepSeek 协议:
                    # tool 消息前须有含同 id 的 assistant,否则端点 400——真链实测)
                    am: dict = {"role": "assistant", "content": c or None}
                    if calls:
                        am["tool_calls"] = []
                        for tc in calls:
                            fn = tc.get("function") or {}   # 兼容扁平/嵌套两种 wire
                            am["tool_calls"].append({
                                "id": tc.get("id") or fn.get("id") or "",
                                "type": "function",
                                "function": {
                                    "name": tc.get("name") or fn.get("name", ""),
                                    "arguments": tc.get("arguments")
                                                 or fn.get("arguments") or ""}})
                    msgs.append(am)
                    sources.append(ev.seq)
            elif t == "tool.result":
                cid = call_id_of(ev)
                msgs.append({"role": "tool",
                             "tool_call_id": cid,
                             "content": p.get("summary") or "",
                             "name": p.get("name")})
                sources.append(ev.seq)
                paired.add(cid)
            elif t == "tool.error":
                # 失败也须配对 tool 消息(assistant.tool_calls 后悬空 → 端点 400,
                # 实测 LLM-304 刷屏);content = 错误摘要回喂,LLM 可据此改口
                cid = call_id_of(ev)
                msgs.append({"role": "tool",
                             "tool_call_id": cid,
                             "content": (str(p.get("code") or "")
                                         + " " + str(p.get("message") or ""))[:500],
                             "name": p.get("name")})
                sources.append(ev.seq)
                paired.add(cid)
            elif t == "approval.requested":
                # 仅建索引(**不入上下文**):供 denied/timeout 反查 call_id
                req_index[ev.seq] = (call_id_of(ev),
                                     str(p.get("tool") or ""))
            elif t in ("approval.denied", "approval.timeout"):
                # ADR-022 D-1/D-2:审批拒绝亦须配对,否则同上悬空。
                # approval.denied 的 trace 不带 call_id ⇒ 经 approval_id 反查
                # approval.requested(其 trace 载 call_id)。查不到 ⇒ fail-safe 跳过
                # (D-8:绝不上抛),交由终局兜底剥离。
                cid, tool = req_index.get(int(p.get("approval_id") or 0), ("", ""))
                if cid and cid not in paired:
                    msgs.append({"role": "tool", "tool_call_id": cid,
                                 "name": tool or None,
                                 "content": _APPROVAL_REJECT_NOTES[t]})
                    sources.append(ev.seq)
                    paired.add(cid)
            elif t == "guard.rejected":
                # ADR-022 D-1/D-2:guard 链拒绝(scope-hidden/g-rule/critical)配对。
                cid = call_id_of(ev)
                if cid and cid not in paired:
                    msgs.append({"role": "tool", "tool_call_id": cid,
                                 "name": str(p.get("tool") or "") or None,
                                 "content": _guard_reject_note(p)})
                    sources.append(ev.seq)
                    paired.add(cid)
            # agent.message/guard.evaluated/decision.issued/receipt.emitted/其余
            # llm.*/E 组:审计流或派生外,**不进** LLM 上下文(§4.2 映射表,按
            # ADR-022 修订:拒绝类事实**不以原形**进入,只经上述配对投影进入)。
            # llm.chunk 为瞬时事件,日志中天然不存在。
        return self._drop_unpaired_tool_calls(msgs, paired)

    @staticmethod
    def _drop_unpaired_tool_calls(msgs: list[dict],
                                  paired: set) -> list[dict]:
        """ADR-022 终局兜底:仍无配对 tool 消息的 `assistant.tool_calls` 予以剔除。

        为什么必须有:拒绝类事件在**旧日志/异常时序**下可能缺失(如历史遗留会话、
        反查失败),此时投影仍会悬空 —— 而 OpenAI/DeepSeek 端点对"`assistant.tool_calls`
        无配对 `tool` 消息"直接 400(session.py 上文两处注释即作者真链实测)。
        本兜底**只作用于投影**,不动日志(INV-01:真源仍是 append-only 事件流)。

        无配对的 assistant 消息:仍有 content ⇒ 降级为纯文本 assistant;
        连 content 也没有 ⇒ **整条丢弃**(等价于"该轮未发生"),避免空 assistant 消息。
        """
        out: list[dict] = []
        for m in msgs:
            calls = m.get("tool_calls")
            if not calls:
                out.append(m)
                continue
            kept = [c for c in calls if c.get("id") in paired]
            if len(kept) == len(calls):
                out.append(m)
            elif kept:
                out.append({**m, "tool_calls": kept})
            elif m.get("content"):
                out.append({"role": "assistant", "content": m["content"]})
        return out

    @staticmethod
    def _truncate_head(msgs: list[dict], max_tokens: Optional[int]) -> list[dict]:
        """超窗头部截断:自最新消息向前累计,超窗即丢更旧头部(整条粒度)。

        最新一条恒保留(空上下文无意义);压缩摘要职责归 F058,本函数不承担语义裁
        剪——仅窗口预算粗闸。None = 不截断(测试/无窗口场景)。
        """
        if max_tokens is None:
            return msgs
        kept: list[dict] = []
        acc = 0
        for m in reversed(msgs):              # 新 → 旧累计,旧消息先丢
            t = _estimate_tokens(m.get("content") or "")
            if acc > 0 and acc + t > max_tokens:
                break                          # 预算已满:更旧的头部整体丢弃
            kept.append(m)
            acc += t
        if not kept and msgs:                  # 单条即超窗:保最新一条
            kept = [msgs[-1]]
        kept.reverse()
        return kept

    # ============================================================ 唯一写入口
    async def append(self, type_: str, payload: dict, *, actor: str,
                     sync: bool = False, trace: Optional[dict] = None,
                     origin: Optional[str] = None,
                     task_id: Optional[str] = None) -> Envelope:
        """唯一写路径(F009):校验 → 分配 seq → 入内存 → 总线分发 →(强同步)落盘。

        校验链 5 步(events.validate_envelope:信封/注册/payload/seq/状态)任一失败
        拒写——事件不进内存/总线/日志,零副作用。seq/ts 只由框架在此分配,调用方
        无权自报(防伪造乱序)。

        异常:EVT-104(终态后/重复 finished)、EVT-106(先于 created/重复 created)、
        EVT-100(信封非法/瞬时入日志/BadTarget)、EVT-102(类型未注册)、EVT-101(seq
        失步)、PERS-202(强同步落盘失败,repair 后重试)。
        """
        # 1) 终态拒写:结构上不可能(EVT-104,INV-01 闸)
        if self._closed:
            raise_code("EVT-104", type_=type_, hint="会话已结束(finished 仅一次);"
                       "查绕过 agent.close 的写路径(INV-01)")
        # 2) 瞬时事件禁 append 入日志(EVENT-SCHEMA §1.3:llm.chunk 仅总线)
        if is_transient(type_):
            raise_code("EVT-100", type_=type_, hint="瞬时事件禁止 append 入日志:"
                       "llm.chunk/registry.updated 仅经总线分发")
        # 暂停的存储会拒收新事件，但总线会隔离订阅者异常。
        # 在分配 seq/写内存前拒绝，避免后续 flush 只恢复旧行却向新消息报成功。
        check_writable = getattr(self._persistence, "ensure_writable", None)
        if callable(check_writable):
            check_writable()
        # 3) finished 单次:置位防并发双写(终态不可逆;EVT-104 由第 1 步接住重复)。
        #    第 1 步至此全为同步段(无 await),同一次事件循环回合内不可能插入第二条
        #    finished,故置位仍具并发防护力。
        #    S1-04 修复:置位必须**在失败时回滚**——否则 EVT-100/102(校验)或
        #    PERS-202(落盘)之后 _closed 永久为真,agent.close 的重试(其自身
        #    state 已回滚,agent.py:307-314)会被第 1 步 EVT-104 短路,"close
        #    幂等可重入"不成立。回滚语义:未成功落盘的 finished 不构成事实。
        finished_inflight = type_ == "session.finished"
        if finished_inflight:
            self._closed = True
        absorbed = False
        try:
            # 4) session.created 必须为会话首事件且仅一条(seq=1 引导,EVT-106)
            if type_ == "session.created" and self._seq > 0:
                raise_code("EVT-106", type_=type_,
                           hint="session.created 必须为会话首事件(seq=1),"
                                "同会话仅一条")
            # 5) 引用锚点预检:edited→user.message / feedback→agent.message(BadTarget)
            if type_ == "user.message_edited" and not self._ref_exists(
                    payload.get("target_seq", 0), "user.message"):
                raise_code("EVT-100", type_=type_,
                           hint=f"BadTarget:target_seq 必须指向已存在的 "
                                f"user.message,got {payload.get('target_seq')}")
            if type_ == "user.feedback" and not self._ref_exists(
                    payload.get("target_seq", 0), "agent.message"):
                raise_code("EVT-100", type_=type_,
                           hint=f"BadTarget:target_seq 必须指向已存在的 "
                                f"agent.message,got {payload.get('target_seq')}")
            # 6) 框架打点 + 五步校验链(失败拒写:EVT-100/102/101/106)
            env = make_envelope(self.sid, type_, actor, payload,
                                origin=origin, task_id=task_id, trace=trace,
                                tenant_id=self.tenant_id,
                                seq_state=self._seq_state)
            # 7) 记账 + 入内存:订阅者/派生视图可即时读(先于总线,spec 顺序)
            self._seq = env.seq
            self._seq_state.commit(env)       # 分配器前进(防漂移;失败由 repair 收尾)
            if env.type == "session.created":
                self._seq_state.open.add(self.sid)  # pending → active 开闸
            self._absorb(env)
            absorbed = True
            # 8) 总线分发:日志订阅者(§8)负责物理落盘
            await self._dispatch(env)
            # 9) 强同步三类/显式 sync:落盘成功才返回(PERS-202 上抛,repair 后重试)
            if sync or env.type in SYNC_TYPES:
                await self._flush(env)
        except BaseException:
            if finished_inflight:
                # 回滚语义:未成功的 finished 不构成事实。判据取**内存事实**——
                # absorbed 且缓存尾已是 finished(事件确已入内存并已分发,总线
                # 订阅者可能已落盘)→ 保持终态位,维持"缓存有 finished ⇔ _closed"
                # 的一致;否则(校验 EVT-100/102 等在入缓存前失败)→ 回滚置位,
                # 使 agent.close 的重试可重入(修复目标)。
                # 残留边界:同进程内"已分发但 flush 失败"仍为终态(retry 会 EVT-104),
                # 跨进程 repair 后按日志事实重算 → 重试可入;与 agent.close
                # docstring 6 的"repair 后重试"口径一致(非本次修复范围)。
                absorbed_finished = (
                    absorbed and bool(self._cache)
                    and self._cache[-1].type == "session.finished")
                self._closed = absorbed_finished
            raise
        # 10) 派生缓存整体失效(INV-03:缓存纪律)
        self.history_cache = None
        self._needs_rebuild = False
        return env

    # ============================================================ 派生视图工厂
    def derive_messages(self, max_tokens: Optional[int] = None) -> list[dict]:
        """派生历史:日志 → LLM 消息列表(§3.5 reducer 唯一权威;别名 derive_history)。

        纯函数:输入仅为本类日志与 max_tokens,无副作用、无第二份状态(INV-01/02)。
        history_cache 仅尾部未变时有效,append 即失效——调用方不得绕过本函数自建
        历史。max_tokens = 窗口余量(scope.window_tokens 联动;None = 不截断)。
        """
        self._ensure_rebuilt()
        if max_tokens is None and self.history_cache is not None:
            return [dict(m) for m in self.history_cache]  # 浅拷贝防外部改写缓存
        out = self._truncate_head(self._fold_history(), max_tokens)
        if max_tokens is None:
            self.history_cache = out            # 尾部未变时有效;append 即失效
        return out

    # derive_history 别名(§3.5/§4.1:同函数对象,禁止第二份实现)
    derive_history = derive_messages

    def events_after(self, after_seq: int = 0) -> Iterator[Envelope]:
        """seq 升序增量读:返回 seq > after_seq 的全部事件(排他下界,0 = 全量)。

        UI 增量同步/子代理拉新事件/审计尾随共用;内存缺失时惰性从 persistence
        replay 补齐(可弃优化)。只读遍历,绝不在迭代中写日志。
        """
        self._ensure_rebuilt()
        start = bisect.bisect_right(self._seq_index(), after_seq)
        for env in self._cache[start:]:
            yield env

    def events_between(self, lo: int, hi: int) -> Iterator[Envelope]:
        """[lo, hi] 闭区间事件迭代(lo > hi = 空);基于增量读,不另存状态。"""
        for env in self.events_after(lo - 1):   # 下界排他转闭区间
            if env.seq > hi:
                break                           # 上界截断
            yield env

    def get(self, seq: int) -> Optional[Envelope]:
        """按 seq 取单条事件;越界/空洞(不存在)→ None 不抛。

        空洞 = 日志物理缺失(compacted/repair 声明区间或坏行隔离);内存阶段缓存
        全量保留原文,故折叠区间内仍可读到原事件(get 以缓存实际为准)。
        """
        self._ensure_rebuilt()
        if not (1 <= seq <= self._seq):
            return None
        i = bisect.bisect_left(self._seq_index(), seq)
        if i >= len(self._cache) or self._cache[i].seq != seq:
            return None                          # 空洞(声明区间/隔离行)
        return self._cache[i]

    # ============================================================ 恢复与统计
    def rebuild_from_log(self) -> None:
        """缓存整体重建(INV-03):弃内存 _cache/history_cache,从 persistence 重放。

        append 后调用方可比对重建结果与缓存逐事件一致(测试钉死);坏行由
        persistence.replay 记跳隔离(PERS-201),本函数不中断。
        """
        self._cache = []
        self._seqs = []
        self._folded = []
        self._holes_declared = []
        self._holes_warned = []
        self._seq = 0
        self.history_cache = None
        self._closed = False
        self._seq_state = SeqState()
        last: Optional[Envelope] = None
        if self._persistence is not None:
            for env in self._persistence.replay():
                if env.session_id != self.sid:
                    continue                    # 多会话文件过滤(轮转合并兜底)
                self._absorb(env)
                last = env
        if last:
            # 从最后完整点续写(max+1,空洞不回填)并开闸;终态按日志事实重算
            self._seq_state.rebuild(self.sid, self._seq)
            self._recompute_terminal()
        self._needs_rebuild = False

    def stats(self) -> dict:
        """只读统计:审计/外壳查询;纯只读,无副作用。"""
        self._ensure_rebuilt()
        return {
            "session_id": self.sid,
            "seq": self._seq,
            "event_count": len(self._cache),
            "closed": self._closed,
            "folded_ranges": [list(r) for r in self._folded],
            "holes_declared": [list(r) for r in self._holes_declared],
            "holes_warned": list(self._holes_warned),
        }

    def close_marker(self) -> None:
        """终态拒写置位:由 agent.close 在 finished 落盘后调用(不写事件)。

        后续任何 append → EVT-104;事件本身由 agent.close 经 append 写入。
        """
        self._closed = True


# ------------------------------------------------------------------ 工厂/恢复
async def open_session(sid: str,
                       persistence: SessionStoreProtocol,
                       *, tenant_id: Optional[str] = None) -> SessionLog:
    """启动/恢复重建:重放日志重建会话状态(崩溃恢复与审计回放同一条代码路径)。

    repair(F060)先于本函数执行;文件不存在 = 新会话(等 session.created,由校验链
    EVT-106 守卫);坏行 PERS-201 记跳不中断;无 compacted/recovered 声明的空洞 →
    warn_hole 告警(F031);_seq 从最后完整点续写(空洞不回填);终态会话恢复后仍拒写。

    ``tenant_id``(GAP-10)**两源合一,日志优先**:调用方传入的是"本次装配认为的
    租户";而日志里已落的 ``Envelope.tenant_id`` 是**既成事实**。恢复时以日志为准
    ——否则重启后换一个调用方就能给历史会话贴上不同租户(那正是 L-1 登记的"租户
    未随会话落盘"残余)。两者不一致时保留日志值并告警,不回写、不覆盖事实。
    """
    log_ = SessionLog(sid=sid, persistence=persistence, tenant_id=tenant_id)
    last: Optional[Envelope] = None
    first_seq: Optional[int] = None
    logged_tenant: Optional[str] = None
    gaps: list[tuple[int, int]] = []             # 待判定空洞(声明可能落在其后)
    for env in persistence.replay():            # 坏行由 replay 记跳隔离,不中断
        if env.session_id != sid:
            continue                            # 多会话文件过滤
        if getattr(env, "tenant_id", None):
            logged_tenant = env.tenant_id        # 日志是既成事实:最后一条胜出
        if first_seq is None:
            first_seq = env.seq
            if env.seq > 1:
                # 首段连续缺失(轮转文件丢失/坏文件):seq 1..(env.seq-1) 整体缺失,
                # 旧实现只查相邻差、对前缀失明(P1-5,与 repair.check_seq_gap 对齐)
                gaps.append((1, env.seq - 1))
        elif last and env.seq != last.seq + 1:
            gaps.append((last.seq + 1, env.seq - 1))
        log_._absorb(env)
        last = env
    # 空洞判定**必须在声明全部吸收之后**(R14-9):`session.recovered` 声明通常由 repair
    # **追加在流尾**,边读边判会把"刚被声明的修复空洞"重新报成未声明空洞(假 F031 线索)。
    for gap_lo, gap_hi in gaps:
        if not log_._gap_declared(gap_lo, gap_hi):
            log_.warn_hole(gap_lo)               # 报**首个缺失的 seq**(此前误报空洞后那条)
    if last:
        log_._seq_state.rebuild(sid, log_._seq)  # 开闸:从最后完整点续写
        if last.type == "session.finished":
            log_._closed = True                  # 终态会话恢复后仍拒写(EVT-104)
    log_.history_cache = None                    # 派生缓存一律重建(INV-03)
    log_._needs_rebuild = False
    # GAP-10:租户以**日志**为准(既成事实)。日志无归属(旧会话/单租户)时保留
    # 调用方注入值;两者冲突时日志胜出并告警——绝不因重开而改写历史归属。
    if logged_tenant is not None:
        if tenant_id is not None and tenant_id != logged_tenant:
            log.warning("会话 %s 租户不一致:装配=%s 日志=%s(以日志为准)",
                        sid, tenant_id, logged_tenant)
        log_.tenant_id = logged_tenant
    return log_


__all__ = [
    "SessionLog", "open_session", "SessionStoreProtocol", "SessionLogProtocol",
]

# 向后兼容导出别名(session 层消费方按协议注入;避免与 core/persistence 真类混淆)
SessionLogProtocol = SessionStoreProtocol
