# 任务收件箱与运行尝试提交（S06）

> 当前状态更新（2026-10-21）：本规格对应能力已在S08–S18相应阶段及S18统一版本中验收。下文旧阶段状态为实现历史，当前边界与交付核验见[统一版本说明](../user-guide/unified-delivery.md)。S19最终交付须以新源码冻结和新CI为准。


覆盖A03：草稿与正式任务定义保留，运行尝试是显式提交后独立、不可变的资源。保存定义、预检、列表、查询均零执行。S06只持久预留执行意图；S07才连接受治理执行、开始与效果收据。没有模型/队列/工具入口因本步而自动运行。

## 命令与精确绑定

`SubmitAttempt`严格要求整数schema_version=1、attempt_id（ra-16hex）、确切task_ref；可选document_refs/citation_refs各最多20及delivery_ref。引用均含ID/revision/digest/source_seq，数组不允许重复ID。不接收actor、tenant、host路径、预检票据、status或执行授权。服务端派生RunAttemptPrerequisites，不将旧context_reference_only当正式任务。

RunAttempt revision1不可变，保存原选择、服务端主体/租户/项目/任务/清单/输入前置、引用并集、binding_digest及submission_key。created_by/tenant_id/subject_ref不公开。清单内引用及相应资料自动展开去重，合计各不超过20；不同版本冲突拒绝。提交时依赖必须当前、可定位、未退役/缺失/过期，任务已绑定私有输入。只读取私有捕获字节并核验摘要，不读取活宿主正文或创建执行副本。

提交在唯一WorkspaceStore owner锁内重新读取磁盘事实，复用与只读preflight共享的前置核验。blob写入并读回后、append之前再次核对角色、版本、来源授权、根身份、UTC及私有字节，末尾合并所有依赖授权检查。客户端预检摘要不构成授权。沿原OS lease、唯一控制SessionLog、不可变blob，无第二任务数据库。最多200次attempt历史/项目，blob不超过32KiB，失败不追加成功事实。

## 接口

前缀`/api/v1/projects/{project_id}`。每次重新解析服务生命周期及身份；owner/member仅本人私有attempt，viewer/跨租户/跨项目/跨主体不可读写。

| 路径 | 行为 |
|---|---|
| POST /run-attempts | 严格SubmitAttempt及Idempotency-Key；同主体同key同语义恰一事实，异命令冲突；同ID其他key冲突 |
| GET /run-attempts | limit 1–50、稳定水位cursor；当前受权主体列表与精确total，不执行/补投 |
| GET /run-attempts/{attempt_id} | 精确提交及当前失效观察，不替换任何版本 |
| GET /run-submissions/by-key?key=... | 恰一个key，严格[A-Za-z0-9._-] 1–128；已提交/未找到事实，不授权执行 |

固定query端点使合法`.`/`..` key不会被浏览器路径归一化。新私有接口响应no-store。返回`status=reserved, persisted=true, attempt_reserved=true, execution_authorized=false, execution_started=false, dispatch_policy=explicit_governed_start`。后续输入变化单列input_readiness=stale和原因，原引用及binding_digest不变。S07需在真实开始边界重新核验，提交收据不是永久执行授权。归档、撤权、过期、根不可用或角色丢失拒读；被撤销依赖不在列表泄露。

## 未知提交与故障隔离

明确拒绝、持久确认、未知结果分开。append/磁盘确认失败的owner继续禁止写入；查询可严格只读观察同一磁盘：验证截断/隔离区、序列、租户、类型和严格blob/引用图，复用原lease。不清除_failed、不更新未确认写缓存、不启动第二写者、不repair。完整磁盘无原键才not_found，这不是可重复执行或恢复writer证明；writer_requires_reopen指示隔离仍存在。损坏或无法确认返回未知错误，不能伪造not_found。原键显式重试仍逐次鉴权。

旧任务/资料变更后原attempt固定原版本并标记stale。历史重放按事件时资源验证，不用今天的期限、解析器或角色把历史判损坏。所有查询零append、零引擎/队列/Provider调用。

## 界面与浏览器恢复

项目空间→任务收件箱。草稿计数单列，选择已绑定输入的正式任务及可选资料/引用/清单显式提交。双击在首次await前拦截；成功必须原key重新从服务端核对原选择及request_digest。新请求先清上次成功提示。刷新/关闭/选择变化/换主体使迟到结果失效；权限失败清旧结果。网络前后核对主体，原URLs和namespace在await前固定。

浏览器仅保存按主体/项目隔离的原键、摘要、随机ID及**引用选择元数据**（严格ID/修订/摘要/序号），不保存资料正文、任务目标、凭据或执行授权。完整刷新后可只读查询或显式原键原选择重试；打开/刷新零POST。恢复元数据不是权威，篡改则拒重试；服务端仍重验权限和绑定。存储不可用时保留内存线索，手动key查询/服务端列表仍可用，不从本地线索恢复成功。

| AC | 验收标准 |
|---|---|
| S06-01 | 草稿/上下文引用零执行，严格命令、本人/租户/项目绑定 |
| S06-02 | 同锁重验，预检后版本/授权/私有字节变化零提交 |
| S06-03 | 资料/引用/交付精确版本并集，历史不跳，变化标失效 |
| S06-04 | 持久幂等，双击一次，未知原键磁盘查询，writer继续隔离 |
| S06-05 | 稳定分页/磁盘重开，损坏保真，撤权/到期/生命周期拒绝 |
| S06-06 | Chrome真实渲染，页面刷新恢复、显式重试、身份/迟到/文字安全 |
| S06-07 | 当前源码全量、16核心覆盖、真实HTTP/重开/独立安装及旧成果保全 |

完成只以本轮新证据为准。S07–S19及26项整体仍未完成；真实外部模型、渠道、Docker及物理原生设备不以合成后端替代。
