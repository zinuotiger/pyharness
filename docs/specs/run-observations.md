# S12 上下文、权限预演与运行比较

> 当前状态更新（2026-10-21）：本规格对应能力已在S08–S18相应阶段及S18统一版本中验收。下文旧阶段状态为实现历史，当前边界与交付核验见[统一版本说明](../user-guide/unified-delivery.md)。S19最终交付须以新源码冻结和新CI为准。


状态：S12已验收。候选3冻结8310f61bb476c1d56b0cb07af358b5222021cd34da75bb770b6cd5816c31f496：4136通过、46条件跳过、0失败/错误；305Chrome、475安装运行文件字节核对、两种模式各60项实际HTTP和恢复包/完整归档均通过。ADR-031及持续进度账本记录明确边界；S13–S19另按顺序推进。

## 实际公开请求

原产品执行的模型调用仍走唯一LLM入口、原预算闸和原最终模型/来源权限检查。新增观察只保存实际入口请求的有界公开副本，不重新组装历史、不提供执行权限。

- 自定义适配器记录 `adapter_input`；OpenAI兼容适配器仅记录真正清洗工具名后的 `transport_payload`，不重复记上层副本。
- 同一个原root/child执行JSONL中的新独立 `execution.context` 事件；显式sync，原89种事件Schema与22种SYNC类型不变。总事件类型90。
- 阶段始终 `prepared`。只有原传输请求的 `request_seq` 对应到真实 `llm.response/error`，才显示已记录响应/错误；不把准备完成说成供应商收讫。
- 公开投影原始40KiB，类型规范化后最多48KiB；事件payload最多60KiB，为原信封上限预留空间。消息/工具各64项、调用16项、树2048节点、深度6、扫描总字符有限。
- 不读取新凭据；已有实际transport key只用于精确过滤。固定有限秘密模式、隐藏推理、不支持对象整项withheld；图片不保存URL/base64/正文。有限模式不是完整DLP。
- 保留公开角色、正文、工具调用和公开schema；展示裁剪与原模型历史窗口裁剪分别记录。计数来自同次原derive/assemble且在请求作用域匹配session/tenant/role/消息摘要，未知保持不可用。
- 主调用绑定当时确切检查点和materialized_messages_digest；辅助调用、fallback尝试、子会话分别归因。旧日志缺事实返回unavailable，不从今天的内容回填。

私有只读接口：

|方法|路径（前缀 `/api/v1/projects/{project}`）|含义|
|---|---|---|
|GET|`/executions/{execution}/contexts`|最近20条目录、总数、裁剪标记与原准入子会话|
|GET|`/executions/{execution}/contexts/{observation}`|确切脱敏副本与原事实依据|

确切详情额外返回有界public_view_canonical UTF8文本，使用原SHA256逐字节核对，再与解析view核对；原始JSON按textContent显示，保留1.0、科学计数法及大整数的原字符，不能用JavaScript重新序列化后的数字冒充原文。该返回文本最多48KiB，与原结构化公开副本合计约96KiB；不改变持久事件Schema。

两个GET仅允许一个可选 `session_id`；root之外只接受原执行真实准入的child。前后验证实际Actor、当前来源与历史绑定；不创建writer/engine/queue，不调用模型。成功和错误均no-store。

## 固定选择的权限预测

`POST /permission-previews`：

```json
{
  "schema_version": 1,
  "attempt_ref": {"attempt_id":"ra-0123456789abcdef","revision":1,"digest":"<64 hex>","source_seq":5},
  "agent_ref": {"agent_id":"a-0123456789abcdef0123456789abcdef","version":1,"sha256":"<64 hex>"},
  "tool_name":"fs.read_file",
  "arguments":{"path":"sample.txt","char_start":0,"char_limit":1200}
}
```

严格拒绝额外主体/授权字段、额外query、超限参数。参数最大32KiB、2048节点、深度8。原capture_execution_materials在预测前后分别读取并核验精确尝试/来源/发布Agent/编译配置，Guard await期间不占控制锁；返回前再核对实际Actor与资料/配置摘要。预演不调用chat_readiness探针、不执行模型。

元数据收集器使用原ToolRegistry schema编译并丢弃所有Provider绑定。复用原fs工具定义、抽出的原Platform文件metadata配置、原range schema、原预设和审批收紧规则，以及原Scope.can_use和builtin Guard。仅构造局部Scope，不调用注册active scope的build_scope；不持久audit、不装载任意plugin guard、不申请审批或分配沙箱。

结果 `allow/reject/approval/unknown` 均为策略预测，`execution_authorized=false`、`side_effects_executed=false`、`persisted=false`、`live_recheck_required=true`，没有可消费的授权token。Shell/外部插件等未建模行为unknown；原scope/显式拒绝和disabled sandbox拒绝不能被unknown覆盖。文件预测使用未创建的隔离命名空间和固定输入的存在性；不证明真实文件/link状态、实时预算、容器可用性或人工审批结果。

## 原事实运行比较

`POST /run-comparisons`：

```json
{"schema_version":1,"left_execution_id":"ex-0123456789abcdef","right_execution_id":"ex-fedcba9876543210"}
```

在同一个原控制只读快照内验证两者，分别读取严格验证的原root/准入child日志，读取完毕后重新检查**两个**执行的实际主体与当前来源。每个日志只计一次，读取不会执行/修复/重放。

- 输入基准为确切task_ref、input_binding、资料和引用版本；不包含不同attempt身份，否则相同输入也会被误判不同。
- 声明验证基准为发布snapshot.output_schema和固定delivery_ref；不同返回 `validators_differ`。输入不同返回 `fixed_inputs_differ`；修正/续跑的完整有效历史目前不支持对齐，返回 `input_alignment_unavailable`。
- Agent、模型和编译配置版本可不同，作为对照维度返回原固定引用与fingerprint。原日志没有validator implementation版本时明确 `not_recorded`，不凭当前实现补历史版本。
- 双方终态且基准对齐时可指出执行 `completed -> failed` 回归；pending不判回归，取消不算通过。业务验收原状态独立展示；`business_quality_verdict=not_evaluated`。S13/S14后接固定Case和白名单断言，不以当前执行状态冒充质量评分。
- 每个原日志按model核对request和usage条数；缺失/跨model/不完整用量不能成为完整总额。输入unknown即input_tokens=null；没有价格或未知输入即cost_est=null，不能补0或使用今天价格重算历史账单。估计计量与记录计量分别标记；费用仍是原记录的估计。
- 公开结果文本再走有界公开投影；输出摘要用于变化判断。差值仅在可比且两值均已知时计算。

## 连接标识与真实传输验收

引擎将公共模型名编译为带endpoint、秘密引用、timeout、credential generation和store根的内部连接键；该内部键不是另一个模型版本。S12真实loopback TCP/HTTP验收发现原最终闸直接比较内部键与公共名会错误拒绝实际连接。

具体连接身份检查留在core/llm.py唯一适配器持有边界内，应用层仅导入返回两个布尔匹配结果的只读门面，不持有具体适配器类型，也没有新增动态派发/出网入口；既有LLM出网不变量和白名单不扩宽。

修复只允许由原唯一纯connection_registry_key计算得到、实际应用注册表中确切OpenAICompatAdapter持有、未关闭且triple/timeout/credential binding当前有效的别名。其余完整LLM配置仍逐字段相等，未知调用模型仍拒绝；父/子均复用同一校验。既有显式借用适配器行为与旧callable参数不变。没有移除或跳过原最终权限闸，也不会为了检查而解析秘密或发请求。

真实验收中，原产品队列经过生产HTTP传输向自有127.0.0.1端点发请求，原JSONL记录transport公开副本并关联真实response，再经受权限控制的GET读取；实际wire工具名清洗、输入中反射的合成key被公开副本遮蔽、供应商隐藏推理不外露。自有端点仅模拟模型回答，不代表付费服务或真实模型质量已验证。

## 界面与保全

项目空间有观测入口，原执行窗口在运行前可传递确切尝试/发布版本用于预演，已运行时可载入原选择。界面区分主/子会话、目录/确切事实、准备/传输确认、模型窗口/展示裁剪、权限预测、执行回归与未知费用。仅按用户动作读取或预测，不自动启动/重试/审批；私有正文不进入localStorage。

每次异步操作前后核对实际账户realm和界面generation；关窗、切换选择、权限拒绝或主体变化会清除正文并丢弃迟到响应。DOM使用textContent，确切公开副本摘要与目录绑定后才展示。整体完成仍需当前源码全量/16核心、Chrome、实际HTTP/Qt、独立安装、保全、恢复包与全会话归档通过。

完整浏览器冷读还须经过项目目录。项目HTTP列表采用新增严格只读投影，空目录不初始化日志，已有日志从原文件验证，损坏拒绝；不获取legacy控制writer。原WorkspaceStore.projects()显式初始化接口及其签名/行为保留，现有写入与错误注入调用方不受影响。目录HTTP成功/错误均no-store，主体读取前后重查。

遇到无法检查的消息、角色、content对象或内容part时，图片数量保留已识别的数值但images_exact=false，不能把屏蔽内容说成精确的零张。正常纯文本/None内容及显式image_url/input_image按既有受限语义计数，不记录URL或字节。

列表工具省略可选path时，原schema归一化给None，原工具语义仍是工作区根目录。权限预演同样按根目录预测；file.list在standard策略下与fs.list_dir保持该默认行为，不能将正常缺省值误判越界。
