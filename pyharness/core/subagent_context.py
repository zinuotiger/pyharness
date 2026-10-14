"""SubagentContext operations, separated from facade assembly."""
from __future__ import annotations
from typing import Any, Optional
from pyharness.errors import PyHError, raise_code
from pyharness.core.session import SessionLog
from pyharness.core.subagent_models import log, DEFAULT_BUDGET_RATIO, SUBA_OWNER, CHILD_PREFIX

class SubagentContext:
    """SubagentContext responsibility; state is owned by the composing facade."""

    @staticmethod
    def _resolve_budget_ratio(budget_ratio: Optional[float],
                              cfg: Any) -> float:
        """比例上限解析:cfg.budget.subagent_ratio → 常量 0.25(cfg 缺席容错)。"""
        if cfg is not None:
            try:
                v = getattr(getattr(cfg, "budget", None), "subagent_ratio", None)
                if isinstance(v, (int, float)) and float(v) > 0:
                    return float(v)
            except Exception:  # noqa: BLE001 第三方配置对象 getattr 钩子异常
                log.debug("subagent budget ratio config unavailable",
                          exc_info=True)
        return DEFAULT_BUDGET_RATIO

    def _ensure_session(self) -> Any:
        """父会话前置:未绑定 → EVT-100(spawn/join/status 的事件写口依赖)。"""
        if self._session is None:
            raise_code("EVT-100", hint="SubagentManager 未绑定父会话",
                       advice="构造时注入父会话 SessionLog(ctx.agent.subagent 挂载点)")
        return self._session

    def _default_sub_id(self) -> str:
        """子会话 ID 跨父会话和重启唯一；不复用旧子日志。"""
        import uuid
        return f"{CHILD_PREFIX}{uuid.uuid4().hex}"

    def _default_session(self, sub_id: str) -> SessionLog:
        """默认子会话工厂:独立内存 SessionLog(自带 JSONL 由装配注 persistence)。"""
        return SessionLog(sid=sub_id)

    async def _bootstrap_child(self, child: Any, sub_id: str) -> None:
        """子会话引导:写 session.created(seq=1,首事件 EVT-106 守卫)。

        SessionLog 无 Session.spawn 工厂(偏离 1):created 引导后子会话才 open,
        子循环/执行器的后续 append 才能过校验链第 5 步。model 沿父会话主模型。
        """
        await child.append("session.created",
                           {"title": f"subagent {sub_id}",
                            "model": self._parent_model()}, actor="system")

    def _parent_model(self) -> str:
        """父会话主模型(created payload model;缺失回落默认名)。"""
        sess = self._session
        if sess is not None and hasattr(sess, "events_after"):
            for ev in sess.events_after(0):
                if ev.type == "session.created":
                    m = (ev.payload or {}).get("model")
                    if m:
                        return m
        return "deepseek-chat"


    def _default_child_scope(self, parent_scope: Any, sub_id: str,
                             child_session: Any, limits: Any,
                             deny_extra: Optional[list[str]] = None) -> Any:
        """默认子 scope 工厂:父策略快照值拷贝 + deny_extra 只收窄 + 独立根。

        workspace 根 = storage.workspaces_dir/{sub_id}(F055 每会话独立根,派生见
        `_child_workspace_root`)且**立即建目录**(否则子代理 fs.* 必然 TLB-802);
        预算 limits 用缩放后的子预算(父×ratio,下限 1);spec.deny_extra 并入子策略
        deny_tools(只紧不松,无父级担保通道)。Scope 直接构造不注册全局表——子 scope
        生命周期随 handle(偏离 1)。
        """
        if parent_scope is None:
            return None
        snap = getattr(parent_scope, "snapshot", None)
        if not callable(snap):
            return None
        try:
            from pathlib import Path
            from pyharness.core.scope import Scope, ScopePolicy
            s = snap()
            pol = ScopePolicy(**s.policy.model_dump())      # 值拷贝(COW 起点)
            root = self._child_workspace_root(s.policy.workspace_root, sub_id)
            pol.workspace_root = root                        # 独立根(F055)
            try:
                Path(root).mkdir(parents=True, exist_ok=True)   # 根必须存在
            except OSError as exc:                          # 权限/磁盘:不降级 scope
                log.warning("子 workspace 根创建失败 sub=%s path=%s: %s"
                            "(文件类工具将报 TLB-802)", sub_id, root, exc)
            pol.deny_tools |= set(deny_extra or ())         # deny 只收窄(单调)
            return Scope(policy=pol, limits=limits,
                         session_id=sub_id,
                         window_tokens=getattr(s, "window_tokens", 64000),
                         window_ratio=getattr(s, "window_ratio", 0.75),
                         session=child_session,
                         # 继承父预算计数器:子 agent 用量并入同一预算闸(P2 修复,
                         # 此前 None → 子 scope 预算恒 ok,烧钱无感)
                         counters=self._child_counters(parent_scope, limits),
                         bus=getattr(self, "_bus", None))
        except Exception as exc:                            # noqa: BLE001
            raise PyHError('GRD-403', ctx={'reason': 'child_scope_unavailable'}) from exc

    @staticmethod
    def _child_counters(parent_scope, limits):
        from pyharness.core.llm import UsageCounters
        parent = getattr(parent_scope, '_counters', None)
        if isinstance(parent, UsageCounters):
            parent._limits = parent_scope.limits
            return parent.new_task(limits, parent=parent)
        return parent

    async def _write_event(self, type_: str, payload: dict, *, actor: str,
                           trace: Optional[dict] = None,
                           swallow104: bool = True) -> Any:
        """父会话事件写口:actor/origin 统一打点;EVT-104 兜底只记日志(偏离 10)。

        子终态回写(joined/failed/cancelled 审计)在父会话已 finished 时被 EVT-104
        拒——child-first 清理保证结构上先于此;真发生(时序漏洞)只记日志不阻断
        子回收(spec _run_child 异常表语义)。spawned 落盘不吞(派发闸,调方直用
        session.append,见 spawn)。
        """
        sess = self._ensure_session()
        try:
            return await sess.append(type_, payload, actor=actor,
                                     trace=trace, origin=SUBA_OWNER)
        except PyHError as e:
            if e.code == "EVT-104" and swallow104:
                log.warning("subagent 终态回写被拒(EVT-104 父已终态),仅本地日志"
                            " type=%s sub_id=%s", type_, payload.get("sub_id"))
                return None
            raise

