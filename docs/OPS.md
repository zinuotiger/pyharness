# OPS — 运维操作手册

> **类型**:运维手册 = DEP.md(部署/命令/目录结构权威)的持续运行展开 + CFG.md §3/§6 运行面 + ERR.md(错误码)排查面;故障场景对齐 CONSTRAINTS-08 F-01~F-10 与 PRD-Core F007/F032/F060。
> **版本**:v1.0 | **日期**:2026-09-06 | **状态**:随代码阶段落地,描述最终态
> **读者**:许子诺 / 演示机与生产机运维者 / 值班排障。**权威声明**:命令取自 DEP.md(标注小节);配置键/热更边界以 CFG.md 为准;错误码以 ERR.md 为准;冲突时 PRD-Core 优先。

**8 章 + 附**:1 定位 · 2 日常操作 · 3 监控与告警 · 4 故障排查(S-01~S-09) · 5 数据管理 · 6 升级与变更 · 7 安全运维 · 8 常见坑(Windows) · 9 关联文档

**命令环境约定**(同 DEP):Windows 11 + git-bash;`~` = `C:\Users\<用户名>`;项目根 = `~/Desktop/mini-harness`;统一 `uv run` 前缀(无需激活 venv);中文路径/文件原生 UTF-8。**用户数据根 `~/.pyharness`(storage.root)** 不在项目内,删项目不影响历史(DEP §7.1)。

---

# 1 定位与核心概念

PyHarness **无后台常驻服务、无守护进程**:能力由 CLI(`uv run pyharness …`)、桌面程序(`pyharness-desktop`)、ACP 桥按需拉起,进程退出即"停止"(DEP §8)。运维对象三类:

| 对象 | 性质 | 位置 | 说明 |
|---|---|---|---|
| 会话事件日志 JSONL | **唯一真源,不可重建** | `~/.pyharness/sessions/<sid>.jsonl` | 只追加;>50MB 轮转 `{sid}.{n}.jsonl`(CFG §3.6) |
| SQLite(KV+FTS) | 派生视图,可重建 | `~/.pyharness/pyharness.db`(+`-wal`/`-shm`) | 删库后按日志重灌(F056),非备份对象 |
| 工作区 / spill | 会话产物 / 私有区 | `~/.pyharness/workspaces/{sid}/`、`~/.pyharness/spill/` | spill 权限 600、随会话生命周期(CFG §3.6) |

**两条运维铁律**:
1. **排障先回放、不靠猜**:事件 JSONL 不受日志级别影响、始终是审计真源(DEP §6);"丢了/错了/卡了"先 `session show <sid>` + `tail` JSONL 定位断点(CONSTRAINTS-08 FR-02)。
2. **错误必有码(F019)**:失败以 `域-编号` 表达,日志/事件 `code=XXX` 可 grep,按码查 ERR.md,禁止对着厂商文案猜(ERR §1)。

---

# 2 日常操作

## 2.1 启动前检查

```bash
uv run pyharness config validate          # ① 配置预检:CFG-60x 明细或 OK(DEP §3.2)
printenv DEEPSEEK_API_KEY >/dev/null && echo "key: set" || echo "key: MISSING"   # ② 只查存在性,不打印值
curl -sS -o /dev/null -w "api:%{http_code}\n" https://api.deepseek.com           # ③ 上游可达(预期 200)
uv run pyharness chat --once "你好,用一句话介绍你自己"   # ④ 冒烟(预期末尾 [llm.usage] 行)
```

任一步失败先修再启:①→S-08;②/③→S-01/S-02;④→§3.2 看日志。公司网先 `export https_proxy=http://127.0.0.1:7890`(DEP §1.4)。

## 2.2 启动

| 形态 | 命令 | 说明 |
|---|---|---|
| 交互对话(默认流式) | `uv run pyharness chat` | 单轮 `chat --once "…"`;带历史 `chat --session <sid>` |
| 无人值守任务 | `uv run pyharness run "<目标>"` | 单发即退;危险动作 headless 自动拒(R8) |
| 方案→批准→执行 | `uv run pyharness plan "<目标>"` | 须交互终端,勿走管道(DEP §4.5) |
| 桌面程序 | `pyharness-desktop` | pywebview 窗口,127.0.0.1 仅回环(DEP §4.7B) |
| ACP 桥 | `uv run pyharness acp` | JSON-RPC over stdio,引擎日志走 stderr |

启动即恢复会话:历史由 JSONL 回放派生,无第二份内存历史(INV-01);崩溃现场重启 `chat --session <sid> --once "继续"`(见 S-05)。

## 2.3 停止

- **优雅**:会话内 `/quit`(等价 `/exit`)或 Ctrl-D;桌面**关窗即优雅停服**(强同步事件先落盘,DEP §4.7B)。
- **Ctrl-C**:一次取消当前轮,两次退出(F064),勿狂按。
- **强制**(无响应):任务管理器结束进程即可——无中间态数据库,重启后 `repair --session <sid>` 收尾(§4 S-05),不损坏真源。全部进程退出 = 停止完成,无 stop 服务命令。

## 2.4 查看状态

| 想看什么 | 命令 | 预期 |
|---|---|---|
| 会话列表 | `uv run pyharness session` | sid/时间/标题(DEP §5.2) |
| 单会话回放 | `uv run pyharness session show <sid>` | seq 连续;可 `\| grep guard.rejected` / `\| grep task_id`(DEP §4.5/§9) |
| 原始 JSONL 尾部 | `tail -n 6 ~/.pyharness/sessions/<sid>.jsonl` | 末事件 + seq 落点 |
| 成本/预算 | 会话内 `/budget` 或 `uv run pyharness budget` | in/out/cost_est、硬闸 ¥1.00、warn 80%(DEP §4.3.4) |
| 队列/定时记录 | `uv run pyharness job list` | 结果保留 7 天(F051) |
| 全站统计 | `uv run pyharness stats` | 离线可用(DEP §5.1) |
| 生效配置 | `uv run pyharness config show --json` | 秘密只回显 `env:NAME`(INV-09) |

## 2.5 备份与恢复(先退出所有 pyharness 进程;DEP §7.2 权威命令)

```bash
tar -C ~ -czf ~/pyharness-backup-$(date +%F).tar.gz .pyharness   # 全量
cp -r ~/.pyharness/sessions ~/pyharness-sessions-$(date +%F)     # 最小集(只保真源,日常推荐)
# 恢复:停进程 → 解包 → repair 声明不一致 → 回放验证
tar -xzf ~/pyharness-backup-2026-09-06.tar.gz -C ~
uv run pyharness repair --session <sid>
uv run pyharness chat --session <sid> --once "验证恢复"
```

要点:SQLite 丢了**无需**手工处理(派生视图,删库重灌);`sessions/*.jsonl` 丢了才真丢历史。重要演示会话**录完即备份**;可配 Windows 任务计划程序定时执行 tar 命令(§3.5)。

---

# 3 监控与告警

## 3.1 日志位置与级别(CFG §3.4)

| 流 | 位置 | 级别/轮转 | 用途 |
|---|---|---|---|
| 引擎日志 | 默认**仅控制台**;设 `log.file`(如 `~/.pyharness/pyharness.log`)落盘 | `log.level`=debug/info/warning/error(默认 info);50MB 轮转留 3 份 | 排障:堆栈/重试序列/错误码行 |
| 事件 JSONL | `~/.pyharness/sessions/<sid>.jsonl` | 不受日志级别影响,全量审计 | **真源**,故障第一现场 |

- 临时提级免改文件:`export PH_LOG_LEVEL=debug`(env 白名单,DEP §3.4)再启动;堆栈只进本地 debug、绝不上行(ERR §6.1)。
- 运维基线:常开 `log.file` + 保留轮转 3 份,否则滚动日志关机即失。

## 3.2 怎么看异常(按序)

```bash
# ① 日志层:按级别+码定位(域前缀即 grep 线索,ERR §1.2)
grep -n "ERROR" ~/.pyharness/pyharness.log                      # 拒写/终态/暂停/拒绝类
grep -nE "code=(LLM-3|PERS-2|CFG-6|CYC-9)" ~/.pyharness/pyharness.log
# ② 事件层(真源):哪些会话出过什么码
grep -l "LLM-310" ~/.pyharness/sessions/*.jsonl                 # 全链失败会话
grep -c "PERS-202" ~/.pyharness/sessions/*.jsonl                # 落盘失败计数
# ③ 回放定位断点:崩溃/卡死前最后事件
uv run pyharness session show <sid> | tail -n 20
```

级别语义(ERR §6.1):`ERROR`=拒写/终态/暂停/拒绝(必须可 grep);`WARNING`=隔离/降级/重试/告警(主流程未断);`INFO`=事件化留痕;`DEBUG`=堆栈与 ctx。**WARNING 密集即异常前兆**(连串 LLM-303 = 上游开始限流)。

## 3.3 磁盘监控

```bash
df -h /c                                        # 盘符空间
du -sh ~/.pyharness/*                           # 谁在长胖
du -sh ~/.pyharness/sessions/*.jsonl | sort -rh | head -n 5    # 最大会话
ls -lh ~/.pyharness/pyharness.db*               # SQLite + WAL 体积
```

体积红线(CFG §3.4/§3.6):会话 JSONL 单文件 >50MB 自动轮转;spill ≤100MB/会话、单文件 ≤10MB;日志 50MB×3;SQLite WAL 为派生视图,异常膨胀可删库重灌(先备真源)。磁盘逼近满 → 先清 spill 与归档旧会话(§5.4),真源备份后再删,勿在满盘时跑长任务(触发 PERS-202 暂停,S-04)。

## 3.4 API 成本监控

| 手段 | 命令/位置 | 说明 |
|---|---|---|
| 会话内实时 | `/budget` | in/out/估算 ¥、硬闸 ¥1.00/任务、80% warn |
| CLI 报表 | `uv run pyharness budget` / `stats` | 跨会话累计 |
| 月度硬闸 | `budget.monthly.limit_yuan`(默认 0=关) | 启用后超限拒新任务;`PH_BUDGET_MONTHLY_LIMIT_YUAN` 可 env 覆盖;80% 告警 |
| 账单元数据 | 事件 `llm.usage` | 单价表只影响估算;**硬闸以 token 为准**(N5,F032) |
| 异常信号 | WARNING 日志、`exhausted` 终态 | 超闸=任务杀死;单会话降级 >5 次告警(F013) |

成本异常先查"任务是否失控"再查单价:死循环由轮数闸+预算闸代码内双保险兜底(§4 S-06/S-07);`budget.task.*` 只许人工改 config 重启,禁 env/CLI 静默放宽(CFG §4.5)。

## 3.5 巡检清单与被动告警

无内置外部推送(邮件/IM);告警面 = 控制台/日志/事件 + 桌面预算仪表盘(F065)。需被动告警时用 Windows 任务计划程序定时跑巡检脚本(示例,非零即异常):

```bash
uv run pyharness config validate || echo "ALERT config invalid"
[ "$(df -h /c | awk 'NR==2{print $5}' | tr -d '%')" -lt 90 ] || echo "ALERT disk>=90%"
grep -l "PERS-202" ~/.pyharness/sessions/*.jsonl 2>/dev/null && echo "ALERT persist failures"
```

| 巡检对象 | 命令 | 异常判定 |
|---|---|---|
| 配置 | `config validate` | 非 OK |
| 上游 | `curl -sS -o /dev/null -w "%{http_code}" https://api.deepseek.com` | 非 200 |
| 磁盘 | `df -h /c` | ≥90% |
| 落盘/全链失败 | grep `PERS-202` / `LLM-310` sessions/*.jsonl | 任一命中 |
| 未预期崩溃 | grep `CYC-999` 日志 | 命中=内部 bug,提单 |
| 成本 | `uv run pyharness budget` | 顶到硬闸/月度线 |
| 备份 | 检查当日 `~/pyharness-backup-*.tar.gz` | 缺失 |

---

# 4 故障排查场景(症状→诊断→修复→验证)

**排障前置 SOP**(CONSTRAINTS-08 §6 落地):① `session show <sid> \| tail` 回放定位断点 → ② grep `code=XXX` 查码 → ③ 对照本文 S-01~S-09 与 ERR.md §4 高频码流程 → ④ 最小复现 → ⑤ 按各场景"验证"收尾 → ⑥ 新故障回写 CONSTRAINTS-08。

## S-01 LLM API 全挂(F-01)

| 项 | 内容 |
|---|---|
| 症状 | 重试耗尽后降级,备用模型也失败 → **LLM-310 链尾全败**,任务终态 `reason=error`;日志 `llm.error`;不静默、不卡死 |
| 诊断 | `curl` 上游连通性;`grep LLM-310` 看会话与 err_hist;`llm.request` 看 `degraded_from` 链;主/备(qwen-max)凭据独立配置? |
| 修复 | 按序:修网络/代理(DEP §1.4)→ 核 key(S-08)→ 上游故障则等待;恢复由探针自动回切(`llm.probe.interval_s` 60s:3 败判 down、2 健回切,F033);应急可 `export PH_LLM_BASE_URL` 切可用兼容端点再启 |
| 验证 | `chat --once` 出 `[llm.usage]`;`llm.request` 无新 `degraded_from`;无新 LLM-310 |

## S-02 单模型限流(F-02)

| 项 | 内容 |
|---|---|
| 症状 | **LLM-303**(429/5xx/断网)→ 指数退避(第 N/4 次,1s×2ⁿ±30%)→ 连续 ≥2 次触发降级 `qwen-max`(`degraded_from` 入事件,F013) |
| 诊断 | `llm.retry` 事件序列(attempt/delay);`llm.request(degraded_from=…)`;区分 301 超时/302 认证/303 限流——只有可重试错退避,4xx 业务错(LLM-304)不重试不降级 |
| 修复 | 错峰或升上游配额;持久限流调 `llm.retry.attempts`(≤8)/`base_delay_s`,或收紧 `fallback_models`(列表整体替换,CFG §1.2)——改 config 重启;降级 >5 次/会话告警 → 查配额,别堆重试 |
| 验证 | 会话出回复且事件带 `degraded_from`;恢复后主模型回切;无 LLM-310 误判 |

## S-03 会话日志损坏(F-03)

| 项 | 内容 |
|---|---|
| 症状 | 重启后历史不完整;`session show` 报 seq 空洞/截断;回放坏行 → **PERS-201**(隔离记跳不中断);续写撞 seq → EVT-101 |
| 诊断 | `tail -n 10 …/<sid>.jsonl` 看尾部半行截断(崩溃常见);`session show` 定位空洞;确认**无进程双开同会话**(双写=空洞主因,INV-01) |
| 修复 | `uv run pyharness repair --session <sid>`:尾部截断隔离并备份 `{sid}.corrupt-<ts>.jsonl`,追加 `session.recovered`(修复是事件不是抹除,F060);中部坏行只隔离不自动删,删留人工定 |
| 验证 | `session show` seq 连续;`chat --session` 可继续;备份文件存在;恢复前后回放一致 |

## S-04 磁盘满(F-04)

| 项 | 内容 |
|---|---|
| 症状 | 写 JSONL 失败 → **PERS-202**:强同步三类(user.message/guard.rejected/approval.*)当场抛,异步路径重试 3 次后**会话暂停(拒新不丢旧)**;JSONL 不增长 |
| 诊断 | `df -h /c` 确认满;`du -sh ~/.pyharness/*` 找大户(sessions/spill/log/db-wal);查未 flush 攒批(≤0.5s/64 条,CFG §3.4) |
| 修复 | 清空间:已备份旧会话/死会话 spill 先清(§5.4),轮转日志删旧份;**勿在满盘时删真源**;清出后 `repair --session <sid>` 恢复,重试队列零丢失 |
| 验证 | `df` 有裕量;会话可续写且事件不丢;无新增 PERS-202;seq 连续 |

## S-05 进程崩溃(F-05)

| 项 | 内容 |
|---|---|
| 症状 | 进程被杀/闪退/断电;重启需恢复原会话;桌面窗口消失 |
| 诊断 | 崩溃只丢 ≤0.5s 攒批普通事件(强同步三类先写后返回,CFG §3.4);`tail` JSONL 看末事件是否半行截断;演练对照 CONSTRAINTS-08 §10(Ctrl+C 后重启强同步不丢) |
| 修复 | 直接重启恢复:`uv run pyharness chat --session <sid> --once "继续"`;repair 提示尾部截断 → 按 S-03 跑 `repair --session <sid>`;未 flush 中间态丢失属预期,不 panic |
| 验证 | 回放能引用崩溃前内容(INV-03);无 EVT-101;guard.rejected/approval.* 等强同步事件齐全 |

## S-06 死循环(模型失控,F-06)

| 项 | 内容 |
|---|---|
| 症状 | 任务不结束、同工具连发/重复写同一文件、轮数狂涨、进程被占输入无响应 |
| 诊断 | `loop.max_turns`(默认 30)超限 → **强制终态 `reason=max_turns`**(F007),日志可解释;`session show` 看同参数连发(无新信息;`loop.convergence_rounds`=3 应提前终止);查输入含注入源? |
| 修复 | 三闸(轮数/预算/取消)在代码内每轮强制,不在提示词里(SECURITY §1 D-1):失控 30 轮内必然终态;人工 Ctrl-C 一次取消当前轮、两次退出;反复失控调小 `PH_LOOP_MAX_TURNS` 重启(只许收紧) |
| 验证 | 失控任务终态且日志含 max_turns 原因;guard 拒绝齐全(副作用可回放审计,INV-06);正常任务不受影响 |

## S-07 成本超限(F-07)

| 项 | 内容 |
|---|---|
| 症状 | 任务被杀/`job exhausted`;`/budget` 顶到硬闸;warn 80% → paused 100% → exhausted(F032) |
| 诊断 | `budget` + `llm.usage` 聚合看烧在哪;区分单任务超 ¥1.00(默认 `budget.task.max_cost_yuan`)vs 月度超限(启用后拒新任务)vs 任务失控(S-06) |
| 修复 | 正常安全机制,先查任务设计(是否该拆小/收敛);确需放大 → 改 config.yaml 并**重启**(只许人工,禁 env 放宽,CFG §4.5);启用月度线做总量护栏;单价表修正只影响估算,硬闸看 token 不怕表错 |
| 验证 | `budget` 回落;新任务可入队;非预期 `exhausted` 不再出现 |

## S-08 配置错误(F-08)

| 项 | 内容 |
|---|---|
| 症状 | 启动即 **CFG-601 中止**,列字段+reason(file_parse/env_parse/type_range/structural/secret_literal/越权);**不静默用默认值**(半套配置比没配置危险) |
| 诊断 | `uv run pyharness config validate` 看"码+字段+建议";`config show --json` 比对生效值来源层(L1 默认→config.yaml→PH_→CLI,CFG §1);典型:YAML 制表符/裸 `D:\x`(写 `D:/`)、温度超范围、明文 `sk-`(secret_literal)、guard 全关/danger 调低(越权,只紧不松) |
| 修复 | 按 reason 逐条改回;秘密改 `env:NAME` 引用(CFG §7.1);多余键删除或忽略 CFG-607 警告(未知键不中止、向前兼容);改完复检 |
| 验证 | `config validate` OK;`chat --once` 冒烟通过;退出码 0;无 CFG-601 |

## S-09 工具执行挂起(F-09)

| 项 | 内容 |
|---|---|
| 症状 | 工具长时间无返回;`loop.step_timeout_s`(默认 60s)墙钟到 → **TLB-805** 超时回喂(tool.error,重试性由 LLM 判断);subprocess/PTY 超时 → **TO-301 已杀进程树** |
| 诊断 | tool.error 事件与工具名;exec 类核对 `security.sandbox.proc_wallclock_s`(60s)与 `proc_mem_limit_mb`(默认 0=不限,Windows 尽力而为);PTY 查单例占用(PTY-001);`tasklist \| grep python` 查残留 |
| 修复 | 单次挂起:等超时自动杀或 Ctrl-C,LLM 收错自行换招;确需长任务 → 调大 `loop.step_timeout_s`(≤600)重启,确认超时=杀整个进程树(F052);反复挂起换实现(分批/异步)或查工具 Bug |
| 验证 | 有 tool.result/tool.error 回喂(不静默);会话可继续;无悬挂 python 子进程;连败 2 次自动终止该轮属预期(F026) |

**故障速查**(详表:ERR.md §7、CONSTRAINTS-08 §9):

| 症状 | 码 | 第一动作 |
|---|---|---|
| AI 不回复/全挂 | LLM-301/303→310 | curl 上游 + grep LLM-310 |
| 单模型限流 | LLM-303 | 看 llm.retry 序列,错峰/升配额 |
| 重启失忆/回放坏 | PERS-201 / EVT-101 | repair --session |
| 写不进日志 | PERS-202 | 查磁盘(§3.3) |
| 失控不结束 | max_turns 终态 | 查轮数闸触发原因 |
| 危险操作被拦 | GRD-401(正常) | 确认 policy_ref 属预期拦截 |
| 工具不执行 | TLB-802/803→GRD-401 | 四关定位:契约/校验/guard/审批 |
| 审批不弹 | APR-501(headless 即拒,预期) | 交互终端重跑 |
| 未预期崩溃 | CYC-999 | 本地 debug 堆栈,按事件回放提单 |

---

# 5 数据管理

## 5.1 目录地图(storage.root=`~/.pyharness`)

| 目录/文件 | 内容 | 生命周期与上限 |
|---|---|---|
| `sessions/{sid}.jsonl` | 事件真源,只追加 | >50MB 轮转 `{sid}.{n}.jsonl` 按序合并(F011) |
| `sessions/{sid}.corrupt-<ts>.jsonl` | repair 隔离截断备份 | 确认无误后可删 |
| `pyharness.db`(+wal/shm) | SQLite KV+FTS 派生视图 | 可删库重灌(F056);WAL 串行 |
| `workspaces/{sid}/` | 会话工作区(文件工具边界) | 随会话;备份对象 |
| `spill/` | >64KB 读入/抓取归档私有区,600 | ≤100MB/会话、单文件 ≤10MB(F039/PERS-223);随会话生命周期 |
| `config.yaml`/`config.d/*.yaml`/`credentials.yaml` | 配置与凭据(600) | 只存引用不存秘密值 |
| 引擎日志(设 `log.file` 后) | 排障日志 | 50MB×3 轮转 |

## 5.2 会话日志(JSONL)

- 命名固定 `{session_id}.jsonl`(PRD §3.6);轮转文件按序号合并即完整会话。
- 校验:seq 连续 +1、ts UTC 微秒、首事件 `session.created`(INV-01);怀疑损坏走 S-03。
- 删除:会话删除按 `storage.archive_days`(默认 30 天)归档,0=立即清;手工清理**先备份真源**(§2.5)再 `rm ~/.pyharness/sessions/<sid>.jsonl`——删了不可重建。

## 5.3 备份策略

| 级别 | 命令 | 频率建议 |
|---|---|---|
| 全量 | `tar -C ~ -czf ~/pyharness-backup-$(date +%F).tar.gz .pyharness` | 每周 + 版本升级前 |
| 最小集 | `cp -r ~/.pyharness/sessions …` | 每日(任务计划程序);重要演示当场备份 |
| 恢复演练 | 解包 → repair → 回放对比 | 每月一次(CONSTRAINTS-08 §8) |

## 5.4 清理策略

| 对象 | 策略 | 说明 |
|---|---|---|
| 旧会话 | 备份后删除;归档期 `storage.archive_days` 控制 | 先 `cp` sessions 再 `rm`(§5.2) |
| spill | 随会话生命周期回收;死会话残留手工清 | `du -sh ~/.pyharness/spill` 按会话清;600 目录勿整删 |
| 引擎日志 | 自动 50MB×3 轮转 | 备份归档后可删旧份 |
| job 结果 | 自动保留 7 天(F051) | `job list` 查看 |
| SQLite | 不清理;异常膨胀停进程删库,重启重灌 | 删前确认 sessions 备份完好 |

## 5.5 spill 特别说明

spill 是**会话私有超大内容归档区**(读入 >64KB 转 `spill_ref`,抓取 >32KB 截断转引用,CFG §3.2):内容只进引用不爆上下文。要点:①权限 600 勿放宽;②越权读(ref `../` 逃逸)→ PERS-222 拒绝零读取;③超限 → PERS-223 拒写回喂;④单会话 >100MB 说明反复大读,配合 S-09 查工具行为。

---

# 6 升级与变更

## 6.1 升级步骤(先备份,可回滚)

```bash
# ① 备份:tar -C ~ -czf ~/pre-upgrade-$(date +%F).tar.gz .pyharness
# ② 拉新代码:cd ~/Desktop/mini-harness && git pull(或换新包)
# ③ 依赖更新:uv sync        # .venv 不自动跟随源码,改依赖必跑(DEP §2.5)
# ④ 配置预检:uv run pyharness config validate
# ⑤ 冒烟:    uv run pyharness chat --once "你好"
# ⑥ 旧会话抽检:uv run pyharness session show <旧sid> | tail -n 3
```

- **回滚**:git 切旧版本 → `uv sync` → 用 ① 备份还原 `~/.pyharness` → `config validate` + 冒烟;SQLite 版本不兼容直接删库重灌(真源在备份里)。
- 升级后必查配置漂移:`config show --json` 与升级前比对——新版读旧配置容忍(§6.3),但废弃键的兜底默认值可能非你所愿。

## 6.2 配置变更分级与热更新(CFG §6 权威)

配置默认**全部需重启**(C3,Settings 不可变快照);热更仅限"观测/无害"四组,经内部重载 API 触发、逐项留 `config.updated(key, old, new, by)` 事件,**CLI 无热更子命令——日常统一:改文件 → `config validate` → 重启**。

| 变更对象 | 生效 | 说明 |
|---|---|---|
| `log.level`/`log.file` | ✅ 热更 | 即时,排障提级最快路径 |
| `llm.probe.interval_s` | ✅ 热更 | 下一周期,纯观测 |
| `llm.usage.unit_price` | ✅ 热更 | 下笔记账,只影响估算 |
| `budget.monthly.alert_ratio` | ✅ 热更 | 即时,纯提醒 |
| `llm.model`/温度/max_tokens/超时/重试/降级 | ❌ 重启 | 客户端单点构造(F012) |
| `loop.max_turns`/窗口/收敛、`budget.task.*` | ❌ 重启 | 三闸可变=绕过审计(F007/F032) |
| `security.*`(沙箱/deny/danger/guard/审批) | ❌ 重启 | 单调 L0 防漂移;下调走显式确认+`sandbox.opened` 事件 |
| secret-ref 秘密 | ❌ 重启 | **例外:TTL ≤5min 轮换**(§7.1) |
| `plugins.*`/`storage.*`/`shell.*` | ❌ 重启 | 装载序/启动期固定 |

运行期对只读键热更 → **CFG-608 拒绝**(不静默、不部分生效)并留事件——见 CFG-608 即查来源。**安全下调纪律**:`security.*` 禁 env/CLI 放宽(CFG §4.5);沙箱 strict→basic/off 只许 config.yaml + 启动确认 + `sandbox.opened`,演示完改回(DEP §4.4)。

## 6.3 版本兼容

- **配置向前兼容**:未知键/白名单外 `PH_*` → CFG-607 警告不中止;破坏性变更升 `config.schema_version`(L1=1)给迁移提示(CFG §5.3)。
- **错误码只增不改**(ERR §1.5)、**事件类型只增不改**(PRD §3.8):升级后旧回放/旧脚本不断。
- 命令行面以 `pyharness --help` 与 DEP §5 为准,升级前 diff 该表;业务值(降级链/单价表)属配置不属代码,随 6.1 ④⑥ 验证。

---

# 7 安全运维

## 7.1 凭据轮换(CRED 域)

```bash
# ① 写入新引用目标(二选一,不落明文)
export DEEPSEEK_API_KEY="sk-新密钥"     # 环境变量(重开终端生效)
# 或编辑 ~/.pyharness/credentials.yaml(600,内容只写 env: 引用)
# ② 吊销旧 key(厂商控制台)
# ③ 生效:轮换 ≤5min 自动生效(cache_ttl_s=300,CFG §6.2 例外);其他秘密改动需重启
# ④ 验证
uv run pyharness chat --once "你好"      # 预期出 [llm.usage],无 CRED-701/LLM-302
```

要点:密钥缺失 → **CRED-701 拒**(绝不空串续跑);credentials.yaml 权限 >600 → **CRED-702 启动拒载**;配置层只存 `env:NAME`/`file:PATH`,字面量 `sk-`/≥32 位被 CFG-601(secret_literal)拒载;备用模型用独立 key,主 key 泄露不暴露备用通道(ERR §2.4)。**外泄处置**:CRED-703(脱敏自检发现疑似 key)→ 立即吊销+轮换,按审计日志追外发点(ERR §3)。

## 7.2 日志脱敏验证(INV-09)

```bash
uv run pyharness config show --json | grep -E "sk-|[A-Za-z0-9]{32,}" || echo "no secrets leaked"
grep -rnE "sk-[A-Za-z0-9]{16,}|[A-Za-z0-9]{32,}" ~/.pyharness/sessions/ ~/.pyharness/*.log 2>/dev/null || echo "clean"
```

`log.redact_enabled=true`(默认)全出口脱敏:事件 payload/错误消息/tool.result summary/spill/PTY io/debug,密钥显示 `sk-***last4`;`config show` 无 `--show-secrets`;guard.rejected 只记 policy_ref 无参数原文(SECURITY §8.3)。**每周跑一次 ② 作安全基线**。

## 7.3 权限最小化

| 面 | 基线(strict 默认) | 纪律 |
|---|---|---|
| 沙箱 | `security.sandbox.level: strict`(独立根+allowlist 空+高危 deny+subprocess 禁) | 下调须 config+确认+`sandbox.opened`,事后改回 |
| deny 工具 | `deny_tools_extra: []` 只增 | deny 空/通配过宽 → CFG-601 |
| danger | high→审批、critical→拒不可审批 | `tool_danger_extra` 只许调高;调低 → CFG-601 |
| guard | 五内置默认全激活(g-fs-path/g-credential-read/g-net-outbound/g-exec/g-overwrite) | 只许关单个留 `guard.disabled` 事件;全关 → CFG-601 |
| 网络 | `allowed_domains: []` = 禁一切外发 | 演示放行(`[wttr.in]` 等)后删回空 |
| 凭据文件 | credentials.yaml 600 | CRED-702 启动校验 |
| headless | stdin 非 tty = 无审批通道 | high/critical 自动拒(APR-501/R8),无配置开关 |
| 工作区外读 | `read_extra_dirs` 默认空 | 只读例外须 config+guard 留痕(F055) |

单调性总则:安全面**只紧不松、调整留痕、禁 env/CLI 静默放宽**(CFG §4.5/SECURITY §4)。

## 7.4 审计

审计真源 = 会话 JSONL(append-only,含强同步三类);SQLite/FTS 只是加速视图。示例:`session show <sid> | grep -n guard.rejected`(每行带 guard_id/policy_ref/call_id);`grep -l 'code=CRED-703' ~/.pyharness/sessions/*.jsonl`。配置留痕事件:启动加载摘要、`guard.disabled`、`sandbox.opened`、`config.updated`、CFG-607 警告集(CFG §7.3)——查"谁改过策略"看这几类。日志/配置不进版本库、gitignore;备份 tar 含会话内容,勿放公共盘。

---

# 8 常见坑(Windows 特有)

| # | 现象 | 根因与修复 | 来源 |
|---|---|---|---|
| 1 | PowerShell/cmd 中文乱码 | 用 git-bash;cmd 场景 `chcp 65001` + `set PYTHONUTF8=1` | DEP §2.5-4 |
| 2 | YAML 路径反斜杠被吞(报配置错) | 裸 `D:\x` 被 YAML 当转义 → 写 `D:/杂乱文件夹` | DEP §2.5-5、S-08 |
| 3 | Web 端口被占(8000) | `netstat -ano \| grep 8000` 查 PID;`export PH_WEB_PORT=8001` 再启;或 `taskkill //F //PID <pid>`(git-bash 双斜杠) | DEP §6-9 |
| 4 | 裸 `pyharness` 找不到 | venv 未激活:Windows 激活脚本在 `.venv/Scripts/` 不在 bin/;或统一 `uv run pyharness` | DEP §2.5-3 |
| 5 | `uv: command not found` | uv 在 `~/.local/bin`,补 PATH 后**重开终端** | DEP §2.5-1 |
| 6 | `uv sync` 卡住/SSL 错 | `UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple uv sync`;公司网配代理 | DEP §1.4 |
| 7 | 改了代码/依赖但行为没变 | `.venv` 不自动跟随源码 → `uv sync` 重装 | DEP §2.5-7 |
| 8 | Defender 首次拦截 python/uv 联网 | 放行;项目零 WSL/Docker,勿为此装 WSL | DEP §2.5-6 |
| 9 | 双开同会话/同目录多实例 | seq 冲突 → EVT-101/PERS-201;同一 `storage.root` 单实例,双开会话先关旧进程 | DEP §6-8 |
| 10 | 进程残留 | Windows 无 kill -9 语义:任务管理器结束进程树;`tasklist \| grep python` 补杀 | §4 S-09 |
| 11 | Ctrl-C 表现"奇怪" | 一次=取消当前轮,两次=退出(F064);狂按=直接退出 | DEP §5.2 |
| 12 | chmod 600"好像没用" | git-bash chmod 对 NTFS 尽力而为;以 CRED-702 启动校验为准,必要时 icacls 收紧 ACL | DEP §3.3B |
| 13 | 桌面连不上/白屏 | 默认 `127.0.0.1:8000` 仅回环;绑非回环须配 `shell.web.token` 否则 CFG-601;查端口占用(坑 3) | CFG §3.8 |
| 14 | 备份 tar 中文名乱码 | git-bash tar 默认 UTF-8;解包用同一环境,勿混用系统 tar | §2.5 |

---

# 9 关联文档

| 文档 | 关键章节 | 关系 |
|---|---|---|
| DEP.md | §5 CLI 参考 / §6 故障排查 / §7 数据位置与备份 | **命令/路径权威**,OPS 命令溯源处 |
| CFG.md | §3 全量配置目录 / §4 env 映射 / §6 热更新 / §7 配置安全 | **配置键/默认值/热更边界权威** |
| ERR.md | §2 码目录 / §3 明细 / §4 高频排查流程 / §6 日志规范 | 错误码查表依据,S-01~S-09 码均在此登记 |
| CONSTRAINTS-08-Failure.md | §1 F-01~F-10 / §6 排障 SOP / §8 备份 / §10 演练清单 | 故障场景母本,本文 S-01~S-09 对应 F-01~F-09 |
| PRD-Core.md | F007 三闸 / F032 预算 / F060 repair / §3.6 落盘 | 功能语义最终权威,冲突裁决方 |
| SECURITY.md | §4 单调性 / §6 凭据脱敏 / §7 沙箱 | §7 安全运维依据 |
| EVENT-SCHEMA.md | §6 EVT 处置 / §7 功能码 | 事件字段与 QUE/JOB/PTY/TO 码定义 |
| specs/README.md | specs 索引 | 实现层细节(函数级)按需深挖 |

---

# 附:一致性声明

1. 本文命令逐条取自 DEP.md(标小节);配置键/热更边界引用 CFG §3/§6;错误码(LLM-301~304/310、PERS-201/202、CFG-601/607/608、CRED-701~703、GRD-401、APR-501、TLB-805、TO-301、CYC-999)以 ERR.md 为唯一查表依据;冲突以 PRD-Core 为准。
2. S-01~S-09 对应 CONSTRAINTS-08 F-01~F-09,四步(症状→诊断→修复→验证)闭合;F-10(备份恢复)在 §2.5 落地。CONSTRAINTS-08 中 LLM-399/LLM-305 等未登记码不在本文使用,链尾全败统一以 LLM-310 表达。
3. 本文为值班/巡检/演示机日常运维落地文件;实现细节以 specs/ 函数级规格为准。

*— OPS v1.0 完 — 运维操作手册:日常操作/监控告警/S-01~S-09 排障/数据管理/升级变更/安全运维/Windows 坑,命令与 DEP 对齐,码与 ERR 对齐。*

---

*本文档由 PyHarness 文档流水线产出。命令均为 Windows git-bash + uv 兼容。*
