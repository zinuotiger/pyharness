# ADR-036：受控浏览器通过原 MCP 工具链接入

状态：S17 实现中，须通过完整阶段门后才验收。

用户已授权总计划 S17 的真实浏览器工作台。因此仅将既有架构中“浏览器留待后续”的排除范围增量扩展为 docs/specs/controlled-browser.md 所述边界，保留旧 ADR 的历史内容和其余排除项。

采用独立打包 MCP server，复用 managed MCP 配置、固定 Agent 版本、原任务队列、Guard 审批及 EffectRecorder。避免第二执行权威和第二收据库。浏览器动作属于宿主且有外部效果，原高危险审批保持；配置与发现不代表动作授权。

使用原 ProcessOwner Windows JobObject，在 Node/Chrome 启动前建立所有权。页面观察和动作引用具会话/代次绑定；超时永久丢弃旧载体。首版 Windows-only，其他平台 fail closed，后续必须以实际进程清理证据扩展。

拒绝复用用户已登录浏览器、任意 evaluate、把 web.fetch 当作浏览器、自动批准浏览器动作、应用层获取 Provider 或用虚构模型结果代替实际工具执行。代价是显式本地依赖配置、动态页面可能需要频繁重新观察、仅支持有限顶层 DOM 操作。
