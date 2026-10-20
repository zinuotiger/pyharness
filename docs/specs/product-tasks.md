# S04 — 正式产品任务与输入收口

本文件保留S04范围及其历史验收说明。后续S06已接入独立的任务收件箱与持久RunAttempt提交，当前契约见[task-inbox](task-inbox.md)；本页定义保存和preflight仍不执行或预留尝试。

本增量完成总计划S04的控制与输入前置，不交付S06收件箱/RunAttempt提交或S07执行。正式ProductTask是一份真实持久的版本化产品定义，有目标、验收项、输入绑定及执行策略；`mode=product_task`。既有`TaskContextReference`仍是schema1、`context_reference_only`，不会自动升级或获得执行权。两者共享`pt-16hex`命名空间，但同ID禁止转换类型；原接口、严格模型和数据均保留。

## 领域与唯一事实

`ProjectRevision`、`SourceGrant`、`SourceSnapshot`/`WorkspaceSnapshot`、`ContextManifest` schema1/2、`WorkspaceInput` schema1/2沿既有不可变协议。新增`ProductTask` schema2，包含product_task_id、revision、project_id/project_revision、服务端created_by、title、objective、acceptance_criteria、input_binding、retired、execution_policy=governed-attempt-only。领域定义无执行历史；不会拿任务名、清单引用或已准备副本冒充一次执行。

正式定义继续使用`workspace.resource_committed`的`task_definition`资源及严格pt ID，事件封套版本仍为1；blob按schema_version分派。无新增事件名/第二日志/第二数据库。定义先不可变blob、确认读回，再强同步SessionLog、准确磁盘核对。命令键按现有租户/主体域唯一；CAS修订及全项目累计最多200次正式任务修订。运行时Python模块仍≤600行。旧task_definition schema1永远按旧类型重放。

TaskInputBinding单向固定ManifestReference及InputReference，均含ID/revision/digest/source_seq，输入revision只能为1。任务→已提交私有输入→确切清单→快照/授权；不修改历史清单制造反向引用。清单可保留旧上下文引用，该引用不构成执行准入。任务的所有者、项目/输入/清单主体必须一致，项目修订也必须一致。创建后可用新修订明确更换输入，原修订与原字节仍保留；不会自动追随最新版本。

## HTTP接口

沿workspace路由的认证、统一异常映射和服务关闭边界：

| 方法/路径（前缀/api/v1/projects/{project_id}） | 行为 |
|---|---|
| GET /product-tasks | 当前主体可访问的正式定义，精确total，不混入旧上下文引用 |
| POST /product-tasks | 严格ProductTaskCommand和必填Idempotency-Key；创建或CAS修订 |
| GET /product-tasks/{task_id}/revisions/{revision} | 确切原修订；历史版本不会误换成新任务或新输入 |
| POST /product-tasks/{task_id}/preflight | 只读TaskPreflight={schema_version:1,task_ref:{product_task_id,revision,digest,source_seq}}；无幂等命令/控制事件/副本/attempt |

命令仅schema_version=2、product_task_id、expected_revision(0–199)、project_revision、title(非空白≤100)、objective(非空白≤8000)、acceptance_criteria(1–30条非空白，每条≤1000)、input_binding(可空草稿)、retired。拒额外actor/tenant/路径/模式/执行权和布尔版本号。expected_revision不存入领域blob。owner/member可管理自己的定义，viewer不可访问私有任务输入；团队owner也不能读取其他主体的任务。每次服务准入、等待owner锁后、阻塞私有I/O后与提交前/收据返回前重新核对身份、权限和UTC。member只新增两个精确形状的POST准入，其他管理权限不扩张。

返回status为draft（未绑定）、definition_ready（当前定义与输入齐备）、stale（任务/项目/清单/来源已更新）、retired（不可恢复）。所有返回均`executable=false, attempt_reserved=false`；definition_ready不等于执行已授权。读取失效历史保留原输入引用；撤销、到期、归档或私有根身份不可用时拒读，列表不泄露被撤销输入的信息。损坏日志/blob/内容拒绝并保留字节，不repair。命令提交后确认失败返回未知；原key/body可在重开后核对重试，幂等重试仍重新鉴权。

## RunAttempt前置契约

`RunAttemptPrerequisites`是供后续提交消费的内部不可变契约：服务端tenant_id/subject_ref、project_id/revision/digest/source_seq、确切task_ref、input_binding、execution_policy。preflight重放磁盘事实，重新核对角色/项目/源授权/root身份/清单/任务版本，检查私有已提交字节与摘要；禁止读取宿主源正文或创建执行副本。返回安全契约（不暴露原始主体/租户）、binding_digest、checked_at、subject_bound=true，以及`execution_authorized=false, attempt_reserved=false, persisted=false`。

binding_digest仅证明这次观察绑定一致，绝不是token，不可据此免校验提交。当前没有可接受客户端预检摘要直接执行的端点。S06必须在实际attempt预留的同一所有者锁和权限边界内重新校验这些前置，保存精确绑定与持久幂等键；S07才消费真实治理authorize/效果收据。崩溃/失联恢复和未知效果语义留S08。

## Web与验收矩阵

项目空间→“正式产品任务”：查看/新建/编辑/退役，选择当前主体的确切私有输入，输入分页不隐式切换绑定；旧绑定即使不在当前页也显式保留。保存后GET精确修订并核对原正文及摘要才显示持久确认。未知命令在内存按账户主体/项目保留原key/body，关闭/重新打开可显式只读核对或原样重试；不存浏览器持久存储，不自动重发。完整浏览器/进程关闭后用户应从任务列表检查服务端已提交定义；S06的持久提交查询尚未交付。明确首次4xx拒绝可重新编辑，未知后的4xx重试不会丢弃原命令。

页面文字安全转义，异步结果受dialog生命周期/版本控制，关闭、刷新、编辑或输入变化后旧检查失效。没有执行按钮，检查前置不写日志/运行尝试。API/页面不暴露私有宿主根或服务端主体。

| AC | 交付与验收 | 证据入口 | 范围结论 |
|---|---|---|---|
| S04-01 | 正式定义、严格输入、CAS/幂等、不可变所有者、退役与精确历史；旧引用拒绝提升 | test_product_tasks.py | 本步实现 |
| S04-02 | 非Git与dirty/linked Git的确切输入绑定，保留HEAD/index/working/未跟踪字节 | test_product_tasks.py、test_product_task_boundaries.py及原workspace套件 | 输入部分实现；执行/写回仍后续 |
| S04-03 | 撤权/降级/等待锁后撤销、UTC、失效、跨主体/租户/版本拒绝 | test_product_task_boundaries.py及原权限套件 | 当前准入重新检查 |
| S04-04 | 运行前置契约，零attempt/引擎/队列/执行 | exact input/preflight、Git/nonGit链路测试 | 前置实现；S06提交未实现 |
| S04-05 | 单一SessionLog、重开、损坏保真、未知提交原键核对 | unknown commit/reopen及原store/进程锁测试 | 本步实现 |
| S04-06 | Web实际操作、未知核对、身份切换、过时响应、文本转义 | product-tasks.test.cjs、真实HTTP浏览器驱动 | 按本轮冻结证据验收 |
| S04-07 | 新源码冻结全量回归、包/独立安装与基线文件保全 | 任务外pyharness-s04-20261018证据目录 | 以实际报告为准 |

A01项目控制前置收口；C03输入部分收口，执行隔离/三方合并/批准写回分别仍在S07/S11。P01/P02作为全领域平台底座仍会随后续类型扩展，不能把本步或26项整体标为完成。本轮真实模型、真实渠道、实际Docker执行和物理原生输入条件仍单独列限，不用协议/合成后端代替。
