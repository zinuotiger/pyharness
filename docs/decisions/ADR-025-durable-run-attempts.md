# ADR-025 — 产品任务持久提交与未知结果查询

状态：S06实现决策，用户已授权持续S05–S19；验收以当前阶段证据为准。

沿ADR-038的唯一ApplicationService/Projects/WorkspaceStore owner、OS lease、SessionLog及不可变blob扩展run_attempt，不新建任务数据库。草稿/定义与尝试分离，revision1固定服务端主体/租户、项目/任务/输入及资料/引用/交付版本。只预留意图，dispatch_policy=explicit_governed_start；S07真实执行边界另验，不自动泵送队列，不绕agent-loop、guard、审批或既有聊天运行入口。

只读preflight与提交共用锁内前置谓词，不接受预检票据。每次提交、磁盘写后、收据返回前重新核验；捕获字节按摘要读取，不读活宿主正文。幂等key属于当前主体，同键同语义一次，异命令或异key同ID冲突。清单隐含引用展开精确绑定；历史仅观察固定版本及当前失效原因。

未知append继续隔离writer。为核对可能落盘的事实，新增窄范围只读磁盘查询：验证序列/租户/类型/blob/引用图及截断/隔离区，派生同一事实源；保留_failed与原lease，不改写缓存、修复或恢复写入，不补投。磁盘坏即未知错误，完整磁盘无原键才not_found，也不构成执行或恢复writer授权。其他资源原故障隔离行为不变。

浏览器仅缓存原键及引用选择元数据，无正文/凭据，不作真源；重启后重新鉴权和查询。显式重试同key同选择摘要，打开/刷新零POST。Chrome、真实HTTP到唯一日志再磁盘重开和独立安装验证链路；合成后端与真实外部条件分列。详见[task-inbox](../specs/task-inbox.md)。

拒绝的替代：草稿直接queue.submit混淆定义/执行；另存任务表造成第二真源；超时自动新key会重复；直接清_failed可能继续损坏写入。
