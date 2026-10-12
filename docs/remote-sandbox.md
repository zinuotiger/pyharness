# 独立远程 Docker 执行服务

服务模块：`pyharness.remote_sandbox`。客户端：`pyharness.core.remote_sandbox.RemoteSandboxBackend`。用户电脑不需要 Docker；执行服务所在电脑必须有可用的 **Linux Docker 引擎** 和 `python:3.13-slim` 镜像。没有引擎或镜像时拒绝执行，不降级到宿主。

## 同机验证与启动

安装当前项目的 Web 依赖：`python -m pip install -e ".[web]"`。这不需要配置付费模型。

为服务准备独立目录和受保护的账户文件，例如 `D:\pyharness-worker\accounts.json`：

```json
{"user-one":"在这里填写至少32字符的随机令牌，每个账户不同"}
```

请使用密码生成器生成令牌；文件只允许服务操作者读取。Windows 应设置目录 ACL，Linux 设置目录 0700、文件 0600。不要把令牌放进命令行、Git、截图或聊天内容。客户端密钥只在聊天权限弹窗的远程连接表单中填写；存于租户独立文件，Windows 使用 DPAPI，POSIX 使用 0600。

```powershell
python -B -m pyharness.remote_sandbox --data-dir D:\pyharness-worker\data --accounts-file D:\pyharness-worker\accounts.json --host 127.0.0.1 --port 60500
```

聊天窗口 → 权限 → 远程沙盒 → 配置远程服务，填写 `http://127.0.0.1:60500` 和对应账户令牌，保存并检查，返回原助手表单选择连接。创建 / 发布助手之后发送任务。

## 跨网络部署

非回环监听必须提供 TLS 证书和私钥，客户端必须使用证书链有效、主机名匹配的 HTTPS 地址。客户端不关闭证书校验，不跟随重定向，不读取系统 HTTP 代理凭据。示例路径只是占位，不代表已部署：

```text
python -m pyharness.remote_sandbox --data-dir /srv/pyharness-worker/data --accounts-file /srv/pyharness-worker/accounts.json --host 0.0.0.0 --port 60500 --ssl-certfile /etc/pyharness/fullchain.pem --ssl-keyfile /etc/pyharness/privkey.pem
```

仅运行一个 worker 进程管理同一 data-dir；不要使用多个 uvicorn workers。防火墙限制可访问客户端。账户与令牌由服务操作者分配；共享一个令牌就代表共享同一远程账户，不能用于区分不同用户。服务重启会清理该服务目录所属的旧容器，不恢复中断代码或自动重跑。

真正部署前需要提供：服务器操作系统与 Docker 条件、域名与 TLS 证书方案、访问端口 / 防火墙、账户划分和资源预算、部署授权。本次未向外部服务器部署。

## 执行边界与配额

- 每个任务独立工作区、非 root、只读根文件系统、无网络、无 Docker socket、无宿主 HOME / 项目挂载；只挂任务私有副本。
- CPU 默认 1、内存 256 MB、PID 64、执行超时 60 秒、输出上限 64 KiB。客户端配置范围受模型字段限制，服务镜像白名单固定为 `python:3.13-slim`。
- 每个账户最多 4 个有效工作区，文件传输总计最多 10 MiB、500 文件、1000 目录项，HTTP 请求体上限 15 MiB。工作区磁盘大小每两秒检查，超限销毁；**这不是硬磁盘配额**，对不可信多租户生产部署仍需服务器文件系统 / 容器存储层硬配额，防止检查间隔内写满磁盘。
- 工作区默认保留 15 分钟、最长一小时；客户端正常完成或取消会提前删除。失联时依靠服务端代码超时及租约清理，取消请求在网络彻底断开时无法立即送达。
- 容器清理失败会保留可恢复记录，不能宣称已删除。服务重启仅清理 owner 标签匹配当前服务目录的资源。
- 每个远程任务绑定认证账户；不同账户读取、写入、执行或取消该任务均返回 404。
- 执行请求带唯一操作 ID，客户端不会因断线自动重新提交代码。输入和输出按严格相对路径传输，拒绝链接、设备、越界路径与过量文件。
- 执行记录存于服务 data-dir 的 `audit.jsonl`，含账户、任务、动作、参数哈希和状态，不含令牌 / 命令原文。日志约 5 MiB 轮转一次并保留上一份；任务元数据有数量上限，已删除任务一天后清理。

## 可重复本地验收

```powershell
python -B -m pytest tests/unit/test_chat_execution.py --no-cov
python -B scripts/chat_execution_acceptance.py --real-docker --output D:\zinuotiger-workspace\temporary-runtimes\chat-worker-new-test
```

第二条必须使用新的目录，会启动本地 HTTP 服务并调用真实 Docker，验证输入、中文输出、二进制产物、取消、超时以及显式本机执行；还用脚本测试模型跑通真实任务队列、独立自动审核、人工批准、远程执行和导出批准后的产物取回。第一条中的模型、审核器、隔离后端替身均在测试用例中标注。两者都不证明跨机器网络、生产 TLS、服务器硬磁盘配额或真实付费模型验收。
