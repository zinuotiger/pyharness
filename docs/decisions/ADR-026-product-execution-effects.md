# ADR-026 — 产品任务执行授权与真实效果观察

状态：S07实施中，未完成整阶段验收。日期：2026-10-19。

S06的RunAttempt只固定任务、输入及资料版本，不执行。S07控制日志保存不可变执行授权和取消意图；运行日志保存实际生命周期及工具效果，查询从原事实派生，不复制运行结果到第二个持久库。精确引用、当前主体与来源授权以及已发布Agent快照在正式入口绑定；预览不授予执行权。

新增`execution.lifecycle`和`execution.effect`两种严格schema1强同步事件；保留原83事件及16强同步类型，现为85/18。既有`receipt.emitted`仍是治理决策凭证，不改变其含义。现行词表计数测试按此明确加法更新，未删除旧事件或弱化旧断言。

`execution.lifecycle`含执行ID、控制授权摘要/seq、主体、阶段、有限原因码。bound须在发布私有会话句柄前持久提交。其余阶段不能冒充业务交付已验收。

`execution.effect`绑定同一会话中真实tool.call的seq/call_id/tool、复用现有审批实参sha1绑定和真实decision.issued的seq。ToolExecutor仅在既有治理与审批门通过后，把身份放到Task局部ContextVar，创建Provider Task后立即还原。核心模块不引用application，旧非产品调用的参数、事件及返回保持。

执行前先追加started并严格重读同一物理日志，核对截断、坏行、缓存与磁盘一致性以及精确事件。没有确认时零Provider调用。SessionLog/EventBus的缓存事件或空flush不是凭据。同一日志的锁阻止两个收据对象并发重放相同调用。拒绝/未知的调用不自动重试。

Provider返回后记录returned、渲染结果sha256及可选ProviderOutcome.ok；这些是Provider返回观察，不能据此声称目标文件已写回或业务验收成功。异常/取消等待记录unknown；同步线程、远端工具可能仍有部分效果。收据不可落盘时保留started，查询必须当未知处理。事件不存结果原文、参数正文或凭据。

运行会话的私有读取、输入物化、启动/取消API与界面属于同一S07验收范围，必须接线并验收后才将S07标为完成。恢复/纠偏归S08，独立验收归S10，宿主合并写回归S11。

## 取消控制增量

不可变`execution_cancellation`仍使用原`workspace.resource_committed`和同一WorkspaceStore owner/OS lease/blob/强同步提交；不新增事件名或结果数据库。严格schema1命令包含`ec-16hex`、执行的ID/revision/digest/source_seq和原Idempotency-Key，主体、租户与提交键绑定由服务端生成。一执行只接受一个取消意图；意图、当前在飞状态、实际停止观察分别表达，不把请求登记当成已停或撤销已发生的效果。

停止只降低权限。当前owner/member及原执行creator仍必查；来源撤销、正文丢失、项目归档不能强迫停止入口重新获得资料读权。最小控制投影只从同一严格JSONL派生Project、RunAttempt引用、ExecutionAuthorization和Cancellation元数据，验证序列/租户/无截断或坏行、所有资源ID种类与revision及命令键连续性、所用blob SHA/schema/主体/历史项目/attempt/授权摘要和精确引用。它不读取SourceSnapshot/WorkspaceSnapshot原字节、不能用于启动或资料读取。普通完整投影和旧调用签名保持。

向真实队列发停止信号前再核对当前主体、服务端固定授权receipt、session/task、tenant、当前PlatformRuntime及spine归属。重复原键只观察，重启查询只用严格只读回放，不构建writer、runner或pump，不清除已失败writer标志。损坏日志保持原样并拒绝。队列等待任务被真实取消时追加强同步产品cancelled事实；在飞任务由原运行owner写终态。模型返回后、finish前再次检查取消与权限，避免取消已登记后仍收集并标记成功。

回执丢失或持久化错误不能困住事先核对过的在飞任务：仅向同一精确且仍由当前主体控制的真实runtime尽力发停止信号，原PERS-202错误仍报告，writer仍须重开；绝不声称未确认的意图已提交。后续原键查询只能由真实磁盘事实确认。取消后的unknown工具效果仍保留，不因此重放或宣称已撤回。

## 私有委派事实增量

既有subagent.spawn必须继续走原治理、真实子runner及缩减scope/预算，但产品子任务不能退回普通共享会话而丢失父来源/主体/模型/截止时间。新增严格schema1强同步`execution.child`，在父原日志先写authorized（真实tool.call/decision/effect-start、实际spawn、调用者日志身份与输入锚、Root授权/材料版本、意图/spec和scope摘要、父子预算及深度），子日志seq2再写bound锁定父authorized的精确seq/摘要。事实不存原始任务/资料正文或秘密，不另建委派数据库。

此为有依据的加法：保留前85类型/18强同步类型，当前86/19；原不变量仍测试全部旧类型，不改治理receipt含义。嵌套委派区分实际调用日志与Core manager的spawn日志，不跨sid比较seq，不借未来decision。同一父调用不能授权两次；父admission顺序/深度≤3、真实输入/委派、缩减预算、子阶段/effect均校验。子任务没有TaskQueue，不伪造enqueued/started。

产品专用适配器保留原runner/审批/问答/notify等能力；子任务在原owner生命周期内完成准备、agent-loop、finish和release，再记录运行终态，并继承实际actor与Root绝对截止时间。每次模型/工具和完成前核Root当前授权/取消与父任务活性。读取子日志先取有界绑定元数据，通过Root creator/tenant/当前资料权限后才读正文及验证调用链；重开不创建writer/repair/engine，旧缓存/列表/SSE不绕权。普通非产品委派保持原路径。

## 具体文件观察与产物引用增量

保持86/19词表，不再新增事件名。execution.effect的schema1增加严格可选file_observation、observation_scope、observation_reason及file_changes；旧事实缺省为None仍可回放。started/unknown不得携带“已观察文件”；returned的observed须有唯一排序的相对路径变化及真实before/after SHA，created/modified/deleted形状严格约束；unavailable不能使用空列表冒充没有变化。读工具不得携写观察，单目标写/patch须与原tool.call的实际路径一致。

产品Runtime通过SandboxManager.observe读取实际后端字节，在持久started之后、原Provider前后观察：文件write/patch为单目标，命令/进程控制为有界用户文件工作区（不含派生资料区）。observe与旧snapshot共用受控读取，但远程观察只下载、不上传；旧可变snapshot接口保持。观察不等于事务、独占因果或宿主写回证明，后台进程可能在两个观察点之间继续活动；proc.start仅能声明尚无法核实其后续文件效果。观察前后重核当前权限/来源/deadline。获取快照失败、撤权或后台未结束明确unavailable；实际Provider异常/等待取消仍unknown、不自动重试，不以预期写入参数替代实际after哈希。

原platform.artifact generated事实和原始内容仍是产物来源，不建立第二结果库。查询验证实际session/task/run、system身份、生成时处于running、唯一f-ID与私有storage标识、record摘要、真实字节大小/sha256及manifest摘要；字节缺失或篡改分别unavailable/mismatch。校验读取至多32MiB，容纳原有文本diff/manifest可能大于单个10MiB原始文件的情形；未扩大输入、输出文件、Docker/remote传输协议配额。收据不含storage_path/正文，不产生下载授权、写回授权或业务验收；独立验收仍not_evaluated。子产物先经精确父准入/子日志链验证，再保留各自session/task/source_seq归属聚合，不能借Root身份。

完整执行/会话查询、预览与材料读取也须严格只读重放同一控制JSONL并验证全部原定义/来源，不取得writer/OS lease或构建engine/pump。与最小停止投影共用严格控制事件读取，但完整资料权限与定义校验仍独立保留；既有写命令仍通过唯一owner/lease提交，失败writer标记不被只读查询清除。
