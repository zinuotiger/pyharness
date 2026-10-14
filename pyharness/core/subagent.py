"""pyharness/core/subagent.py — 子 Agent 进程内派发 (specs/subagent.py.md 契约;F049,阶段 4)

一句话:主 agent 派生子 agent(F049)——子任务跑在**独立 session**(自己的 JSONL/seq/
workspace 根,F044 段不嵌套),与主会话共享进程/凭据口/guard 策略;主会话只落
``subagent.spawned/joined/failed`` 三件委派事实(结果摘要 ≤2KB 以**数据身份**回主
会话,不是第二指令源);并发 ≤8、递归深度 ≤3、子预算=父×1/4 且计入父任务;危险操作
**同权过 guard、无父级担保**;模块自身按 DIS-SEAM §2.4 以能力 Provider 形态接入
(enter→announce→detach),detach/会话关闭走 **child-first 清理**(先杀光在途子任务
并落终态事件,再摘父侧接线)。

安全边界(子=分工非特权,SECURITY §3.5/§6.2):①子会话 scope = 父策略快照且只可更紧
(继承 deny_tools/allowed_domains;spec.tools_subset/deny_extra 只收窄,无"父级担保"
通道);②子任务内任何危险操作照走 F014/F015 同权 guard;③凭据最小权限
(spec.creds_allowed 子集,透传给子循环 F016 读口过滤);④子摘要(≤2KB)以 tool-result
同等数据身份入主会话,永远成不了 system 指令(reducer 不把 subagent.* 映射为消息,
F049 的 LLM 可见摘要经 spawn 工具的 tool.result 路径回喂,见偏离 8);⑤child-first
清理:任何拆除路径(用户取消/父会话关闭/模块 detach)先终止全部在途子任务,防止子
任务在父会话 finished 后回写 joined → EVT-104。

偏离说明(契约=specs/subagent.py.md;以下为与既有实现/规格冲突处的取舍,均列理由,
与 task_queue.py/goal.py/agent.py 同款先例,已入 docstring 供审查):
1. ``Session.spawn(parent=, budget=, policy=)`` 工厂不存在(SessionLog 无 spawn 面,
   agent.enter 才写 session.created)→ 子会话经注入的 **session_factory** seam 创建
   (默认 SessionLog 纯内存 + 就地引导 session.created,seq=1),父策略/预算缩放传给
   child_scope_factory(默认自 scope.from_snapshot 同源值拷贝构造独立 Scope,workspace
   根换成 storage.workspaces_dir/{sub_id},不注册全局 _scopes 表——子 scope 生命周期
   随 ChildHandle,release 由装配层随子会话关闭处理)。
2. 父会话绑定:spec 伪码事件全经 ctx.* 取服务;实际代码库能力模块(goal/task_queue)
   先例 = 构造期绑定 session(父会话 SessionLog),spawn/join/cancel/status 的事件写口
   用绑定会话,ctx 只作 scope/tools/bus/轮锚的附加来源(允许缺省)。
3. 预算表达:spec 伪码 ``ctx.scope.budget * spec.budget_ratio`` 为单一标量;实际 scope
   落地为 BudgetLimits(max_in_tokens/max_out_tokens/max_cost_yuan/warn_ratio)→ 子预算
   = 父 limits 逐字段 × ratio(下限 1),"计入父任务"的用量回写(meter 层 task 级聚合)
   属装配层接线:本模块提供 ChildContext.budget_limits 供子循环 scope 同源取用,并留
   charge_hook 注入位(默认 None = 纯分配记账;真 wire 由装配层把子用量 report 进父
   任务计数器,防 8 个子任务各 1/4 超父总盘)。
4. 并发闸语义:spec 结构表 _sem=Semaphore(acquire 排队)与其异常表"并发满→BUSY 拒新"
   矛盾 → 用**计数闸**(单线程事件循环,计数增减间无 await,原子):满(≥limit)→ BUSY
   拒新,不排队;子任务终态释放。max_concurrent 可注入(默认 8)便于测试。
5. 深度闸语义:spec.depth 即"本子任务所处层级"(主会话=0,首层=1,…≤3);除上界
   (depth > 3 → BUSY)外补**递增闸**(spec.depth < 自身层级+1 → BUSY,防平级/降级派发
   绕过递归闸);非 int → EVT-100。子执行器拿到 env.spec.depth,装配层据其构造下一层
   manager(depth=spec.depth),形成 ≤3 的硬链。
6. ChildHandle.started_ts 用 epoch 秒(float):spec 表型 str 与 status 伪码
   ``int(now - h.started_ts)`` 减法矛盾,取数值语义。
7. agent_loop.run(sub_session, intent=, tools=) 面不存在(实际 agent_loop.run(ctx) 是
   排水监督器,无 intent 入口)→ 子执行器经构造注入 **runner seam**(async
   run_child(env) -> Any,env=ChildContext{session/spec/scope/budget_limits}),
   task_queue.runner 同款先例;未装配 runner → 子任务 CYC-999 快速失败(防 S-1"伪造
   已执行",宁失败不静默假完成)。F007 唯一 LLM 入口纪律(INV-02)由 runner 内部保证。
8. F049 工具路径:announce 注册 ``subagent.spawn`` 工具(Definition danger=none +
   Provider 适配器),Provider.handle = spawn + join,返回 ≤2KB 摘要——LLM 视角一次
   工具调用即"派发并回收",摘要经 executor 的 tool.result 管道以 tool 数据身份进主
   会话派生历史(reducer §4.2 不映射 subagent.* 事件,故摘要不入主历史则永不可见)。
9. enter/announce/detach 的 ctx 可为 None(装配层已构造期注入 bus/tools 时);事件类型
   登记按 EventBus 实例弱引用去重(同 bus 重复 enter/重入幂等,防 register_type 二次
   注册撞 EVT-102;tools_registry._registered_buses 同款先例)。
10. 终态事件写口统一 ``_write_event``:actor=agent/origin=cap:subagent;EVT-104(父会话
    已 finished)兜底只记本地日志不阻断子回收(spec _run_child 异常表同语义);spawned
    落盘失败则 EVT-104 上抛(派发闸,spec spawn 异常表)。
11. "session.closing" 为规范预关钩子事件名(词表外,无触发方;agent.close 目前先
    cancel 在途 run 再 append finished,能力级 pre-finished 清理钩子属阶段 5 装配)——
    announce 按 spec 挂订阅 owner=cap:subagent;detach 自身即 child-first,兜底无孤儿;
    测试直接 emit 或调用 _on_session_closing 驱动。

错误全走 raise_code(EVT-1xx/TLB-8xx)/字面量 BUSY(PyHError 直构,未登记码先例),
禁裸 raise str。
"""
from __future__ import annotations

from pyharness.core.lifecycle import admitted, stop_admissions

import asyncio
import inspect
import itertools
import json
import logging
import time
import weakref
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional

from pyharness.errors import PyHError, raise_code, wrap_unexpected
from pyharness.core.session import SessionLog


# ------------------------------------------------------------------ 约束常量
# 参数锚定(PARAMETER-ANCHOR/CFG.md 锁死;禁自创数字):并发 ≤8/深度 ≤3/预算 1/4/摘要 ≤2KB


# ------------------------------------------------------------------ 数据结构


# ------------------------------------------------------------------ 文本辅助
from pyharness.core.result_text import render_result_text


# ------------------------------------------------------------------ SubagentManager
from pyharness.core.subagent_models import (log, MAX_DEPTH, MAX_CONCURRENT, DEFAULT_BUDGET_RATIO, SUMMARY_MAX_CHARS, TOOL_NAME, SUBA_OWNER, CHILD_PREFIX, EVENT_TYPES, _TERMINAL, _raise_busy, ChildState, SubagentSpec, ChildContext, ChildHandle, SubagentStatus, summarize, _scale_limits)

from pyharness.core.subagent_provider import (SubagentSpawnProvider)

from pyharness.core.subagent_context import SubagentContext
from pyharness.core.subagent_lifecycle import SubagentLifecycle
from pyharness.core.subagent_execution import SubagentExecution

class SubagentManager(SubagentContext, SubagentLifecycle, SubagentExecution):
    """子 Agent 进程内派发管理器(F049;编排层,绑定父会话)。

    生命周期 = DIS-SEAM §2.4 能力三动作(enter→announce→detach,均可 None ctx/幂等);
    核心入口 spawn(spec, ctx) 四道约束闸(深度 ≤3/工具子集在册/并发 ≤8/预算=父×ratio
    且计入父任务)全过 → 建独立子会话 → 父会话落 subagent.spawned → 登记 ChildHandle
    并起 _run_child 协程;join/cancel/status 供主循环回收/取消/查询;子任务终态 →
    摘要(≤2KB)→ subagent.joined / PyHError → subagent.failed;child-first 清理在
    detach/_on_session_closing 统一收口(先杀光在途子任务并落终态事件,再摘父侧接线)。

    构造注入(INV-08 装配纪律):session = 父会话 SessionLog(事件写口,必填用于
    spawn);runner = 子执行器 seam(偏离 7);parent_scope/tools/bus = ctx 缺省来源
    (spawn 亦可经 ctx 现取);cfg = 分层配置(Settings,读 budget.subagent_ratio 上限);
    depth = 本 manager 所处递归层级(主会话=0);session_factory/child_scope_factory/
    child_id_factory = 子会话/子 scope/子 id 构造 seam(默认实现见下,测试注入替身);
    max_concurrent/max_depth/budget_ratio/summary_max_chars = 约束闸参数(默认取常量)。
    """

    # EventBus 实例弱引用去重:同 bus 重复 register_type 会 EVT-102(偏离 9)
    _typed_buses: "weakref.WeakKeyDictionary[Any, None]" = weakref.WeakKeyDictionary()

    def __init__(self, session: Optional[Any] = None, *,
                 runner: Any = None, parent_scope: Any = None,
                 tools: Any = None, bus: Any = None, cfg: Any = None,
                 depth: int = 0,
                 max_concurrent: int = MAX_CONCURRENT,
                 max_depth: int = MAX_DEPTH,
                 budget_ratio: Optional[float] = None,
                 summary_max_chars: int = SUMMARY_MAX_CHARS,
                 session_factory: Optional[Callable[[str], Any]] = None,
                 child_scope_factory: Optional[Callable[..., Any]] = None,
                 child_id_factory: Optional[Callable[[], str]] = None,
                 charge_hook: Optional[Callable[..., Any]] = None,
                 owner: str = SUBA_OWNER) -> None:
        self._session: Any = session            # 父会话(事件写口)
        self._runner: Any = runner              # 子执行器 seam(见偏离 7)
        self._parent_scope: Any = parent_scope  # 父 scope(预算/策略快照源)
        self._tools: Any = tools                # 工具注册表(tools_subset 在册校验)
        self._bus: Any = bus                    # 总线(类型登记/订阅/留痕)
        self._cfg: Any = cfg                    # Settings(读 budget.subagent_ratio)
        self._depth: int = int(depth)           # 本 manager 递归层级(主=0)
        self._max_concurrent: int = int(max_concurrent)
        self._max_depth: int = int(max_depth)
        # 预算比例上限:显式 > cfg.budget.subagent_ratio > 常量 0.25(PARAMETER-ANCHOR)
        self._budget_ratio: float = self._resolve_budget_ratio(
            budget_ratio, cfg) if budget_ratio is None else float(budget_ratio)
        self._summary_max_chars: int = int(summary_max_chars)
        self._session_factory = session_factory or self._default_session
        self._child_scope_factory = child_scope_factory or self._default_child_scope
        self._child_id_factory = child_id_factory or self._default_sub_id
        self._charge_hook: Any = charge_hook    # 子用量计入父任务钩子(偏离 3)
        self._owner: str = owner                # 订阅属主(默认 cap:subagent)
        self._entered: bool = False             # enter 完成标记(幂等闸)
        self._announced: bool = False           # announce 完成标记(幂等闸)
        self._children: dict[str, ChildHandle] = {}
        # Bounded immutable results, never completed tasks/session/scope handles.
        from collections import OrderedDict
        self._completed = OrderedDict()
        self._completed_limit = 128
        self._sem = asyncio.Semaphore(self._max_concurrent)  # 结构表字段(计数闸见偏离 4)
        self._active: int = 0                   # 并发计数(真源;BUSY 拒新判定)
        self._sub_seq = itertools.count(1)      # 子 id 单调源
        self._mount_ns: Any = None              # announce mount 目标(agent 命名空间)
        self._mount_prev: Any = None            # mount 前旧值(detach 恢复)
        self._announce_ctx: Any = None          # announce 时的 ctx(供 detach 无参摘除)

    def _child_workspace_root(self, parent_root: Any, sub_id: str) -> str:
        """F055 子会话独立根 = {storage.workspaces_dir}/{sub_id}(唯一派生点)。

        权威:`specs/subagent.py.md` §约束常量"child workspace 根=
        `~/.pyharness/workspaces/{sub_id}/`"、`CFG.md §3.6`。根目录取 config 权威键
        `storage.workspaces_dir`;cfg 未注入(单测注入替身)时按**父根同族**推导
        ——父根形态即 `{workspaces_dir}/{parent_sid}`,取 `.parent` 回到树根。
        两条路径都经 `scope.session_workspace` 拼接,禁止就地字符串拼接。

        2026-09-21 修(N10):此前恒用 `base.parent/{sub_id}`,而装配层父根曾为**裸**
        workspaces_dir → 子根落到 `~/.pyharness/{sub_id}`(越出 workspaces 树)且
        无人 mkdir,子代理文件类工具全废(治理判 allow、provider 报 TLB-802)。
        """
        ws_dir = getattr(getattr(self._cfg, "storage", None),
                         "workspaces_dir", None)
        if not ws_dir:
            from pathlib import Path
            base = Path(str(parent_root or
                            "~/.pyharness/workspaces")).expanduser()
            ws_dir = str(base.parent)
        from pyharness.core.scope import session_workspace
        return session_workspace(str(ws_dir), sub_id)


    # ============================================================ 内部辅助


    # ============================================================ 事件写口

    # ============================================================ DIS-SEAM §2.4 ①


    # ============================================================ DIS-SEAM §2.4 ②


    # ============================================================ DIS-SEAM §2.4 ③


    # ============================================================ F049 派发核心


    # ============================================================ 子任务执行回收


    # ============================================================ 结果回收

    # ============================================================ 取消(F025)

    # ============================================================ 状态查询

    # ============================================================ child-first 清理


    # 便捷别名:装配层/测试可直接调 _on_session_closing 语义(类型过滤占位)


# ------------------------------------------------------------------ 工具 Provider


__all__ = [
    "SubagentManager", "SubagentSpec", "ChildContext", "ChildHandle",
    "SubagentStatus", "SubagentSpawnProvider", "ChildState",
    "MAX_DEPTH", "MAX_CONCURRENT", "DEFAULT_BUDGET_RATIO",
    "SUMMARY_MAX_CHARS", "TOOL_NAME", "SUBA_OWNER", "summarize",
]

# 向后兼容别名(装配层两种拼写消费)
Subagent = SubagentManager
