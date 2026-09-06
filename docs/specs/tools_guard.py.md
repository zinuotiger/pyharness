# specs/tools_guard.py.md — 编码规格

> **模块文件**:`pyharness/core/tools_guard.py` | **功能编号**:F014(guard 单调链核心)· F023(内置危险 guard g1-g7)· 联动 F026(g1 schema)/F015(approval 交接) | **权威口径**:PRD-Core §4.3(原则 3)/§5.2 F014/F023、SECURITY §4(单调策略 L0-L4 全展开)、DIS-CORE §7(链序)、DIS-SEAM §5(伪代码级);冲突以 PRD-Core 为准
> **一句话**:工具执行前的单调安全求值器——Guard 接口 + GuardChain(按注册序 waterfall,任一 guard 可拒绝且**拒绝不可被后续覆盖**)+ g1-g7 内置 guard + danger 分级(L0-L4)默认策略;决策三值 allow/reject/approval(任务上下文亦写作 allow/deny/ask,**ask ≡ approval,内部无 bypass 值**);每个 reject 必出强同步 `guard.rejected` 事件,审计可证"拦了且没执行"(INV-04/05)。
> **代码目录**:本文件合并 DIS-SEAM §5 中 `core/guard.py`(GuardChain)与 `guards/`(Guard 基类+内置 g1-g7)内容,按 specs/ 文件拆分落地;实现时若保持 guards/ 目录仅移动文件,不改接口。

## 模块职责

1. **三值决策与单调终局(原则 3)**:决策枚举 `{allow, reject, approval}`,无 bypass 值——不存在"跳过 guard 的标记位";任一 guard 返回非 allow 即短路(waterfall),reject = 终局,同 call 后续全部 guard 不再求值、Provider 零执行(INV-05),**无任何 API 能把 reject 翻回 allow**;插件 guard 只加拒绝面(只增链尾,不可移除/重排内置节)。
2. **审计事件(INV-04/R4)**:每次求值必写 `guard.evaluated`(tool/decision/guard_ids/policy_ref)——执行前缺该事件 = 非法执行;每次 reject 必写 `guard.rejected`(tool/guard_id/reason/call_id/policy_ref,**强同步三类之一**,§3.6),两事件是"单调性证明"的审计素材。
3. **danger 分级默认策略(SECURITY §4.2/L0-L4)**:策略分层 L0 系统配置 → L1 会话 scope(deny_tools/allowlist/workspace,只紧不松)→ L2 工具契约(Definition.danger,不可变)→ L3 调用裁决(本文件 GuardChain)→ L4 人类审批(approval.py);guard 求值只服从已定策略,`g-danger` 按 danger 输出:high→approval、critical→reject(POL-DGR-1,不可审批,强制把 approval 转 reject)、none/low→allow;critical 无审批通道是刻意设计(人类真想删须下调沙箱留事件,而非弹窗点同意)。
4. **内置 g1-g7(F023)**:g1-g5 恒在(g-schema/g-danger/g-fs-path/g-credential-read/g-net-outbound),g6/g7(g-exec/g-overwrite)与插件 guard 按注册序追加链尾;五内置 guard 不可整体关闭——单个关闭须 config 显式声明并写 `guard.disabled` 事件(SECURITY §4.1);按**动作形态**兜底(match 按名前缀/Definition 结构),不信任描述文本(防"低危声明+高危实现"伪装工具)。
5. **审批重入支持(PRD §6.7)**:evaluate 只返回决策;approval 的裁决与"granted 后重入链起点"由 executor/approval 编排(本文件提供可重复调用的 evaluate,单调性高于人类即时意志——批准期间策略收紧则重入 reject,GRD-403 语义)。
6. **取消语义**:审批等待期间策略收紧/会话取消,以新一次 evaluate 决策为准,本文件无状态残留(纯策略求值,唯一状态 = 链装配)。

## 依赖

- **单向依赖**:本文件 → `errors.raise_code`(F020)、`session.append`(经注入写 evaluated/rejected)、scope(SECURITY §6 scope.can_use 前置)、credentials(g4 凭据路径清单,F016)、workspace resolve(g3 单点,F055);不 import Provider/executor(反向依赖禁止)。
- **消费方**:tools_executor.execute(关2,每调用一次 evaluate)、approval.py(granted 后再次 evaluate)、agent-loop(经 ctx.guard.evaluate 的间接消费)。
- **装配**:启动第④步脊柱硬接线按序构造 g1-g7(DIS-SEAM §2.5);guard.disabled 判定读 config(F021 运行期只读,防漂移)。

## 数据结构表

### Decision(三值;内部唯一字面量)

| 值 | 别名口径 | 语义 | 后续 |
|---|---|---|---|
| `allow` | allow | 放行,继续下一 guard 或执行 | 全链 allow → Provider 执行 |
| `reject` | deny | 终局拒绝 | guard.rejected 强同步;零副作用;无续跑 API |
| `approval` | ask | 请求人类裁决(仅 danger=high 且有通道) | 交 approval.py;granted 后**重入链起点** |

### Guard 接口

| 字段/方法 | 签名 | 规则 |
|---|---|---|
| `id` | `str` | 事件 guard_id,如 "g-fs-path" |
| `match(call)` | `(call) -> bool` | 管不管该工具:按名前缀(`fs.`/`workspace.`/`exec.`…)或 Definition 声明 |
| `check(call, scope)` | `async (call, scope) -> (decision, policy_ref)` | 判定;policy_ref 如 POL-FS-1;不读 description |

### GuardChain 内部

`chain: list[Guard]`(注册序=求值序,只增不改)、`disabled: set[str]`(config 显式声明,写事件)、`_snapshot_seq`(装配版本,审批重入复核用)。

### 策略分层 L0-L4(SECURITY §4.1 权威)

| 层 | 载体 | 只紧不松机制 |
|---|---|---|
| L0 | config/环境变量 | 启动只读,运行期不热重载(防策略漂移,F021) |
| L1 | 会话 ScopePolicy | 仅 tighten(追加 deny/降级标记),无 relax API;下调须显式确认+事件 |
| L2 | Definition.danger/guard_hooks | frozen 不可变;改=注销重注册留痕 |
| L3 | GuardChain(本文件) | 任一 reject 终局;新 guard 只增链尾 |
| L4 | 人类审批(approval.py) | granted 后重入 L3,单调性高于人类即时意志 |

### 内置 guard 链(g1-g7)

| 位 | id | match 依据 | 判定要点 | policy_ref |
|---|---|---|---|---|
| g1 | g-schema | 所有工具+内部调用 | schema 复查(入口 F026 先行,链内防内层直调 INV-04) | TLB-803 转 reject |
| g2 | g-danger | danger≠none | high→approval;critical→reject(不可审批) | POL-DGR-1 |
| g3 | g-fs-path | `fs.*`/`workspace.*` | 绝对路径越界/`..` 逃逸/symlink 解析后越界(单点 resolve,F055) | POL-FS-1/2/3 |
| g4 | g-credential-read | 读路径类工具 | 目标命中凭据文件清单(credentials.yaml 等)→拒 | POL-CRED-1 |
| g5 | g-net-outbound | web.*/网络工具 | 目标域不在 allowed_domains(默认空=禁外发)→拒 | POL-NET-1 |
| g6 | g-exec | exec.subprocess/exec.pty | 无 shell=True;cwd 限 workspace;需 scope 显式授权 | POL-EXEC-1 |
| g7 | g-overwrite | fs.write 等写工具 | 目标已存在 → approval(覆写转审批) | POL-OVW-1 |

## 类与函数清单

### `async def evaluate(call, scope) -> Decision` — 单调链求值(F014 主函数)

**功能**:scope 前置(不可见 → 终局 reject,GRD-401)→ 按注册序对命中的 guard 逐个 check → 首个非 allow 短路返回;每次求值先写 guard.evaluated,reject 再强同步 guard.rejected;全链 allow 才返回 allow。**拒绝终局、无续跑 API、审批需重入本函数**。

```python
async def evaluate(self, call, scope):
    if not scope.can_use(call.name):                    # scope 前置:不可见→终局
        await self._audit(call, "reject", "scope-hidden", "GRD-401")
        return Decision.REJECT
    for g in self.chain:                                # 按注册序 waterfall
        if g.id in self.disabled: continue              # 仅 config 显式关闭(留过事件)
        if not g.match(call): continue                  # 非本 guard 管辖→看下一个
        d, policy = await g.check(call, scope)          # (allow/reject/approval, policy_ref)
        if d == "approval" and call.defn.danger == "critical":
            d, policy = "reject", "POL-DGR-1"           # critical 不可审批:强制转 reject
        if d != "allow":                                # 首个非 allow 即短路
            await self._audit(call, d, g.id, policy)
            if d == "reject":                           # 强同步:拦了且没执行(INV-05)
                await self._append_rejected(call, g.id, policy)
            return d                                    # reject/approval 都到此为止
    await self._audit(call, "allow", [g.id for g in self.chain if g.id not in self.disabled], None)
    return Decision.ALLOW                               # 全链 allow → executor 关3
```

**参数表**:`call` = ToolCall(name/raw_args/call_id/defn 引用);`scope` = 会话 ScopePolicy。**返回**:`Decision`(allow/reject/approval)。**异常表**:

| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| `PyHError` | scope 前置不可见(deny/strict/critical) | GRD-401 | guard.rejected(scope-hidden) 已落;换工具/调 scope |
| `PyHError` | 对已拒绝 call_id 重求值 | GRD-402 | 防重放:executor 层拦,不重复审计 |
| `PyHError` | 批准后重入被(新)拒 | GRD-403 | 以新决策为准(非错误);两条 evaluated 可证 |
| `PyHError` | 强同步落盘失败 | PERS-202 | append 抛错;repair 后重试 |

**关联测试**:GWT-T7-03(拒绝零副作用:critical 删除工具 → guard.rejected 强同步、Provider mock 计数 0)、T-SEC-02(workspace 内删除亦拒且**无 approval.requested**)、GWT-S6-03(scope 前置拒绝)、DIS-SEAM G2(critical 强制转 reject 不发请求)。

### `async def _append_rejected(call, guard_id, policy_ref) -> None` — 拒绝强同步留痕

**功能**:reject 的唯一事件出口——`guard.rejected`(tool/guard_id/reason/call_id/policy_ref)**强同步落盘**(sync=True,§3.6),成功才返回;审计以此证明"拦了且没执行"。policy_ref 不含参数原文(SECURITY §6.4,防凭据入审计)。

```python
async def _append_rejected(self, call, guard_id, policy_ref):
    await self._session.append("guard.rejected",
        {"tool": call.name, "guard_id": guard_id, "reason": policy_ref,
         "call_id": call.call_id, "policy_ref": policy_ref},
        actor="tool", sync=True)        # 强同步三类之一:崩溃不丢拦截事实
```

**参数表**:见上。**异常表**:PERS-202(落盘失败,抛错——拒绝事实必须落地)。**关联测试**:T-SEC-01/05(事件含 guard_id+policy_ref,回放逐条可查)。

### `async def _audit(call, decision, guard_ids, policy_ref) -> None` — evaluated 审计点(INV-04)

**功能**:每次求值(无论结果)写一条 `guard.evaluated`(tool/decision/guard_ids/policy_ref),普通异步落盘;INV-04 断言每个 tool.result 前必有同 call_id 的 evaluated——本函数即该不变量的写入点。

```python
async def _audit(self, call, decision, guard_ids, policy_ref):
    await self._session.append("guard.evaluated",
        {"tool": call.name, "decision": decision,
         "guard_ids": guard_ids if isinstance(guard_ids, list) else [guard_ids],
         "policy_ref": policy_ref}, actor="tool")
```

**异常表**:EVT-100/101(信封/seq 违规拒写——属内部 bug,查 append 调用方)。

### `def register_plugin_guard(guard: Guard) -> None` — 插件 guard 只加严挂载

**功能**:把能力 guard_hooks 声明的 Guard 追加链尾;单调性规则:只增拒绝面,无 API 移除/重排 g1-g7;卸 guard 须先停用其工具(F014 边界);装配版本号 +1 供审批重入复核。

```python
def register_plugin_guard(self, guard):
    ids = [g.id for g in self.chain]
    if guard.id in ids:
        raise PyHError("TLB-801", ctx={"guard": guard.id,      # 重名拒
            "advice": "guard id 全链唯一"})
    if guard.id in self.disabled:                       # 已禁用 guard 拒装
        raise PyHError("BUSY", ctx={"guard": guard.id, "advice": "先启用(config)再挂载"})
    self.chain.append(guard)                            # 恒在链尾(单调)
    self._snapshot_seq += 1
    bus.emit("registry.updated", {"op": "add", "kind": "guard", "key": guard.id})
```

**异常表**:TLB-801(重名)、BUSY(禁用的 guard)。**关联测试**:DIS-SEAM G4(链长=6 且新 guard 恒在链尾;无 API 移除 g1-g5)。

### `def disable(guard_id: str, *, config_ref: str) -> None` — 显式降级口(只可收严的反面特例)

**功能**:单个内置 guard 关闭,必须 config 显式声明(默认拒绝);写 `guard.disabled` 事件留痕(SECURITY §4.1);五内置 guard 整体关闭在装配层即拒(CFG-601)。

```python
def disable(self, guard_id, *, config_ref):
    if guard_id in ("g-schema", "g-danger") or guard_id not in {g.id for g in self.chain}:
        raise PyHError("CFG-601", ctx={"guard": guard_id,       # g1/g2 不可关
            "why": "g-schema/g-danger 强制恒在;其余须 config 声明"})
    self.disabled.add(guard_id)
    session.append("guard.disabled", {"guard_id": guard_id,
        "config_ref": config_ref}, actor="system")      # 谁在何时放松安全,永远可审计
```

**异常表**:CFG-601(g1/g2 或未声明关闭)、EVT 系(事件拒写)。

### 内置 guard 实现清单(每个 = match + check 两个函数;F023)

| 函数 | check 判定(伪代码分支) | policy_ref |
|---|---|---|
| `def g_schema_match(call)` / `async def g_schema_check(call, scope)` | 入口 F026 已验;链内对内部直调复查——`try registry.validate_args(...) except TLB-803: return ("reject","TLB-803")`;失败 = 内层绕过证据 | TLB-803 |
| `def g_danger_match(call)` / `async def g_danger_check(call, scope)` | `if defn.danger == "high": return ("approval", None)`;`if defn.danger == "critical": return ("reject", "POL-DGR-1")`;none/low → allow | POL-DGR-1 |
| `def g_fs_path_match(call)` / `async def g_fs_path_check(call, scope)` | 路径 = resolve(args.path/src/dst);`绝对且不在 workspace → POL-FS-1`;`".." 逃逸且 normpath 越界 → POL-FS-2`;`symlink 最终解析越界 → POL-FS-3`;全过 → allow | POL-FS-1/2/3 |
| `def g_credential_read_match(call)` / `async def g_credential_read_check(call, scope)` | 读类工具;`resolve(path) 命中凭据文件清单 → ("reject","POL-CRED-1")`;否则 allow | POL-CRED-1 |
| `def g_net_outbound_match(call)` / `async def g_net_outbound_check(call, scope)` | 域名归一后 `not in scope.policy.allowed_domains → ("reject","POL-NET-1")`(默认空=禁一切外发);IP 直连/混淆同样归一比对 | POL-NET-1 |
| `def g_exec_match(call)` / `async def g_exec_check(call, scope)` | `无 scope 显式授权 or strict 级别 → reject`;`args.shell is True → reject`;`cwd 不在 workspace → reject`;否则 allow | POL-EXEC-1 |
| `def g_overwrite_match(call)` / `async def g_overwrite_check(call, scope)` | 写类工具;`目标已存在且 mode=write → ("approval","POL-OVW-1")`;append/新建 → allow | POL-OVW-1 |

每个内置 check 伪代码满足 ≥5 行全分支,范式(g3 权威样例,PRD F023):

```python
async def g_fs_path_check(self, call, scope):
    if not (call.name.startswith("fs.") or call.name.startswith("workspace.")):
        return ("allow", None)                          # 非文件域不管(match 兜底)
    p = resolve(call.args.get("path") or call.args.get("src") or "")
    if is_absolute(p) and not p.startswith(scope.workspace):
        return ("reject", "POL-FS-1")                   # 绝对路径越界
    if ".." in parts(p) and normpath(p) not in scope.workspace:
        return ("reject", "POL-FS-2")                   # .. 逃逸
    if is_symlink(p) and resolve_final(p) not in scope.workspace:
        return ("reject", "POL-FS-3")                   # symlink 解析后越界
    return ("allow", None)                              # 几何边界内 → 放行
```

### 其余函数速览

| 函数 | 功能一句话 | 备注 |
|---|---|---|
| `def danger_default_policy(danger, *, has_channel) -> Decision` | danger 分级默认策略:none/low→allow;high→(has_channel ? approval : reject);critical→reject | SECURITY §4.2 机器表达;headless=R8 |
| `def match_guard_by_hook(defn) -> list[Guard]` | 按 Definition.guard_hooks 名字解析出 Guard 实例 | announce 时供 register_plugin_guard |
| `def enabled_guard_ids() -> list[str]` | 当前生效链 id(审计/自检 F031) | 排除 disabled |
| `def chain_version() -> int` | 装配版本号(审批重入复核策略是否已变) | 单调 +1 |
| `def from_config(cfg, *, scope, credentials, workspace) -> GuardChain` | 启动第④步装配 g1-g7(固定序)+ config 禁用项 | DIS-SEAM §2.5 |
| `def _reject_record(call, policy_ref) -> dict` | 构造 rejected 事件 payload(统一字段) | 脱敏:无参数原文 |

**模块级测试**:T-SEC-01~05(主场景三条 guard.rejected、Provider 全零、审计逐条回放)、T-SEC-07(网络白名单拒)、T-SEC-10(审批重入:granted 后策略收紧仍拒,事件序 approval.granted→guard.evaluated(reject),无"granted 后直接执行"序列)、test_f023_builtin_guards.py(五 guard 命中+放行例)、INV-04/05。

## 关联文档

1. PRD-Core.md §4.3(原则 3 单调拒绝,唯一权威)/§5.2 F014/F023/§6.2 R2/R4。
2. SECURITY.md §4(guard 单调策略面:L0-L4 分层、内置 guard 集、三种审计证明)、§9(INV-04/05)。
3. DIS-CORE.md §7.4/§7.5(单调用状态机与错误路径)、§7.7 GWT-T7-03/04。
4. DIS-SEAM.md §5(决策模型/链求值伪代码/g1-g7 表/新增 guard 模板),ERR.md §2.5 GRD-4xx 与 §2.11 POL-* 全标识,EVENT-SCHEMA.md(guard.evaluated/rejected/disabled)。
5. 验收测试:tests/security/test_sec_*.py(T-SEC-01~05/07/10)、tests/acceptance/test_f014_guard.py、test_f023_builtin_guards.py、tests/invariants/(INV-04/05)。
