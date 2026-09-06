# ERR.md — 错误码全量目录(错误码契约权威清单)

> **元信息**:项目 PyHarness(用 Python 复刻 DSH 全部功能的 Agent 框架);错误码体系 = PRD-Core F019(码体系)/F020(结构化错误),本文档即 ADR-011「错误码为对外契约」的登记册与查表依据。
> **权威源与冲突裁决**:码结构与域区间以 PRD-Core.md F019 为唯一权威;单码语义以 DIS-CORE/DIS-SEAM/EVENT-SCHEMA 异常表细化;冲突时 PRD-Core 优先。
> **关联文档**:PRD-Core.md(§3.7 EVT-100~106 处置表、F012/F013/F014/F019~F021/F026/F030/F039)· EVENT-SCHEMA.md(§6 EVT 映射、§7 事件级错误码)· DIS-CORE.md(§1-§8 各模块异常表)· DIS-SEAM.md(§2.7/§5.4/§6.1-6.3)· SECURITY.md(GRD/POL)· ADD.md(ADR-011)。
> **更新规则**:新增码 = PRD F019 注册表 + 本文档 + EVENT-SCHEMA + 测试四件同步;码一经发布不改含义,只增新码;跨文档冲突按 §9.2 记录,不擅改码。

# 1 设计原则

## 1.1 为什么(ADR-011)

失败必然发生(配置错/断网/限流/校验失败/guard 拒绝/超时/内部 bug),消费方各异——agent-loop 要决策**降级/终止**,外壳要**展示**,测试要**断言**,LLM 要**理解自纠**,审计要**统计失败分布**。裸异常+文本只可字符串匹配,厂商文案一改判断即断(参数错被无限重试烧预算、限流错被放弃降级)。故:

> **全系统失败以稳定错误码表达(域前缀-NNN),每码带分类与默认处置;结构化错误携带 code+ctx 跨模块边界传递,不传裸异常。**

## 1.2 码结构与域区间(F019)

格式 `域前缀-NNN`,十个主域编号互不重叠:

| 域 | 区间 | 职责域 | 失败语义 | 权威出处 |
|---|---|---|---|---|
| BUS | 0xx | 插件总线/注册表/生命周期 | 装配层失败,拒绝不改状态 | PRD F001/F003/F006、DIS-SEAM §4 |
| EVT | 1xx | 事件域(写入口校验+分发) | 拒写(不进日志)/订阅者隔离 | PRD §3.7、EVENT-SCHEMA §6 |
| PERS | 2xx | 持久化(JSONL 真源) | 落盘/回放故障 | PRD F011/F039/F060、DIS-CORE §8 |
| LLM | 3xx | 模型域(唯一 LLM 出口+降级链) | 归一后上游失败,驱动重试/降级 | PRD F012/F013/F028/F030、DIS-CORE §4 |
| GRD | 4xx | guard/scope 单调安全链 | 拒绝终局,零副作用 | PRD F014、DIS-SEAM §5 |
| APR | 5xx | 人类审批流 | 通道/裁决异常,安全默认拒 | PRD F015、DIS-SEAM §6.2 |
| CFG | 6xx | 配置合并+提示词/护栏装配 | 启动非法/装配失败 | PRD F021、DIS-CORE §5/§6 |
| CRED | 7xx | 凭据管理(单一读取口) | 缺失/权限/泄漏,拒非空串 | PRD F016、DIS-SEAM §6.3 |
| TLB | 8xx | 工具(注册+执行管道四关) | 注册/校验/执行失败,回喂 | PRD F008/F022/F023/F026、DIS-CORE §7 |
| CYC | 9xx | 主循环/兜底(=通用兜底域) | 终态原因、未预期兜底 | DIS-CORE §1/§2(PRD F007) |

> 注:任务流转语境的「通用域」即 9xx,全部文档以 `CYC-` 书写,无 `CON-` 码。十域外还有已引用标识不占主编号(§2.11):POL-*(guard 拒绝原因)、QUE/JOB/PTY/ATT/EDT/TO(功能特性码)、BUSY(命名状态字面量)。已发布码:主域 37 条 + 功能码 6 + POL 标识 9 + BUSY。

## 1.3 分类体系(三轴,注册表 ErrorSpec 必填)

- **可重试性**:R 可自动重试(瞬时故障,退避有预算:LLM-301/303);F 修复后重试(改参数/repair 后:EVT-100、PERS-201/202、CFG-602/603);N 不可重试(重试白搭或有害:LLM-304、GRD-4xx、EVT-102/104/106、CFG-601/603、CRED-701)。
- **处置动作**(六值,码表"处置"列):拒写 / 回喂(转 tool.error·llm.error 给 LLM 自纠)/ 降级(换模型三元组重发,带 degraded_from)/ 暂停(会话挂起等 repair,拒新不丢旧)/ 终态(任务/会话终止写 reason)/ 隔离(记跳不中断)。
- **致命度**:致命(终止/暂停/拒载:LLM-310、CYC-999、PERS-202、CFG-601/603、CRED-702)vs 非致命(告警/回喂/拦截后续跑:EVT-103/105、TLB-802/803、GRD-401、LLM-302)。

**消费方决策口诀**:401/429/超时→退避或降级(按码不按文本);TLB-802/803→回喂不降级,连败 2 次终止该轮;GRD-401→终局零副作用,同 call_id 不可重放(GRD-402);CFG-6xx→装配失败,缺护栏不发请求;EVT-1xx→拒写不进日志;PERS-202→强同步抛/异步暂停;CRED-701→拒而非空串;未知失败→CYC-999,禁现场造码。

## 1.4 错误码 × 异常 × 事件 × 日志 × 文本(F020 五形态)

| 层 | 形态 | 承载 | 消费方 |
|---|---|---|---|
| 异常 | `PyHError(code, ctx=...)`,唯一入口 `raise_code(code, **ctx)` | code/name/message/ctx/advice/retryable | 同模块/上层 |
| 事件 | `system.error`(通用,actor=system)/`tool.error`(actor=tool)/`llm.error`(actor=llm),payload=code+hint/advice+ctx | append-only JSONL,可审计回放 | agent-loop、UI、审计 |
| 日志 | `log.error(struct_error(code, **ctx))` 单行;堆栈仅本地 debug | 排障(§6) | 运维 |
| 给 LLM | `to_llm_text()` ≤2000 字符含 advice,可行动 | LLM 上下文 | LLM 自纠 |
| 给用户/远端 | 只含 code+advice,不吐 ctx 敏感值 | API 响应/前端 | 用户 |

转换规则(§5 展开):各层必须**透传 code**,禁止模糊化;错误事件 actor=触发模块,禁用户冒名;强同步三类(user.message/guard.rejected/approval.*)失败立即抛(PERS-202);ctx 只放脱敏值,secret 原文永不出 CredentialsProvider(INV-09)。

## 1.5 登记纪律

1. 抛错唯一出口 raise_code;`ERRORS.get(code) or UnknownCode(code)` 兜底。
2. 禁裸 raise str / 无码异常跨边界;禁现场发明新码;未知失败归 CYC-999(堆栈仅本地)。
3. 新码五步:F019 注册表 → 本文档 §2/§3 登记 → 配测试(test_f019_error_codes.py + 场景 GWT)→ 新事件则同步 EVENT-SCHEMA → §9.2 一致性核对。
4. **建议码转正通道**:DIS-SEAM §6 已提出 PERS-221/222/223、APR-501/502/503、CRED-702/703,GRD-402/403、CFG-602/603、TLB-805/806 已细化落码——本文档已全部登记;引用实现须同步状态列。
5. 编号空洞(BUS-000/001、EVT-107+、PERS-203~220/224+、LLM-305~309、GRD-404+、APR-504+、CFG-604+、CRED-704+、TLB-804/807+、CYC-900~998)预留,禁占位登记;表外码出现即错误(§9.2 已记一例)。

# 2 全量错误码目录(按域分组)

> 状态口径:定稿=PRD-Core 直接定义;细化=DIS 授权落码且多处引用;建议=DIS-SEAM §6 提出待转正,单向演进。每码触发场景/可重试性/消息/排查见 §3 明细表。

## 2.1 BUS-0xx 总线/注册域(总线/注册表/生命周期,PRD F001/F003/F006)

| 码 | 名称 | 处置 | 状态 |
|---|---|---|---|
| BUS-002 | 保留名冲突(注册/卸载脊柱八模块或 spine 子系统) | 拒绝,不改注册表,registry.updated 留痕 | 定稿 |
| BUS-003 | 非法状态迁移 | 拒绝,状态机层抛(F006) | 定稿 |

## 2.2 EVT-1xx 事件域(写入口校验链+总线分发,PRD §3.7)

校验链(信封→类型→payload→seq→会话状态)任一失败拒写,日志行数不变。

| 码 | 名称 | 处置 | 状态 |
|---|---|---|---|
| EVT-100 | 信封/载荷非法 | 拒写,返字段明细,修正后重试 | 定稿 |
| EVT-101 | seq 不连续/重复 | 拒写;疑丢事件跑 repair | 定稿 |
| EVT-102 | 类型未注册 | 拒写;先注册 schema 才能 emit | 定稿 |
| EVT-103 | 订阅者异常 | 封装隔离,不中断其他订阅者 | 定稿 |
| EVT-104 | 终态写违规 | 拒写;查绕过路径(INV-01) | 定稿 |
| EVT-105 | 未知斜杠命令 | system.error 回显,会话继续,零 LLM | 定稿 |
| EVT-106 | 先于 created | 拒写 | 定稿 |

## 2.3 PERS-2xx 持久化域(JSONL 真源,F011/F060)

| 码 | 名称 | 处置 | 状态 |
|---|---|---|---|
| PERS-201 | 回放坏行 | 记跳+隔离,不中断回放;删否经 repair | 定稿 |
| PERS-202 | 落盘失败 | 强同步抛错;异步→system.error+会话暂停(拒新不丢旧),repair 恢复 | 定稿 |
| PERS-221 | spill 写失败 | 结构化错误+tool.error 回喂 | 建议 |
| PERS-222 | spill 越权读 | 拒绝零读取,回喂 | 建议 |
| PERS-223 | spill 超限拒写 | 拒写,回喂 | 建议 |

## 2.4 LLM-3xx 模型域(唯一出口+降级链,F012/F013;超时三档 10s/60s/180s)

| 码 | 名称 | 处置 | 状态 |
|---|---|---|---|
| LLM-301 | 超时 | R 退避≤4(落 llm.retry)→降级 | 定稿 |
| LLM-302 | 认证失败 | 不重试直接降级,带 degraded_from;查 CRED-701 | 定稿 |
| LLM-303 | 可重试上游错 | R 退避≤4,耗尽降级;可取消 | 定稿 |
| LLM-304 | 业务性失败 | 不重试不降级上抛;agent-loop 收 reason=error | 定稿 |
| LLM-305 | 预算超限 | 任务成本超预算,中止循环(reason=budget) | 细化 |
| LLM-310 | 链尾全败 | 终止任务(reason=error);降级>5 次/会话告警 | 定稿 |
| LLM-399 | 模型域未预期 | 非 PyHError 异常兜底归因;按 bug 提单,禁重试 | 建议 |

## 2.5 GRD-4xx guard/scope 域(单调链;reject 必有 evaluated+rejected 强同步)

| 码 | 名称 | 处置 | 状态 |
|---|---|---|---|
| GRD-401 | 调用被拒 | 终局:guard.rejected 强同步,Provider 零执行,无 tool.result | 定稿 |
| GRD-402 | 重放已拒调用 | 拒绝+system.error 防重放 | 细化 |
| GRD-403 | 批准后被新拒 | 以新决策为准(非错误),单调性高于批准 | 细化 |

## 2.6 APR-5xx 审批域(F015;TTL 120s 超时=denied;granted≠放行)

| 码 | 名称 | 处置 | 状态 |
|---|---|---|---|
| APR-501 | 无审批通道 | 直接拒;不发 approval.requested,无等待 | 建议 |
| APR-502 | 等待被取消 | denied,无悬挂 Future | 建议 |
| APR-503 | 裁决重放/未知 id | system.error,不重复执行 | 建议 |

## 2.7 CFG-6xx 配置/装配域(四层合并,F021)

| 码 | 名称 | 处置 | 状态 |
|---|---|---|---|
| CFG-601 | 配置非法/越权 | 拒绝启动会话,列非法字段;HARDENED 只紧不松 | 定稿 |
| CFG-602 | 模板缺变量 | 结构化错误,会话继续 | 细化 |
| CFG-603 | 护栏段构建失败 | 拒绝本次 LLM 调用(缺护栏不发请求) | 细化 |
| CFG-607 | 未知配置键/白名单外 env | 结构化错误;核对 CFG §4.3 白名单与键拼写 | 细化 |
| CFG-608 | 热更只读键被拒 | 策略/预算类键需重启生效;拒热更 | 细化 |

## 2.8 CRED-7xx 凭据域(F016 单一读取口,禁自行 os.environ)

| 码 | 名称 | 处置 | 状态 |
|---|---|---|---|
| CRED-701 | 凭据缺失 | 拒绝(结构化错误),绝不空串/None 续跑 | 定稿 |
| CRED-702 | 权限过宽(>600) | 启动拒载 | 建议 |
| CRED-703 | 疑似密钥外泄 | system.error 告警+打码放行 | 建议 |

## 2.9 TLB-8xx 工具域(注册+执行管道四关,F022/F023/F026)

| 码 | 名称 | 处置 | 状态 |
|---|---|---|---|
| TLB-801 | 重复/非法注册 | 拒绝并提示注销重注册;无脏数据 | 定稿 |
| TLB-802 | 工具未注册 | tool.error 回喂 LLM 自查,不静默 | 定稿 |
| TLB-803 | 参数校验失败 | 回喂明细零执行(INV-06);连败 2 次终止轮 | 定稿 |
| TLB-805 | 执行超时/异常 | tool.error 回喂,重试性由 LLM 判断 | 细化 |
| TLB-806 | 二进制拒读 | 结构化错误,提示换工具 | 定稿 |

## 2.10 CYC-9xx 主循环/兜底域(通用)

| 码 | 名称 | 处置 | 状态 |
|---|---|---|---|
| CYC-999 | 未预期异常 | system.error+终态 reason=error;堆栈仅本地 debug | 定稿 |

## 2.11 其他已引用标识(非 F019 主编号)

**POL-* 策略拒绝原因**:guard 拒绝细分,作 GRD-401 事件与 guard.evaluated 的 reason/policy_ref,处置一律=GRD-401(DIS-CORE §0.2 列为已知码):POL-FS-1 绝对路径越界 / POL-FS-2 `..` 逃逸 / POL-FS-3 symlink-junction 解析后越界(g3,resolve 单点 F055);POL-DGR-1 critical 直拒不可审批(g2);POL-CRED-1 读路径命中凭据文件(g4);POL-NET-1 域不在 allowed_domains(g5,默认空);POL-EXEC-1 exec 无授权/shell=True/cwd 越界(g6);POL-OVW-1 覆写已有文件转审批(g7);scope-hidden 前置不可见;POL-DIR-1 插件 guard 示例(DIS-SEAM §5.4)。

**功能特性码**(EVENT-SCHEMA §7 收编):QUE-001 任务队列满(≥32)拒入队(F043);JOB-001 并发 job 达上限(≥4)拒新;PTY-001 pty 单例被占用拒新会话;ATT-001 附件 >10MB/mime 非白名单拒写(F061);EDT-001 编辑超 30 分钟窗拒写(F063);TO-301 subprocess/PTY 超时已杀死。

**BUSY 命名字面量**:loop 忙(队深>10)/重复 create active agent/closed 后 submit → 以 PyHError("BUSY",advice) 抛并 system.error[BUSY] 事件化,显式拒新不静默;不占 NNN 位,禁造数字别名;建议=等当前 run 结束或新开会话。

# 3 核心码明细(36 条:编号/名称/触发/可重试/处置降级/用户消息/排查)

可重试 = R 自动(有预算)/ F 修复后 / N 否;用户消息为外壳渲染基准,可措辞微调但必须保留码与 advice。

| 码·名称 | 触发场景 | 可重试·处置/降级 | 用户可见消息(基准) | 排查指引 |
|---|---|---|---|---|
| EVT-100 信封/载荷非法 | 信封字段非法、payload 强校验失败、空消息、BadTarget | F·拒写,返字段明细 | “事件校验失败:〈明细〉,已拒绝写入” | 对照 EVENT-SCHEMA 词表与 payload 模型;查 append 调用方 |
| EVT-101 seq 不连续/重复 | seq 跳跃/重复、引用事件不存在、无父 result | F·拒写,提示 repair | “序号不连续(期望 X 得 Y);疑丢事件请跑 repair” | 查 JSONL seq 空洞;查绕过 session.append 路径(INV-01) |
| EVT-102 类型未注册 | 类型不在词表且未 register_type | F·拒写 | “事件类型未注册:〈type〉” | emit 前先注册类型 schema;查拼写 |
| EVT-103 订阅者异常 | handler/谓词抛异常 | N·隔离(跳过该订阅) | 用户一般不可见,errored 统计可见 | 查本地日志订阅者堆栈;修复后重载 |
| EVT-104 终态写违规 | finished 重复/closed 后 append | N·拒写+告警(内部 bug 信号) | “会话已结束,不能追加事件” | 查绕过 agent.close 的写路径(INV-01);终态不可逆 |
| EVT-105 未知斜杠命令 | `/xx` 不在命令表 | N·告警回显,会话继续 | “未知命令 /xx,可用 /help 查看” | 查拼写;确认在 F041 命令表;零 LLM 属预期 |
| EVT-106 先于 created | append 先于 session.created | N·拒写 | “会话未创建,首事件必须是 session.created” | 查外壳是否先 create 再 submit;bootstrap 顺序 |
| BUS-002 保留名冲突 | 注册/卸载脊柱八模块或 spine 子系统 | N·拒绝,不改注册表 | “〈名〉为系统保留,不可注册/卸载” | 换名;确认不在 RESERVED 八名+guard/approval/credentials |
| BUS-003 非法状态迁移 | activate/detach/uninstall 前驱不合法 | N·拒绝(状态机层) | “状态迁移非法:〈from→to〉” | 对照 F006 生命周期状态机;查调用顺序 |
| PERS-201 回放坏行 | replay 遇 JSON 解析失败行 | F·隔离(记跳不中断) | “检测到损坏事件行 #N,已隔离(未删除)” | repair 看隔离区决定删留;尾部半行截断 |
| PERS-202 落盘失败 | 磁盘满/IO/权限;异步重试 3 次仍败 | F·强同步抛错;异步暂停会话 | “落盘通道故障,会话暂停;请运行 repair 恢复” | 查磁盘/权限;repair 后验证重试队列不丢事件 |
| PERS-221 spill 写失败 | 私有 spill 区写失败 | F·错误+tool.error 回喂 | “输出归档写入失败,请重试” | 查 spill 目录权限(600)与空间(F039) |
| PERS-222 spill 越权读 | ref `../` 逃逸出私有区 | N·拒绝零读取,回喂 | “spill 引用越界,已拒绝读取” | 查 ref 来源;只允许本会话私有区 |
| PERS-223 spill 超限 | put >10MB | N·拒写,回喂 | “输出超过 10MB,已拒绝写入” | 上游截断/分块;查输出源体积 |
| LLM-301 超时 | 连接 10s/首 token 60s/总 180s | R·退避≤4→降级 | “模型响应超时,正在重试(第 N 次)” | 查网络/上游;持续超时看降级链;超时配置 F025 |
| LLM-302 认证失败 | 401/AuthenticationError | N·直接降级,带 degraded_from | “认证失败(401),已切换备用模型;请检查 API key” | 查 CRED-701 配置与 key;备用模型独立 key |
| LLM-303 可重试上游错 | 429/5xx/断网 | R·退避≤4(落 llm.retry),耗尽降级 | “上游限流/暂不可用(429),第 N 次重试中” | 看 llm.retry 序列;错峰/升配额 |
| LLM-304 业务性失败 | 其余 4xx;模型未注册(F030) | N·不重试不降级,错误终态 | “模型调用被拒(4xx),本轮已结束” | 核对 model 在适配器注册表;查请求体 |
| LLM-310 链尾全败 | 降级链全败 | N·终态 reason=error | “所有模型均失败,任务已终止” | 看 err_hist/llm.error;降级>5 次查配额 |
| GRD-401 调用被拒 | scope 不可见/guard reject | N·终局,guard.rejected 强同步,零副作用 | “调用被安全策略拒绝(〈guard〉/〈policy_ref〉),未执行” | 查 rejected 事件;按 policy_ref 调 scope/换招;无 tool.result 属预期 |
| GRD-402 重放已拒调用 | 已拒 call_id 再执行 | N·拒绝+system.error | “该调用已裁决,禁止重复执行” | 查 call_id 重复来源;防重放路径 |
| GRD-403 批准后被新拒 | granted 后重入链被新拒 | N·以新决策为准 | “审批后安全检查未通过,未执行” | 看两条 guard.evaluated;核对窗口内策略变更 |
| APR-501 无审批通道 | headless/channel=None | N·直接拒(安全默认) | “无人工审批通道,请求已拒绝” | headless 勿用 high 工具或接通道 |
| APR-502 等待被取消 | 审批期取消/detach 置 denied | N·denied 无悬挂 | “审批等待已取消,按拒绝处理” | 查取消/detach 路径;重试需重发请求 |
| APR-503 裁决重放/未知 id | approval_id 已消费或不存在 | N·system.error 不重执行 | “收到未知/已处理的裁决,已忽略” | 查裁决来源;approval_id=请求 seq 防伪 |
| CFG-601 配置非法/越权 | 校验失败/越权(deny 空/通配过宽)/装配序违反 | N·拒绝启动,列字段 | “配置非法或越权:〈字段〉,无法启动会话” | 修四层配置;HARDENED 无回退 |
| CFG-602 模板缺变量 | system-prompt 装配缺变量 | F·错误,会话继续 | “提示词装配缺少变量:〈part〉” | 查 scope/config 派生变量表 |
| CFG-603 护栏段构建失败 | 护栏段 kind 未知/构建失败 | F·拒本次 LLM 调用 | “护栏段构建失败,本次请求未发送” | 查护栏 kind;缺护栏不发请求属预期 |
| CRED-701 凭据缺失 | 环境与文件均无 key | N·拒绝,绝不空串 | “凭据〈name〉未配置,请检查环境变量/credentials.yaml” | 配置后重启(不热重载);查拼写 |
| CRED-702 权限过宽 | credentials.yaml >600 | N·启动拒载 | “凭据文件权限过宽,已拒载(需 600)” | chmod 600 后重启 |
| CRED-703 疑似密钥外泄 | 脱敏自检发现疑似 key | N·告警+打码放行 | “疑似密钥出现在出口载荷,已脱敏” | INV-09 grep 32+ 位;查外发路径;轮换 key |
| TLB-801 重复/非法注册 | 重名/NAME_RE 不匹配/二次注册 | N·拒绝,提示注销重注册 | “工具名〈name〉已注册或非法,注册被拒” | 注销旧定义重注册;保留名走 BUS-002 |
| TLB-802 工具未注册 | 查不到工具(幻觉名/已卸载/文件不存在) | N·tool.error 回喂自查 | “工具〈name〉不存在或不可用” | 查注册表与拼写;读文件场景先 list_dir |
| TLB-803 参数校验失败 | raw_args 强校验失败;schema 不可编译 | N·回喂明细零执行;连败 2 次终止轮 | “参数校验失败:〈明细〉,未执行任何操作” | 按明细修正;INV-06 核对执行 args=日志 args |
| TLB-805 执行超时/异常 | Provider 60s 超时/运行异常 | N·tool.error 回喂,LLM 判重试 | “工具执行超时(60s)/异常:〈tool〉” | 查 Provider 实现;确认 subprocess 已杀树 |
| TLB-806 二进制拒读 | read_file 命中二进制 | N·错误提示 | “文件为二进制,无法按文本读取,请换工具” | 确认文件类型;换用合适工具 |
| CYC-999 未预期异常 | 非 PyHError 未预期异常 | N·兜底终态,堆栈仅本地 | “发生未预期错误,会话已终止(详情见日志)” | 取本地堆栈;按 bug 提单;禁现场造码 |

---

# 4 高频码排查流程(伪代码)

排障统一起点:先查事件流(JSONL 可 grep `code=<码>`),再查本地日志堆栈,最后按下面流程逐层。域前缀即 grep 定位线索(F019 设计理由)。

**① TLB-802 工具不执行(最常见:LLM 幻觉工具名 / 路径不存在)**

```text
工具报 TLB-802 → 1) 读 tool.error 事件:name 字段是什么
  ├─ name 不在注册表 → 核对 Definition 是否 announce/install;拼写是否幻觉 → 提示 LLM 用 tools.schemas() 里的名
  ├─ name 在注册表但已卸载 → 查 plugin.uninstalled/registry.updated 时间线;重装插件
  └─ name=read_file 类且 hint=路径 → 文件/目录不存在:先 list_dir 或核对绝对路径再重试
2) 修复后同一轮内重发即可;连败 2 次该轮自动终止(F026)属预期
```

**② TLB-803 参数校验失败(零执行,防错删关键路径)**

```text
报 TLB-803 → 1) 读 tool.error 事件 detail 明细(summarize_validation)
  ├─ 类型错(如 path=123)→ LLM 重发正确类型;禁止实现层 str() 隐式转换(INV-06 守护)
  ├─ 多余字段(strict 拒)→ 删掉非 schema 字段
  └─ 必填缺失/非法 JSON → 对照 Definition.schema 补全
2) 断言:真实函数零调用、日志 args==执行 args;连续 2 次失败终止该轮
```

**③ EVT-105 未知斜杠命令(会话仍活,预期内)**

```text
system.error(code=EVT-105) → 1) 确认输入以 / 开头且命令不在表内(new/undo/plan/budget/help/quit)
2) 检查命令拼写 → 回显提示后会话继续;零 LLM 消耗属预期,无需修复
3) 若 /xxx 本应命中却报 105 → 查 F041 命令路由注册
```

**④ GRD-401 guard 拒绝(安全拦截,不是故障)**

```text
guard.rejected(code=GRD-401) → 1) 读事件 guard_id + policy_ref:
  ├─ scope-hidden     → 工具不在当前 scope:deny_tools/allowlist 配置问题(CFG-601 方向查)
  ├─ POL-FS-1/2/3     → 路径越界:确认 workspace 几何与 resolve 单点(F055)
  ├─ POL-DGR-1        → critical 直接拒且不可审批:换实现/降 danger(需 Definition 重注册)
  ├─ POL-CRED-1       → 读路径命中凭据文件:调整目标或授权
  ├─ POL-NET-1        → 域不在 allowed_domains:scope 增域(仅收紧方向)
  └─ POL-OVW-1        → 覆写转审批:走 approval
2) 验证:无 tool.result(零副作用)、guard.rejected 强同步在日志;同 call_id 重发 → GRD-402
```

**⑤ LLM-301/302 模型超时与认证(降级链入口)**

```text
llm.error(code=LLM-30x) → 1) 区分:301=超时(三档)、302=401 认证、303=429/5xx/断网、304=业务 4xx
  ├─ 301/303 → 查 llm.retry 事件序列(attempt/delay);4 次耗尽 → 降级 qwen-max(degraded_from 字段)
  ├─ 302     → 先查 CRED-701:key 是否配置/轮换;备用模型用独立 key,主 key 泄露不暴露备用通道
  └─ 304     → 查请求体与适配器注册表(F030),不重试
2) 全链败 → LLM-310 终态:看 err_hist;单会话降级 >5 次触发告警
```

**⑥ PERS-202 落盘失败(会话暂停,数据安全)**

```text
PERS-202 → 1) 查磁盘空间/写权限/IO(强同步路径当场抛;异步路径 3 次重试后暂停)
2) 修复存储 → 运行 repair(F060)→ RECOVERING → NORMAL;验证 _retry_q 事件零丢失(拒新不丢旧)
3) 若伴随 PERS-201:repair 隔离区决定坏行删留(中部坏行不自动删)
```

**⑦ CYC-999 未预期异常(内部 bug 兜底)**

```text
CYC-999 → 1) 本地 debug 日志取完整堆栈(堆栈绝不上行/外泄)
2) 按事件回放(ADR-001:bug 拿日志从头重演)归因模块
3) 修复后补对应错误码或消除异常路径;禁止用 CYC-999 当常态错误用
```

# 5 错误传播链(底层异常 → 用户可见)

## 5.1 五层转换规则

| 层 | 转换规则 | 保留 | 丢弃/不进下一层 |
|---|---|---|---|
| ① 底层异常 | 捕获 SDK/OS/库异常,在**唯一出口**映射为所属域码;禁止异常文本外泄 | 根因类别(超时/认证/限流/IO) | 厂商原文文案 |
| ② PyHError 包装 | `raise_code(code, **ctx)`;ctx 只放脱敏结构化现场(module/attempt/字段明细) | code/name/advice/retryable | 堆栈(仅本地日志) |
| ③ 事件化 | 按触发模块写 system.error/tool.error/llm.error;actor=system/tool/llm;三类强同步场景失败即时抛 | code+hint/ctx 全量入 append-only 日志 | 无(事件=事实) |
| ④ 模型可见 | `to_llm_text()`:≤2000 字符、含可行动 advice;tool.error/llm.error 回喂给模型修正 | code+name+advice | 堆栈、ctx 内部细节 |
| ⑤ 用户/远端 | API/UI 响应只含 code+advice;系统错误经脱敏(redact_out 出口横切) | code+advice | ctx 明细、内部路径 |

## 5.2 三条主链

**工具错误链**:tool_calls 解析 → Definition 校验(TLB-803,零执行)→ guard(GRD-401 终局,强同步 guard.rejected)→ Provider(异常/超时 TLB-805)→ 全部收敛为 tool.error 事件 → agent-loop 把错误文本回喂 LLM → LLM 修正重发(连败 2 次该轮终止)。**关键不变式:每一跳 code 原样透传,audit 可凭 code+call_id 复原全过程。**

**模型失败链**:openai SDK 异常 → F012 归一(LLM-301/302/303/304)→ F028 退避(llm.retry 事件)→ F013 降级(degraded_from 入 llm.request)→ 链尾 LLM-310 → agent-loop 收 reason=error 终态(llm.error 事件)。**降级/终止决策只看码,不匹配文本(ADR-007/011)。**

**事件写链**:session.append 校验(EVT-100/101/102/104/106 拒写,日志行数不变)→ 总线分发(订阅者异常 EVT-103 隔离)→ persistence 落盘(PERS-202 强同步抛错/异步暂停)→ 错误本身也以 system.error 事件入流(可审计、可回放)。**拒写 = 状态零变更 + 结构化错误返回,调用方按 F(修复后)重试。**

## 5.3 禁止事项

禁止把 code 翻译成纯文本丢弃码(前端判断会断);禁拼裸异常 str 当 message(堆栈/文案泄入事件流);错误事件禁 user actor 冒名;CYC-999 不当可重试错误重发。

---

# 6 日志规范

## 6.1 格式与级别

统一单行结构化:时间 级别 模块 code=XXX key=value…;堆栈仅进本地 debug 日志,绝不上行。示例:`2026-09-06T10:12:33.001+08:00 ERROR session code=EVT-103 type_=agent.message owner=echo exc=ValueError:bad payload`。

| 级别 | 适用码/场景 | 说明 |
|---|---|---|
| ERROR | EVT-100/101/102/104/106 拒写、PERS-202、LLM-310、GRD-401/402、CFG-601/603、CRED-701/702、CYC-999 | 拒写/终态/暂停/拒绝类,必须可 grep 定位 |
| WARNING | EVT-103/105、PERS-201、LLM-301/302/303、CRED-703、降级>5 次告警 | 隔离/降级/重试/告警类,主流程未断 |
| INFO | llm.retry、llm.error 落事件、approval 裁决、降级事实 | 事件化留痕为主 |
| DEBUG | 堆栈、ctx 明细 | 仅本地排障 |

## 6.2 必带上下文字段

| 场景 | 必带字段 |
|---|---|
| 事件写链 | code、seq、type_、actor |
| 工具管道 | code、call_id、tool、attempt |
| LLM | code、model、attempt、degraded_from |
| guard | code、guard_id、policy_ref、call_id |
| 持久化 | code、seq/file、line_no(仅 PERS-201) |

`retryable/advice` 属注册表字段(ErrorSpec),不必每行重复;message 用中文简述根因。

## 6.3 脱敏与留痕

全出口(日志/事件/LLM 文本)经 credentials.redact_out 横切打码(owner=spine 不可卸、只可加严);密钥显示 sk-abc***;INV-09 断言日志 grep 无 32+ 位疑似密钥;credentials.yaml 权限 600 且不进版本库。**双落纪律**:每次 raise_code ≥ 一行日志 + 一条错误事件(事件=审计事实、日志=排障);强同步三类错误须双落后再抛/暂停。

# 7 排查速查(症状反查)

| 症状 | 按概率序查码 | 首查动作 |
|---|---|---|
| 会话打不开/历史不完整 | PERS-201 → EVT-106 → CFG-601 | repair 看隔离区;查首事件是否 session.created |
| 事件写不进、JSONL 不涨 | EVT-100/101/102/104/106 | 看拒写返回的字段明细与校验链断点(§5.2 事件写链) |
| submit 无反应 | BUSY → EVT-100 | 查会话状态(closed/running)与队深 |
| 重启后对话错乱 | EVT-101 → PERS-201 | repair;查是否有多写路径绕过 session.append(INV-01) |
| 工具不执行 | TLB-802 → TLB-803 → GRD-401 → APR-501 | tool.error/guard.rejected 事件定位卡在哪一关(四关:契约/校验/guard/审批) |
| LLM 慢/反复失败 | LLM-301/303 → LLM-302(→CRED-701)→ LLM-310 | 看 llm.retry 与 err_hist |
| 持续 401 | CRED-701/702 → LLM-302 | 配置 key(600)、轮换、备用独立 key |
| 审批不弹/长等 | APR-501 → APR-502 → APR-503 | 通道是否 headless;TTL 超时=denied 属安全默认 |
| 插件装不上/激活失败 | BUS-002 → BUS-003 → TLB-801 → EVT-102 | 保留名/状态机前驱/类型未注册 |
| 文件工具报路径错 | POL-FS-1/2/3 → TLB-802 → TLB-806 | resolve 几何(F055);先 list_dir 自查 |
| 输出被截/超限 | PERS-223 → PERS-221/222 | 查 spill 目录权限与 ref 合法性 |
| 日志疑似泄密钥 | CRED-703 | 吊销+轮换+按审计日志追外发点 |
| 未预期崩溃 | CYC-999 | 取本地堆栈按事件回放归因,提单修复 |

# 8 关联测试(错误码正确性,GWT ≥5)

码即断言对象(ADR-011):所有失败断言精确到码,不匹配文本。

- **GWT-ERR-01 注册表完整**:Given §2 全量码表;When 遍历 ERRORS;Then 每码具 ErrorSpec(code/name/advice/retryable) 且 raise_code 未登记码抛 UnknownCode(test_f019_error_codes.py)。
- **GWT-ERR-02 EVT-105 回显不杀会话**:When submit `/xx`;Then system.error(code=EVT-105,hint 含 /xx)、无 llm.* 事件、会话随后可正常对话(F041 对齐)。
- **GWT-ERR-03 TLB-803 畸形参数零执行**:Given delete_file 要求 path:str;When 30 例畸形 raw_args;Then 每条 tool.error(code=TLB-803) 含明细、真实函数零调用、args==日志 args、连败 2 次终止轮(INV-06/T-SEC-08 对齐)。
- **GWT-ERR-04 GRD-401 终局强同步**:Given fs.wipe 在 deny_tools;When 发起调用;Then guard.rejected(code=GRD-401,policy_ref,sync=True)、无 tool.result、Provider 零调用、同 call_id 重发→GRD-402(GWT-S6-03/T-SEC-06 对齐)。
- **GWT-ERR-05 LLM 归一+降级**:Given mock SDK 抛 AuthenticationError/TimeoutError/RateLimitError;When chat_with_fallback;Then 映射 LLM-302/301/303 无裸异常外泄、deepseek 持续 401 落 qwen-max 且 degraded_from 标注、双败→LLM-310(GWT-L4-01/02 对齐)。
- **GWT-ERR-06 传播链码不变式**:Given 任一路径 PyHError(code=X);When 事件化+日志化+to_llm_text+远端响应;Then 事件 payload.code==X、日志 code==X、LLM 文本含 [X]+advice、远端仅 code+advice、堆栈只现本地。
- **GWT-ERR-07 拒写不落盘**:When append 未知类型/坏信封/乱 seq/closed 后写;Then 分别 EVT-102/100/101/104 且日志行数不变(GWT-S3-01 对齐)。
- **GWT-ERR-08 PERS 双场景**:Given 中部注入坏行;When replay;Then PERS-201 记跳、其余完整 yield 不中断;Given mock 磁盘满×3;When append;Then PERS-202 暂停,修复恢复后重试队列零丢失(GWT-P8-02/04 对齐)。

# 9 附录

## 9.1 码组 → 状态 → 语义权威处(快速溯源)

| 码组 | 状态 | 语义权威处 |
|---|---|---|
| EVT-100~106 | 定稿 | PRD §3.7 处置表 + EVENT-SCHEMA §6 |
| BUS-002/003 | 定稿 | PRD F001/F003/F006 + DIS-SEAM §2.7 |
| PERS-201/202 | 定稿 | DIS-CORE §8 + EVENT-SCHEMA §7 |
| PERS-221/222/223 | 建议 | DIS-SEAM §6.1(F039) |
| LLM-301~304/310 | 定稿 | PRD F012/F013/F028/F030 + DIS-CORE §4 |
| GRD-401 | 定稿 | PRD F014 + SECURITY §7 + DIS-SEAM §5.3 |
| GRD-402/403 | 细化 | DIS-SEAM §5.4 |
| APR-501/502/503 | 建议 | DIS-SEAM §6.2(F015) |
| CFG-601 | 定稿 | PRD F021 + DIS-CORE §6 |
| CFG-602/603 | 细化 | DIS-CORE §5(提示词装配) |
| CRED-701 | 定稿 | PRD F016 |
| CRED-702/703 | 建议 | DIS-SEAM §6.3 |
| TLB-801/802/803/806 | 定稿 | PRD F008/F022/F023/F026 + DIS-CORE §7 |
| TLB-805 | 细化 | DIS-CORE §7 + SECURITY(执行 60s) |
| CYC-999 | 定稿 | DIS-CORE §1(agent-loop 兜底) |
| POL-* 9 个 | 策略标识 | SECURITY §7.5 + DIS-SEAM §5.2 |
| QUE-001/JOB-001/PTY-001/ATT-001/EDT-001/TO-301 | 功能码 | PRD 阶段5 F043/F061/F063 + EVENT-SCHEMA §7 |
| BUSY | 字面量 | DIS-CORE §1/§2(agent/agent-loop) |

## 9.2 跨文档一致性注记(已知差异,登记不改码)

1. **TLB-404 示例冲突**:EVENT-SCHEMA §4.7 tool.error 示例 `code=TLB-404`(路径不存在)与 PRD(read_file 不存在→TLB-802)及 TLB 已用码(801-803/805/806)冲突。裁决:示例应为 TLB-802;TLB-404 未登记,实现禁引用,待修订。
2. **BUSY 编号**:以非 NNN 字面量在 PRD F007(队列队深>10)与 DIS-CORE(重复 create/关闭后 submit)使用;如需数字收编须先修订 F019 注册表,当前禁止另造别名。
3. **CFG-602/603 归属**:DIS-CORE §5 明确"CFG-6xx 域,DIS 细化落码",域归属与 PRD F019 一致,无编号冲突;仅提示词装配属 CFG 而非 LLM 域,引用时勿误用 LLM-3xx。
4. **PERS-221~223**:DIS-SEAM §6.1「建议 PERS-2xx」落位,占 221-223 不与已定稿 201/202 冲突;转正前状态=建议,引用须同步本表。

## 9.3 维护责任

- 本文档由文档流水线产出,与 PRD-Core(F019/F020 权威)、EVENT-SCHEMA(§6/§7)、DIS 系列异常表同源;任一变更须四件同步并过评审。
- 新增失败路径先查表:能归入现有码不新开码;确需新码走 §1.5 五步;跨文档冲突一律先记 §9.2,不在本目录擅自改码或改编号。
