# ADR-023：产品控制事实复用 SessionLog，领域分包与双壳目标

状态：接受（依据 t_9675ca9e 及父设计包07的全量实施授权）。本批只实现 project 元数据子协议，下面未实现目标不作为交付事实。

## 背景
项目/产品任务/交接跨执行会话，不能借裸sid当全局身份，不能用平台配置JSON再造任务事实源。ADR-001/006仍适用；当前平台已有大量用户未提交改动，采用最小接线而非门面重写。

## 决策
每租户 workspace-v1/control/s-workspace-control-v1.jsonl 是产品控制日志，复用既有 SessionLog、SessionStore、EventBus recorder；独立 OS owner lease 防止同进程两门面和跨进程双写。控制日志不出现在普通聊天列表，不注入模型。首事件仍 session.created。不可变定义位于同租户 blobs/<sha256>.json，事件仅保身份/版本/摘要/命令与服务端主体。先原子写blob，再强同步append提交，再从磁盘读回确切事件，最后返回派生投影。未被提交引用的blob不算创建。持久幂等绑定主体+命令键+规范化请求摘要，CAS防旧版本覆盖。append失败后owner失效，关闭重开后才可确认；不拿内存缓存当成功凭证。

当前严格资源类型为project/source_grant/source_snapshot；新增类型必须逐个定义有界DTO，不用任意dict。上层Projects做账户/租户权限验证；HTTP与JS只适配服务。现有Agent/Run/Workflow/审批与队列不改。

双壳目标继承父包07：Web完整业务页，原生共享服务控制与摘要/受权复杂详情，保留原生既有能力。本批仅Web项目元数据，原生尚未完成，不能宣称parity。后续暂停/checkpoint/DAG/浏览器需另立递增ADR明确替代ADR-014/016/017或扩展受控进程边界；本ADR不提前修改冻结历史。

## 后果
一个可审计真源，定义不可变、投影可重建；代价为事件/blob一致性核对和读取成本。本切片项目数上限200、单blob32KiB。t_d02c150c增量提供显式版本化审计有界响应与主体绑定签名游标，保留旧无参接口，详情见workspace-control规格；每页仍全量重放校验，未实现通用分页索引和全部领域类型/attempt预留，故P02不可标完整完成。游标签名密钥是租户私有安全元数据而非资源事实真源，不能进入导出/证据包。

## 违反后果
以配置JSON或内存为权威会在重启后丢版本/双提交；把空flush当持久凭证会因总线隔离订阅者异常而假报成功；把另一Agent名字当审阅主体会误授独立验收身份。

## 备选
不采用新SQLite权威表、新日志格式、新任务循环。SQLite后续仅作可丢投影。未来执行准入继续复用唯一队列，不在本服务直调Provider。

## 关联
父包02/03/07、ADD ADR-001/006/014/016/017、.ai-coding/TASK-t_9675ca9e.md、docs/specs/workspace-control.md。

## t_6321c198 增量
只读SourceGrant复用同一控制日志/不可变私有blob/owner lease/CAS/命令键域；不新增事件类型，不改变project历史或旧audit cursor。严格project/source_grant子模型与投影分派；根的规范化/身份绑定、owner签发权限、只返回摘要及不可逆撤销见workspace-control规格和TASK-t_6321c198。签发不隐式扫描或授权工具执行，状态判据不等于完整文件访问链。私有源路径只在授权blob，不入事件/API/公共证据；未来快照/正文访问须重新核对身份和授权。其余P01/P02/P03目标仍未完成。

## t_e295c357 增量
选择性UTF-8文本快照复用同一owner/lease/SessionLog；不另建权威日志。有限内容拒绝策略及人工摘要确认之后才持久化原字节，blob/manifest先行、强同步提交后磁盘精确读回；只新增严格source_snapshot子类型，词表82/强同步16不变。单manifest上限仅此类型512KiB，内容单项/累计10MiB。每次SourceGrant与角色验证，持锁与提交前重查；预览不是授权token。源读取前后及提交前稳定性观察不等于OS级原子性，有限秘密模式不等于完整DLP。完整协议和局限见workspace-control末节。P03完整Git dirty快照/宿主回写及其余26需求仍未完成。

## desktop-20261017 持久ContextManifest增量
沿本ADR唯一SessionLog/owner/blob协议，严格新增context_manifest与task_definition资源；后者仅mode=context_reference_only且不可执行。清单单向固定项目/可选任务版本和快照摘要，旧版本stale不替换输入。blob写后精确读回再append、最终磁盘和账户I/O后的授权/UTC复查、精确历史GET与未知命令核对、512KiB/200历史限额见workspace-control。没有第二真源或新事件名，P05真正任务准入仍待消费。原ADR-023双号属既有P00登记缺口，此处按精确文件引用，不覆盖或重编号另一历史ADR。


## desktop-20261017 Git所选层预览前置增量

沿现有SourceGrant/owner，只读预览所选HEAD/index/working摘要和删除标记。linked外置common必须另有精确同项目metadata SourceGrant；临时bare视图不消费源config/hooks/filters或全局配置，固定Git读取且禁网络。名称先检、字节和Git元数据稳定性、最终角色/UTC与monotonic预算、限额及Web摘要核验见workspace-control。零新增SessionLog/blob/队列/Provider，事件词表82/强同步16不变；不是持久WorkspaceSnapshot或完整P03。既有上下文清单接缝的批准冻结保留为历史证据，当前Git增量另行冻结验收。原26需求保持PARTIAL。

## desktop-20261017 持久Git输入及清单schema2增量

严格workspace_snapshot与ContextManifest schema2复用本ADR唯一控制SessionLog/owner/immutable blob，不新增权威数据库、日志格式、队列或AgentLoop。SourceGrant受权原字节在首次私有写入前与最终提交决定前核对；source/common与private存储必须双向分离，blob/收据限额提前检查。清单单向绑定确切来源快照、项目及仅上下文任务版本；所有派生来源字段/items由服务端产生，旧schema保持严格隔离。提交后的撤权拒绝泄露收据，但返回unknown语义保留原命令，而不宣称未提交。日志重放、部分失败/原键恢复、分页视图/当前授权、原字节读取及前端失效规则详见workspace-control末节。公开receipt_digest/view_digest只证明内容和视图一致，不能作为授权或独立账户认证；当前限制和每片冻结/测试/独立上下文审查证据如实分列。原26项/P00–P19全部保留，P03完整执行隔离及其余领域仍待按依赖验收。


## desktop-20261017 P03 Git候选预演增量

候选页是已有owner/SourceGrant/角色检查下的有界瞬时观察，不新增资源事实或权威数据库；API/JS只消费共享ApplicationService结果。metadata页/游标不签发内容许可。原生Git在既有受控临时视图中使用普通文件空占位与经首次read前FD身份验证的有界规则metadata拷贝，关闭宿主忽略规则被替换后native越界读取的窗口；本模块新增临时写盘站点已按INV01登记，原唯一消息历史扫描断言不变。SessionLog/blob/强同步词表、WS/CM提交及未知恢复均保持现有协议。显式选择回填只是草稿，实际内容与不可变来源仍经原服务端预览/确认/首次写入和最终gate形成，不建立第二权威控制状态。完整限额、受控忽略分类范围及拒绝规则见workspace-control本片；26需求/P00–P19仍PARTIAL。

共享Git metadata reader亦将FD身份（含nlink）与regular/nlink=1检查前移至首个read之前，保留既有EVT-101/git_metadata_changed及读后复核；不改变公共schema或控制事实。两个合成RED证明此前先读字节后拒绝，最小修复后回归锁定错误码/原因。


## desktop-20261017 P03 自有输入准备增量

严格workspace_input沿唯一owner/SessionLog/immutable blob提交核验后输入定义，不另建事实源。临时副本生命周期仅一次显式使用；受限可信transfer仅isolated/empty，不是执行授权或OS沙箱。所有共享源首字节/Git blob启动前gate及私有checked-FD读回、最终收据核权、unknown原key/body恢复、配额和Windows权限限制见workspace-control本片。typed kind复用workspace.resource_committed，词表82/强同步16不变。原26需求/P00–P19保留PARTIAL，真正任务准入/执行、回写合并和真实双壳环境按原依赖继续。


## desktop-20261017 P03 非Git显式输入接缝

workspace_input定义新增显式schema2/CMv1 SourceSnapshot来源；旧schema1/Git CMv2严格语义保留。两种协议共享ApplicationService、唯一WorkspaceStore owner/SessionLog事实和不可变blob；事件时纯绑定重放与当前权限/根/UTC/版本检查分离。CM私有、既有SourceGrant团队可读；显式子集不是整树Git谱系，不伪造HEAD/index。复用有界私有物化/临时清理/未知原键/一次transfer；准备与执行准入分开。Web只消费共享Service API并严格摘要读回。原26及后续P04/P05/P09依赖保持，完整产品仍PARTIAL。


## 2026-10-18 S04 正式产品任务与输入收口

沿原唯一SessionLog/owner/blob协议扩展task_definition的blob schema2为正式ProductTask。旧schema1/context_reference_only与原接口不变，同pt ID禁止跨类型转换；不将引用目标当执行任务。单向绑定确切CM与私有输入，角色/主体/租户/版本/根身份/授权期限及私有字节重新检查。内部RunAttemptPrerequisites固定服务端租户/主体及项目/任务/输入版本；只读预检摘要不构成授权，不预留attempt。真正提交在S06接入现有治理入口重新核验，不因definition_ready放行执行。完整契约、收口矩阵和限制见product-tasks.md；C03执行与合并、其余26项及P01/P02后续领域扩展保持原依赖。
