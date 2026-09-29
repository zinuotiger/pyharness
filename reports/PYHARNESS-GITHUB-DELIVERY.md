# PyHarness 当前成果 GitHub 交付

起始提交：`42c0307866696aa2ea7915b9defa051d45ac64d6`。功能分支：`feat/pyharness-platform-web-v1`。已有 [Draft PR](https://github.com/zinuotiger/pyharness/pull/2) 以 `codex/pyharness-engineering-rc-20260924` 为基线。本报告随交付提交保存；最终 commit、workflow、包 SHA256 的对应清单置于仓库外，避免报告自引用产生无限新提交。

## 交付变化

- 聊天默认布局、草稿/IME/SSE 保留、持久失败提示、只读模型准备状态与可追踪 Run。
- 本机独立 Web 入口；真实账户与团队登录、角色和资源隔离；保留 CLI、classic 和 native 入口。
- 受治理子任务失败传播、独立审批身份、持久历史；后台工作流、团队记忆及关闭恢复。
- 显式单次、64 输出 Token、60 秒的模型连接测试；自动验证仅使用合成适配器和受控本机 HTTP 服务。
- 浏览器契约、账户/恢复测试、运行指南及现有成果的公开状态整理。

## 事实修正

服务在 2026-09-28 已恢复启动，独立启动复测成功。此前工具策略拒绝的具体触发规则未查明，不再作为持续阻断。用户报告已在 Web 完成模型配置；本轮不读取用户配置、Key 或运行数据，不调用供应商，不能据此将完整真实模型闭环标为通过。

## 最终候选验证

2026-09-29 对当前候选重新执行完整 pytest：2596 passed、41 skipped，Coverage 83.69%，通过 75% 门禁。12 个页面的浏览器 smoke 保存 31 张截图，连续功能验收另保存 19 张截图，均无未捕获 JavaScript 错误；覆盖审批、子任务、Artifact、Patch、账户隔离和重启恢复，模型响应均为合成或本机受控服务。前端错误契约、Python 语法、授权 AST、敏感信息及公开路径扫描通过。wheel/sdist 构建、独立 base/native/desktop/web 安装、入口预检、隐藏 WebView smoke 通过；已安装 Web 包使用合成数据完成两次启动和正常关闭，HTTP、Session、Artifact SHA256、团队记忆及账户恢复通过。归档内容扫描和源码与 wheel 逐字节对照通过。包 SHA256 与最终提交对应关系记录于 PR 和仓库外交付清单。最终远程 CI 只认本次 head 的正式 pull_request workflow；推送前不预先宣称通过。

## 限制

本地模型加真实 Docker 的人工闭环、外部语义 RAG、第三方 MCP、公网 TLS/身份系统、真机触摸和跨机器凭据恢复未在本轮验收。PTY 未开放。原有 Ubuntu 真实 Docker CI 保留，其确定性模型不能冒充真实供应商。所有测试数据和构建产物位于仓库外；无模型 Key、密码、用户数据库、会话或私有检查点进入提交。PR 保持 Draft，不合并、不打标签、不创建 Release。
