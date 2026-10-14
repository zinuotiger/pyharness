# PyHarness 实施矩阵

本表记录本地实现及其证据边界，不表示所有真实人工验收完成。服务已恢复启动，用户报告已配置模型；真实模型完整闭环尚未复验。本轮获授权交付现有 Draft PR，最新验证和 CI 对应关系见 [交付报告](reports/PYHARNESS-GITHUB-DELIVERY.md)。

| 编号 | 功能 | 状态 | 本轮审查/验证证据 | 实际边界 |
|---|---|---|---|---|
| 01 | 原功能保留 | completed | 完整回归；兼容表保留 30 项壳层契约、经典/CLI/native 入口 | 原生 GUI 全部人工交互不由浏览器替代 |
| 02 | 子任务 | completed | 真实浏览器由合成模型调用 subagent.spawn，子队列完成并汇总；失败/取消/并发审批回归 | 模型为确定性替身；真实供应商分派质量未验 |
| 03 | 聊天主界面 | completed | 真实无配置实例复现后修复；常驻发送/运行反馈、可追踪 Run；12 页及 1440/1280/390 布局 | 390 为浏览器视口，非真机触摸 |
| 04 | 左下设置 | completed | 设置往返、所有管理入口、模型配置引导；保留草稿与附件归属 | 团队管理入口受服务端角色约束 |
| 05 | Enter/IME | completed | 真实浏览器 Enter/Shift+Enter/composition/229/repeat/重复 submit；30 秒真实超时保留草稿 | 系统输入法硬件体验未代验 |
| 06 | 模型连接 | partial | UI 保存 → profile → LLMClient → OpenAI-compatible Adapter → HTTP → 本机受控服务：1 次请求、64 tokens、温度 0、无工具；认证/超时/并发测试 | 用户报告已配置连接；本轮不读取配置、不调用供应商，真实模型完整闭环仍待验 |
| 07 | Session/Run | completed | 实际提交/队列/SSE/历史；未配置在落盘前拒绝；启动异常保留 failed Run；取消/重启恢复 | 无自动重放中断副作用任务 |
| 08 | 审批/反问 | completed | 真实 waiting_approval、答复、导出批准、写入拒绝、独立 Patch 应用批准与历史；强同步回归 | 重启保留历史，不重建旧可执行审批凭据 |
| 09 | Agent/工具/Skill/插件 | completed | 浏览器创建/发布/编辑/v1 不变/v2 差异/废弃/历史；原 Skill/插件安装、哈希、回滚与生命周期回归 | 第三方市场未验；团队不开放宿主插件执行 |
| 10 | 知识库/RAG | partial | 真实页面上传、关键词命中引用、无证据拒答；替换/删除/重启/远端失败回归 | 外部语义 RAG、rag-engine、BGE 服务未配置，未声称通过 |
| 11 | MCP/记忆 | partial | 本地 MCP 协议既有证据保留；完整回归覆盖记忆/跨会话/持久化；浏览器团队写读与跨团队不可见 | 第三方 MCP 环境未配置；不将本地协议通过扩大为所有服务兼容 |
| 12 | 计划/后台/工作流 | completed | 页面创建/编辑/暂停/恢复/立即运行/执行历史/删除；真实队列完成；浏览器关闭后工作流仍完成；失败/幂等/重启回归 | 服务必须常驻；未启动付费周期任务 |
| 13 | Artifact/Patch | completed | 真实 UI 预览/导出审批/下载 SHA256；合成 manifest 单独应用审批，批准前目标不变，批准后最小变化，二次应用拒绝；部分失败回滚回归 | Patch 为显式合成夹具，非真实模型或 Docker 生成；只修改仓库外验收工作区 |
| 14 | 本地 SaaS | completed | 双账户双团队 UI 创建/登录；会话/Artifact/Memory 隔离；实际 owner 模型保存/替换、member 新增/替换拒绝、跨团队模型列表隔离；角色恢复 viewer；配额/CSRF/owner 回归 | 公网 TLS/代理/外部身份系统、强对抗多租户未验；UI 删除确认受工具限制，API 清理不等同 UI 删除通过；无支付 |
| 15 | 用量/日志/状态 | completed | 实际 Run/Trace/用量与健康页面；错误失败终态与脱敏回归；缺失用量仍 unknown | 本轮用量是合成测试数据，非真实供应商账单 |
| 16 | PTY 评估 | completed | 保留底层评估和启用前置条件 | 仅评估完成；PTY 仍禁用 |
| 17 | 安装/恢复 | completed | 当前源码回归与同数据目录恢复通过；候选安装包/HTTP/隐藏 WebView/两次正常停服证据由外部 package-summary、web-lifecycle-summary、installed-ui 记录 | 仅当前 Windows 环境；跨系统 DPAPI、跨机与热备不在已验证范围 |
| 18 | 远程交付 | partial | 本轮已授权整理、普通提交、推送及更新已有 Draft PR | 以本次最终提交的正式 PR workflow 为准；提交前不预先宣称远程通过 |
| 19 | Docker | deferred | 本地真实模型加容器闭环延期；保留正式 Ubuntu 真实 Docker CI | CI 使用确定性模型，与人工供应商闭环区分；无 host_approved 替代 |

历史本地回归（不是本次最终候选证明）：2596 passed / 41 skipped，Coverage 83.69%。真实 Chrome 12 页、31 图；连续页面闭环 19 图。模型/生成 Patch 为明确合成测试，供应商请求 0。

14 completed、4 partial、Docker deferred；completed 只覆盖表中限定本地证据，PTY 仅评估。真实模型、外部 RAG/MCP、公网环境与远程交付不宣称完成。本轮允许普通 commit/push 和 Draft PR 更新，不合并或发布，不修改 main/RC，不覆盖旧实例/历史证据。
