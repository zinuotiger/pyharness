"""pyharness/core/tools_guard.py — 工具安全闸:guard 单调拒绝链 (specs/tools_guard.py.md 契约)

功能编号:F014(guard 单调链核心)· F023(内置危险 guard g1-g7)· 联动 F026(g1
schema 复查)/F015(approval 交接)。
权威口径:specs/tools_guard.py.md(编码契约)、DIS-SEAM §5(决策模型/链求值伪代码/
g1-g7 表)、ERR.md §2.5(GRD-401)/§2.11(POL-*)、SECURITY.md §4(L0-L4 单调策略面);
冲突以 PRD-Core 为准(本模块未见冲突,偏离均为事件载荷适配,见下)。

职责一句话:工具执行前的单调安全求值器——Guard 接口 + GuardChain(按注册序
waterfall,任一 guard 可拒绝且拒绝不可被后续覆盖)+ g1-g7 内置 guard + danger
分级(L0-L4)默认策略;决策三值 allow/reject/approval(**无 bypass 值**,不存在
"跳过 guard"标记位);每个 reject 必出强同步 guard.rejected,审计可证"拦了且
没执行"(INV-05)。guard 只有拒绝和请示两条非放行路径,**永远没有"放行"
API**——全链 allow 只是"本 guard 不反对",最终执行权在 executor 管道
(DIS-CORE §7.3.2 关3 Provider)。

单调性三层结构防线(原则 3 / INV-04):
1. waterfall 短路:evaluate 按注册序求值,首个非 allow 即终局返回,后续 guard
   不再求值(测试断言短路后链尾 guard.check 零调用);
2. 无续跑/回翻 API:拒绝后本模块不提供任何把 reject 翻回 allow 的通道(无
   override/bypass/force-allow/execute 方法;审批重入 = 重新 evaluate 一次,
   新决策以新策略为准,单调性高于人类即时意志,GRD-403 语义);
3. 链只增:register_plugin_guard 只追加链尾(TLB-801 重名拒/BUSY 已禁用拒),
   无移除/重排 API;单个内置 guard 关闭须 config 显式声明(disable,CFG-601
   兜底),g-schema/g-danger 恒在不可关。

偏离说明(相对 spec 伪码;契约=spec,偏离均列理由,scope.py 偏离注同款先例):
1. **guard.evaluated/rejected 载荷字段以已落地的 events 词表模型为准**
   (events/payload.py 的 GuardEvaluatedPayload/GuardRejectedPayload,
   extra="forbid" 拒多余字段,74 类型已锁定;实测:超字段 append → EVT-100):
   - evaluated 载荷 = {tool, decision, guard_ids, reasons};spec 伪码的
     policy_ref 字段在该模型不存在 → policy_ref 并入 reasons(模型同义字段);
     decision 词表为 allow|deny|need_approval(deny≡reject、need_approval≡
     approval,spec 明示别名口径),内部 Decision 三值经 _AUDIT_WORD 映射落盘;
   - rejected 载荷 = {tool, guard_id, reason, policy_ref}:spec 伪码的 call_id
     字段模型不允许 → 经 Envelope.trace={"call_id": …} 携带(信封 trace 为自由
     dict,不触发载荷校验),回放按 trace/事件序与 tool.call(含 call_id)配对;
   - guard.disabled 未入 74 词表(实测 append → EVT-102)→ 经注入 session
     尽力而为留痕(scope.py 偏离 4 同款:真实 SessionLog 拒写属 events 模块
     后续阶段门,测试全替身覆盖)。
2. **g1 g-schema 的 registry 依赖注入式装配**:tools_registry(F026)本阶段未
   实现 → GuardChain 经 validator 回调注入(签名 validator(name, raw_args) ->
   dict,校验失败抛 TLB-803);validator=None(未装配)时 g1 视为入口 F026 已验
   直接 allow,待 registry 落地后由装配层注入(链内复查防内层直调,INV-04)。
3. **approval 通道(has_channel)注入式装配**:danger_default_policy(high →
   has_channel ? approval : reject,headless=R8)是 SECURITY §4.2 机器表达;
   链在 evaluate 层统一把 approval 决策降级(无通道 → reject,policy_ref
   APR-501;critical → reject,POL-DGR-1 不可审批)。通道状态由装配层注入
   (GuardChain(approval_channel=…)),None = 未接线视同有通道(guard 只出决策,
   headless 终拦归 approval 模块 APR-501,R8——偏离:伪码未显式处理通道位)。
4. **BUSY 未登记码直构 PyHError("BUSY")**(scope.py 偏离 6 同款先例:
   raise_code 会把未登记码改写为 CYC-999,语义不符);已登记码(TLB-801/
   CFG-601/GRD-4xx)全走 raise_code(全系统唯一抛出入口纪律)。
5. **g3/g4/g7 路径单点解析在 guard 内自含**:F055 单点 resolve 属 tool_fs 模块
   (resolve_in_workspace,F034 第二道闸,本阶段未实现)→ guard 先实现几何判定
   (绝对越界/.. 逃逸/symlink-junction 终解析越界),语义与 tool_fs 规格同码
   (POL-FS-1/2/3);workspace 根取自 scope.policy.workspace_root;read_extra_dirs
   只读例外是工具层(F034)纵深设施,guard 层不消费(只紧不松)。
6. **exec "scope 显式授权"判定 = 沙箱级别**:strict 沙箱下 exec.* 域外不可见
   (scope.can_use 前置已拦,见 scope.py 偏离 7)→ g6 对 strict 兜底直拒
   (POL-EXEC-1,双保险);basic/off 下无 shell=True 且 cwd 不越界才 allow。
7. 签名差异:spec 伪码 evaluate/check 混用 Decision 枚举与字符串三值——落地为
   Decision(StrEnum,ALLOW/REJECT/APPROVAL,与字符串 "reject" 等值可比较,
   executor 伪码 `d == "reject"` 直接可用)+ check 返回 (decision_str,
   policy_ref) 二元组(spec 伪代码权威);"其余函数速览"表内 enabled_guard_ids/
   chain_version/match_guard_by_hook 以链实例方法/模块函数形式落地(表为速览,
   装配/审计消费按实例)。
8. g5 域名匹配为**字面 hostname 精确匹配**(归一化后;子域/IP/混淆不隐含放行,
   须显式列入 allowed_domains),通配 "*" 视为越权不构成放行(config 层已拒,
   此处纵深兜底)。

依赖方向(单向,禁止反向):errors.raise_code(F020)← session.append(注入)←
scope(Scope.can_use 前置 + ScopePolicy 策略面)← config(装配只读);credentials/
workspace 解析均经构造注入;不 import Provider/executor/approval(反向依赖禁止)。
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import os
import stat
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable, Iterable, Optional
from urllib.parse import urlsplit

from pyharness.errors import PyHError, raise_code


# ====================================================================== 常量
# guard 决策三值(内部唯一字面量;无 bypass 值——不存在第四态/跳过标记)

# 审计事件 decision 词表(events GuardEvaluatedPayload 锁定:
# allow|deny|need_approval;deny≡reject、need_approval≡approval,spec 别名口径)

# 危险分级 L0-L4(scope/config 同源表;rank 仅供匹配比较)

# 五内置可关 guard(config.py BUILTIN_GUARDS 同源:F023 五内置真子集);
# g-schema/g-danger 恒在不可关(disable → CFG-601)

# g3 文件域工具名前缀(与 scope.WORKSPACE_DOMAINS 同源;fs./workspace.)
# g5 外发域工具名前缀(与 scope.ALLOWLIST_DOMAINS 同源;web./net.)

# 读路径类工具前缀(g4 match:读凭据文件风险面;list_dir 只列名不读内容)
# 写类工具前缀(g7 match:覆写转审批;fs.write_file 等)
# exec 类工具前缀(g6 match:exec.subprocess/exec.pty 等)

# 凭据文件默认清单项(config security.credentials.file 同源;g4 兜底)

# 凭据型文件名(清单外纵深:任何位置命中该名 = 凭据文件形态,拒读)


# ==================================================================== 决策


# ================================================================= ToolCall


# ==================================================================== Guard


# ------------------------------------------------------------------ 路径几何


# ============================================================ g1 g-schema


# ============================================================== g2 g-danger


# ============================================================== g3 g-fs-path


# ======================================================== g4 g-credential-read


# ============================================================ g5 g-net-outbound


# ================================================================ g6 g-exec


# ============================================================= g7 g-overwrite


# ================================================================ 内置链装配
# 求值序 = 动作形状 guard(g3 路径/g4 凭据/g6 exec 约束/g7 覆写)先行,危险分级
# (g2)殿后——分级只决定"是否需要人类审批",不短路形状 guard 求值:exec(high)
# 的 shell/cwd/strict 约束与写工具的覆写审批在任何一次人类批准前已被强制求值
# (修复前 g-danger 在 g2 短路,使 g6/g7 对 high 工具恒为死代码)。
from pyharness.core.guard_models import (log, _DECISIONS, _AUDIT_WORD, DANGER_LEVELS, FORCED_GUARDS, FS_DOMAINS, NET_DOMAINS, _READ_PREFIXES, _WRITE_PREFIXES, _EXEC_PREFIXES, DEFAULT_CREDENTIAL_FILES, _CRED_BASENAMES, Decision, danger_default_policy, ToolCall, Guard, _policy_of, _danger_of, _is_domain)
from pyharness.core.guard_paths import (_normcase_path, _inside_workspace, _norm_target, _path_from_args, _fs_target, _has_dotdot, _final_realpath, _is_linkish, _in_read_extra, _resolve_geometry)
from pyharness.core.guard_rules import (g_schema_match, g_schema_check, g_danger_match, g_danger_check, g_fs_path_match, g_fs_path_check, g_credential_read_match, g_credential_read_check, g_net_outbound_match, _normalize_domain, g_net_outbound_check, g_exec_match, g_exec_check, g_overwrite_match, g_overwrite_check)
from pyharness.core.guard_descriptors import (_RULE_POLICY_REFS, RuleDescriptor, describe_rules)


# ================================================================ GuardChain
from pyharness.core.guard_builtin import (_BUILTIN_IDS, build_builtin_chain, _FuncGuard)

class GuardChain:
    """guard 单调拒绝链(原则 3 落地;L3 调用裁决层)。

    结构不可变纪律:chain 注册序 = 求值序,只增不改(插件追加链尾);disabled
    仅经 disable()(config 显式声明 + guard.disabled 留痕)写入;运行期无任何
    API 可移除/重排/回翻一次拒绝(INV-04,测试钉死方法面)。唯一状态 = 链装配
    (chain/disabled/_snapshot_seq),求值本身无状态残留(取消/重入语义 = 重新
    evaluate 一次,以新策略为准,GRD-403)。
    """

    # 无 bypass/放行/执行类方法面(测试断言 AttributeError;executor 才持有
    # Provider 执行权——guard 永远只是"拒绝或请示"的裁决器)
    _FORBIDDEN_API: tuple[str, ...] = (
        "override", "bypass", "force_allow", "set_allow", "allow", "execute",
        "run", "mark_allowed", "clear_reject", "release_decision",
    )

    def __init__(self, chain: Optional[list[Guard]] = None, *,
                 session: Any = None, disabled: Optional[Iterable[str]] = None,
                 credential_paths: Optional[Iterable[str]] = None,
                 validator: Optional[Callable[..., dict]] = None,
                 approval_channel: Optional[bool] = None,
                 path_exists: Optional[Callable[[str], bool]] = None,
                 link_resolver: Optional[Callable[[str], str]] = None,
                 bus: Any = None) -> None:
        """构造链:chain 缺省 = 内置 g1-g7(固定序);session/bus 注入式装配。

        session = 事件落点(SessionLog.append 同型:append(type, payload, *,
        actor=…, sync=…, trace=…));None = 未接线(evaluated/rejected 日志降级,
        但 rejected 强同步语义要求装配层注入真实 session——单测以替身覆盖)。
        approval_channel = 审批通道在位与否(偏离 3;None 视同有通道)。
        """
        built = chain if chain is not None else build_builtin_chain(
            credential_paths=credential_paths, validator=validator,
            approval_channel=approval_channel, path_exists=path_exists,
            link_resolver=link_resolver)
        ids = [g.id for g in built]
        if len(set(ids)) != len(ids):
            raise_code("TLB-801", guard="chain",
                       advice="guard id 全链唯一(重复装配被拒)")
        self.chain: list[Guard] = list(built)    # 注册序 = 求值序(只增不改)
        self.disabled: set[str] = set(disabled or ())
        bad = self.disabled - set(ids)
        if bad or self.disabled & FORCED_GUARDS:
            raise_code("CFG-601", reason="越权",
                       fields=[f"guards.disabled:{sorted(bad or self.disabled)}"],
                       detail="仅五内置可关且须 config 声明;g-schema/g-danger "
                              "强制恒在")
        self._session: Any = session              # 事件落点(注入)
        self._bus: Any = bus                      # registry.updated 尽力出口
        self._approval_channel: Optional[bool] = approval_channel
        self._snapshot_seq: int = 1               # 装配版本(审批重入复核)
        self._tasks: set[asyncio.Task] = set()    # fire-and-forget 任务登记

    # ------------------------------------------------------ 单调链求值主函数
    async def _evaluate_full(self, call: Any, scope: Any, *,
                             session: Any = None
                             ) -> tuple[Decision, tuple[str, ...], tuple[str, ...]]:
        """**唯一** waterfall 求值实现(S3-2-1;B1)。

        返回 ``(verdict, guard_ids, policy_refs)``——三者来自**同一次**实际求值:
        verdict 与命中面在同一循环迭代内产生并同时返回,**不重跑、不缓存、不读
        历史事件**(S3-2-1 明令)。

        规则(spec 伪代码权威):
        - scope 前置:can_use 不可见 → 终局 REJECT(guard_id=scope-hidden,
          policy_ref=GRD-401;executor 关2a 已先行拦截,此处兜底直调消费方);
        - 命中 guard 逐个 check;首个非 allow 即短路返回(reject/approval 都到
          此为止,后续 guard 不再求值 = 单调拒绝的求值面保证);
        - approval 降级两闸(统一在 evaluate 层,插件 approval 也覆盖):
          danger=critical → 强制转 reject(POL-DGR-1,不可审批,F014);
          无审批通道 → 转 reject(APR-501,headless=R8,偏离 3);
        - 每次求值恰好一条 guard.evaluated(INV-04:执行前必有;缺 = 非法执行);
          reject 再强同步 guard.rejected(sync=True,崩溃不丢拦截事实,INV-05);
        - 全链 allow 才返回 ALLOW——guard 无放行权,执行权归 executor 关3。
        异常:append 强同步失败按 PERS-202/EVT-1xx 语义上抛(拒绝事实必须落地,
        调用方 fail-closed:缺 evaluated/rejected 不进入 Provider)。

        **事件汇点按调用解析(ADR-021)**:``session`` 缺省(None)= 沿用装配期
        ``self._session``(主路径**逐字不变**);治理层经 ``authorize()`` 显式传入
        ``ctx.session`` ⇒ 子 Agent 的 ``guard.evaluated``/``guard.rejected`` 与
        同 ``call_id`` 的 ``tool.call``/``tool.result`` **落同一会话**。链对象仍
        **唯一**(``spine.guard is policy.chain``),变的是汇点而非链。
        """
        sess = session if session is not None else self._session
        can_use = getattr(scope, "can_use", None)
        if can_use is None:
            raise_code("GRD-401", hint="evaluate 需要会话 Scope(can_use 前置 "
                       "不可缺);直接传 ScopePolicy 违反调用契约")
        if not can_use(call.name):                # scope 前置:不可见 → 终局
            await self._audit(call, Decision.REJECT, "scope-hidden", "GRD-401",
                              session=sess)
            await self._append_rejected(call, "scope-hidden", "GRD-401",
                                        session=sess)
            return Decision.REJECT, ("scope-hidden",), ("GRD-401",)
        enabled = self.enabled_guard_ids()
        for g in self.chain:                      # 按注册序 waterfall
            if g.id in self.disabled:
                continue                          # 仅 config 显式关闭(留过事件)
            if not g.match(call):
                continue                          # 非本 guard 管辖 → 看下一个
            d_str, policy = await g.check(call, scope)   # (allow/reject/approval)
            if d_str not in _DECISIONS:
                raise_code("CYC-999", guard=g.id, decision=d_str,
                           hint="guard.check 只能返回 allow/reject/approval"
                                "(插件契约违规=bug 证据)")
            d = Decision(d_str)
            if d is Decision.APPROVAL:            # approval 降级两闸(统一兜底)
                if _danger_of(call) == "critical":
                    d, policy = Decision.REJECT, "POL-DGR-1"  # 不可审批(F014)
                elif self._approval_channel is False:
                    d, policy = Decision.REJECT, "APR-501"    # 无通道(R8)
            if d is not Decision.ALLOW:           # 首个非 allow 即短路(waterfall)
                await self._audit(call, d, g.id, policy, session=sess)
                if d is Decision.REJECT:          # 强同步:拦了且没执行(INV-05)
                    await self._append_rejected(call, g.id, policy,
                                                session=sess)
                return d, (g.id,), ((policy,) if policy else ())  # reject/approval 到此为止
        await self._audit(call, Decision.ALLOW, enabled, None, session=sess)
        return Decision.ALLOW, tuple(enabled), ()  # 全链 allow → executor 关3

    async def evaluate(self, call: Any, scope: Any, *,
                       session: Any = None) -> Decision:
        """兼容 wrapper(位置参数/返回类型/求值语义不变;仅新增可选 kwarg)。

        返回 ``Decision``(StrEnum;executor 伪码 ``d == "reject"`` 直接可比较)。
        实现是 ``_evaluate_full`` 的**视图**,不是第二套求值。
        ``session``(ADR-021)同 ``_evaluate_full``:缺省 None = 沿用装配期会话。
        """
        return (await self._evaluate_full(call, scope, session=session))[0]

    async def evaluate_detailed(self, call: Any, scope: Any, *,
                                session: Any = None) -> Any:
        """rich result API(S3-2-1;B1):同一次求值的结构化投影。

        返回 ``governance.decision.EvaluationResult``(verdict + guard_ids +
        policy_refs),供治理层装配 ``Decision``。**不重跑求值、不缓存上次结果、
        不读历史事件**——直接复用 ``_evaluate_full`` 的返回值。

        ``session``(ADR-021):治理层经 ``authorize()`` 传 ``ctx.session``,使
        ``guard.evaluated``/``guard.rejected`` 落在**发起该调用**的会话;缺省
        None = 沿用装配期会话(主路径不变)。

        ``EvaluationResult`` 经**函数内**延迟 import 取得,避免给 ``core`` 增加
        模块级治理依赖(import 图不变)。
        """
        from pyharness.governance.decision import EvaluationResult
        d, guard_ids, policy_refs = await self._evaluate_full(
            call, scope, session=session)
        return EvaluationResult(verdict=str(d), guard_ids=tuple(guard_ids),
                                policy_refs=tuple(policy_refs))

    # ------------------------------------------------------------ 插件挂载
    def register_plugin_guard(self, guard: Guard) -> None:
        """插件 guard 只加严挂载:追加链尾(单调;只增拒绝面)。

        拒绝面:①重名(TLB-801,guard id 全链唯一);②已禁用 guard(BUSY,须先
        config 启用再挂)。成功 → 链尾 + 装配版本 +1(审批重入复核策略是否已
        变)+ registry.updated 总线广播(尽力而为)。无任何移除/重排 API。
        """
        if not isinstance(guard, Guard):
            raise_code("TLB-801", guard=type(guard).__name__,
                       advice="插件 guard 须继承 Guard(契约:id/match/check)")
        ids = [g.id for g in self.chain]
        if guard.id in ids:
            raise_code("TLB-801", guard=guard.id,
                       advice="guard id 全链唯一(重复挂载被拒)")
        if guard.id in self.disabled:
            # BUSY 未登记码直构(见偏离 4:raise_code 会改写为 CYC-999)
            raise PyHError("BUSY", ctx={"guard": guard.id,
                                        "advice": "先启用(config)再挂载"})
        self.chain.append(guard)                  # 恒在链尾(单调)
        self._snapshot_seq += 1
        self._emit_bus("registry.updated",
                       {"op": "add", "kind": "guard", "key": guard.id})

    # ------------------------------------------------------------ 显式降级口
    def disable(self, guard_id: str, *, config_ref: str) -> None:
        """单个内置 guard 关闭(只可收严的反面特例;SECURITY §4.1)。

        必须 config 显式声明(config_ref 指明出处留痕),默认拒绝:
        - g-schema/g-danger(强制恒在)或不在链上的 id → CFG-601;
        - 五内置之一且 config 声明 → 关 + guard.disabled 事件(谁在何时放松
          安全,永远可审计;事件词表外 → 尽力而为留痕,见偏离 1)。
        """
        if guard_id in FORCED_GUARDS or guard_id not in {g.id for g in self.chain}:
            raise_code("CFG-601", guard=guard_id,
                       why="g-schema/g-danger 强制恒在;其余须 config 声明",
                       fields=[f"guards.disabled:{guard_id}"])
        if guard_id in self.disabled:
            return                                 # 幂等:已关不重复留痕
        self.disabled.add(guard_id)
        self._snapshot_seq += 1
        self._record("guard.disabled",
                     {"guard_id": guard_id, "config_ref": config_ref})

    # ------------------------------------------------------------ 只读审计面
    def enabled_guard_ids(self) -> list[str]:
        """当前生效链 id(审计/自检 F031;排除 config 显式关闭项)。"""
        return [g.id for g in self.chain if g.id not in self.disabled]

    def chain_version(self) -> int:
        """装配版本号(审批重入复核:策略装配是否已变;单调 +1)。"""
        return self._snapshot_seq

    def __getattr__(self, name: str) -> Any:
        """结构防线:禁 bypass/放行/执行类方法面(INV-04 测试钉死)。"""
        if name in self._FORBIDDEN_API:
            raise AttributeError(
                f"GuardChain 无 {name} API:guard 只有拒绝和请示,永远没有"
                f"放行/执行路径(单调拒绝,INV-04);执行权归 executor 关3")
        raise AttributeError(f"{type(self).__name__!r} object has no "
                             f"attribute {name!r}")

    # ------------------------------------------------------------ 事件出口
    async def _audit(self, call: Any, decision: Decision, guard_ids: Any,
                     policy_ref: Optional[str], *,
                     session: Any = None) -> None:
        """evaluated 审计点(INV-04):每次求值恰一条,决策落词表词。

        payload 适配已落地 GuardEvaluatedPayload(偏离 1):decision ∈
        allow|deny|need_approval(内部 Decision 映射);policy_ref 并入 reasons;
        call_id 经 trace 携带(与 tool.call 配对回放)。普通异步落盘。
        ``session``(ADR-021):None = 沿用装配期会话;治理层传 ``ctx.session``。
        """
        await self._append(
            "guard.evaluated",
            {"tool": call.name,
             "decision": _AUDIT_WORD[decision.value],
             "guard_ids": ([guard_ids] if isinstance(guard_ids, str)
                           else list(guard_ids or [])),
             "reasons": ([policy_ref] if policy_ref else None)},
            actor="tool", trace={"call_id": getattr(call, "call_id", "")},
            session=session)

    async def _append_rejected(self, call: Any, guard_id: str, policy_ref: str,
                               *, session: Any = None) -> None:
        """拒绝强同步留痕:guard.rejected 唯一事件出口(INV-05 审计证据)。

        sync=True:落盘成功才返回(崩溃不丢"拦了"事实);失败(PERS-202/EVT-1xx)
        上抛——调用方 fail-closed,绝不带着未落盘的拒绝继续(审计不能撒谎)。
        policy_ref 不含参数原文(防凭据入审计,SECURITY §6.4)。
        ``session``(ADR-021):与 ``_audit`` **同源**——成功与拒绝必须同归一会话,
        否则同一条链的两个出口分属两处(语义自相矛盾)。
        """
        await self._append(
            "guard.rejected",
            {"tool": call.name, "guard_id": guard_id,
             "reason": policy_ref or "GRD-401",
             "policy_ref": policy_ref or "GRD-401"},
            actor="tool", sync=True,
            trace={"call_id": getattr(call, "call_id", "")},
            session=session)

    async def _append(self, type_: str, payload: dict, *, actor: str,
                      sync: bool = False,
                      trace: Optional[dict] = None,
                      session: Any = None) -> None:
        """append 统一封装:兼容同步替身/异步 SessionLog(await 实际返回)。

        **唯一解析点**(ADR-021):``session is None`` → 沿用装配期 ``self._session``
        (主路径逐字不变);显式传入 → 该事件落调用方指定会话。
        """
        sess = session if session is not None else self._session
        if sess is None:
            log.debug("guard 未接线事件出口,%s 未记录(sync=%s)", type_, sync)
            return
        r = sess.append(type_, payload, actor=actor, sync=sync, trace=trace)
        if inspect.isawaitable(r):
            await r

    def _record(self, type_: str, payload: dict) -> None:
        """词表外事件(guard.disabled)尽力而为留痕(偏离 1;scope.py 同款)。

        真实 SessionLog 现会 EVT-102 拒写(词表 57 锁定,后续阶段门扩展);
        未接线/失败只记日志,不阻断策略主路径(与 scope._record 一致)。
        """
        sess = self._session
        if sess is None:
            log.debug("guard 未接线事件出口,%s 未记录", type_)
            return
        try:
            r = sess.append(type_, payload, actor="system")
        except Exception as exc:                   # noqa: BLE001 尽力而为
            log.warning("guard append %s 失败: %s", type_, exc)
            return
        if inspect.isawaitable(r):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return
            task = asyncio.create_task(self._safe_record(r, type_))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _safe_record(self, coro: Any, type_: str) -> None:
        try:
            await coro
        except Exception as exc:                    # noqa: BLE001 EVT-102 等
            log.warning("guard append %s 异步失败: %s", type_, exc)

    def _emit_bus(self, type_: str, payload: dict) -> None:
        """总线尽力而为出口(registry.updated 瞬时事件,仅总线无载荷模型)。"""
        bus = self._bus
        if bus is None:
            return
        from pyharness.bus import schedule_emit
        try:
            schedule_emit(bus, type_, payload)
        except Exception as exc:                    # noqa: BLE001 未注册类型等
            log.warning("guard emit %s 失败: %s", type_, exc)


# ================================================================= 模块函数
# 插件 guard 钩子注册表(hook 名 → Guard 实例;capability 经 Definition
# guard_hooks 声明后由 match_guard_by_hook 解析,announce 时挂链尾,F023)
_PLUGIN_HOOKS: dict[str, Guard] = {}


def register_guard_hook(hook: str, guard: Guard) -> None:
    """登记插件 guard 钩子名(能力包启动期调用;重名/非 Guard → TLB-801)。"""
    if not isinstance(guard, Guard):
        raise_code("TLB-801", hook=hook, advice="插件 guard 须继承 Guard")
    if hook in _PLUGIN_HOOKS:
        raise_code("TLB-801", hook=hook, advice="guard hook 名全库唯一")
    _PLUGIN_HOOKS[hook] = guard


def unregister_guard_hook(hook: str) -> bool:
    """注销插件 guard 钩子名(幂等;返回是否真的摘除)。

    2026-09-21 补:此前**只有注册没有注销**,而 ``PluginManager.uninstall`` 的
    docstring 明写"支持重装(F004)" —— 含 guard 钩子的插件重装时会在
    ``register_guard_hook`` 处撞 TLB-801"名全库唯一",即**该声明对这类插件为假**;
    且卸载后陈旧 Guard 仍留在表里,``match_guard_by_hook`` 仍会把它挂到新声明上。
    """
    return _PLUGIN_HOOKS.pop(str(hook), None) is not None


def match_guard_by_hook(defn: Any) -> list[Guard]:
    """按 Definition.guard_hooks 名字解析出 Guard 实例(announce 时挂载用)。

    guard_hooks 中未登记的钩子 = 声明了不存在的 guard(非法注册证据)→
    TLB-801 拒(能力须先 register_guard_hook 再声明)。返回实例按 hooks 声明序。
    """
    hooks = tuple(getattr(defn, "guard_hooks", ()) or ())
    out: list[Guard] = []
    for name in hooks:
        g = _PLUGIN_HOOKS.get(str(name))
        if g is None:
            raise_code("TLB-801", hook=str(name),
                       advice="guard_hooks 声明了未登记的 guard:先 "
                              "register_guard_hook 再挂载")
        out.append(g)
    return out


def from_config(cfg: Any, *, credentials: Any = None,
                workspace: Any = None, session: Any = None,
                bus: Any = None,
                approval_channel: Optional[bool] = None,
                validator: Optional[Callable[..., dict]] = None,
                path_exists: Optional[Callable[[str], bool]] = None,
                link_resolver: Optional[Callable[[str], str]] = None) -> GuardChain:
    """启动第④步装配:内置 g1-g7(固定序)+ config 显式禁用项(DIS-SEAM §2.5)。

    读取:cfg.security.guards.disabled(五内置真子集,越权在 config 层已拒
    CFG-601,此处 disable() 复核兜底,逐个写 guard.disabled 事件);
    cfg.security.credentials.file(默认 ~/.pyharness/credentials.yaml)→ g4
    凭据清单;approval_channel 由外壳装配注入(headless=False,偏离 3)。
    签名差异(spec 伪码含 scope 参数):链按 call 绑定 scope,装配不消费 scope
    → 移除;credentials/workspace 参数保留为注入位(见偏离 5/7)。
    异常:CFG-601(装配序违反/非法禁用项)、TLB-801(链装配重名)。

    注入位(2026-09-14 S1-01 补齐):validator/path_exists/link_resolver 原经
    GuardChain 构造注入,但本工厂此前不透传 → 装配层若改用本入口,则 g1 的
    validator 恒 None(g-schema 空转,INV-04 的内层复查失效)、g4 落到兜底清单、
    g7 用默认 os.path.exists。现全部透传(关键字可选,缺省 None = 既有行为,
    API 向后兼容);validator 生产值 = registry.validate_args(engine 装配注入)。
    """
    cred_paths: list[str] = []
    if credentials is not None:
        paths = getattr(credentials, "paths", credentials)
        cred_paths = [str(p) for p in (paths or ())]
    else:
        f = getattr(getattr(cfg, "security", None), "credentials", None)
        cred_paths = [str(getattr(f, "file", DEFAULT_CREDENTIAL_FILES[0]))]
    chain = GuardChain(
        credential_paths=cred_paths, session=session, bus=bus,
        approval_channel=approval_channel, validator=validator,
        path_exists=path_exists, link_resolver=link_resolver)
    for gid in (getattr(getattr(cfg, "security", None), "guards", None)
                and getattr(cfg.security.guards, "disabled", None)) or []:
        chain.disable(str(gid), config_ref="security.guards.disabled")
    return chain


# ============================================ 规则声明式描述(2026-09-14 S2-1)
# 供治理层 PolicyEngine 装配消费的**只读描述**面(pyharness/governance/)。
# 纪律:① **不改 g1-g7 判定逻辑**(G-3 规则实现冻结;本段只读既有 chain 构造);
#       ② **不 import governance**(反向依赖禁止,ADR-018);
#       ③ 描述符只含声明与可调用对象引用,不含判定本身。

# rule_id → 该规则可能产出的策略引用(policy_ref)。来源 = 各 g*_check 的返回
# 字面量;本表是**声明**,不参与判定——判定仍以 check 函数返回为准(只读投影)。


# 目录不存在/被误当包 import 时的告警(防装配静默失败;正常 import 无输出)
_log_ready = log.getEffectiveLevel()

__all__ = [
    # 决策
    "Decision", "danger_default_policy",
    # 契约
    "Guard", "GuardChain", "ToolCall",
    # 内置 guard 函数对(F023;match/check 分离,spec 清单)
    "g_schema_match", "g_schema_check",
    "g_danger_match", "g_danger_check",
    "g_fs_path_match", "g_fs_path_check",
    "g_credential_read_match", "g_credential_read_check",
    "g_net_outbound_match", "g_net_outbound_check",
    "g_exec_match", "g_exec_check",
    "g_overwrite_match", "g_overwrite_check",
    # 装配/工厂
    "build_builtin_chain", "from_config",
    "register_guard_hook", "unregister_guard_hook", "match_guard_by_hook",
    # 声明式描述(S2-1;治理层装配消费面——只读,不改判定)
    "RuleDescriptor", "describe_rules",
    # 常量(审计/测试锚点)
    "FORCED_GUARDS", "DANGER_LEVELS", "_BUILTIN_IDS",
]
