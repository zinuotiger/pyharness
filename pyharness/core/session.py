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
from typing import Iterator, Optional, Protocol, TYPE_CHECKING, Union

from pyharness.errors import raise_code
from pyharness.events import Envelope, SeqState, SYNC_TYPES, is_transient, make_envelope

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
                 bus: Optional["EventBus"] = None) -> None:
        """构造空会话日志。

        参数: sid = 会话 id;persistence = SessionStore 注入(新会话/恢复都建议经
        open_session 走重放路径);bus = EventBus 注入(总线日志订阅者负责物理落盘;
        None = 纯内存模式,单测/未装配阶段用)。_persistence 为 DIS-CORE §3.3.4
        旧写法的兼容别名(两份规格调用点写法不一致,见偏离说明)。
        """
        if _persistence is not None:  # DIS 兼容:两处规格对构造参数命名不一致
            persistence = _persistence
        self.sid: str = sid
        self._persistence = persistence
        self._bus = bus
        self._seq: int = 0
        self._cache: list[Envelope] = []
        self._seqs: list[int] = []            # 与 _cache 平行的 seq 升序索引(二分用)
        self._folded: list[list[int]] = []    # compacted 声明区间 [lo, hi] 列表
        self._closed: bool = False
        self.history_cache: Optional[list[dict]] = None
        self._holes_warned: list[int] = []    # warn_hole 告警的 seq(F031)
        self._seq_state = SeqState()          # 框架 seq 分配单真源(events 层)
        # 存在持久化真源但尚未回放:派生读先惰性 rebuild(events_after 语义)
        self._needs_rebuild: bool = persistence is not None

    # ============================================================ 内部辅助
    def _absorb(self, env: Envelope) -> None:
        """吸收一条事件入缓存(append/回放共用):seq 推进 + folded 索引同步。"""
        self._cache.append(env)
        self._seqs.append(env.seq)
        self._seq = env.seq
        if env.type == "context.compacted":
            # 折叠声明入索引(空洞合法化口径;区间合法性已由 payload 模型保证)
            for pair in env.payload["ranges"]:
                self._folded.append(list(pair))
            self._folded.sort(key=lambda r: r[0])

    def _seq_index(self) -> list[int]:
        """seq 升序索引(与缓存平行;append-only 保证与 _cache 严格对齐)。"""
        return self._seqs

    def _ensure_rebuilt(self) -> None:
        """惰性重建:缓存不可用(构造后未回放)时先从磁盘真源补齐。"""
        if self._needs_rebuild:
            self.rebuild_from_log()

    def _folded_contains(self, seq: int) -> bool:
        """seq 是否落在某 compacted 声明区间内。"""
        return any(lo <= seq <= hi for lo, hi in self._folded)

    def _gap_declared(self, gap_lo: int, gap_hi: int) -> bool:
        """空洞区间 [gap_lo, gap_hi] 是否被声明区间完全覆盖(合法空洞不回填)。"""
        return all(self._folded_contains(s) for s in range(gap_lo, gap_hi + 1))

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
        pending: Optional[int] = None         # 待配对 tool 的 response seq
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
                if c:
                    msgs.append({"role": "assistant", "content": c})
                    sources.append(ev.seq)
                else:
                    pending = ev.seq           # 空 content = 工具调用轮,等配对
            elif t == "tool.result" and pending is not None:
                msgs.append({"role": "tool", "content": p["summary"],
                             "name": p["name"]})
                sources.append(ev.seq)
                pending = None
            # guard.*/agent.message/tool.error/其余 llm.*/E 组:审计流或派生外,
            # 不进 LLM 上下文(§4.2 映射表);llm.chunk 为瞬时事件,日志中天然不存在
        return msgs

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
            t = _estimate_tokens(m["content"])
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
        # 3) finished 单次:先置位防并发双写(终态不可逆;EVT-104 由第 1 步接住重复)
        if type_ == "session.finished":
            self._closed = True
        # 4) session.created 必须为会话首事件且仅一条(seq=1 引导,EVT-106)
        if type_ == "session.created" and self._seq > 0:
            raise_code("EVT-106", type_=type_,
                       hint="session.created 必须为会话首事件(seq=1),同会话仅一条")
        # 5) 引用锚点预检:edited→user.message / feedback→agent.message(BadTarget)
        if type_ == "user.message_edited" and not self._ref_exists(
                payload.get("target_seq", 0), "user.message"):
            raise_code("EVT-100", type_=type_,
                       hint=f"BadTarget:target_seq 必须指向已存在的 user.message,"
                            f"got {payload.get('target_seq')}")
        if type_ == "user.feedback" and not self._ref_exists(
                payload.get("target_seq", 0), "agent.message"):
            raise_code("EVT-100", type_=type_,
                       hint=f"BadTarget:target_seq 必须指向已存在的 agent.message,"
                            f"got {payload.get('target_seq')}")
        # 6) 框架打点 + 五步校验链(失败拒写:EVT-100/102/101/106)
        env = make_envelope(self.sid, type_, actor, payload,
                            origin=origin, task_id=task_id, trace=trace,
                            seq_state=self._seq_state)
        # 7) 记账 + 入内存:订阅者/派生视图可即时读(先于总线,spec 顺序)
        self._seq = env.seq
        self._seq_state.commit(env)           # 分配器前进(防漂移;失败由 repair 收尾)
        if env.type == "session.created":
            self._seq_state.open.add(self.sid)  # pending → active 开闸
        self._absorb(env)
        # 8) 总线分发:日志订阅者(§8)负责物理落盘
        await self._dispatch(env)
        # 9) 强同步三类/显式 sync:落盘成功才返回(PERS-202 上抛,repair 后重试)
        if sync or env.type in SYNC_TYPES:
            await self._flush(env)
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
            "holes_warned": list(self._holes_warned),
        }

    def close_marker(self) -> None:
        """终态拒写置位:由 agent.close 在 finished 落盘后调用(不写事件)。

        后续任何 append → EVT-104;事件本身由 agent.close 经 append 写入。
        """
        self._closed = True


# ------------------------------------------------------------------ 工厂/恢复
async def open_session(sid: str,
                       persistence: SessionStoreProtocol) -> SessionLog:
    """启动/恢复重建:重放日志重建会话状态(崩溃恢复与审计回放同一条代码路径)。

    repair(F060)先于本函数执行;文件不存在 = 新会话(等 session.created,由校验链
    EVT-106 守卫);坏行 PERS-201 记跳不中断;无 compacted/recovered 声明的空洞 →
    warn_hole 告警(F031);_seq 从最后完整点续写(空洞不回填);终态会话恢复后仍拒写。
    """
    log_ = SessionLog(sid=sid, persistence=persistence)
    last: Optional[Envelope] = None
    for env in persistence.replay():            # 坏行由 replay 记跳隔离,不中断
        if env.session_id != sid:
            continue                            # 多会话文件过滤
        if last and env.seq != last.seq + 1:
            gap_lo, gap_hi = last.seq + 1, env.seq - 1
            if not log_._gap_declared(gap_lo, gap_hi):
                log_.warn_hole(env.seq)         # 无声明空洞 → 告警(F031)
        log_._absorb(env)
        last = env
    if last:
        log_._seq_state.rebuild(sid, log_._seq)  # 开闸:从最后完整点续写
        if last.type == "session.finished":
            log_._closed = True                  # 终态会话恢复后仍拒写(EVT-104)
    log_.history_cache = None                    # 派生缓存一律重建(INV-03)
    log_._needs_rebuild = False
    return log_


__all__ = [
    "SessionLog", "open_session", "SessionStoreProtocol", "SessionLogProtocol",
]

# 向后兼容导出别名(session 层消费方按协议注入;避免与 core/persistence 真类混淆)
SessionLogProtocol = SessionStoreProtocol
