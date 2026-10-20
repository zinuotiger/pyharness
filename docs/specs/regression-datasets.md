# 失败候选、用例和数据集

> 当前状态更新（2026-10-21）：本规格对应能力已在S08–S18相应阶段及S18统一版本中验收。下文旧阶段状态为实现历史，当前边界与交付核验见[统一版本说明](../user-guide/unified-delivery.md)。S19最终交付须以新源码冻结和新CI为准。


状态：S13候选2已验收及归档，源码冻结`7f8bf064499ac4872dcd6d8fa5d88e71efbd4c89de096966b3a6ca2f44ae6bb9`。ADR-032为新增控制资源决策；S14批次执行正在独立推进。

## 已接线的私有入口

前缀`/api/v1/projects/{project}`；写操作使用原Idempotency-Key命令键。成功和拒绝均no-store；当前读和保存都要求实际成员及资源所有者，不能自报Actor或授权。

| 方法 | 路径 | 含义 |
|---|---|---|
| POST | `/failure-candidates` | `schema_version=1, execution_id`；原失败事实的公开预览，零持久化和执行 |
| POST | `/regression-cases` | 显式确认候选、人工复现输入、白名单断言后保存新修订 |
| GET | `/regression-cases` | 本主体目录；来源不可用时不返回标题和正文 |
| GET | `/regression-cases/{case}` | 可选单一`revision`；读取精确历史修订 |
| POST | `/regression-datasets` | 显式确认固定Case引用后保存新修订 |
| GET | `/regression-datasets` | 本主体目录，当前来源检查 |
| GET | `/regression-datasets/{dataset}` | 可选单一`revision`；旧集合不漂移 |

Case选择包含case_id、expected_revision、project_revision、title、origin、candidate_digest、input_text、assertions和严格布尔confirmed=true。返回保存记录、digest、source_seq以及record_canonical原规范UTF8文本；文本最多原32KiB记录大小，摘要与实际不可变blob逐字节一致。浏览器核对原字符串SHA后显示精确原始数值，不以JS再次序列化冒充原字节。Dataset选择包含dataset_id、expected_revision、project_revision、title、case_refs和confirmed=true。CaseRef固定case_id/revision/digest/source_seq。

实际观察的输入/输出是有界公开文本；隐藏、裁剪和无事实分别标识。人工复现输入需独立确认，不能把被屏蔽的原请求当作完整等价输入。未知供应商调用状态、隐藏推理或秘密不通过此数据通道补写。

## 限额与断言

- 每项目每种资源最多200次历史提交；单记录仍受原32KiB blob限制。Case最多16条断言，人工输入2048字符、标题100字符，Dataset最多32个不同Case引用。
- 普通请求最多64KiB；导入的JSON字符串包装请求最多192KiB，原数据包UTF8最多64KiB。重复JSON键、非有限数、过深或节点超限拒绝。来源前缀最多10000条事件和16MiB规范字节；不把截断前缀伪称完整。
- `output_contains/output_excludes/output_equals`要求非空文本期望；`json_pointer_equals`只允许RFC6901字面指针和标量、最多8段；不执行脚本或表达式。JSON断言区分布尔、数值、文本及null，`true`不等于`1`；`1`与`1.0`同属JSON number，数值比较不把任意大整数先舍入成double。
- `execution_status`只接受completed/failed期望，取消或缺证据为unknown。输出被屏蔽、不完整、无输出、超过64KiB或超有限JSON检查能力时，输出断言为unknown。最终批次通过分母由S14另行核对。

候选、当前来源和实际主体在读取及返回前核对；保存前和不可变blob写入后重新检查。相同命令键只读取原提交结果，不重复保存，也不重放历史调用。Case或Dataset不能作为执行授权；没有发布到外部服务的自动动作。

## 显式数据包传输与原键恢复

| 方法 | 路径 | 含义 |
|---|---|---|
| POST | `/regression-datasets/{dataset}/export-previews` | revision固定旧版，核对当前来源并返回确认摘要，不含可下载正文 |
| POST | `/regression-datasets/{dataset}/exports` | revision、confirmation_digest和严格confirmed=true；再次核对后返回原规范UTF8数据包及SHA |
| POST | `/regression-dataset-import-previews` | 读取目标ID/expected_revision/project_revision/title和bundle_canonical字符串；零入库 |
| POST | `/regression-dataset-imports` | 同一预览内容加confirmation_digest/confirmed=true/原命令键；只新增一次Dataset修订 |
| GET | `/regression-submissions/{key}` | 实际主体的原提交结果；只读、不重放，不返回其他资源种类 |

数据包格式pyharness-regression-data-v1，固定tenant_id、subject_ref、project_id、Dataset完整定义/引用及Case完整定义/引用，使用regression-assertions-v1。每个原blob的规范字节、引用及所有者都必须能在本地原control日志重证；不将包自报的脱敏、SHA或权限当执行权。跨租户/主体/项目先拒绝；篡改、丢失、未知断言或超限拒绝。导入不生成Case，只复用确切已存在Case版本并保存一个Dataset引用修订；import_source固定源DatasetRef和原文件SHA。它不是跨账号迁移或任意外部测试代码导入。

导出最多64KiB原UTF8；导入外层包装最多192KiB，内层最多8192节点/20层。输出同时带bundle_canonical原字符串和bundle_digest，浏览器验证原字符串SHA再下载；文件读入不在JS中数值重序列化，保留浮点表示和大整数原文。超出JS安全整数范围的既有期望可以查看原始资料和导出，界面不静默舍入后编辑，严格后端接口仍支持有限合法JSON标量。

原执行失败后追加task.failed/segment.end等原队列收尾，不会改变已固定前缀。保存及读取核对指定前缀的真实原字节摘要与实际失败事实；不能把成功记录改名为失败。原独立rejected验收后来被新passed验收取代，已冻结Case仍保留原事实，编辑只产生新修订；当前来源和实际主体始终重查。

响应丢失或409/503等不确定结果先查询原键。failed writer不允许盲重试，close/reopen后用同key核对并保存至多一次。字段/大小/结构的422拒绝不会为该请求写入控制事实，界面清除确认勾选并允许修正；不把未知提交当成功。私有正文不放localStorage，关闭、身份变化、拒权及迟到响应不重新显示旧正文。

## 当前验收状态

历史模型只校验固定结构，不调用会变化的保密规则。候选生成的公开输入/输出重用原文本秘密检查，不只依赖旧公开副本或通用上下文过滤；受限文本返回withheld及空值。当前保存、读取、导出和导入重查人工字段和保存证据两侧文本；保密规则变化可使相关旧用例/集合拒读，目录隐藏标题，但不会把旧控制日志误判为损坏、阻塞项目列表或改写历史。限制解除后仍读取原字节版本。这不表示引入了全局动态密钥表。

S13候选2：4235总计/4189PASS/46环境条件SKIP/0FAIL，87.71%、16核心最低84.95%；323Chrome、486运行文件在源码/wheel/独立安装逐字节一致、4入口、Web90、安装图24/恢复33/观测90/回归122、真实Docker6及HTTP92/93均通过。四原工作区17825登记文件hash/HEAD/status未变；恢复包1181文件和全会话24902记录411可读消息158附件归档已核对。付费推理、物理IME/DPI及条件跳过不由这些合成模型/自动化检查替代。

开发历史：候选1虽4184PASS/46SKIP/0FAIL，因新增公开证据保密5项真实RED仍拒绝验收；修复后53GREEN并在候选2重跑全部门。CLI验收脚本漏PDF、错资产名的失败也保留，最终在新数据目录按原86项加4项资产检查通过，未改产品源码掩盖。S14补齐公共审计对Case/Dataset ID的识别，新增混合新资源实际审计回归；本记录不表示S14–S19完成。
