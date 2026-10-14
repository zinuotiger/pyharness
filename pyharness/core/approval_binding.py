"""ApprovalBinding operations, separated from facade assembly."""
from __future__ import annotations
import asyncio
import hashlib
from typing import TYPE_CHECKING, Any, Optional
from pyharness.core.channel import default_channel_of, require_channel
from pyharness.errors import raise_code
from pyharness.core.approval_models import logger, CHANNELS, _TRUST_SET, _TRUST_ADDED, _TRUST_HIT, _TRUST_CLEARED, _danger_of, canonical_json, ApprovalRequest, TrustEntry
if TYPE_CHECKING:  # 仅类型标注:executor 传入的 ToolCall 鸭子契约,运行期不依赖
    pass

class ApprovalBinding:
    """ApprovalBinding responsibility; state is owned by the composing facade."""

    def set_default_channel(self, value: Any) -> None:
        """装配期覆写 provider 缺省通道(N5):与构造期**同一归一化**,避免直写
        ``_channel`` 绕过契约(``"acp:<id>"`` 必须归一为 ``"acp"``)。"""
        self._channel = default_channel_of(value)

    def _ensure_channel(self, ctx: Any) -> Optional[str]:
        """通道判定(**唯一实现 = ``core.channel`` 契约**;N5)。

        返回:交互通道的**归一化名**(``"acp:<id>"`` → ``"acp"``,保留 ACP 身份);
        显式 headless → ``None``(``APR-501`` 入口,既有语义不变)。
        抛:``MISSING`` / ``INVALID`` / ``UNKNOWN`` ⇒ ``APR-503`` fail-closed。

        **与治理层 ``GovernanceContext.principal_of`` 同契约**(逐状态一致性由
        ``tests/invariants`` 的真值表断言钉死)。

        **修复点(N5-b/N6)**:
        - 判据由"精确名 ``ch in CHANNELS``"改为**前缀归一** ⇒ ``"acp:<id>"`` 不再
          被误判为 headless;
        - **删除 ``self._channel`` 回退** ⇒ 硬编码的 ``"desktop"`` 不再劫持
          "ctx 未声明通道"的情形(那会让审批层以为有 desktop 人类通道,而治理层
          对同一输入 fail-closed —— 同一状态两个相反判定)。

        headless 标志(构造期或 ctx 注入)优先且压过通道名(既有语义,不变)。
        """
        headless = self._headless_of(ctx)
        if headless:
            return None
        st = require_channel(ctx, module="approval", field="channel")
        return None if st.is_headless else st.name

    def _headless_of(self, ctx: Any) -> bool:
        """headless 解析:ctx 显式注入优先,回落构造缺省(agent.py 同款两级解析)。"""
        try:
            v = getattr(ctx, "headless", None)
        except Exception:                        # noqa: BLE001 防御第三方 getattr
            v = None
        if v is not None:
            return bool(v)
        return self._headless

    def _fingerprint(self, tool: str, args: Any) -> str:
        """规范化指纹 = sha1(tool + canonical_json(args))(合并与信任共用的唯一键)。"""
        canon = canonical_json(args or {})
        return hashlib.sha1(f"{tool}\n{canon}".encode("utf-8")).hexdigest()

    def grant_binding(self, call_id: str) -> Optional[str]:
        """最近一次 granted(含信任命中)为该 call_id 记录的绑定指纹(None = 无)。

        executor 重入 guard 得到 approval 决策时读取:与当前调用参数指纹不一致
        即"批准针对异参数",拒绝执行(GRD-403)。
        """
        return self._grant_slots.get(call_id)

    def approval_ref_of(self, call_id: str) -> Optional[int]:
        """该 call_id 关联的 **approval identity**(S4-P1-2,只读)。

        值 = 该次 ``approval.requested`` 的 seq(60s 合并批内共享 lead 的 identity)。
        结算后仍可查(与 ``grant_binding`` 同型生命周期)。``None`` = 该 call_id
        **从未产生过** approval 请求(普通 allow/reject 路径,或信任名单命中路径
        ——后者不落 ``approval.requested``,故无 identity,属正确语义)。

        治理层经执行侧把该值填入审批后重新授权决策(D2)的 ``approval_ref``;
        不新造 identity、不用 decision_id/call_id 代替。
        """
        return self._ref_slots.get(call_id)

    @staticmethod
    def _sid_of(ctx: Any, sess: Any) -> str:
        """会话 id 解析:会话日志 sid 优先,回落 ctx 显式。"""
        sid = getattr(sess, "sid", "") or ""
        if not sid:
            sid = getattr(ctx, "session_id", "") or getattr(ctx, "sid", "") or ""
        return sid

    @staticmethod
    def _log_of(ctx: Any) -> Any:
        """会话日志解析:ctx.session(装配注入)优先,None = 未接线。"""
        return getattr(ctx, "session", None)

    def _new_waiter(self, fp: str, call: Any, args_summary: str, ch: str,
                    ttl: int, *, sid: str, sess: Any,
                    binding: Optional[str] = None) -> ApprovalRequest:
        """构造等待者(waiter Future 由运行中事件循环创建;批先含自身)。"""
        req = ApprovalRequest(
            tool=call.name,
            call_id=getattr(call, "call_id", "") or "",
            args_summary=args_summary,
            danger=_danger_of(call),
            ttl_ms=ttl,
            channel=ch,
            fingerprint=fp,
            binding=binding or fp,               # 未绑定 → 回落规范化指纹
            session_id=sid,
            log=sess,
        )
        req.waiter = asyncio.get_running_loop().create_future()
        req.batch = [req]
        return req

    @staticmethod
    def _cancel_timer(req: ApprovalRequest) -> None:
        """取消 TTL 定时器(幂等)。"""
        if req.timer is not None:
            try:
                req.timer.cancel()
            except Exception:                    # noqa: BLE001 已触发/已取消
                pass
            req.timer = None

    def enable_trust(self, on: bool, *, by: str) -> None:
        """信任名单开关(默认关;仅交互会话可开;headless 调用直接拒绝 APR-501)。"""
        self._require_human(by)
        if on and (self._headless_of(None) or self._channel not in CHANNELS):
            raise_code("APR-501", op="enable_trust",
                       hint="headless/无交互通道:信任名单仅交互 cli/web/acp 可开,"
                            "headless 永不生效")
        self._enabled = bool(on)
        self._emit_trust(_TRUST_SET, {"on": self._enabled, "by": by})
        logger.info("approval 信任名单 %s(by=%s)",
                    "启用" if self._enabled else "关闭", by)

    def clear_trust(self, tool: Optional[str] = None) -> int:
        """清除信任(全会话或单工具;用户可手动撤销)→ 返回清除条数。"""
        if tool is None:
            count = len(self._trust)
            self._trust.clear()
        else:
            victims = [fp for fp, e in self._trust.items() if e.tool == tool]
            count = len(victims)
            for fp in victims:
                self._trust.pop(fp, None)
        if count:
            self._emit_trust(_TRUST_CLEARED, {"tool": tool, "count": count})
        return count

    def _trust_hit(self, fp: str, sid: str, ch: str) -> bool:
        """信任命中:开关开 + 交互通道 + 指纹在表(同会话);命中计数 + 留痕。"""
        if not self._enabled or ch not in CHANNELS:
            return False
        entry = self._trust.get(fp)
        if entry is None:
            return False
        if sid and entry.session_id and entry.session_id != sid:
            return False                         # 会话级:他会话信任不生效
        entry.hits += 1
        self._emit_trust(_TRUST_HIT, {"tool": entry.tool, "hits": entry.hits})
        return True                              # 命中 ≠ 跳过 guard(executor 重入)

    def _remember(self, req: ApprovalRequest) -> None:
        """granted 后写信任表(≤N 条 FIFO 淘汰)+ 留痕;critical 绝不在名单。"""
        if req.danger == "critical":
            return                               # critical 类绝不在名单
        entry = TrustEntry(tool=req.tool, fingerprint=req.fingerprint,
                           by=req.by or "system", added_seq=req.approval_id or 0,
                           session_id=req.session_id)
        self._trust[req.fingerprint] = entry
        while len(self._trust) > self._trust_max:  # FIFO 淘汰(按加入序)
            oldest = min(self._trust.values(), key=lambda e: e.added_seq)
            self._trust.pop(oldest.fingerprint, None)
        self._emit_trust(_TRUST_ADDED, {"tool": entry.tool, "by": entry.by,
                                        "seq": entry.added_seq})

