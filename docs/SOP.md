# SOP — 标准操作流程(Standard Operating Procedures)

> **类型**: 操作流程 = 六大高频链路的"按序执行 + 判据验收":①开发环境搭建 → ②测试运行 → ③编码流程 → ④部署与演示准备 → ⑤故障恢复 → ⑥文档维护。
> **版本**: v1.0 | **日期**: 2026-09-06 | **读者**: 许子诺 / 演示者 / AI 编码 Agent。命令唯一来源 = DEP.md;编码顺序 = specs/README.md;测试 = CONSTRAINTS-06;故障 = CONSTRAINTS-08;阶段门 = PRD-Core §4.6/§8.3。冲突以 PRD-Core 与 CFG.md 为准,错误码以 ERR.md 为准。
> **命令环境**(与 DEP 一致): Windows 11 + git-bash;`~` = `C:\Users\<你的用户名>`;项目根 = `~/Desktop/mini-harness`;统一 `uv run` 前缀(无需激活 venv),等效裸 `pyharness`;路径写正斜杠 `D:/x`,中文原生 UTF-8。

## 快速索引

| 编号 | 流程 | 适用时机 | 关键判据 |
|---|---|---|---|
| §1 | 开发环境搭建 | 新机器/换机演示 | 四条冒烟命令退出码 0 |
| §2 | 测试运行 | 每阶段开发/修复后/演示前 | invariants 先绿 + 覆盖率 ≥85% |
| §3 | 编码流程(阶段门) | 每阶段开发 | 验收全绿 + demo_phaseN 人工确认 |
| §4 | 部署与演示准备 | 录屏/面试/交付前 | config validate OK + 预演 ≥2 次 |
| §5 | 故障恢复 | 任何异常 | 回放定位 → 按码修复 → 回归绿 |
| §6 | 文档维护 | 每功能落地后 | 六面同步 + 一致性审查过 |

---

# 1 开发环境搭建

**目的**: 把 Windows 11 机器从零变成可运行/可测试/可演示的开发机。权威步骤 = DEP §1/§2。

**步骤**:

1. **前置检查**: 终端为 git-bash;`git --version` 有输出;`uv --version` 输出 ≥0.5.x。
2. **装 uv(缺失时)**: PowerShell `winget install --id=astral-sh.uv -e`;或 git-bash `curl -LsSf https://astral.sh/uv/install.sh | sh` + `export PATH="$HOME/.local/bin:$PATH"` 并写入 `~/.bashrc`,**重开终端**后验证。
3. **装受管 Python 3.11**(不碰系统 Python): `uv python install 3.11`;`uv python list` 确认含 3.11.x(pyproject 声明 `requires-python = ">=3.11"`,`uv sync` 自动挑选)。
4. **获取代码**: `git clone <仓库地址> ~/Desktop/mini-harness`(或复制现成目录),`cd` 后确认顶层含 `pyproject.toml / pyharness/ / scripts/ / tests/ / docs/`(结构见 DEP §2.1)。用户数据在 `~/.pyharness`,不在项目内。
5. **装依赖**: `uv sync`(①读 pyproject ②建 .venv ③装依赖写 uv.lock)。幂等,依赖变更后重跑即更新 .venv。
6. **依赖冒烟**: `uv run python -c "import pydantic, openai, yaml; print('deps ok')"` → 输出 `deps ok`。
7. **CLI 冒烟**: `uv run pyharness --help` → 列出 14 个子命令(chat/run/plan/schedule/job/search/session/fork/repair/desktop/acp/config/budget/stats,DEP §5.1)与 `--config/--json/--session`。
8. **离线冒烟(免 key)**: `uv run pyharness config validate` → OK;`uv run pyharness config show --json` → 含 `"llm.model": "deepseek-chat"`。两条退出码 0 = 安装链路通。
9. **激活方式**: 日常统一 `uv run pyharness …`;裸 `pyharness` 需先 `source .venv/Scripts/activate`(Windows venv 在 **Scripts/**,不是 bin/)。
10. **IDE 配置(VS Code)**: `.vscode/settings.json` 设 `"python.defaultInterpreterPath": ".venv/Scripts/python.exe"`;pytest 扩展指向 `tests`;`"files.encoding": "utf8"`;`.gitignore` 含 `.venv/`。
11. **网络前置(可选)**: 代理 `export https_proxy=http://127.0.0.1:7890`;镜像 `UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple uv sync`。LLM 端点请求不受 `allowed_domains` 约束。
12. **完成定义**: 第 6/7/8 步命令全部退出码 0 且输出符合预期 → 进入 §3 配置(DEP)。

**常见坑**: `uv: command not found` → 补 PATH 重开终端;`uv sync` 卡/SSL → 换镜像或代理;裸 `pyharness` 找不到 → 激活或 `uv run`;中文乱码 → git-bash `export PYTHONUTF8=1`;裸 `D:\x` 被 YAML 当转义 → 写 `D:/x`(CFG-602);改了代码行为没变 → `.venv` 不自动跟随源码,`uv sync` 重装。

---

# 2 测试运行

**目的**: 统一命令跑完测试并解读。分层 = CONSTRAINTS-06(TS-01/§8);验收门禁 = PRD §8.3、DEP §4 通用前置。判据从严:宁要红,不要假绿。

**拓扑速记**: `tests/invariants/`(9 条 INV,§4 六原则的机器表达)、`tests/acceptance/test_f{nnn}_{slug}.py`(66 项验收,F001-F066)、`tests/unit/`(单模块)、`tests/integration/`(跨模块,mock LLM)、`tests/e2e/`(`@pytest.mark.e2e`,真实 LLM 手动跑)、`tests/manual/test_deploy_*.py`(部署 GWT)。

**步骤**:

1. **不变量最先**(每次提交前): `uv run pytest tests/invariants/ -q` → 9 条 INV(INV-01 只追加 … INV-09 脱敏)全绿。不变量破,先修代码别跑后面。
2. **本阶段验收(开发期日常)**: `uv run pytest tests/acceptance/ -q --tb=short -k "f0N"` — 阶段 N 换 `f0N`(f00 = F001-F006,… f06 = F060-F066)。`-k` 是子串过滤,只用于快速反馈;**门禁以第 3 步全量为准**。
3. **全量验收(阶段门/发布前)**: `uv run pytest tests/acceptance/ -q --tb=short` → 66 模块全绿。
4. **分层单测+集成(免真实 API)**: `uv run pytest tests/unit tests/integration -q`。LLM 一律 mock(TS-07),mock 要模拟真实失败(5xx/超时/坏 JSON),不许"永远成功"。
5. **e2e 手动跑(演示/录屏前)**: `uv run pytest tests/e2e -m e2e` — 真实 LLM+网络+key;日常不跑。演示场景提前跑通 ≥2 次。
6. **覆盖率**: `uv run pytest --cov=pyharness --cov-report=term` — 核心脊柱 ≥85%(PRD §8.3 N8;TS-10 的 80% 为最低基线)。看核心模块,不看全库平均。
7. **快速反馈旗标**: 单条 `-k 子串` / 失败即停 `-x` / 上次失败 `--lf` / 完整堆栈 `--tb=long`。修复后先 `--lf` 确认转绿,再跑 1/3 步全量。
8. **失败排查五拍**: ①`--lf` 复现单条;②`--tb=long` 定位断言;③判层(unit=mock 与模块逻辑,integration=跨模块契约,e2e=先查网络/key);④对照 ERR.md 错误码(F019,如 LLM-310/CFG-601/GRD-401);⑤TS-09 先写失败测试 → 修复 → 回归绿。
9. **部署 GWT(演示/发布会前)**: `uv run pytest tests/manual/ -q` → DEP §10 的 G1-G6 逐条过。
10. **写作纪律(新增测试)**: docstring 用 GWT(TS-03);每模块 ≥3 条含正常+异常+边界(TS-04);每错误码 ≥1 条(TS-06);guard 拒绝测试必须断言 mock 调用次数 = 0(TS-05/INV-04)——只断言"返回拒绝"是假绿,安全可能已失效。

**常见坑**: 假绿 = 断言太弱或 mock 太假(防御见 CONSTRAINTS-06 §6:断言文件 hash/调用次数=0/备用 base_url);测试依赖真实 API → 网络一抖全挂,真实 API 只进 e2e;`--lf` 冒充全量;覆盖率看总数被外壳模块稀释;忘 `-k` 阶段过滤导致全量跑太久;临时文件勿写死 `/tmp` 或裸 `D:\`。

---

# 3 编码流程(阶段门制)

**目的**: 按 6 阶段推进(F001-F066),每阶段走"实现 → 测试 → 门禁 → 演示"四拍,门没过不进下一阶段(PRD §4.6)。编码入口 = specs/README.md;功能字段权威 = PRD §5(每项含验收伪代码)。

**步骤**:

1. **开工前定位**: specs/README.md 按阶段找模块 → 读 `specs/<模块>.py.md` 函数清单(签名/伪代码/异常表/关联测试)→ 对照 PRD §5 该 F 编号验收伪代码。冲突以 PRD 为准。
2. **严守编码顺序**(specs/README.md 原表):
   ```text
   阶段0: bus → events → errors → config               (地基先立)
   阶段1: session → persistence → agent_loop → agent
          → system_prompt → llm → llm_fallback → scope
          → tools_registry → tools_guard → tools_executor → approval
   阶段3: tool_fs → spill → tool_web → commands
   阶段4: task_queue → plan_mode → goal → schedule → subagent → jobs
   阶段5: session_query → compaction
   阶段6: cli → repair → acp → desktop                  (壳最后)
   ```
   硬约束: 阶段 N 不得 import 阶段 N+1 模块(INV-08,不变量测试兜底)。
3. **实现一拍**: 照 spec 签名逐个落地,伪代码转真代码;依赖总线/事件/错误码按对应 specs。发现偏离规格先记下,阶段收尾按 §6 回写。
4. **测试一拍(先行)**: 不变量类 RED→GREEN(TS-02,先写失败测试再实现);每模块 ≥3 条 GWT 含异常+边界(TS-04);危险工具测试断言零副作用(TS-05);验收文件按 `tests/acceptance/test_f{nnn}_{slug}.py` 对齐 PRD §8.2 清单。
5. **本阶段验收**: `uv run pytest tests/acceptance/ -q --tb=short -k "f0N"` 转绿;再 `uv run pytest tests/invariants/ -q` 确认 9 条 INV 未被破坏。
6. **覆盖率自检**: `uv run pytest --cov=pyharness --cov-report=term` ≥85%;不足补测试,不许删分支凑数。
7. **门禁 = 里程碑演示(人工确认,不可省)**: `uv run python scripts/demo_phase<N>.py`(阶段 0 亦可 `uv run python -m pyharness.demo_bus`),人工确认预期输出。演示与验收命令速查(DEP §4.0 ↔ PRD §4.6.2):

   | 阶段 | 里程碑 | 验收过滤 | 演示命令 |
   |:---:|---|---|---|
   | 0 | 双插件互发事件;热卸载事件停 | `-k "f00"` | `uv run python -m pyharness.demo_bus` |
   | 1 | 单轮对话→JSONL→重启回放;guard 拒危险 | `-k "f01"` | `uv run pyharness chat --once "你好"` |
   | 2 | 流式/断网重试/401 降级/成本 | `-k "f02"` | `uv run pyharness chat --stream` |
   | 3 | 整理文件夹按主题归类全自动 | `-k "f03"` | `uv run pyharness run "整理 D:/杂乱文件夹"` |
   | 4 | plan 批准执行;排队串行;定时触发 | `-k "f04"` | `uv run pyharness plan "每周备份笔记"` |
   | 5 | subprocess/PTY;FTS;compaction;fork | `-k "f05"` | `uv run pyharness search "备份"` |
   | 6 | repair;CLI 全命令;桌面程序;ACP | `-k "f06"` | `pyharness-desktop` / `uv run pyharness acp` |

8. **演示判据**: 输出不必逐字一致(DEP"预期"为示意),硬判据 = 事件类型与 seq:JSONL 首行 `session.created`(seq=1)、事件序与 seq 连续 +1(INV-01/02);guard 拒绝看 `guard.rejected` 强同步落盘且真实文件零副作用(INV-05);降级看 `degraded_from` 字段(F013)。
9. **阶段收尾**: 更新 PRD §8.2 该阶段勾选清单;按 §6 回写 specs/文档;commit 注明阶段与门禁(如 `phase3: gate green, demo ok`)。
10. **v1.0 全量五连**(PRD §8.3): invariants 全绿 → acceptance 全绿 → `--cov` ≥85% → `python scripts/demo_phase{0..6}.py` 逐个确认 → `python scripts/check_size.py`(N6/N7)。

**常见坑**: 跳阶段门直接写后续(PRD §4.6 推演:并发 job 三态机错乱、无 spill 读 3MB 打爆上下文,返工扩大化)— 门没过不进下一阶段是硬纪律;把 docs 当"已实现"一次写 66 项(文档描述最终态,代码按阶段生长);只绿测试不跑 demo(pytest 不覆盖真实 LLM 链 + 演示叙事);阶段 import 方向漂移(靠 INV-08 兜底);headless 跑 plan/审批类命令(无审批通道,危险动作自动拒,R8),演示用交互终端。

---

# 4 部署与演示准备

**目的**: 开发机变演示机:配置就绪 → 场景可复现 → 桌面形态可用 → 录屏可控。权威 = DEP §3/§4/§9;预案 = CONSTRAINTS-08 §7。

**步骤**:

1. **生成配置并预检(离线)**: `uv run pyharness config init` → `config validate`(OK)→ `config show --json` 看生效值。文件缺失回落默认不报错;语法错 → CFG-601/602 启动中止,先修。
2. **填 API key(禁明文)**: A(推荐)`export DEEPSEEK_API_KEY="sk-…"`,永久化追加 `~/.bashrc` 重开终端,`printenv` 验证;B(多密钥)配 `security.credentials.file`,文件内只写 `env:NAME` 引用并 `chmod 600`。明文 key(≥32 位或 `sk-` 前缀)被 CFG-601 拒载(SECURITY §6)。
3. **降级链核对**: 无 qwen 凭据就把 `llm.fallback_models` 改 `[]`,否则 401 后降级也失败直落 LLM-310。
4. **首轮真实对话哨兵**: `uv run pyharness chat --once "你好,用一句话介绍你自己"` → 预期末尾 `[llm.usage] … cost_est=¥0.00xx`。输出 CRED-701 = key 没读到。录屏当天必先跑。
5. **选运行形态**: 交互演示用 `uv run pyharness chat --stream`(逐 token 流式,Ctrl-C 一次取消/两次退出);无人值守整理用 `uv run pyharness run "…"`;plan 用 `uv run pyharness plan "…"`(**必须交互终端**)。子命令速查 DEP §5.1。
6. **准备演示素材(工作区内自包含,零配置改动)**: 按 DEP §4.4 方案 A 一条 run 命令先造素材再归类(可重复);录屏版按 DEP §9 台本 S1 让 AI 自建 5 个文件(含"协程笔记_重复.md"制造删除诱因)。默认 strict 沙箱即可——strict 下删工作区文件被 g-danger critical 拦截正是录屏核心卖点。
7. **场景预演 ≥2 次**(CONSTRAINTS-08 §7): 记录预期片段/耗时/cost(硬闸 ¥1.00 内)。guard 拦截场景用 mock 模型保证必现,别赌真实 LLM 一定去删文件。预演不过先用 §5 排障,不带病录屏。
8. **桌面程序验证(阶段 6,面试主演示)**: ①`uv run pyharness desktop`(等价 `pyharness-desktop`:pywebview 窗口加载 `http://127.0.0.1:随机端口`,仅回环);②冒烟:会话列表→提问→SSE 流式→**轨迹时间线面板逐帧显示工具调用/guard 拦截/结果**→危险操作弹审批窗→预算仪表盘→关窗优雅退出;③exe 打包 = PyInstaller 单 exe,前端资源内嵌(specs/desktop.py.md;外壳阶段才引入 pyinstaller,H-02),入口 desktop 模块 `main()`(console_scripts `pyharness-desktop`),`--windowed` 无控制台;④依赖 WebView2(Win10/11 自带);⑤发布前在**干净机**双击验证,Defender 拦截则加白。
9. **录屏流程**(DEP §9 台本 S0-S6): S0 `chat --stream` 启动 → S1 自建素材 → S2 派整理任务 → S3 AI 试图删重复文件被 g-danger 拒(**核心**)→ S4 绕过也被 g-fs-path/g-danger 拦 → S5 另开终端 `session show <sid> | grep -n guard.rejected` + `ls -R` 验零副作用 → S6 `/quit`。录前:config validate OK + warmup 已跑 + 网络预案在手。
10. **现场与数据保全**: 备用网络 = 手机热点;备用 key 提前导出(断网重试/401 降级是阶段 2 专门演示点,留到对应台本再断)。重要演示会话录完即备份 `tar -C ~ -czf ~/pyharness-backup-$(date +%F).tar.gz .pyharness`(先退出进程,DEP §7.2)。若按方案 B 下调过沙箱/放行过域名,**录完改回 `strict` + `allowed_domains: []` 再 validate**(安全面只许收紧,CFG §5.1)。

**常见坑**: 明文 key 进 config → CFG-601 拒载;无 qwen 留默认降级链 → LLM-310;headless 危险全拒(演示用真终端,R8);**双开同一会话** → JSONL 损坏(一个会话一个终端);端口占用 → `netstat -ano | grep 8000`,`export PH_WEB_PORT=8001`;录屏现场才首次 warmup → 首轮慢/超时翻车;演示完忘复位安全级别,后续"危险操作没被拦"说不清。

---

# 5 故障恢复

**目的**: 异常按"回放定位 → 按码修复 → 回归验证"处置,不猜、不静默丢数据。权威 = CONSTRAINTS-08(F-01~F-10 + §6 七拍)、DEP §6(12 行排查表)、ERR.md。

**步骤**:

1. **标准排查七拍**(所有故障第一步,不是猜):
   ```text
   1 看状态:  /status —— 当前任务/轮数/成本/最后事件 seq
   2 回放:    tail -n 6 ~/.pyharness/sessions/<sid>.jsonl —— 崩溃前最后发生了什么
   3 查错误:  grep "ERROR" 日志 —— 错误码;排障重跑 export PH_LOG_LEVEL=debug
   4 对码查:  ERR.md 诊断流程(症状→根因→修复)
   5 复现:    最小复现(同输入重跑或 mock 故障源)
   6 修复+回归: 先写失败测试证明 bug → 修复 → 测试绿(TS-09)
   7 沉淀:    新故障模式 → 更新 CONSTRAINTS-08 与 DEP §6
   ```
2. **配置类(CFG-601/602,启动即中止)**: `config validate` 看字段+原因。典型:值超范围、YAML 制表符、裸 `D:\`(写 `D:/`)、安全单调被违(下调只许收紧,CFG §5.1)、字面量密钥。CFG-607 未知键警告可忽略。修复后重启。
3. **LLM 链路(LLM-301/302/310/399)**: 401/403 → `printenv DEEPSEEK_API_KEY` 空?`config show` 引用对?;超时(LLM-301 retryable,自动重试 4 次指数退避)→ `curl -sS -o /dev/null -w "%{http_code}" https://api.deepseek.com` 探活;401 自动降级 qwen-max(需备用凭据);双挂 → LLM-310/399 终态告知用户,**不静默不卡死**。
4. **会话损坏(F-03,seq 空洞/无法继续)**: `session show <sid>` 确认 → `uv run pyharness repair --session <sid>`(截断隔离 lost 计数 + 备份 `.corrupt-<ts>.jsonl` + 追加 `session.recovered`,修复是事件不是抹除 F060)→ `chat --session <sid> --once "继续"` 验证。**禁双开同会话**。
5. **进程崩溃(F-05,含 kill -9 演练)**: 强同步 3 类事件已落盘 → 重启 `repair --session <sid>` → 回放验证;丢的只有未 flush 中间态。演练 = DEP §4.7A:聊几句 → 任务管理器结束进程 → 重启 repair → 继续对话。
6. **死循环/模型失控(F-06)**: 最大轮数闸(默认 10)+ 每轮成本检查 + Ctrl-C 可中断(一次取消/两次退出),失控必在限内终止且日志可解释(max_turns 触发)。
7. **成本超限(F-07,F032 exhausted)**: `/budget` 查是否顶到硬闸(默认 ¥1.00/任务,warn 80%)。超闸是正常机制;调大只许人工改 `budget.task.max_cost_yuan` 重启。
8. **磁盘满/写失败(EVT-105/F-04)**: JSONL 写失败报 PERS 错误提示清理;JSONL >50MB 自动轮转(N11);删会话前先备份。
9. **工具挂起(F-09)/工具不执行**: 工具级超时(60s)掐掉 → `tool.error` 给模型改道;查模型原始 tool_call(TLB-801/802);"危险操作被拦"查 GRD-401 = 预期拦截,正常。
10. **备份恢复(F-10)**: 停进程 → 解包回 `~/.pyharness` → `repair --session <sid>` → 回放对比。SQLite(KV+FTS)可删库按日志重灌;**`sessions/*.jsonl` 丢了才真丢历史**。
11. **演示现场预案**(CONSTRAINTS-08 §7): key 失效 → 换备用 key;网络 → 手机热点;场景不可复现 → 用上次成功录屏(提前声明);日志 → 展示 JSONL 结构;guard 演示 → mock 模型保证必现。
12. **面试前演练自测**(CONSTRAINTS-08 §10): 拔网线跑(超时→重试→降级→LLM-399,不崩溃不静默);手动截断 JSONL 重启(repair 不静默丢);max_turns=3 跑长任务(第 3 轮终止);填错 key(报 CFG/凭据错误指向 env);危险工具(GRD 拦截+审计+零执行);长任务 Ctrl-C(可中断,强同步事件不丢)。

**常见坑**: 不回放直接猜 → 慢 10 倍且可能修错地方(第一动作永远回放,FR-02);改配置不 validate 直接跑;截断会话直接删文件/覆盖备份(先 repair,自带备份);修 bug 不带回归测试(TS-09);Windows 双开会话 → 回放/repair 全是假象(排障前确认无残留进程);演示现场临时改配置(所有改动提前一晚完成并 validate)。

---

# 6 文档维护

**目的**: 功能落地后文档与代码一致(文档描述最终态)。总原则:**冲突以 PRD-Core.md 为唯一权威**;DEP 可定义 PRD 未固定的 CLI 旗标,但须声明"由本文定义"。

**步骤**:

1. **变更定位**: 定 F 编号(新增按阶段内序,更新 §5.0 分布计数)、所属阶段、受影响文档面。文档面:需求 = PRD §5/§8;设计 = DIS-CORE/DIS-SEAM(GWT)+ ADD(ADR);协议 = EVENT-SCHEMA + ERR;规格 = specs/(每模块一份);运维 = DEP + CFG;约束 = CONSTRAINTS-01~08;测试 = CONSTRAINTS-06。
2. **同步 PRD-Core**: §5 该 F 编号五字段(描述/输入输出/边界/验收伪代码 ≥5 行、核心 ≥15 行/理由);§8.2 勾选清单 `- [ ]` → `- [x]`;阶段计数与 §4.6.2 里程碑表如有变化同步。**任何文档不得与 PRD 冲突**。
3. **同步 specs 面**: 改模块 → 更新 `specs/<模块>.py.md`(签名/伪代码/异常表/关联测试);新模块 → specs/README.md 索引对应阶段分组加行并更新总览(规格份数/函数数/体量)与编码顺序建议。实现偏离规格的,回写规格。
4. **同步设计/协议/错误面**: DIS 对应章伪代码与 GWT;事件改动 → EVENT-SCHEMA(信封/词表/JSONL),**只增不改**(§3.8,改旧事件 = 破回放,R11);新错误码 → ERR.md,且每码 ≥1 条测试(TS-06)。
5. **同步 DEP/CFG**: 命令变化 → DEP §5.1 总表 + §4 演示;故障 → DEP §6 表;验收 GWT → DEP §10;新配置键 → CFG.md 全量表(默认/层级/安全单调面)+ §3.4 检查清单。
6. **登记 IMPACT-MATRIX(影响矩阵)**: 行 = 变更项(F 编号 + 一句话),列 = 受影响文档,标注改动类型(新增/修订/删除/仅引用)。矩阵是后续一致性审查输入:按行核对,矛盾按 P0(阻断)→ P1(演示受影响)→ P2(文字)修复,修完验证闭环。
7. **同步测试与约束面**: 验收文件 `tests/acceptance/test_f{nnn}_{slug}.py` 与 F 编号一一对应;新约束写进对应 CONSTRAINTS-0X 并加验收项;测试策略变化更新 CONSTRAINTS-06。
8. **交叉一致性检查**: 更新 docs/README.md 导航表(分组/大小/描述)与 AI 编码地图;检查相对链接有效;历史格式改动跑旧会话回放回归(R11)。
9. **验证闭环 + 收尾**: 跑 §2 全量测试(不变量 + 验收 + 覆盖),抽查 PRD 字段 ↔ spec 函数 ↔ 验收测试 ↔ DEP 命令四处互指一致;同步 docs/docs_html/ 镜像(与流水线一致);`git add docs/ && git commit` 注明文档集与 F 编号。留意流水线预算:单文档 18-24KB,超 ≤3KB 可接受,过大拆章。

**常见坑**: 只改代码不改文档(面试讲不清,下轮按旧 spec 写错)——功能落地 = 代码+文档同步;PRD 与 specs 双写漂移(先改 PRD 再改 specs);改事件 schema 破回放(只增不改 + 旧会话回归);PRD 字段改了测试没跟上 = 假绿(三处同步:PRD ↔ acceptance ↔ §8.2 勾选);忘同步 docs_html/README 索引;在 DEP 乱加 PRD 没定的旗标(可加但须声明"由本文定义")。

---

## 关联文档

| 文档 | 关系 |
|---|---|
| DEP.md | 操作命令唯一来源:安装/运行/命令/故障排查/录屏台本(本文 §1/§3/§4/§5 引用其 §1-§10) |
| specs/README.md | 编码顺序建议与规格索引(本文 §3 顺序唯一依据;32 规格/313 函数) |
| PRD-Core.md | 唯一权威:§4.6 六阶段里程碑、§8.3 验收命令与阶段门、§5 功能 F001-F066 |
| CONSTRAINTS-06-Testing.md | 测试策略:TS-01 分层/TS-02 不变量优先/TS-04 GWT 数量/TS-10 覆盖/§6 假绿(本文 §2) |
| CONSTRAINTS-08-Failure.md | 故障模式 F-01~F-10、七拍 §6、演示预案 §7、演练 §10(本文 §5) |
| ERR.md | 错误码权威(CFG-601/602、CRED-701、LLM-301/302/310/399、EVT-105、GRD-401 等) |
| CFG.md | 配置权威:分层 L1-L4、安全单调面、sandbox 语义(本文 §4) |
| EVENT-SCHEMA.md | 事件信封/词表/JSONL 与只增不改约束(本文 §3 判据、§6 第 4 步) |
| README.md | 文档导航总索引(本文 §6 第 8 步要求同步) |

*— SOP v1.0 完 — 六大标准操作流程;命令与 DEP 对齐,判据取自 PRD §8.3 与 CONSTRAINTS-06/08。*

---

*本文档由 PyHarness 文档流水线产出。命令均为 Windows git-bash + uv 兼容。*
