# 产品依赖任务图协议（S09）

实施与证据见 `.ai-coding/TASK-s09-20261019.md`；新增接口不代表阶段已验收。兼容边界见 [ADR-028](../decisions/ADR-028-versioned-task-graphs.md)。既有顺序 WorkflowRunner 继续按原契约运行。

## 定义与身份

控制唯一事实仍为 WorkspaceStore 的强同步 `workspace.resource_committed`。`task_graph/tg-16hex` 支持 1–200 修订，节点 1–20 个，稳定 Kahn 拒环。节点 ID 为可读 ASCII 标识，依赖唯一且必须存在；固定同主体、同租户、同项目的 ProductTask、文档、引用、交付及 published Agent 版本。图不接受客户端授予身份、工具、路径或条件代码。

`task_graph_run/gr-16hex`、`task_graph_dispatch/gd-16hex`、`task_graph_cancellation/gc-16hex` 均为不可变首修订。图授权固定全部节点原 attempt/execution ID、配置指纹及预算；这些身份不能被普通提交、普通开始或 S08 续做消费。调度意图在同一控制日志强确认之后，才创建 schema3 GraphRunAttempt 与 GraphNodeExecutionAuthorization；GraphNodeBasis 固定 graph_run_ref、node_id、intent_seq 和 intent_digest。实际模型、工具、审批、Scope、队列、沙箱及收据仍走原执行链。

并发 1–4，同时活跃图最多 4 个；当前服务串行核对图准入。图额度为整数 input_tokens、output_tokens、tool_calls、cost_microyuan；所有节点已发布 Agent 预算之和不得超过图总额，每节点也保留原更窄预算。价格未知不按零计，费用是运行时估算，不是供应商账单承诺。图额度分配不因失败、取消或重启而自动返还给新节点。

## 接口

下表路径均以 `/api/v1/projects/{project_id}` 为前缀；POST 命令除预检外带独立 Idempotency-Key。响应及可处理错误均 no-store。每次重新核对当前角色、租户与 creator。

| 方法与路径 | 行为 | 执行边界 |
|---|---|---|
| POST /task-graphs | CAS 保存图版本 | 零模型、零队列 |
| GET /task-graphs | 当前可读图分页 | 不获取图执行权 |
| GET /task-graphs/{id}/revisions/{revision} | 原版本与当前失效原因 | 不替换为最新引用 |
| GET /task-graph-submissions/by-key?key= | 定义原键核对 | 不补投 |
| POST /task-graph-previews | 固定环境与节点分配额度 | 不联系 Provider、不写控制日志 |
| POST /task-graph-runs | 显式授权、首次准入才调度 | 原键重复只观察 |
| GET /task-graph-runs | 当前可读授权分页 | 不自动恢复 |
| GET /task-graph-runs/{id} | 节点原日志与产物校验 | 完整来源检查，只读 |
| GET /task-graph-run-submissions/by-key?key= | 图授权原键核对 | 不补节点 |
| GET /task-graph-runs/{id}/control | 最小本人停止引用 | 不读来源正文，不启动队列 |
| POST /task-graph-cancellations | 强确认停止意图后停止精确孩子 | 不以请求冒充实际停止 |
| GET /task-graph-cancellation-submissions/by-key?key= | 原停止命令核对 | 来源撤权后仍可降低权限 |

图和运行列表复用原有 limit 25/最大50、稳定水位游标及512KiB响应上限。图定义、图授权单blob上限512KiB；dispatch/cancel采用原32KiB上限。每项目历史与预留 attempt 槽共享200上限；重复原键不消费第二份额度。所有旧 payload 仍保持原 schema 及字段，事件词表与强同步事件集合不增加。

## 调度与未知

就绪依赖只接受上游真实队列已退出、原生命周期 completed 且没有未知效果或失效产物的节点。菱形 D 必须等 B/C 均确认完成；节点完成不等于 S10 业务验收，依赖边不隐式复制产物或授权宿主写回。大上下文必须满足原执行入口的实际读取能力检查。

图 Root 自身不调用模型，也不另建私有 Root 事实库。节点意图只允许一次，强确认失败不创建队列；丢失回执只能查询原键。图边界必须仍有当前进程原调度者拥有的并发槽；重启后没有原调度者，不把旧授权当成新执行权。冷读按原节点日志显示 completed/interrupted/unknown，未调度的 join 不能冒称完成。读取、列表、刷新、浏览器重开及原键重复 POST 均不重新投递旧节点。

通用 S08 续做当前不提供图调度槽：图节点预检明确列出 `graph_continuation_requires_scheduler_admission`，实际续做入口在提交新 attempt 前拒绝；普通 schema1/2 S08 功能保持。显式恢复图调度是另需协议的扩展，当前不能声称支持。停止传播遍历已授权实际节点及可验证的图祖先归属；来源撤权不移除同主体最小停止能力。旧运行日志、捕获输入和旧宿主工作区不会因只读重启而改写。

完整工作台仍可能因既有项目列表页面打开原 control writer；图专用冷读接口本身不打开 writer。控制 writer 与模型/队列/节点日志 writer 必须分别观察，不能把它们混为零写者声明。
