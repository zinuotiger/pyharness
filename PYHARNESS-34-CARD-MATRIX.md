# PyHarness 34 卡实施矩阵

29 completed / 5 partial；所有卡已处理，未宣称34/34完整人工验收。详见 [完整报告](reports/PYHARNESS-34-CARD-IMPLEMENTATION.md) 与同名 JSON。

|卡号|目标|状态|实际变化|限制|
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
|B10|图片进入模型的能力决策|partial|显式supports_vision；每轮检查真实runtime连接/模型/endpoint；重试保持附件；不支持能力时零图片出网|现有shidongai视觉能力未声明；真实视觉供应商验收未执行，只有fake transport协议与拒绝边界|
|B11|已有工具与 API 的 Web 入口选择|completed|接入历史搜索、编辑、反馈、任务活动与取消，沿用现有服务及角色权限|不新增一级页面；插件生命周期在Platform中明确拒绝|
|B12|父子任务与异步编排契约|completed|子工具集合取交集，继承任务身份，独立证据与审批；父取消清理子进程；join超时不误取消子任务|合成模型编排；不证明任意多Agent复杂任务质量|
|B13|本机真实 isolated 与文件资源边界|completed|实际DockerSandboxManager容器、non-root、只读root、网络/文件/资源边界与进程清理|Docker Desktop Linux Engine 29.6.2；14+10项受控断言，不是跨平台形式化安全证明|
|B14|真实模型受治理代码变更闭环|completed|相对路径契约与执行前拒绝；新真实Run完成审批、Docker修复/pytest、最小Patch应用与重启恢复|累计5请求，其中成功Run4请求；后续通用审批/证据修复只做确定性回归，未对最终安装包重复付费模型验收；旧tasks路由的完成/恢复截图排除，由零新请求的只读恢复截图补证。|
|B15|外部 RAG 的真实兼容与无证据处理|completed|严格兼容固定协议，只接受授权原文引用并推导行号；错误/超时分型；独立probe|真实rag-engine 0.2.0离线NGram/extractive；PDF/DOCX、BGE/向量质量和远端云鉴权未验|
|B16|MCP KV 和 evleven 的独立验收|completed|在仓库外固定wheel与依赖环境运行现有16项；受控真实MCP stdio；KV会话/团队及重启回归|真实进程/协议，planner为确定性；KV不是语义记忆；未配置未知MCP|
|B17|Web 真实交互补验|partial|现有Chrome真实页面/API、错误重试/SSE/草稿/附件/主题；12页36截图无未捕获JS|物理中文输入法、真实触屏未覆盖；合成composition事件不代替物理IME|
|B18|桌面 WebView 壳人工验收|partial|真实隐藏WebView2加载页面、桥接偏好、隐藏暂停和关闭服务；安装入口smoke|未操作物理窗口IME/高DPI/缩放；隐藏WebView不等于完整人工桌面验收|
|B19|Native Qt 人工验收|partial|接入QSS/受控Painter、QSettings及生命周期timer；offscreen窗口和安装入口|物理IME、高DPI、多屏及人工长时间交互未覆盖|
|B20|CLI 与 ACP 分开验收|partial|安装包CLI中文流式JSONL/退出码；独立真实stdio initialize/chat/read_events/shutdown/重启游标与多会话|使用本地确定性HTTP传输；审批/Ctrl+C/断连由完整测试覆盖，未做物理终端或第三方ACP客户端人工验收|
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
|U09|可选 Native Qt 主题适配|completed|Qt QSS/Painter实现受控主题，独立timer和偏好重载|不是Web Canvas嵌入Qt；物理操作单列B19|
|U10|主题业务保持与复用验收|completed|第二宿主挂载销毁、缺粒子回退、草稿附件保持、暂停泄漏检查及有界性能对比|无移动实机续航和整机功耗测量|
