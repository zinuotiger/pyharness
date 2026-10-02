# PyHarness 34 卡实施矩阵

29 completed / 5 partial；所有卡已处理，未宣称34/34完整人工验收。详见 [完整报告](reports/PYHARNESS-34-CARD-IMPLEMENTATION.md) 与同名 JSON。

|ID|卡片|状态|当前结果|剩余限制|
|---|---|---|---|---|
|B01|基线与范围冻结|completed|建立干净起点、文件校验、独立数据及命令证据|工作区基线29b1bb8；本轮不使用历史CI证明当前代码|
|B02|团队记忆成员委派政策|completed|按每个任务绑定可信actor，执行时重查团队角色；直接API和Agent共用授权|本地角色隔离；不是生产SaaS认证审计|
|B03|知识问答无证据边界|completed|明确require_evidence；普通聊天不受影响，主任务/定时/子任务自身证据检查在模型前执行|引用验证不能保证模型所有事实的语义正确|
|B04|清空备用模型配置|completed|区分字段缺省与显式空数组，持久化清空和回滚回归|无真实供应商fallback调用|
|B05|空请求体发布 Agent|completed|无body及{}合法，非法JSON受控拒绝，角色与发布快照不可变|只覆盖当前发布API版本|
|B06|自定义审批字段的有效契约|completed|收紧式approve/deny规则；注册表只变更治理元数据不暴露Provider；缺模型前置拒绝|不支持任意规则表达式，不能降低固定高风险审批|
|B07|状态文档与七个无效配置|completed|七字段非默认值明确拒绝；说明工具超时与宿主内存边界；probe文档改为bounded generation|保持兼容默认值，不声称已实现统一并发或宿主内存限制|
|B08|文件和图片四条路径契约|completed|区分上传、预览、文件输入与视觉发送；会话所有权、SHA、大小和格式校验|预览容器校验不是完整图像解码或恶意文件安全证明|
|B09|聊天图片缩略图与附件操作|completed|缩略图、单项移除、草稿恢复、会话隔离；迟到上传响应不重新添加已清除附件|已上传但移除的文件仍可在Artifact库存在；不自动删除用户文件|
|B10|图片进入模型的能力决策|partial|Provider-neutral视觉协议、文件边界和能力闸已完成；离线fake transport及拒绝边界证据保留|没有经过确认的真实视觉连接，因此未执行真实图像请求。本阶段未读取连接配置或Secret；沿用已记录的能力元数据，不推断当前供应商能力。|
|B11|已有工具与 API 的 Web 入口选择|completed|接入历史搜索、编辑、反馈、任务活动与取消，沿用现有服务及角色权限|不新增一级页面；插件生命周期在Platform中明确拒绝|
|B12|父子任务与异步编排契约|completed|子工具集合取交集，继承任务身份，独立证据与审批；父取消清理子进程；join超时不误取消子任务|合成模型编排；不证明任意多Agent复杂任务质量|
|B13|本机真实 isolated 与文件资源边界|completed|实际DockerSandboxManager容器、non-root、只读root、网络/文件/资源边界与进程清理|Docker Desktop Linux Engine 29.6.2；14+10项受控断言，不是跨平台形式化安全证明|
|B14|真实模型受治理代码变更闭环|completed|相对路径契约与执行前拒绝；新真实Run完成审批、Docker修复/pytest、最小Patch应用与重启恢复|累计5请求，其中成功Run4请求；后续通用审批/证据修复只做确定性回归，未对最终安装包重复付费模型验收；旧tasks路由的完成/恢复截图排除，由零新请求的只读恢复截图补证。|
|B15|外部 RAG 的真实兼容与无证据处理|completed|严格兼容固定协议，只接受授权原文引用并推导行号；错误/超时分型；独立probe|真实rag-engine 0.2.0离线NGram/extractive；PDF/DOCX、BGE/向量质量和远端云鉴权未验|
|B16|MCP KV 和 evleven 的独立验收|completed|在仓库外固定wheel与依赖环境运行现有16项；受控真实MCP stdio；KV会话/团队及重启回归|真实进程/协议，planner为确定性；KV不是语义记忆；未配置未知MCP|
|B17|Web 真实交互补验|partial|Chrome 12页36截图、API和自动交互回归通过；已探测安装中文输入法及系统窗口自动化|触屏探测为0；物理中文IME未完成。Computer Use因无法可靠确定Windows浏览器URL而停止，未换通道绕过；合成composition不代替真实IME。|
|B18|桌面 WebView 壳人工验收|partial|pywebview 6.2.1/WebView2 154.0.4258.48真实可见页面与关闭服务已观察；新安装包隐藏入口通过|可见页DPR为2只是观察；最小化/恢复/缩放、文件对话框、物理IME及完整高DPI体验未完成。|
|B19|Native Qt 人工验收|partial|Qt 6.11.2可见窗口和菜单已观察；修复事件循环停止及C++销毁时的外观资源释放，定向与安装入口通过|单显示器，Qt DPR为2；物理输入、高DPI/多屏和完整人工窗口体验未验。|
|B20|CLI 与 ACP 分开验收|completed|真实ConPTY完成TTY/help/中文流式/审批输入/退出；有序Ctrl+C自然退出130；旧审批订阅已修复，最终安装包APR-503为零，句柄清理通过；独立真实stdio ACP证据保留|使用确定性本地传输，未测试特定第三方ACP品牌客户端；独立真实stdio协议客户端已验证。ConPTY合并输出，stdout/stderr分离由独立pipe证据验证。|
|B21|正常重启 回放与错误恢复|completed|真实B14正常重启及RAG/ACP重启；Session/Run/审批/Trace/Artifact/usage/KV/账户/主题与repair回归|不承诺副作用重放、跨机Secret、热备、断电事务或多进程HA|
|B22|性能与资源的有界测量|completed|100会话、1000消息长会话、1000合成轨迹、50Artifact、4并发Run与4动效模式测量|headless/CDP TaskDuration、JS heap和Python traced allocation；非整机CPU/RSS/功耗或真实显示器FPS保证 首次结果错误字段results及零帧旧间隔已在followup明确纠正；空Trace不作压力证据，独立1000条有效事件结果非空。|
|B23|本地账户权限与 SaaS 边界|completed|回归owner/member/viewer、跨团队、降权、最后owner与资源授权；文档明确边界|不含支付、Organization、云Worker、公网认证或生产多租户安全认证|
|B24|汇总验证 打包与交付判断|completed|最终全量、授权AST、语法/敏感路径检查、新wheel/sdist三种安装及包内离线资源/MIME通过|本地候选；没有远程CI或发布。完整人工设备与真实视觉仍见partial卡。|
|U01|范围与模块契约定版|completed|独立theme-kit与host adapter契约，注入配置/偏好，不接业务API|仅现有静态Web/Qt体系，不迁移前端框架|
|U02|主题 tokens 与可读性基线|completed|命名空间tokens、light/dark/tech-blue对比和静态可读回退|自动布局/截图不是全面WCAG认证|
|U03|接入真实 Platform 静态首版|completed|真实Platform页面共享tokens和控制器，业务DOM及API不替换|不宣称新产品页面或完整设计重构|
|U04|纯 Canvas 粒子引擎|completed|纯Canvas、单RAF、粒子80上限、DPR2及逻辑面积上限；销毁释放|有界装饰动效，不承诺所有GPU性能|
|U05|动效偏好与可见性策略|completed|normal/low/off、reduced-motion、页面/宿主/遮挡组合暂停|低动效使用静态回退，关闭时不继续RAF|
|U06|注入式 PreferenceStore|completed|注入Web localStorage及WebView原子FilePreferences白名单；无Secret字段|本地偏好，不跨账户云同步|
|U07|离线资源与安装包|completed|新wheel/sdist包含7项theme-kit/adapter离线资源；安装包逐项内容/SHA/MIME/nosniff及越界404通过|本地候选；没有远程CI或发布。完整人工设备与真实视觉仍见partial卡。|
|U08|WebView 与可选经典页适配|completed|平台/classic adapter接入同一组件；宿主桥接与页面可见性|物理WebView人工验收单列B18|
|U09|可选 Native Qt 主题适配|partial|Native主题代码、偏好和资源生命周期已完成；真实可见窗口已观察，补充C++销毁与提前停循环回归|与B19统一标准：物理输入、高DPI/多屏和完整人工窗口体验未验，不能仅凭offscreen或DPR观察标completed。|
|U10|主题业务保持与复用验收|completed|第二宿主挂载销毁、缺粒子回退、草稿附件保持、暂停泄漏检查及有界性能对比|无移动实机续航和整机功耗测量|

## 最终 partial 收口复核

本阶段以 `19945f06ec0bda02f506e3a6a687413831530471` 为冻结起点。**29 completed / 5 partial / 0 blocked**；partial成员为B10、B17、B18、B19、U09。B20依据真实ConPTY与独立stdio协议证据改为completed；U09因物理输入与DPI/多屏未验改为partial，和B19保持一致。

CLI首次终端审批的一次有效拒绝被遗留的bootstrap ApprovalProvider重复观察，导致旧实例报APR-503；修复旧订阅所有权，原始红测、169项相关回归和最终安装包单次审批证据均保留。Native安装入口曾退出0但在Qt析构时报已删除窗口RuntimeError，该次包检查明确判失败；已修复事件过滤器与timer释放、直接C++销毁和提前停止事件循环，并由两条先失败后通过的回归、36项定向及最终安装入口无异常stderr验证。没有吞异常或弱化测试。

最终全量：2728 collected / 2687 passed / 41 skipped / 0 failed，Coverage 84.10%（门禁75%）。Chrome真实页面、合成模型：12页36截图、0未捕获JS错误；新wheel三种安装、HTTP、隐藏WebView、Native offscreen通过。ConPTY使用最终安装包验证一次拒绝、APR-503为0、退出0及进程/线程/socket句柄清理；已通过的有序Ctrl+C退出130不重复。ConPTY本身合并输出，独立stdout/stderr与ACP证据来自真实stdio驱动；没有测试特定第三方ACP品牌客户端。

安装中文输入法已探测，但Computer Use在激活Chrome时因不能可靠识别浏览器URL而停止；没有换工具绕过。可见WebView/Qt及DPR=2的观察不证明物理IME、高DPI或多屏完整通过；触屏为0、显示器1。误拍遮挡窗口的原图排除，仅独立应用前台截图列入交付。

本阶段新增真实模型请求0，不重跑Docker/RAG/MCP/evleven。B14保留历史累计5请求、成功Run4请求的真实结果；后续修复只有确定性回归，最终wheel没有重复付费模型验收。测试在冻结基线加未提交改动上执行，精确源码hash、普通本地提交及提交后重建包由外部manifest绑定；不使用旧HEAD或旧CI冒充最终提交验证。

证据根：`final-partial-closure-20261003-024521/`。关键索引：`terminal-closure.json`、`native-disposal-root-cause.json`、`final-full-command.json`、`final-browser-command.json`、`final-package-command.json`、`final-source-snapshot.json`。原始失败证据保留；最终包和报告SHA见仓库外交付清单。本轮不push、不更新PR、不合并、不创建tag或Release。
