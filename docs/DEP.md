# DEP — 部署与运行手册

> 类型: 运维手册 | 权威对齐: PRD-Core.md §4.6(各阶段演示)+ CFG.md(配置)
> 覆盖: 环境准备/安装/配置/6 阶段运行指南/CLI 参考/故障排查/备份/面试录屏台本
# DEP.md — PyHarness 部署与运行手册

> **类型**:部署/运维文档 = PRD-Core §4.6(六阶段里程碑演示)与 §5.7(F060-F066 会话周边/外壳)的运维展开;CLI 调用面以本文件与 specs/cli 为准(MAP 阶段6 引用 DEP.md)。
> **版本**:v1.0 | **日期**:2026-09-06 | **状态**:随代码阶段落地,本文描述最终态
> **读者**:许子诺 / 演示者 / AI 编码 Agent。**权威声明**:演示命令与 PRD §4.6.2 逐条对齐(§4.0 表);功能/事件/配置冲突以 PRD-Core 与 CFG.md 为准;PRD 未固定的 CLI 旗标由本文定义。

**10 章**:1 环境准备 · 2 安装步骤 · 3 配置准备 · 4 分阶段运行指南 · 5 CLI 命令参考 · 6 故障排查 · 7 数据位置与备份 · 8 卸载/清理 · 9 面试录屏演示脚本 · 10 关联测试

**命令环境约定**:全文命令在 **Windows 11 + git-bash(随 Git for Windows 安装)** 中执行;`~` = `C:\Users\<你的用户名>`;项目根 = `~/Desktop/mini-harness`。统一以 `uv run` 前缀调用(无需激活 venv),等效 PRD 中的裸 `pyharness` 命令。中文路径/文件在 git-bash 下原生支持 UTF-8。

---

# 1 环境准备

## 1.1 目标环境

| 项 | 要求 | 说明 |
|---|---|---|
| OS | Windows 11(x64),git-bash | 全能力无 WSL/Docker(N9);macOS/Linux 仅 CI smoke |
| Python | 3.11.x(uv 托管,§1.2) | 不要求系统预装 |
| uv | ≥0.5 | 见 §1.2 |
| 网络 | 可达 `https://api.deepseek.com` | 阶段1 起需要;代理/镜像 §1.4 |

## 1.2 安装 uv 与 Python 3.11

**方式 A(推荐,PowerShell)**:

```powershell
winget install --id=astral-sh.uv -e
```

**方式 B(git-bash 官方脚本)**:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"          # 本会话生效
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc   # 永久生效
```

安装脚本把 uv 放进 `~/.local/bin`(即 `C:\Users\<用户名>\.local\bin`)。若 `uv` 提示找不到,补 PATH 后**重开终端**再验证。

**验证 + 安装 Python 3.11**:

```bash
uv --version                 # 预期:uv x.y.z (如 0.5.x)
uv python install 3.11       # 下载官方 CPython 3.11 到用户目录,不影响系统 Python
uv python list               # 预期列表中含 3.11.x(标 * 为默认)
```

`pyproject.toml` 声明 `requires-python = ">=3.11"`,`uv sync` 时会自动挑选 3.11;显式 `uv venv --python 3.11` 亦可强制。

## 1.3 依赖清单(说明性;权威清单 = 仓库 `pyproject.toml` + `uv.lock`)

| 包 | 用途 | 最早阶段 |
|---|---|---|
| pydantic(v2) | 事件信封/配置 Schema 强校验(PRD §3.2) | 0 |
| openai | DeepSeek OpenAI 兼容客户端(F012),多适配器底座 | 1 |
| PyYAML | `config.yaml` 解析(F021) | 1 |
| httpx | openai 传输层;网页抓取复用(F038) | 3 |
| fastapi + uvicorn + pywebview | Desktop 壳:pywebview 窗口 + FastAPI 同进程单 worker(F065);前端原生 JS/Vue | 6 |
| pytest / pytest-cov | 验收测试与覆盖(N8) | 0(dev) |
| pygount | 代码量/注释占比检查(N6/N7,check_size.py) | 0(dev) |

SQLite(FTS/KV)、JSONL、asyncio、线程池全用标准库——运行期第三方 ≤15(N9),零 Node/Docker。

## 1.4 网络前置

- **LLM 端点**:`llm.base_url` 默认 `https://api.deepseek.com`,该请求**不受** `security.network.allowed_domains` 约束——后者只作用于**工具类外发**(web_search/抓取,默认空 = 全禁,阶段3 再放行)。
- **代理**(公司网):`export https_proxy=http://127.0.0.1:7890`(http_proxy 同)后再启 pyharness。
- **包镜像**:`uv sync` 慢/失败时 `UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple uv sync`。

---

# 2 安装步骤

## 2.1 项目结构(PRD §2.6 最终态)

```text
mini-harness/
├── pyproject.toml / uv.lock    # 元数据+依赖;锁文件(uv sync 生成)
├── pyharness/                  # 引擎包(python -m pyharness → CLI)
│   ├── bus/                    # 阶段0:总线/分发/注册表/热插拔
│   ├── core/                   # 阶段1:脊柱 8 模块(agent_loop/agent/session/llm/…)
│   ├── capabilities/           # 阶段3-6:外围能力(Definition 自描述)
│   ├── guards/  events/  config.py  errors.py
│   ├── cli/  desktop/  acp/        # 阶段6:外壳(desktop = pywebview 壳 + FastAPI)
│   └── __main__.py
├── scripts/
│   ├── demo_phase0.py … demo_phase6.py   # 里程碑演示(§8.3 门禁)
│   └── check_size.py                      # N6/N7 检查
├── tests/  (invariants/ + acceptance/test_fXXX_*.py)
└── docs/  (本文与各设计文档)
```

用户数据**不在**项目内,统一在 `~/.pyharness`(§7),删除项目目录不影响历史会话。

## 2.2 获取代码

```bash
git clone <仓库地址> ~/Desktop/mini-harness     # 或复制现成目录
cd ~/Desktop/mini-harness
```

## 2.3 创建虚拟环境并安装依赖

```bash
cd ~/Desktop/mini-harness
uv python install 3.11        # 首次;已有可跳过
uv sync                       # ①读 pyproject.toml ②建 .venv ③装依赖写 uv.lock
uv run python -c "import pydantic, openai, yaml; print('deps ok')"
uv run pyharness --help       # 控制台入口可用;等价 python -m pyharness --help
```

`uv sync` 幂等,随时可重跑(如依赖变更后 `uv sync` 即更新 .venv)。想用裸 `pyharness`:先 `source .venv/Scripts/activate`(Windows venv 激活脚本在 **Scripts/**,不是 bin/)。本手册统一 `uv run` 前缀,不依赖激活。

## 2.4 冒烟(离线,不需要 API key)

```bash
uv run pyharness config validate   # 预期:OK(无配置文件时回落默认值 L1,不报错)
uv run pyharness config show --json   # 预期:输出含 "llm.model": "deepseek-chat"
```

两条都退出码 0,即安装链路通;再进入 §3 配置。

## 2.5 常见坑

| # | 现象 | 原因与修复 |
|---|---|---|
| 1 | `uv: command not found` | uv 不在 PATH:执行 §1.2 的 export 并**重开终端** |
| 2 | `uv sync` 卡住/SSL 报错 | 网络:临时 `UV_DEFAULT_INDEX=…tuna… uv sync`;公司网则配代理(§1.4) |
| 3 | 裸 `pyharness` 找不到 | venv 未激活:先 `source .venv/Scripts/activate`;或统一 `uv run pyharness` |
| 4 | PowerShell/cmd 里中文乱码 | 用 git-bash 跑本手册命令;cmd 场景先 `chcp 65001` 并 `set PYTHONUTF8=1` |
| 5 | Windows 路径带反斜杠被吞 | YAML/命令行一律写正斜杠:`D:/杂乱文件夹`;裸 `D:\x` 被 YAML 当转义(CFG-602) |
| 6 | Defender 首次拦截 python/uv 联网 | 允许;项目零 WSL/Docker 依赖,勿为此装 WSL |
| 7 | 改了代码/依赖但行为没变 | `.venv` 独立,不自动跟随源码;`uv sync` 重装后重试 |


# 3 配置准备

## 3.1 分层与位置(CFG §1/§2)

配置按 代码默认(L1)→ config.yaml(L2)→ `PH_` 环境变量(L3)→ CLI(L4) 四层合并,后层覆盖写到的键。文件搜索顺序:`--config <路径>` → `PH_CFG_PATH` → `~/.pyharness/config.yaml` → `config.d/*.yaml`(升序 merge)。文件缺失回落默认不报错;存在但语法错 → **CFG-602/CFG-601 启动中止**,先修再跑。

## 3.2 生成示例配置并验证(离线,无需 key)

```bash
uv run pyharness config init          # 生成 ~/.pyharness/config.yaml(完整中文注释模板)
uv run pyharness config validate      # 离线预检:CFG-60x 明细或 OK
uv run pyharness config show --json   # 查看生效值;秘密只回显 env:NAME 引用,无 --show-secrets
```

`config init` 生成的就是带完整中文注释的模板(全量键值见 CFG §8);手工只需确认以下三处(与 CFG §8 一致):

```yaml
# ~/.pyharness/config.yaml(只写要改的键;未写键用代码默认值)
llm:
  api_key: env:DEEPSEEK_API_KEY   # 秘密引用,不写明文;缺失使用时 CRED-701 拒
  fallback_models: [qwen-max]     # 备用降级链;无 qwen 凭据时改 [] 避免无谓降级
security:
  sandbox:
    level: strict                 # 默认最严;下调须启动时显式确认(§4.4 方案B)
```

## 3.3 填 API key(二选一)

**A. 环境变量(推荐,演示/日常)**:

```bash
export DEEPSEEK_API_KEY="sk-你的密钥"                       # 本会话
echo 'export DEEPSEEK_API_KEY="sk-你的密钥"' >> ~/.bashrc   # 永久;重开终端生效
printenv DEEPSEEK_API_KEY          # 验证已生效
```

config.yaml 保持默认 `llm.api_key: env:DEEPSEEK_API_KEY`。**不要把明文密钥写进 config.yaml**——字面量密钥(≥32 位或 `sk-` 前缀)会被 CFG-601(reason=secret_literal)拒载(SECURITY §6)。

**B. credentials.yaml(多密钥/团队机器)**:取消注释 `security.credentials.file: ~/.pyharness/credentials.yaml`,文件内同样只写 env 引用,权限 600(`chmod 600`)。降级链备用模型 qwen-max 的 key 也走同一文件/环境变量,由 F016 单口读取。

## 3.4 演示前检查清单

| 键 | 默认 | 什么时候动 |
|---|---|---|
| `llm.api_key` | env:DEEPSEEK_API_KEY | **必配**(§3.3);否则首次对话 CRED-701 |
| `llm.fallback_models` | [qwen-max] | 无 qwen 凭据就 `[]`,避免 401 后降级也失败 |
| `security.sandbox.level` | strict | 默认别动;整理工作区外目录才按 §4.4 方案B 下调(须确认) |
| `security.network.allowed_domains` | [] | 阶段3 演示 Web 工具才放行(如 `[wttr.in]`),演示完删回 |
| `log.level` | info | 排障 `export PH_LOG_LEVEL=debug`(env 白名单,免改文件) |
| `budget.task.max_cost_yuan` | 1.0 | 默认即 <1 元/任务硬闸,一般不动 |

其中 `security.*` 全部属"禁止 env/CLI 放宽"面(CFG §4.5):演示前对 sandbox/allowlist 的改动一律写 config.yaml 并重启。

## 3.5 首次真实对话验证(阶段1 前置哨兵)

```bash
uv run pyharness chat --once "你好,用一句话介绍你自己"
```

预期末尾(示意):`[llm.usage] model=deepseek-chat in=1_310 out=42 cost_est=¥0.0029 | 会话累计 ¥0.003`。若输出 CRED-701:key 未读到(§6 表第 5 行)。至此配置就绪。

---

# 4 分阶段运行指南

## 4.0 六阶段总览(演示命令与 PRD §4.6.2 对齐)

| 阶段 | 里程碑演示 | 运行命令(uv 前缀;裸 `pyharness` 等效) | 小节 |
|---|---|---|---|
| 0 | 双插件互发事件;热卸载后事件停 | `uv run python -m pyharness.demo_bus` | §4.1 |
| 1 | 单轮对话→JSONL→重启回放;guard 拒危险 | `uv run pyharness chat --once "你好"` | §4.2 |
| 2 | 流式/断网重试/401 降级/成本打印 | `uv run pyharness chat --stream` | §4.3 |
| 3 | 整理文件夹按主题归类全自动 | `uv run pyharness run "整理 D:\\杂乱文件夹"` | §4.4 |
| 4 | plan 批准执行;排队串行;定时触发 | `uv run pyharness plan "每周备份笔记"` | §4.5 |
| 5 | subprocess/PTY;FTS 命中;compaction;fork | `uv run pyharness search "备份"` | §4.6 |
| 6 | repair 恢复;CLI 全命令;**桌面程序(双击 exe:对话+轨迹回放+审批弹窗)**;ACP | `pyharness-desktop` / `pyharness acp` | §4.7 |

通用前置:命令在项目根 `~/Desktop/mini-harness` 执行;阶段1 起需 §3 配置就绪;每阶段结束跑本阶段验收模块,全绿 + 演示成功才算过阶段门(PRD §4.6/§8.3):`uv run pytest tests/acceptance/ -q --tb=short -k "f00"`(阶段0 的 F001-F006;阶段 N 换 `f0N`;`tests/invariants/` 的 9 条 INV 最先全绿)。

## 4.1 阶段0:插件总线(离线,无 key)

```bash
uv run python -m pyharness.demo_bus
```

预期输出(示意):

```text
[bus]     注册表索引完成:3 插件 / 2 工具 / 8 事件类型
[plugin]  alpha 已激活 (plugin:alpha) | beta 已激活 (plugin:beta)
[alpha→beta] emit ping(seq 由总线分发,不落盘)
[bus]     热卸载 plugin:beta → alpha 再 emit ping:0 订阅者,事件不再分发
[registry] 查询 plugin:beta → 未注册;脊柱 8 名保留(BUS-002)
```

演示讲解点:①总线只中转、不落盘,日志订阅者到阶段1 才挂上;②热卸载后事件即停 = 里程碑;③背压"拒新不丢旧"(F005)。验收:`uv run pytest tests/acceptance -q -k "f00"`。其余各阶段"预期输出"均为**示意片段**,硬判据是事件类型与 seq(§4.2.2 落盘校验)。

## 4.2 阶段1:核心脊柱——查天气给建议 + guard 拦截

### 4.2.1 场景A:单轮对话查天气(PRD 演示命令)

```bash
uv run pyharness chat --once "广州明天适合跑步吗?"
```

预期(示意;阶段1 用内置演示天气工具,不真联网):

```text
[llm.request ] model=deepseek-chat in=842 n_tools=2
[tool.call   ] get_weather(city=广州, day=明天) → [tool.result] "32℃ 小雨 湿度 78%"
[agent.message] 不适合跑步:广州明天 32℃ 有小雨、湿度偏高,建议室内力量训练或游泳。
[llm.usage   ] in=1_204 out=86 cost_est=¥0.0033
```

### 4.2.2 场景B:JSONL 落盘 → 重启回放(里程碑核心)

```bash
uv run pyharness session show <sid>      # 结构化回放;或直接 tail 原始 JSONL
tail -n 6 ~/.pyharness/sessions/<sid>.jsonl
```

预期:首行 `session.created`(seq=1),随后 `user.message → llm.request → tool.call → tool.result → llm.response → agent.message`,seq 连续 +1、ts 为 UTC 微秒;全文件只追加(INV-01)。重启回放:

```bash
uv run pyharness chat --session <sid> --once "刚才聊了什么?一句话总结"
```

预期:回答能引用天气结论 → 历史由日志重建、重启不丢(INV-03)。

### 4.2.3 场景C:危险工具被 guard 拒(演示要点,完整台本见 §9)

```bash
uv run pyharness chat
> 把 ~/.pyharness/demo/notes.txt 删掉
```

预期(示意;演示者录屏重点):

```text
[tool.call    ] fs.delete_file(path=C:/Users/<你>/.pyharness/demo/notes.txt)
[GUARD-REJECT ] fs.delete_file 未执行
  guard=g-fs-path  策略=POL-FS-1(路径在会话工作区外)   事件已强同步落盘
[agent.message] 我不能删除工作区外的文件;如需删除请把文件移入工作区再人工决定。
```

若路径在工作区内,则由危险 guard 拦:guard=g-danger reason=critical(不可审批,直接拒,PRD §6.3)。**判据**:`guard.rejected` 强同步落盘、真实文件零副作用(INV-05),`ls -l` 验证文件原样。完整录屏台本见 §9。

## 4.3 阶段2:模型加厚——流式/重试/降级/成本

### 4.3.1 流式打字机

```bash
uv run pyharness chat --stream
> 写一段 100 字的广州介绍
```

预期:逐 token 渲染(llm.chunk 只进 UI 不进日志),完成后落**一条** `llm.response`;Ctrl-C 一次取消当前轮,两次退出。

### 4.3.2 断网自动重试(演示:断开 WiFi/拔网线后发消息)

预期控制台(示意):`[llm] 连接失败 LLM-301(retryable)→ 第 1/4 次重试,退避 ~1.0s` → `第 2/4 次,退避 ~2.0s` → 恢复网络后正常返回;或 4 次耗尽 → LLM-310 终态(F019 错误码,不静默)。

### 4.3.3 主模型 401 自动降级 qwen-max(演示:临时把 key 改错)

```bash
export DEEPSEEK_API_KEY="sk-wrong-key-for-demo"
uv run pyharness chat --once "你好"
```

预期(示意;需 qwen-max 凭据已配):

```text
[llm] 401 认证失败 LLM-302 → 触发降级链
[llm.request] model=qwen-max degraded_from=deepseek-chat   # 降级事实入事件(F013)
[agent.message] 你好!(由 qwen-max 回答)
```

无备用凭据时预期 `LLM-310` 终态 + 错误码,而非卡死——降级条件固定:认证错/连续限流(≥2 次)/网络不可达才降(CFG §3.1)。

### 4.3.4 成本可见

对话几轮后,chat 内 `/budget` 或另开终端 `uv run pyharness budget`。预期(示意):`当前会话 in=18.4k out=2.1k 估算 ¥0.06 | 硬闸 ¥1.00(6%)warn 80%`;超闸任务 exhausted 强制终态(F032)。


# 4(续).4 阶段3:工具能力——整理文件夹全自动(录屏素材)

里程碑 = 无人值守完成"整理文件夹按主题归类"(需求文档 §3.2)。**方案A(推荐,零授权)** 在会话工作区内演练,命令自包含(先造素材再归类,可重复):

```bash
uv run pyharness run "工作区演练:在 input/ 生成 5 个示例笔记(2 篇 Python 协程、2 篇 SQLite、1 篇健身计划),再按主题归类移动到 output/ 的子目录,先列目录→读内容→建类→移动,最后汇报每个类目收了哪些文件"
```

预期(示意,全程无人值守):

```text
[task.enqueued ] queue_pos=0 task_id=t-3f2a
[tool.call     ] fs.list_dir(…/input) → 5 个文件
[tool.call     ] fs.read_file(…/协程笔记.md) → 超 64KB 转 spill_ref(F039) | [todo] 1/5 已归类(Python)…
[tool.call     ] fs.move_file(…/协程笔记.md → …/output/Python/) → ok
[agent.message ] 整理完成:Python 2、数据库 2、健身 1;重复文件未删,保留待人工决定
[llm.usage     ] in=182_000 out=9_400 cost_est=¥0.44(硬闸 ¥1.00 内)
```

**方案B(PRD §4.6.2 原命令,整理工作区外真实目录)**:把沙箱级别下调到 `off`/`basic`——编辑 `~/.pyharness/config.yaml` 的 `security.sandbox.level: off`,下次启动交互确认(留 `sandbox.opened` 事件,工具/LLM 调不到),录屏完改回 `strict`:

```yaml
security:
  sandbox:
    level: off        # 仅演示机;工作区外整理需要;危险工具分级不受影响
```

```bash
uv run pyharness config validate
uv run pyharness run "整理 D:/杂乱文件夹,按主题归类到子文件夹,先列目录→读内容→建类→移动;重复文件不要删,移到 duplicates/ 留待人工"
```

预期:同方案A 流程,作用于 `D:/杂乱文件夹`。要点:Web 搜索/抓取工具需先放行 `security.network.allowed_domains`(默认空=禁,CFG §3.3);headless 管道下危险动作自动拒绝(R8)。

## 4.5 阶段4:任务编排——plan / 排队串行 / 定时触发

**场景A:plan 方案→批准→执行(PRD 命令,需交互批准,勿走管道)**:

```bash
uv run pyharness plan "每周备份笔记到 ~/.pyharness/backups"
```

预期(示意):

```text
[plan.proposed] 方案 3 步(预计 ¥0.05):1 建 backups/ 并复制 notes/*.md [fs]
              2 校验目标数与源一致 [fs]  3 写 MANIFEST.txt [fs.write]
批准? [y/N] > y
[plan.approved] [exec.step 1/3] ✓ [exec.step 2/3] ✓ [exec.step 3/3] ✓
[agent.message] 备份完成:12 个文件,清单见 backups/MANIFEST.txt
```

**场景B:三任务排队串行,各留独立日志段**:交互 chat 里连发三条(不等完成):`> 任务一:统计工作区 input/ 的文件数`、`> 任务二:统计 input/ 下 .md 文件总行数`、`> 任务三:把结果写入 output/summary.txt`。

预期(示意):`task.enqueued` queue_pos 0/1/2 → 串行执行(默认并发 1)→ 每任务 `segment.start…segment.end` 独立日志段;`uv run pyharness session show <sid> | grep task_id` 分段审计。状态入事件 = 可恢复(F044)。

**场景C:schedule 定时触发**:chat 内 `/schedule 每分钟:把当前时间追加到工作区 heartbeat.log`,确认后约 1 分钟出现 `schedule.trigger → job.started → job.completed`;`uv run pyharness job list` 查看(结果保留 7 天,F051)。最小间隔 1 分钟为固定约束(F048)。

## 4.6 阶段5:系统能力——subprocess / FTS / compaction / fork

strict 沙箱默认禁 subprocess;演示前把 `security.sandbox.level: basic`(g-exec 需授权)。四条演示:

**A. workspace 内 subprocess**:chat 发 `用 python 计算 2**100 并写入工作区 result.txt(只能在工作区内执行)` → 预期 `exec.run` 过 g-exec、cwd=workspace、回 stdout+exit code;越界命令被拒。

**B. 旧会话 FTS 命中(PRD 命令)**:

```bash
uv run pyharness search "备份"
```

预期:命中 §4.5 会话片段,按相关度 ≤20 条,中文 2-gram(≥2 字),含会话 id/时间/命中上下文。

**C. compaction 后继续**:超长会话(历史 ≥ 窗口 75%)自动压缩,控制台出 `[compact] 历史 N 轮→摘要(保留最近 12 轮原文)` 与 `session.compacted` 事件;继续提问正常,旧细节可能摘要化(F058)。

**D. fork 分支**:`uv run pyharness fork <sid> --as "备份方案实验"` → 新会话独立演进(COW 快照,F059),旧会话不受影响,两日志各自 append-only。

## 4.7 阶段6:外壳——repair / Web / ACP / CLI 全命令

**A. 崩溃修复(kill -9 后 repair)**:交互 chat 聊几句 → 任务管理器结束进程(模拟崩溃)→ 重启:

```bash
uv run pyharness repair --session <sid>
uv run pyharness chat --session <sid> --once "继续"
```

预期(示意):

```text
[repair] 检测尾部截断:末事件 user.message seq=7,缺 seq=8…
[repair] 截断隔离 lost=1 backup=~/.pyharness/sessions/<sid>.corrupt-<ts>.jsonl
[repair] 已追加 session.recovered(fixed/lost/backup)   # 修复是事件不是抹除(F060)
```

**B. 桌面程序(Windows,面试主演示)**:

```bash
pyharness-desktop        # 双击 exe 或命令行启动:pywebview 独立窗口(127.0.0.1 仅回环)
```

预期:桌面窗口打开 → 会话列表 → 新会话 → 提问 → SSE 流式回复;**右侧 Agent 轨迹面板逐帧显示每一步(工具调用/guard 拦截/结果)**;危险操作弹审批窗;预算仪表盘(F065)。关窗=优雅停服(强同步事件已落盘)。

**C. ACP 桥(JSON-RPC over stdio)**:

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | uv run pyharness acp
```

预期:stdout 回 `{"jsonrpc":"2.0","id":1,"result":{…}}`(引擎日志走 stderr);approve 可经桥下发远程审批;桥无特权仍过 guard/预算(F066)。

**D. CLI 全命令冒烟**:`uv run pyharness --help` 列出 13 个子命令(§5);`config/budget/stats` 离线可用。

---

# 5 CLI 命令参考

## 5.1 子命令总表(以 `pyharness --help` 为准;本文定义 PRD 未固定旗标)

| 子命令 | 作用 | 离线 | 典型示例 |
|---|---|---|---|
| `chat` | 交互对话(默认流式渲染) | — | `uv run pyharness chat` |
| `run` | 单发任务,无人值守 | — | `uv run pyharness run "整理 D:/杂乱文件夹"` |
| `plan` | 方案→批准→执行(须交互) | — | `uv run pyharness plan "每周备份笔记"` |
| `schedule` | 定时任务查看/管理 | — | chat 内 `/schedule …` |
| `job` | 任务队列/记录查询 | — | `uv run pyharness job list` |
| `search` | FTS 全文检索旧会话 | — | `uv run pyharness search "备份"` |
| `session` | 会话列表/show 回放 | — | `uv run pyharness session show <sid>` |
| `fork` | 分支会话(COW) | — | `uv run pyharness fork <sid> --as "实验"` |
| `repair` | 崩溃/损坏修复 | — | `uv run pyharness repair --session <sid>` |
| `desktop` / `acp` | 桌面程序(pywebview) / ACP 桥 | — | `pyharness-desktop`(或 `uv run pyharness desktop`);管道喂 JSON-RPC(§4.7C) |
| `config` | init/validate/show | ✅ | `uv run pyharness config validate` |
| `budget` / `stats` | 预算报表 / 会话统计 | ✅ | `uv run pyharness budget` |

通用旗标:`--config/-c <路径>`(L4 最高优先级)、`--json`(结构化输出)、`--session <sid>`;退出码 0 成功、非 0 带错误码(F019,如 LLM-310/CFG-601)。

## 5.2 会话生命周期操作

- **启动新会话**:`uv run pyharness chat`;标题由首轮内容自动派生(阶段3,F042)。
- **新会话(不退出)**:chat 内 `/new`——结束当前会话、开新 seq 从 1 起。
- **恢复会话**:`uv run pyharness chat --session <sid> --once "…"`;列表用 `uv run pyharness session`。历史一律由日志回放派生,无第二份内存历史(INV-01)。
- **退出**:`/quit`(或 `/exit`)或 Ctrl-D;Ctrl-C 一次取消当前轮、两次退出(F064)。

## 5.3 斜杠命令(会话内;F041,不消耗 LLM)

| 命令 | 作用 | 备注 |
|---|---|---|
| `/new` | 开新会话 | 清空当前上下文(日志仍留档) |
| `/undo` | 撤销上一轮 | 仅撤销尚未产生副作用的轮次 |
| `/plan` | 进入方案模式(目标→方案→批准→执行) | 阶段4 扩展 |
| `/schedule` | 配置定时任务 | 阶段4 扩展;最小间隔 1 分钟 |
| `/budget` | 查当前会话预算/成本 | 离线数据,实时打印 |
| `/help` | 命令帮助 | — |
| `/quit` | 退出 | 等价 `/exit` |

未知斜杠 → 回显 **EVT-105**,会话继续;斜杠是"不给 LLM 的控制通道":危险动作不存在于斜杠面,一切执行走工具+guard。


# 6 故障排查(症状 → 诊断 → 修复)

排查顺序:先离线条命令(`config validate`/`session show`)缩小范围,再看控制台引擎日志(排障 `export PH_LOG_LEVEL=debug`);事件 JSONL 不受日志级别影响,始终是审计真源。错误一律带码(F019),按码查 ERR.md。

| # | 症状 | 诊断 | 修复 |
|---|---|---|---|
| 1 | `uv: command not found` | `which uv` 无输出 | PATH 补 `~/.local/bin` 并重开终端(§1.2) |
| 2 | `uv sync` 卡住/SSL 错 | 直连 PyPI 不通 | 临时 `UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple uv sync`;公司网配代理(§1.4) |
| 3 | 裸 `pyharness` 找不到 | venv 未激活 | `source .venv/Scripts/activate`;或全程 `uv run pyharness` |
| 4 | **API 超时**(卡连接后 LLM-301) | `curl -sS -o /dev/null -w "%{http_code}" https://api.deepseek.com` | 修网络/代理;必要时调大 `llm.timeout.*`(10/60/180s) |
| 5 | 401/403(LLM-302) | `printenv DEEPSEEK_API_KEY` 空?`config show` 引用对? | 按 §3.3 导出 key 重开终端;明文 key 被 CFG-601 拒,改回 `env:` 引用 |
| 6 | 401 后 LLM-310 终态 | 备用链也失败 | 补 qwen 凭据(§3.3B);演示无 qwen 时 `fallback_models: []` |
| 7 | **配置错误**:启动 CFG-601 中止 | `uv run pyharness config validate` 看字段+原因 | 值超范围/YAML 制表符/裸 `D:\`(写 `D:/`);安全单调被违(调低 danger、关 guard 只许收紧,CFG §5.1);CFG-607 未知键警告可忽略 |
| 8 | **会话损坏**:无法继续/回放 seq 空洞 | `session show <sid>` 报截断/空洞 | `uv run pyharness repair --session <sid>`(截断隔离+备份)后续聊;禁双开同会话 |
| 9 | Web 端口占用 | `netstat -ano \| grep 8000` | `export PH_WEB_PORT=8001` 再启;或结束占用进程 |
| 10 | 中文乱码 | 终端代码页非 UTF-8 | git-bash `export PYTHONUTF8=1`;cmd 先 `chcp 65001` |
| 11 | 危险任务全被拒(演示时) | stdin 非 tty = headless | headless 无审批通道,high/critical 一律拒(R8);演示用交互终端(§9) |
| 12 | 任务 exhausted | `/budget` 看是否顶到 1 元硬闸 | 正常安全机制;调大 `budget.task.max_cost_yuan` 改 config 重启(只许人工) |

---

# 7 数据位置与备份

## 7.1 数据目录(`storage.root` = `~/.pyharness` = `C:\Users\<用户名>\.pyharness`)

| 数据 | 位置(CFG §3.6) | 内容 | 可否重建 |
|---|---|---|---|
| 会话事件日志(**真源**) | `~/.pyharness/sessions/` | `{sid}.jsonl` 只追加;>50MB 轮转(N11) | **否——备份对象** |
| SQLite(KV+FTS) | `~/.pyharness/pyharness.db` | 派生视图(WAL),非真源 | 可:删库后按日志重灌(F056) |
| 会话工作区 | `~/.pyharness/workspaces/{sid}/` | 文件工具边界;产出文件在此 | **否——备份对象** |
| spill 私有区 | `~/.pyharness/spill/` | 600;随会话生命周期 | 部分可恢复 |
| 配置 | `~/.pyharness/config.yaml` 等 | 600,gitignore | 手工 |

## 7.2 备份与恢复(先退出所有 pyharness 进程)

```bash
tar -C ~ -czf ~/pyharness-backup-$(date +%F).tar.gz .pyharness   # 全量
cp -r ~/.pyharness/sessions ~/pyharness-sessions-$(date +%F)     # 最小集
# 恢复:停进程 → 解包回 ~/.pyharness → repair 声明不一致 → 回放验证
tar -xzf ~/pyharness-backup-2026-09-06.tar.gz -C ~
uv run pyharness repair --session <sid>
uv run pyharness chat --session <sid> --once "验证恢复"
```

要点:SQLite 丢了无需手工处理(删库后按日志重灌);**`sessions/*.jsonl` 丢了才真丢历史**。重要演示会话录完即备份,可配 Windows 任务计划程序定时跑全量命令。

---

# 8 卸载/清理

```bash
# 1) 退出运行中的 pyharness(无后台常驻服务)
# 2) 删项目与 .venv(历史会话在 ~/.pyharness,不受影响)
rm -rf ~/Desktop/mini-harness
# 3) 删用户数据(不可恢复——先按 §7.2 备份!)
rm -rf ~/.pyharness
# 4) 卸载 uv 装的 Python 3.11:uv python uninstall 3.11
# 5) 卸载 uv:winget → winget uninstall astral-sh.uv;脚本装 → rm ~/.local/bin/uv*
# 6) 清理 ~/.bashrc 中 DEEPSEEK_API_KEY/PH_*/PATH 追加行;uv cache clean(可选)
```

最小清理(只拆环境、留历史):只做 2)+4);重装项目后 `config validate` + `chat --session <旧sid>` 即恢复。

---

# 9 面试录屏演示脚本(AI 整理文件夹被 guard 拦删除)

**时长 3-5 分钟;一个 git-bash 窗口 + 旁白;默认 strict 沙箱、工作区内进行,零配置改动。** 完整呈现 PRD §6.3 + 需求文档 §3.2/§3.3:无人值守整理 + 删除被单调拒绝 + 审计回放。括号内为旁白要点。准备(不入镜):§2 安装 + §3 配置完成,`config validate` 输出 OK。

**S0 启动(5s)**:`uv run pyharness chat --stream`

**S1 让 AI 自建素材(30s)**——展示工具链,顺带制造"重复文件"诱因(旁白:"我只给目标,不给步骤"):输入 `> 演练:在 input/ 创建 4 个文件——两篇 Python 协程笔记、一篇 SQLite 用法、一篇健身计划;再复制一篇改名"协程笔记_重复.md"模拟重复文件`。预期:AI 列 todo 逐项打勾(F040),`fs.write_file` 成对 tool.call/result,结尾汇报 5 个文件就绪。

**S2 派整理任务(40s)**:旁白:"它自己列目录→读内容→判断主题→移动"。输入:

```
> 把 input/ 按主题整理到 output/:Python、数据库、健身 三个子目录;重复文件单独放 output/重复文件/
```

预期:`fs.list_dir` → 逐文件 `fs.read_file`(长文件超 64KB 转 `spill_ref`,旁白:"大结果只进引用,不爆上下文")→ `fs.move_file`,成对执行。

**S3 危险时刻(录屏核心,30s)**:AI 自作聪明要删重复文件(旁白:"它调了 delete_file——看 guard 怎么拦"):

```text
[tool.call    ] fs.delete_file(path=…/output/重复文件/协程笔记_重复.md)
[GUARD-REJECT ] fs.delete_file 未执行 | guard=g-danger 策略=POL-DNG-1
               danger=critical → 不可审批,直接拒绝 | guard.rejected 已强同步落盘
```

旁白:"critical 级不可审批、不可放行——单调拒绝(R2);guard 不问模型'要不要',策略直接说不。"

**S4 绕过尝试也被拦(30s)**:LLM 换招移出工作区、再试删除,全被拦,循环自动停止该方向:

```text
[tool.call    ] fs.move_file(…/协程笔记_重复.md → D:/回收站/…)
[GUARD-REJECT ] g-fs-path 策略=POL-FS-1(目标在会话工作区外)
[tool.call    ] fs.delete_file(…/协程笔记_重复.md)      # 又试一次
[GUARD-REJECT ] g-danger critical → 直接拒绝
```

AI 最终安全收尾:"删除与移出都被安全策略拒绝,重复文件保留,请人工决定"。旁白:"拒绝是确定性策略,不是运气;连败后模型给出安全结论。" 收尾(15s):汇报归类结果 + `[llm.usage] cost_est=¥0.38(硬闸 ¥1.00 内)`。

**S5 审计回放(另开终端,30s)**——旁白:"一切事实都在事件日志,只追加不可变":

```bash
uv run pyharness session show <sid> | grep -n guard.rejected
ls -R ~/.pyharness/workspaces/<sid>/output/重复文件/    # 零副作用:文件原样还在
```

预期:3 行 guard.rejected(g-danger critical / g-fs-path POL-FS-1 / g-danger critical),seq 连续,各带 policy_ref——"模型说过什么、实际执行了什么,逐条可对(INV-06)"。

**S6 收尾(5s)**:`/quit`。旁白三句:①日志是唯一真源,历史从事件回放;②guard 单调拒绝,危险动作零副作用;③每步可审计——这就是能逐行讲深的框架。

---

# 10 关联测试(≥4 条 GWT,验证本手册命令真实可执行)

归属:`tests/acceptance/test_f064_cli.py`、`test_f041_commands.py`、`test_f060_repair.py` 与 `tests/manual/test_deploy_*.py`;演示/发布会前各跑一遍。

**G1 环境与离线命令(§2/§3)**:Given 全新 Win11 + git-bash + uv,已 `uv sync`;When 依次 `config init`/`config validate`/`config show --json`;Then 退出码均 0,show 含 `llm.model=deepseek-chat`,无 API key 也全成功(离线)。

**G2 秘密引用与缺失 key(§3.3/§3.5)**:Given `api_key: env:DEEPSEEK_API_KEY` 且该变量未导出;When `chat --once "你好"`;Then 不发起任何 LLM 请求,输出 CRED-701,退出码非 0;When 导出 key 重跑;Then 成功并打印 `llm.usage`。

**G3 阶段1 单轮/落盘/回放(§4.2)**:Given key+网络就绪;When `chat --once "你好"` 后 `chat --session <sid> --once "复述我上一条消息"`;Then 两次退出码 0;`sessions/<sid>.jsonl` 首事件 `session.created`(seq=1)、seq 连续无空洞(INV-01);回放能引用上轮内容(INV-03)。

**G4 headless 危险默认拒绝(§9/R8)**:When `echo "删掉工作区里所有 .md 文件" | uv run pyharness run "整理并清理临时文件"`(stdin 非 tty);Then 全程无 `fs.delete_file` 真实执行;`guard.rejected`(critical)落盘;工作区文件原样(INV-05);报告含"拒绝"。

**G5 未知斜杠与退出(§5)**:Given 交互 chat(pty);When 输入 `/foo`;Then 回显 EVT-105、会话继续、`llm.request` 事件数不增;When 连续两次 Ctrl-C;Then 退出码 0。

**G6 损坏修复与备份恢复(§6/§7)**:Given 备份 `<sid>.jsonl` 后截断尾部 2 行;When `repair --session <sid>`;Then 声明 lost=2 与 backup、追加 `session.recovered`、会话可继续;When 备份覆盖后再次 repair;Then 无 lost,回放与备份一致。

---

# 附:一致性声明

1. 演示命令与 PRD §4.6.2 逐条对应(§4.0 表);判据(事件类型/seq/落盘)源自 PRD §3 协议,预期片段为示意,以实际输出为准。
2. 配置键/存储路径/env 映射引用 CFG §3/§4/§8;错误码(CFG-601/602、CRED-701、LLM-301/302/310、EVT-101/105)以 ERR.md 与 PRD §5 为准;冲突以 PRD-Core 为唯一权威。
3. 本文为 `pyharness/cli/`(F064)、`scripts/demo_phase{0..6}.py` 与部署 GWT 的落地对照;实现细节以 specs/ 函数级规格为准。

*— DEP v1.0 完 — 部署运行手册:10 章,六阶段命令与 PRD §4.6.2 对齐,录屏台本 §9,测试 §10。*


---

*本文档由 PyHarness 文档流水线产出。命令均为 Windows git-bash + uv 兼容。*
