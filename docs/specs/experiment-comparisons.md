# 固定版本对照实验

状态：S14实施中，准备、运行、固定报告及工作台已接线，未冻结或整体验收。决策见[ADR-033](../decisions/ADR-033-budgeted-experiment-comparisons.md)，结构与界限见[实现说明](../architecture/fixed-funded-experiments.md)。

准备选择包含：schema_version=1、experiment_id、expected_revision、project_revision、title、固定dataset_ref、显式case_refs（1至10例）、left_agent_ref/right_agent_ref、concurrency（1至4）和原任务图limits。两个Agent引用必须不同，Case不能重复，每个选择都必须属于原数据集的确切版本。预算limits仅约束准备的图定义，不作为运行授权。

| 方法 | 私有路径（前缀`/api/v1/projects/{project}`） | 行为 |
|---|---|---|
| POST | `/experiment-preparation-previews` | 返回原捕获输入绑定、人工复现任务草案、两侧节点、数据集总数/本批数/未纳入数及确认摘要；零写、零执行 |
| POST | `/experiments` | 加严格confirmed=true、confirmation_digest及原Idempotency-Key；准备原任务和图，保存实验定义，仍不执行 |
| GET | `/experiments/{experiment}` | 可选单一revision读取固定实验版本；重查当前主体、来源及保密规则 |
| GET | `/experiment-submissions/{key}` | 原键返回not_found/preparing/committed；不因查询补跑操作 |
| GET | `/experiments`、`/experiment-reports` | 当前主体和项目的定义/报告元数据；私有标题按当前来源规则核对 |
| GET | `/experiment-runs` | 原控制日志的同主体最小运行目录；归档、来源正文不可读时仍可找到停止引用；不读Case正文、不启动写入器 |
| POST | `/experiment-run-previews`、`/experiment-runs` | 固定运行ID、实验Ref、必填budget_grant及边界；先预览，再明确确认并保存唯一预算事实，仅首次沿原图派发；重复原键只观察 |
| GET | `/experiment-runs/{id}` | 原图/队列实际状态；私有正文须通过现行来源门 |
| GET | `/experiment-runs/{id}/control` | 同主体最小停止引用、原键/请求摘要和取消状态，不读来源正文 |
| POST | `/experiment-run-resume-previews`、`/experiment-run-resumptions` | 原图确实未登记且配置/预算/来源仍一致时，单独明确确认后沿原图键继续；已登记只观察 |
| POST | `/experiment-cancellations` | 保存最小停止意图，再调用原取消流程；写入失败或回执未确认仍可减少确切已归属运行权限，响应不谎报已提交 |
| GET | `/experiment-command-submissions/{key}` | 原键核对准备、运行、报告或停止；运行正文撤权时可返回最小控制；不自动重发 |
| POST | `/experiment-report-previews`、`/experiment-reports` | 从确切原控制/root/child前缀预览并明确保存服务端重算的报告；三分母、实际检查器字节摘要，未知值如实保留 |
| GET | `/experiment-reports/{id}` | 按保存时原前缀重算，重查当前主体与来源；后续尾部不替换旧评分 |
| POST | `/experiment-reports/{id}/export-previews`、`/experiment-reports/{id}/exports` | 已保存报告另行预览/布尔确认下载；严格类型、现行秘密扫描及精确原规范字节SHA；不发布 |

准备先保存原键绑定的不可变意图，再保存原任务与原图，最后保存定义。部分完成如实标记preparing；不能用新键绕过同一目标版本的旧准备，须查询原键。failed writer必须关闭重开后核对。重试只复用主体、项目、确切内容均一致的原任务；内容变化拒绝，不覆盖现有数据。新的定义版本不会替换旧定义的Case、Dataset、任务或图引用。

标题、人工Case字段和保存的公开证据按当前保密规则重查；历史模型只检查固定结构，规则变化不把旧控制日志误判损坏。任何Case来源撤权，相关副本均拒读，原键查询不返回其他种类资源。请求有界、额外查询拒绝、成功和失败响应均no-store。

所有新确认在Literal规范化前核对真实布尔类型，数字1不能冒充true；不可变首版引用拒绝布尔true冒充版本1。定义中的execution_authorized=false只描述准备数据；运行权限来自独立原键预算事实。报告nonterminal表示固定前缀尚无终态，不推测当下进程活性。整个S14仍须当前冻结源码完整测试、独立安装、实际HTTP/Docker、来源保全、恢复和完整归档。
