# PyHarness 六种动态场景皮肤验收

工程检查已通过下述范围；视觉目标等待用户查看后确认。既有 **29 completed / 5 partial** 保持不变。

基线 `1235dd0f492007de89077de81f42d555c0eb059d`，分支 `feat/pyharness-platform-web-v1`。结果对应当前未提交工作树与外部 source-manifest，不是该旧 SHA 已包含本次功能的声明。本轮不 commit、不 push、不改 PR。

## 实现与参考差异

| 场景 | 实现 | 与参考图的差异 |
|---|---|---|
| 星空 | 旋涡银河背景；分层闪烁星光和缓慢轨道运动 | 银河主体为静态素材，运动来自前景星光与轨道；没有把整张参考 UI 用作贴图。 |
| 樱花 | 花枝与粉紫暮光；大小、摆动、旋转不同的下落花瓣 | 生成背景增加了远山、落日与建筑剪影，构图并非参考图逐像素复制。 |
| 极光 | 山湖背景；三层渐变流动光幕和星点 | 山水和主极光素材静态，程序光幕缓慢变化；未实现物理水面模拟。 |
| 金色 | 黑金背景；三条金砂波浪与流动轨迹 | 粒子河流和细线运动，不是参考中全部金色光点逐一模拟。 |
| 海洋 | 水下光束、水母背景；上升气泡和三只脉动水母 | 背景中的大型水母静态；额外小水母和气泡动态，无流体模拟。 |
| 科技 | 发光网络球背景；旋转投影球面节点与局部连线 | 保留已有主题 ID；新球面前景为程序化投影，背景地球纹理静态。 |

复用 theme-kit、appearance controller、浏览器/宿主偏好存储。保留浅色、深色、已有默认主题和已有选择。单个背景图与单个 Canvas 位于内容后方，pointer-events:none；聊天区域透明、消息及操作控件较深、对话框保持实底，外观变化不改变业务布局和授权。只扩展包内图片 MIME，不放宽静态路径边界。

六张背景均由本任务内置图像生成能力分别生成，随 wheel/sdist 提供；没有运行时 CDN、字体或外部图片依赖。来源及 SHA256 见 `pyharness/ui/theme-kit/scenes/provenance.json`。

## 验证

- 完整 pytest：2697 passed、41 skipped、0 failed；Coverage **84.13%**，门禁 75%。跳过保留原有平台/显式集成条件，未新增跳过以掩盖失败。
- 现有 12 页面浏览器回归通过，0 未捕获 JavaScript 错误；业务往返是确定性测试模型。
- 六套分别通过真实页面切换、草稿/附件保留、刷新偏好恢复、normal/low/off、系统 reduced-motion、宿主暂停、受控 document hidden、图片失败和引擎失败的静态回退。
- 每套聊天、设置、合成审批卡片截图；1440×900、1280×900、390×900；每套三个实际连续帧及 Canvas 哈希证明动画变化。审批 fixture 仅验证展示，不声称真实审批执行。
- 24 次切换后保持单 Canvas；4 轮 GC 后事件监听器均 69、DOM 节点均 344、document 均 1；有限样本内堆未持续增长。额外多实例替换/销毁检查通过。
- Python AST、JS/内联脚本语法、授权 AST、git diff 检查、改动文件敏感模式扫描通过。未访问凭据存储。
- wheel/sdist 包含六张完整 PNG；base/native/desktop 安装 smoke、HTTP 和隐藏 WebView smoke 通过；安装后的六张图片均 HTTP 200、正确 image/png。Native smoke 不等于 Native 六皮肤验收。

## 有界性能

单 Canvas 最多 240 万像素、DPR 最多 2、目标上限约 30fps，默认 48 / 上限 80 个种子。金色三层最多 240 个粒子、极光三条固定采样曲线、海洋最多三只小水母、科技每节点最多四条邻接线。六张背景约 12.45 MB。

| 场景 | 采样秒 | Renderer task/时间 | 帧间隔中位数 ms | P95 ms |
|---|---:|---:|---:|---:|
| starfield | 2.64 | 7.17% | 33.3 | 33.6 |
| sakura | 2.34 | 6.31% | 33.3 | 33.5 |
| aurora | 2.5 | 6.98% | 33.3 | 33.5 |
| gold | 2.34 | 5.78% | 33.3 | 33.5 |
| ocean | 2.3 | 6.68% | 33.3 | 33.4 |
| tech-blue-particle | 2.17 | 6.92% | 33.3 | 33.4 |

TaskDuration 包含测试交互/截图与脚本，非全系统 CPU 使用率；JS 堆不包含所有图片/GPU 内存。没有用该短样本宣称长期无泄漏。

## 修复和保护

验收脚本早期修正了刷新时模块未初始化、合成消息事件类型和 Windows 浏览器发现环境问题。多次主动断连触发过 Python Proactor 10054/服务收尾超时；最终测试使用 Connection: close 使各受控 HTTP 请求不复用持久连接，最终脚本退出 0、stderr 为空。未修改产品服务以规避这些脚本问题。

原有未提交账号/启动成果保留；页面后续增加了另一个窗口的顶部切换入口，已单独留存并重新核验。`desktop/app.py` 相对本轮检查点仅新增 PNG MIME。main、RC、RAG、evleven 的 branch/HEAD/status 与起始快照一致，本轮未在这些目录写入。无模型 Key 读取、无真实模型请求、未停止原服务。

## 复现与限制

在已有测试环境运行 `python -B scripts/platform_scene_smoke.py --output-dir <仓库外目录>`，使用已安装 Chrome/Edge，不下载浏览器。脚本只创建合成数据，阻止非回环访问。完整测试通过已有 `scripts/ci_verify.py _pytest` 入口运行；所有测试数据、覆盖率及安装产物在仓库外。

预览入口与证据绝对路径由本地交付清单提供；设置 → 外观可切换六套皮肤。

- 用户尚未确认六套视觉效果；不改变既有 29 completed / 5 partial。
- 浏览器 Chrome 桌面自动化覆盖 1440×900、1280×900、390×900；窄屏是模拟视口，不是真实手机验收。
- 隐藏暂停验证为 document.visibilitychange 的受控状态和宿主 visibility 接口；尚未做真实最小化/多显示器人工验收。
- 低动效沿用现有静态降级语义，和关闭均停止 Canvas RAF；背景仍显示。
- WebView 只执行现有隐藏页面 smoke 与偏好存储测试；没有逐套 WebView 视觉或 Native Qt 六场景验收。
- 性能为每套约 2–3 秒的有界 Chrome 测量，包含截图开销；不代表低端设备、GPU 内存、电池或长期稳定性。
- 本轮没有真实模型、Docker 容器、外部 RAG/MCP 或其他项目人工验收；浏览器业务回归使用确定性假模型，场景审批卡片是明确标识的合成 fixture。

## 本轮修改文件

- `pyharness/ui/theme-kit/tokens/themes.js`：六场景注册与原选择兼容。
- `pyharness/ui/theme-kit/appearance/controller.js`：背景加载、静态回退和生命周期。
- `pyharness/ui/theme-kit/particles/engine.js`：单 Canvas、帧率/像素限制及所有权清理。
- `pyharness/ui/theme-kit/particles/scenes.js`：六种独立动态绘制算法。
- `pyharness/ui/theme-kit/styles/theme.css`：玻璃面板、控件对比度与预览卡片。
- `pyharness/ui/host-adapters/platform.js`：复用现有设置入口，增加六张选择卡。
- `pyharness/ui/appearance.py`：WebView 文件偏好允许新场景，Native 默认白名单不变。
- `pyharness/desktop/app.py`：相对本轮起始副本只添加 PNG MIME。
- `pyharness/ui/theme-kit/scenes/`：六张离线 PNG 与 `provenance.json`。
- `tests/unit/test_appearance.py`：场景偏好持久化、Native 边界及六图片 HTTP/MIME 检查。
- `scripts/platform_scene_smoke.py`：有界浏览器验收与连续帧证据。
- `reports/PYHARNESS-SIX-SCENES.md`、`.json`：本报告。

其他起始未提交文件不是本轮新增业务实现，已按原始字节保留；未合并或覆盖其他任务改动。

## 同工作区后续变更核对

另一个“运行这个程序”窗口在最终校验期间增加顶部“界面皮肤”下拉框，涉及 platform.html、platform.js、theme.css 和 docs/user-guide/appearance.md。本轮未覆盖它们；独立保存 concurrent-0911 快照，补充顶部与设置同步、深色切回各场景、1280/390 宽度草稿保留检查，并重新采集全部六场景证据，结果通过。顶部入口证据在 screenshots-final-entry；最终亮度版本证据在 screenshots-brightness。完整 pytest 后生产 Python 未再变化；前端补充检查覆盖此后入口变更。另一窗口随后完成了 CSS 亮度与遮罩调整。保留其文件并再次执行六套场景检查、三种视口、顶部同步/切回、草稿附件、连续帧及资源生命周期检查，全部通过；脚本检查了前后源码哈希不变。最终快照 concurrent-final，未覆盖其他窗口成果。
