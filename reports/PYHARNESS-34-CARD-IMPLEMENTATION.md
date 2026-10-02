# PyHarness 34 卡本地实施与验收报告

本轮收口：**29 completed / 5 partial**。没有 not_started、failed 或 blocked 卡；partial 不等于全部人工验收通过。

起始提交 `29b1bb80a69c1af72ea09643cb3d736c453dcbdb`；分支 `feat/pyharness-platform-web-v1`。最终源码以本地提交及仓库外交付 manifest 识别，本轮不 push/PR/远程 CI。

## 实际结果

- 完整 pytest：2724 collected，2683 passed，41 skipped，0 failed；Coverage 84.13%，门禁75%。跳过原因保留在 JSON；未将历史 CI 或 Docker 跳过算成本轮真实验收。
- Chrome：12 页面、36 张截图、0 未捕获 JS 错误；真实页面与 API、合成模型。WebView2 和 Qt source/安装入口、正常关闭均过；物理人工验收未完成。
- 新 wheel/sdist、base/native/desktop 安装、HTTP/隐藏 WebView/Native offscreen、CLI/ACP、7 个离线主题资源与 MIME 均通过。精确最终包哈希在仓库外 SHA256SUMS。
- 两位只读 reviewer 的2 High、3个去重Medium已修复。全量另发现四项失败，分别修正唯一动态入口检查、Provider获取边界和缺模型零副作用，最终全量通过。

## 真实模型、Docker 和外部服务

shidongai / deepseek-v4-flash-0731：第一次1请求因路径契约取消，之后新Run4请求完成。累计5请求、11401输入Token、1010输出Token；成功Run耗时26.634秒。8次审批中5次对应模型工具，3次为受控操作者的Patch应用与Artifact导出。实际观察waiting_approval，pytest 1 passed，只修改calculator.py，原始sample-source不变，目标仅批准后应用。

Patch SHA256：`492b77287b272429e509272a5adfe24b28e25a3b1e062ef4155b4d4ba7d842d9`。测试报告 SHA256：`e7cbdfa876e00ddac2d46b2fc48f3feb8a92f62527c4dcaba11309befc210448`。

真实Docker使用现有固定本地镜像、Linux Engine 29.6.2；non-root、非privileged、无Docker socket、cap-drop ALL、只读根、私有工作区、默认断网、CPU/内存/PID/超时/输出及父子进程清理已用实际容器验证。未回退宿主。

The successful Run used 4 requests; earlier cancelled Run used 1. Five model-originated tool calls and three operator artifact actions. No additional model requests for final regression, packaging or recovered screenshots. Live test predates final tighten-only registry/early-readiness changes; those changes have deterministic and full regression coverage, not a repeated paid run.

旧run-completed/restart-restored截图使用了无效tasks路由，保留原件但不作为完成证据。另以0新模型请求只读恢复Run、51条Trace、9个Step和2个Artifact，补拍正确页面5张；没有伪造运行中画面。

RAG为真实固定0.2.0离线NGram/extractive服务，验证入库、授权引用与行号、无证据、删除及重启；不是BGE或复杂文档语义质量验收。evleven固定1.1.0的16项已执行，MCP实际stdio但planner确定性；KV验证的是持久键值与权限。

## 34 卡逐项结果

|卡号|内容|状态|实际影响|剩余限制|
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

## 逐卡文件与证据索引

以下证据路径均相对于独立检查点，仅是索引；不把本机文件链接当作公开下载。详细起始行为、变化、环境、命令和结果另见同名 JSON。

### B01 基线与范围冻结

起始行为：旧快照不能代表本次代码

影响文件：`CURRENT-IMPLEMENTATION-STATUS.md`。

命令标签：`final-full-v2`。

证据：`START-STATE.json`。

### B02 团队记忆成员委派政策

起始行为：Agent可绕过团队记忆只读政策

影响文件：`pyharness/application/accounts.py`, `pyharness/application/service.py`, `pyharness/engine.py`。

命令标签：`p1-green`, `final-full-v2`。

证据：`evidence/B24/junit-full-v2.xml`。

### B03 知识问答无证据边界

起始行为：无证据只靠提示词约束

影响文件：`pyharness/application/platform_models.py`, `pyharness/application/platform_runtime.py`, `pyharness/core/orchestration.py`, `pyharness/core/agent_loop.py`。

命令标签：`review-child-evidence-green`, `final-full-v2`。

证据：`evidence/B15/integrated-v2/result.json`。

### B04 清空备用模型配置

起始行为：空列表不能清空fallback

影响文件：`pyharness/core/tenant_settings.py`。

命令标签：`p1-green`, `final-full-v2`。

证据：`evidence/B24/junit-full-v2.xml`。

### B05 空请求体发布 Agent

起始行为：空请求体发布错误

影响文件：`pyharness/desktop/platform_api.py`。

命令标签：`p1-green`, `browser-final`, `final-full-v2`。

证据：`evidence/B17/browser-final/browser-summary.json`。

### B06 自定义审批字段的有效契约

起始行为：approval_rules可保存但未生效

影响文件：`pyharness/application/approval_policy.py`, `pyharness/core/tools_registry.py`, `pyharness/application/platform_service.py`。

命令标签：`final-regression-targeted`, `final-full-v2`。

证据：`evidence/B24/regression-junit.xml`。

### B07 状态文档与七个无效配置

起始行为：七项配置存在静默无效值

影响文件：`pyharness/config.py`, `docs/CFG.md`, `docs/architecture/platform-web-v1.md`。

命令标签：`p1-green`, `final-full-v2`。

证据：`evidence/B24/junit-full-v2.xml`。

### B08 文件和图片四条路径契约

起始行为：附件预览/输入路径边界不明确

影响文件：`pyharness/application/vision.py`, `pyharness/application/platform_service.py`。

命令标签：`p2-contracts`, `browser-final`, `final-full-v2`。

证据：`evidence/B17/browser-final/theme-attachment-summary.json`。

### B09 聊天图片缩略图与附件操作

起始行为：附件摘要与草稿生命周期不完整

影响文件：`pyharness/ui/platform.html`, `scripts/platform_theme_contract.py`。

命令标签：`browser-final`, `final-full-v2`。

证据：`evidence/B17/browser-final/theme-attachment-summary.json`。

### B10 图片进入模型的能力决策

起始行为：图片没有正式模型能力闸

影响文件：`pyharness/application/vision.py`, `pyharness/core/llm.py`, `pyharness/application/platform_runtime.py`。

命令标签：`review-regressions-green`, `final-full-v2`。

证据：`evidence/B24/junit-full-v2.xml`。

### B11 已有工具与 API 的 Web 入口选择

起始行为：部分已有工具缺Web入口

影响文件：`pyharness/desktop/platform_api.py`, `pyharness/application/platform_service.py`, `pyharness/ui/platform.html`。

命令标签：`p3-entry-roles`, `browser-final`, `final-full-v2`。

证据：`evidence/B17/browser-final/browser-summary.json`。

### B12 父子任务与异步编排契约

起始行为：子任务权限和取消范围需收紧

影响文件：`pyharness/core/subagent.py`, `pyharness/core/orchestration.py`, `pyharness/core/scope.py`。

命令标签：`review-child-evidence-green`, `final-full-v2`。

证据：`evidence/B24/junit-full-v2.xml`。

### B13 本机真实 isolated 与文件资源边界

起始行为：本机Docker环境证据缺失

影响文件：`tests/unit/test_platform_path_contract.py`。

命令标签：`docker-local`, `docker-extended`。

证据：`evidence/B13/docker-local.json`, `evidence/B13/docker-extended.json`。

### B14 真实模型受治理代码变更闭环

起始行为：首次模型把容器绝对路径作为工具路径，运行取消

影响文件：`pyharness/application/platform_runtime.py`, `pyharness/core/tools_executor.py`, `pyharness/core/system_prompt.py`。

命令标签：`b14-relative-real`, `final-full-v2`。

证据：`evidence/B14/relative-contract-run/real-model-result.json`, `evidence/B14/relative-contract-run/recovery-screenshots/summary.json`。

### B15 外部 RAG 的真实兼容与无证据处理

起始行为：固定RAG服务返回嵌套协议，与现有query不兼容

影响文件：`pyharness/core/knowledge.py`, `pyharness/application/platform_service.py`。

命令标签：`b15-real-integrated-v2`, `final-full-v2`。

证据：`evidence/B15/integrated-v2/result.json`。

### B16 MCP KV 和 evleven 的独立验收

起始行为：固定evleven依赖尚未实际集成验收

影响文件：`scripts/evleven_integration.py`, `scripts/evleven_r1_server.py`, `tests/integration/test_evleven_r1.py`。

命令标签：`real-evleven`, `final-full-v2`。

证据：`evidence/B16/evleven-junit.xml`。

### B17 Web 真实交互补验

起始行为：新Web行为缺当前浏览器证据

影响文件：`scripts/platform_browser_smoke.py`, `scripts/platform_theme_contract.py`。

命令标签：`browser-final`。

证据：`evidence/B17/browser-final/browser-summary.json`。

### B18 桌面 WebView 壳人工验收

起始行为：WebView主题与生命周期缺验证

影响文件：`pyharness/desktop/bridge.py`, `pyharness/desktop/launcher.py`。

命令标签：`webview-source-entry-v2`, `package-final`。

证据：`evidence/B18/source-entry.json`。

### B19 Native Qt 人工验收

起始行为：Native缺对应主题与动效策略

影响文件：`pyharness/desktop_native/appearance.py`, `pyharness/desktop_native/main_window.py`。

命令标签：`native-source-entry`, `package-final`。

证据：`evidence/B19/source-entry.json`。

### B20 CLI 与 ACP 分开验收

起始行为：CLI和ACP结果不能混称

影响文件：`pyharness/cli.py`, `pyharness/acp.py`。

命令标签：`installed-protocols-v2`, `final-full-v2`。

证据：`evidence/B20/installed-protocols.json`。

### B21 正常重启 回放与错误恢复

起始行为：正常恢复与断电保证容易混称

影响文件：`pyharness/core/orchestration.py`, `pyharness/ui/appearance.py`。

命令标签：`b14-relative-real`, `b15-real-integrated-v2`, `installed-protocols-v2`, `final-full-v2`。

证据：`evidence/B14/relative-contract-run/real-model-result.json`, `evidence/B20/installed-protocols.json`。

### B22 性能与资源的有界测量

起始行为：没有当前有界性能数据

影响文件：`pyharness/ui/theme-kit/particles/engine.js`。

命令标签：`bounded-performance`, `performance-followup-v2`, `trace-performance`。

证据：`evidence/B22/performance.json`, `evidence/B22/followup.json`, `evidence/B22/trace-performance.json`。

### B23 本地账户权限与 SaaS 边界

起始行为：本地账户容易误称完整SaaS

影响文件：`pyharness/application/accounts.py`, `pyharness/desktop/accounts_api.py`, `docs/security/approval-and-memory-policy.md`。

命令标签：`p1-green`, `p3-entry-roles`, `final-full-v2`。

证据：`evidence/B24/junit-full-v2.xml`。

### B24 汇总验证 打包与交付判断

起始行为：需对最终源码重新收口

影响文件：`reports/PYHARNESS-34-CARD-IMPLEMENTATION.md`。

命令标签：`final-regression-targeted`, `final-full-v2`, `package-final`, `installed-assets`, `installed-protocols-v2`, `safety-final-v2`, `static-final`。

证据：`evidence/B24/junit-full-v2.xml`, `validation/artifacts/package-summary.json`, `evidence/B24/installed-assets.json`, `evidence/B24/safety-scan.json`。

### U01 范围与模块契约定版

起始行为：主题组件与宿主边界未定

影响文件：`docs/architecture/theme-kit.md`, `pyharness/ui/theme-kit/appearance/controller.js`。

命令标签：`browser-final`, `final-full-v2`。

证据：`evidence/B17/browser-final/theme-attachment-summary.json`。

### U02 主题 tokens 与可读性基线

起始行为：主题变量分散

影响文件：`pyharness/ui/theme-kit/appearance/controller.js`, `pyharness/ui/theme-kit/contracts/preferences.js`, `pyharness/ui/theme-kit/particles/engine.js`, `pyharness/ui/theme-kit/styles/theme.css`, `pyharness/ui/theme-kit/tokens/themes.js`。

命令标签：`browser-final`。

证据：`evidence/B17/browser-final/appearance-dark.png`, `evidence/B17/browser-final/appearance-light.png`。

### U03 接入真实 Platform 静态首版

起始行为：科技蓝尚未接真实页面

影响文件：`pyharness/ui/platform.html`。

命令标签：`browser-final`。

证据：`evidence/B17/browser-final/dashboard.png`。

### U04 纯 Canvas 粒子引擎

起始行为：没有正式粒子引擎

影响文件：`pyharness/ui/theme-kit/appearance/controller.js`, `pyharness/ui/theme-kit/contracts/preferences.js`, `pyharness/ui/theme-kit/particles/engine.js`, `pyharness/ui/theme-kit/styles/theme.css`, `pyharness/ui/theme-kit/tokens/themes.js`。

命令标签：`browser-final`, `final-full-v2`。

证据：`evidence/B22/performance.json`。

### U05 动效偏好与可见性策略

起始行为：动效偏好和生命周期未统一

影响文件：`pyharness/ui/theme-kit/appearance/controller.js`, `pyharness/ui/theme-kit/contracts/preferences.js`, `pyharness/ui/theme-kit/particles/engine.js`, `pyharness/ui/theme-kit/styles/theme.css`, `pyharness/ui/theme-kit/tokens/themes.js`。

命令标签：`browser-final`, `webview-source-entry-v2`, `final-full-v2`。

证据：`evidence/B17/browser-final/theme-attachment-summary.json`。

### U06 注入式 PreferenceStore

起始行为：偏好存储依赖页面

影响文件：`pyharness/ui/appearance.py`, `pyharness/desktop/bridge.py`, `pyharness/ui/theme-kit/contracts/preferences.js`。

命令标签：`browser-final`, `webview-source-entry-v2`, `final-full-v2`。

证据：`evidence/B18/source-entry.json`。

### U07 离线资源与安装包

起始行为：安装包中离线资源需确认

影响文件：`pyharness/desktop/app.py`, `pyharness/ui/theme-kit`, `pyharness/ui/theme-kit/appearance/controller.js`, `pyharness/ui/theme-kit/contracts/preferences.js`, `pyharness/ui/theme-kit/particles/engine.js`, `pyharness/ui/theme-kit/styles/theme.css`, `pyharness/ui/theme-kit/tokens/themes.js`。

命令标签：`package-final`, `final-full-v2`, `installed-assets`, `installed-protocols-v2`, `safety-final-v2`, `static-final`。

证据：`validation/artifacts/package-summary.json`, `validation/artifacts/package-summary.json`, `evidence/B24/installed-assets.json`, `evidence/B24/safety-scan.json`。

### U08 WebView 与可选经典页适配

起始行为：WebView/classic未共享主题

影响文件：`pyharness/ui/index.html`, `pyharness/ui/host-adapters`。

命令标签：`browser-final`, `webview-source-entry-v2`。

证据：`evidence/B17/browser-final/appearance-classic.png`, `evidence/B18/source-entry.json`。

### U09 可选 Native Qt 主题适配

起始行为：Native主题无对应路径

影响文件：`pyharness/desktop_native/appearance.py`。

命令标签：`native-source-entry`, `final-full-v2`。

证据：`evidence/B19/source-entry.json`。

### U10 主题业务保持与复用验收

起始行为：复用与业务保持缺证据

影响文件：`scripts/platform_theme_contract.py`。

命令标签：`browser-final`, `bounded-performance`, `final-full-v2`。

证据：`evidence/B17/browser-final/theme-attachment-summary.json`, `evidence/B22/followup.json`。

## 性能与限制

历史搜索100会话查询上限50条；首次报告误读results字段，已复核实际hits。1000条合成Trace验证返回最新500条，投影约7.4–10.9ms；50个Artifact和4个Run列表约14ms。并发4个合成Run提交约558ms，不等于真实供应商并发时延。3秒粒子模式renderer任务时间约0.188秒，关闭/低动效约0.004–0.005秒。零帧样本不引用上次RAF间隔。不是整机CPU、RSS、真实屏幕FPS或功耗保证。

## 保护、保留记录和清理

没有修改原始main或RC；分支、HEAD、状态清单与起始相同。未保存受保护文件的起始全量哈希，故不作逐字节取证保证。未读取/导出真实API Key；原模型连接仅由正常运行时解析；未向报告、Artifact或截图写入Key。没有远程写入、合并、tag、Release或公网部署。

本次新增合成Agent/Run/审批/Artifact平台记录保留在已授权隔离数据目录；Session日志、合成项目和截图保留在检查点。建议通过平台正常界面按本轮记录ID归档/删除，不删除整个数据目录或现有连接。可保留固定本地Docker镜像；不要用全局prune清理。精确记录和镜像大小见仓库外资源摘要。

## 后续优化可行性（本轮没有继续实施）

|方向|是否可做|依据|条件或限制|
|---|---|---|---|
|增量历史搜索索引|可行，建议后续|每次重建100会话索引；当前约50–256ms|需继续保持租户/会话授权和删除一致性；本轮不引入第二真源|
|Run/Artifact分页与索引|可行，先扩大有界样本|4 Run与50 Artifact列表约14ms，尚无大规模结论|先测更大但有限数据；不据小样本改成分布式|
|更严格图片解码与解压上限|可行，单独安全任务|当前验证文件结构/大小/像素声明|需要固定解码依赖和畸形图像测试；不冒充已完成|
|真实视觉供应商验收|当前不可完成|shidongai未声明supports_vision|需要用户确认受支持的模型能力及独立调用预算，不自动切换供应商|
|物理IME/高DPI/触屏验收|需设备人工操作|本轮Chrome模拟事件、隐藏WebView、Qt offscreen|保留B17–B20 partial；不能用截图或单元测试代替|
|粒子更低CPU与设备档位|可行，先采样|3秒renderer task正常约0.188s、静态约0.004–0.005s|不是整机功耗；可后续在真实低端设备测FPS再调参|
|RAG向量/复杂文档质量|可行但另行验收|本轮固定0.2.0使用NGram/extractive|需固定embedding模型、语料和评价集；PDF/DOCX/BGE未验|
|CLI第三方ACP客户端兼容|可行，待指定客户端|真实stdio协议已过，外部客户端未测|不新增不存在的cancel方法；保持当前协议|
|生产SaaS、HA、云Worker|不属于本轮可交付能力|仅本地账户/团队/运行时|需要独立架构和安全评估；不应把本轮结果扩大为生产认证|
