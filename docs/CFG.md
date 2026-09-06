# CFG.md — PyHarness 配置系统全量规范

> **类型**:配置系统完整规格 = PRD-Core F021 字段级展开:分层模型、全量配置目录、环境变量映射、校验、重载边界、配置安全;冲突以 PRD §7/NFR、SECURITY.md、DIS-SEAM.md 为准。
> **版本**:v1.0 | **日期**:2026-09-06 | **读者**:许子诺 / AI 编码 Agent(照本文实现 `config.py`、`config` 子命令、`test_f021_config.py` 与安全 GWT)。中文,无 TS/TODO/占位。

---

# 0 定位与设计总则

**一句话**:配置系统 = "**默认即安全、任何环境能跑、启动时只读**"的分层 Settings——代码默认值兜底 → `config.yaml` 承载可版本化策略 → `PH_` 环境变量适配部署 → CLI 覆盖单次运行;全量经 pydantic schema 校验,非法配置在启动第 1 步被拒(CFG-601),不带半套配置运行。

- **C1 分层合并、键级覆盖**:默认→文件→环境→CLI 四层 deep_merge(F021)。
- **C2 默认即安全**:沙箱 strict、网络 allowlist 空、审批 120s 超时即拒、预算 1 元/任务、headless 即拒(N13/SEC §7)。
- **C3 启动只读、不热重载**:Settings 为不可变快照;仅 §6 观测键可热更并留 `config.updated` 事件。
- **C4 敏感项只存引用**:配置层存 `env:NAME`/`file:PATH`,全出口脱敏(SEC §6/INV-09)。

**加载时点**:启动第 1 步"加载配置(CFG)"(PRD §2.5);`ctx.config` 为只读门面(DIS-SEAM §3.2)。

---

# 1 配置分层模型

## 1.1 四层来源与优先级

| 层 | 来源 | 优先级 | 例 |
|---|---|---|---|
| L1 | 代码默认值(§3"默认值"列=唯一权威) | 最低 | `loop.max_turns=30` |
| L2 | `config.yaml`(§2.1) | ▲ | `loop.max_turns: 100` |
| L3 | 环境变量 `PH_` 前缀(仅 §4.3 白名单) | ▲ | `PH_LOOP_MAX_TURNS=100` |
| L4 | CLI 覆盖(键面=§4.3 白名单∪config 路径) | 最高 | `pyharness chat --max-turns 100` |

合并结果整体过 `Settings.model_validate`;L1 保证任意键缺省必可加载。

## 1.2 合并语义(deep_merge)

| 类型 | 规则 | 例 |
|---|---|---|
| 标量 | 后层覆盖前层 | 文件 `llm.temperature: 0.3` 覆盖 L1 的 0.7 |
| 字典 | 键级深合并,两键并集 | `tool_danger_extra` 文件+默认两项并存 |
| 列表 | **整体替换**,不逐元素合并 | 文件 `fallback_models: [qwen-max, glm-4]` 完全替换 L1 |
| null | 显式 null=删键回落 L1;注释掉≠null | 恢复默认=删行 |

## 1.3 层细则

- **L1**:§3 默认值为 `config.py` 字面量,禁读环境/文件生成(可复现)。
- **L2**:文件缺失→回落 L1 记 info;语法错→**CFG-601(file_parse)中止**(半套配置比没配置危险,SECURITY §6"缺失即拒绝");`config.d/*.yaml` 文件名升序逐文件 merge。
- **L3**:只读白名单内 `PH_*`;名单外→CFG-607 警告。**秘密不在白名单**(§4.5)。
- **L4**:覆盖面=L3 白名单+`--config`;秘密不经 CLI 参数(argv/日志泄露);同管线同校验(ADR-011)。

## 1.4 只读与热更新边界

Settings 加载后不可变,`ctx.config` 只读,运行期禁改写。策略类键(deny/沙箱/预算/guard)运行中漂移破坏 guard 单调与可审计性(SECURITY §4 L0)→一律重启;仅 §6 观测键可热更。改环境变量需重启;秘密 TTL≤5min 例外(轮换)。

---

# 2 配置文件:格式与位置

## 2.1 位置与搜索顺序

| 序 | 来源 | 说明 |
|---|---|---|
| 1 | `--config <path>` | 显式指定后不再搜其余路径 |
| 2 | `PH_CFG_PATH` | 部署环境统一指向 |
| 3 | `~/.pyharness/config.yaml` | 用户默认;缺失→回落 L1 不报错 |
| 4 | `~/.pyharness/config.d/*.yaml` | 片段,文件名升序逐文件 merge |

最小文件(§8 为完整注释模板):`llm: {api_key: env:DEEPSEEK_API_KEY}` + `security: {sandbox: {level: strict}}`。

## 2.2 格式选型:YAML(不选 TOML)

**结论:YAML。** 配置是**人类书写的策略文件**(中文注释、4 级嵌套、map/list 混合):YAML 表达与注释友好度占优(TOML 无原生 null、深层表头啰嗦),类型宽松风险由 pydantic schema 收口(§5),安全在加载层保证而非格式兜底——故不选类型更严的 TOML。`.toml` 扩展名可选兼容(同 merge 管线,不新增语义);已知坑(制表符、`D:\` 裸值)由 `config validate` 暴露。

## 2.3 目录语义与权限

配置只放策略与偏好:不放会话事实(真源=事件日志)、不放秘密值(§7.1)、不放可派生数据。文件与父目录建议 600,gitignore(SECURITY §6);片段校验失败→CFG-601 列来源文件+字段。

## 2.4 config 子命令(F064 离线)

`config show`(生效值,秘密只回显 `env:NAME`;`--json`)/ `config validate [path]`(只校验)/ `config init`(生成 §8 模板)。

---

# 3 全量配置项目录(权威清单)

## 3.0 阅读约定

**键名**:点分=YAML 层级(`llm.timeout.total_s`);env/CLI 名见 §4.3。**覆盖列**:L2=仅文件;**L2+**=文件+env+CLI(L2/L3/L4,白名单面一致 §4.5);空=仅 L1 禁外部覆盖。secret-ref 默认形如 `env:NAME`(语法 §7.1);枚举 `a|b`;溯源引用以被引章节为准。

## 3.1 模型域(F012/F013/F028/F029/F030/F033)

| 键 | 类型 | 默认值 | 取值范围 | 说明 | 覆盖 |
|---|---|---|---|---|---|
| `llm.model` | str | `deepseek-chat` | 已注册适配器名(F030) | 主模型;401→LLM-302 触发降级 | L2+ |
| `llm.fallback_models` | list[str] | `[qwen-max]` | 适配器名,可空 | 备用降级链(主模型后);全链败→LLM-310;整体替换 | L2+ |
| `llm.base_url` | str | `https://api.deepseek.com` | 合法 URL | OpenAI 兼容端点(ADR-007) | L2+ |
| `llm.api_key` | secret-ref | `env:DEEPSEEK_API_KEY` | `env:NAME`/`file:PATH` | 主模型凭据引用(F016);缺失使用时→CRED-701 拒 | L2 |
| `llm.temperature` | float | `0.7` | 0-1.5(F012) | 采样温度 | L2+ |
| `llm.max_tokens` | int | `4096` | 1-32768 | 单次输出上限(F012) | L2+ |
| `llm.timeout.connect_s` | int | `10` | 1-300 | 连接超时(F017) | L2+ |
| `llm.timeout.first_token_s` | int | `60` | 1-600,须>connect_s | 首 token 超时 | L2+ |
| `llm.timeout.total_s` | int | `180` | 1-1800,须>first_token_s | 请求总超时 | L2+ |
| `llm.retry.attempts` | int | `4` | 0-8 | 可重试错(429/5xx/断网/超时)退避上限;4xx 业务错不重试 | L2+ |
| `llm.retry.base_delay_s` | float | `1.0` | 0.1-60 | 退避基数 1s×2^n(F028) | L2 |
| `llm.retry.jitter` | float | `0.3` | 0-0.5 | 抖动 ±30%(F028) | L2 |
| `llm.degrade.enabled` | bool | `true` | true/false | 降级总开关;false=主模型败即 LLM-310 | L2+ |
| `llm.degrade.rate_limit_consecutive` | int | `2` | 1-5 | 连续 N 次限流才降级(F013) | L2 |
| `llm.degrade.max_per_session` | int | `5` | 1-20 | 单会话降级>5→告警(F013) | L2 |
| `llm.probe.interval_s` | int | `60` | 10-3600 | 探针周期(F033);3 败 down/2 健回切固定 | L2+ |
| `llm.usage.unit_price` | dict | 见右 | `{模型: {in_per_million, out_per_million}}` 元 | 单价表(F029/N14),`deepseek-chat {2.0, 8.0}`(N5);只影响估算/报表 | L2 |

**降级条件固定**:认证错/连续限流(达阈值)/网络不可达才降;参数错(LLM-304)不降只回喂;降级记 `llm.request(degraded_from)`。

## 3.2 循环域(F007/F010/F017/F022/F026/F043/F058)
| 键 | 类型 | 默认值 | 取值范围 | 说明 | 覆盖 |
|---|---|---|---|---|---|
| `loop.max_turns` | int | `30` | 1-1000 | 轮数上限,超限强制终态 max_turns(F007);只许收紧 | L2+ |
| `loop.step_timeout_s` | int | `60` | 1-600 | 工具/子进程默认墙钟上限(F017/F052);超时杀进程树 | L2+ |
| `loop.input_queue_max` | int | `10` | 1-100 | running 中新输入队深,>10 拒新 BUSY(F007) | L2 |
| `loop.task_queue_max` | int | `32` | 1-1024 | 任务队列深度,满拒 QUE-001(F043) | L2 |
| `loop.convergence_rounds` | int | `3` | 1-10 | 连续 N 轮无新信息提前终止(F007) | L2 |
| `loop.tool_concurrency` | int | `1` | 1-3 | 同轮 tool_calls 并发;默认串行(F022) | L2+ |
| `loop.thread_pool_size` | int | `4` | 1-32 | 同步 handler 线程池(PRD §2.5) | L2 |
| `loop.max_arg_failures_per_round` | int | `2` | 1-5 | 同工具连续 N 次校验败→终止该轮(F026) | L2 |
| `loop.max_context_tokens` | int | `65536` | 1024-1048576 | 派生历史窗口 64k tokens(F010/N3),超窗头部截断 | L2+ |
| `loop.compact.trigger_ratio` | float | `0.75` | 0.5-0.95 | 历史≥窗口 75% 触发压缩(F058) | L2 |
| `loop.compact.min_new_rounds` | int | `10` | 1-100 | 距上次压缩新增≥10 轮才可压(F058) | L2 |
| `loop.compact.keep_recent_rounds` | int | `12` | 1-100 | 保留最近 12 轮原文(F058) | L2 |
| `loop.compact.summarize_budget_tokens` | int | `400` | 50-2000 | 段摘要 token 预算(F058) | L2 |
| `loop.content.file_spill_bytes` | int | `65536` | 1024-1048576 | 读文件>64KB 转 spill(F034) | L2 |
| `loop.content.fetch_max_chars` | int | `32768` | 1024-1048576 | 抓取>32KB 截断转 spill(F038) | L2 |
| `loop.content.search_result_chars` | int | `8000` | 512-32768 | 搜索结果合计≤8KB(F037) | L2 |
| `loop.message_edit_window_s` | int | `1800` | 60-86400 | 消息编辑 30min 窗(F063) | L2 |

**三闸纪律固定**:轮数/预算/取消在 agent-loop 内每轮强制(F007),三闸在代码里不在提示词里(SECURITY §1 D-1)。

## 3.3 安全域(F014/F015/F023/F037/F038/F054/F061;对齐 SECURITY §4-§7)

| 键 | 类型 | 默认值 | 取值范围 | 说明 | 覆盖 |
|---|---|---|---|---|---|
| `security.sandbox.level` | enum | `strict` | strict/basic/off | strict=独立根+allowlist 空+高危入 deny+subprocess 禁;basic=g-exec 需授权;off=会话 workspace(SEC §7)。**下调须显式人类确认+`sandbox.opened` 事件** | L2(文件+确认) |
| `security.sandbox.proc_wallclock_s` | int | `60` | 1-3600 | 子进程墙钟限额,超时杀树(F052) | L2 |
| `security.sandbox.proc_mem_limit_mb` | int | `0` | 0=不限;≥1=MB | 子进程内存限额;Windows 尽力而为(F054) | L2 |
| `security.network.allowed_domains` | list[str] | `[]` | 域名列表 | 外发 allowlist,空=禁一切外发 POL-NET-1(F023/N13);域名归一 | L2 |
| `security.network.web_search_per_session` | int | `20` | 0-1000 | 搜索次数闸,0=禁用(F037) | L2 |
| `security.policy.deny_tools_extra` | list[str] | `[]` | 工具名 | 权限预设基集上**只增**的 deny 追加(GRD-401) | L2 |
| `security.policy.read_extra_dirs` | list[str] | `[]` | 绝对目录 | workspace 外显式授权**只读**例外,须 config+guard 留痕(F055) | L2 |
| `security.tool_danger_extra` | dict | `{}` | `{工具: none/low/high/critical}` | danger 只许**调高收紧**(low→high 转审批);调低→CFG-601 越权拒 | L2 |
| `security.guards.disabled` | list[str] | `[]` | 五内置 guard 真子集 | 显式关单个(g-fs-path/g-credential-read/g-net-outbound/g-exec/g-overwrite),留 `guard.disabled`;**全关/未注册名→CFG-601 越权拒** | L2 |
| `security.approval.ttl_ms` | int | `120000` | 1000-3600000 | 审批 TTL 120s,超时=denied 安全默认(F015/SEC §5) | L2 |
| `security.approval.merge_window_s` | int | `60` | 0-600 | 同工具同参合并窗防轰炸;0=不合并(F015) | L2 |
| `security.trustlist.enabled` | bool | `false` | true/false | 信任名单默认关;仅交互会话可开;headless 永不生效;不随 fork 继承;全留事件;critical 不在名单(SEC §5) | L2(文件+确认) |
| `security.credentials.file` | str | `~/.pyharness/credentials.yaml` | 路径 | 凭据文件 600+gitignore(F016/SEC §6),内容同走 env 引用 | L2 |
| `security.credentials.cache_ttl_s` | int | `300` | 0-3600 | 秘密 TTL≤5min,轮换至多 5min 生效(SEC §6) | L2 |
| `security.attachment.max_bytes` | int | `10485760` | 1-104857600 | 附件≤10MB/张(F061) | L2 |
| `security.attachment.max_per_message` | int | `5` | 1-20 | ≤5 张/消息(F061) | L2 |
| `security.attachment.mime_whitelist` | list[str] | `[image/jpeg,image/png,image/webp,image/gif]` | MIME | 类型白名单+魔数校验(F061) | L2 |

**权限预设(内置,只可追加)**:strict=deny 含 `fs.delete_file` 等+allowlist 空+subprocess 禁;basic=高危部分限制、g-exec 需授权;off=deny 空、workspace 边界仍在(F055)。

**guard 纪律固定**:五内置默认全激活不可整体关闭(F023);high→审批、critical→拒不可审批(SEC §4.2);granted 重入链起点(F014);`guard.rejected`/`approval.*` 强同步。

## 3.4 日志域(F009/F011)

| 键 | 类型 | 默认值 | 取值范围 | 说明 | 覆盖 |
|---|---|---|---|---|---|
| `log.level` | enum | `info` | debug/info/warning/error | 引擎日志级别;事件 JSONL 不受影响(审计全量) | L2+ |
| `log.file` | str | 空 | 路径/空 | 空=仅控制台 | L2+ |
| `log.rotate_bytes` | int | `52428800` | 1MB-1GB | 应用日志 50MB 轮转(N11),留 3 份 | L2 |
| `log.redact_enabled` | bool | `true` | true/false | 全出口脱敏(INV-09):事件/错误/summary/spill/PTY io | L2+ |
| `log.jsonl.flush_interval_s` | float | `0.5` | 0.05-10 | 普通事件攒批 flush≤0.5s(PRD §3.6) | L2 |
| `log.jsonl.flush_batch` | int | `64` | 1-1024 | ≥64 条即 flush(PRD §3.6) | L2 |

**强同步三类固定**:`user.message`/`guard.rejected`/`approval.*` 立即写+flush 成功才返回(F009);崩溃最多丢 ≤0.5s 攒批事件,repair(F060)声明。

## 3.5 成本域(F032/N5;F029)

| 键 | 类型 | 默认值 | 取值范围 | 说明 | 覆盖 |
|---|---|---|---|---|---|
| `budget.task.max_in_tokens` | int | `2000000` | 1-10^8 | 单任务输入 token 硬闸(N5) | L2+ |
| `budget.task.max_out_tokens` | int | `50000` | 1-10^7 | 单任务输出 token 硬闸;判定以 token 为准(F032) | L2+ |
| `budget.task.max_cost_yuan` | float | `1.0` | 0.01-10^6 | 单任务估算成本硬闸(<1 元,N5);job 超预算直接杀死 | L2+ |
| `budget.warn_ratio` | float | `0.8` | 0.5-0.99 | 80% warn;100% paused;超=exhausted 终态(F032) | L2 |
| `budget.subagent_ratio` | float | `0.25` | 0.01-1 | 子 agent=主会话×1/4,计入父任务(F049) | L2 |
| `budget.monthly.limit_yuan` | float | `0` | 0=不启用;>0=元 | 月度成本估算累计(跨会话按日志聚合);超限拒新任务 | L2+ |
| `budget.monthly.alert_ratio` | float | `0.8` | 0.5-0.99 | 月预算 80% 告警 | L2+ |

**检查点固定**:agent-loop 每轮前查(F007);单价表只影响估算/报表(N14)。

## 3.6 存储域(F011/F039/F055/F056/F057)

| 键 | 类型 | 默认值 | 取值范围 | 说明 | 覆盖 |
|---|---|---|---|---|---|
| `storage.root` | str | `~/.pyharness` | 路径 | 基目录;其余目录键相对值以此为根;建议 600 | L2+ |
| `storage.sessions_dir` | str | `~/.pyharness/sessions` | 路径 | 会话 JSONL 目录,文件 `{session_id}.jsonl` 名固定(PRD §3.6);轮转 `{sid}.{n}.jsonl` 按序合并(F011) | L2+ |
| `storage.db_path` | str | `~/.pyharness/pyharness.db` | 路径 | SQLite:KV(F056)+FTS5(F057);WAL 串行;派生视图非真源(ADR-006) | L2+ |
| `storage.workspaces_dir` | str | `~/.pyharness/workspaces` | 路径 | 会话 workspace 根,实际 `{root}/{session_id}/`(F055) | L2+ |
| `storage.archive_days` | int | `30` | 0-3650 | 会话删除归档 30 天(F055);0=立即清 | L2 |
| `storage.jsonl.rotate_bytes` | int | `52428800` | 1MB-1GB | 会话日志 >50MB 轮转(F011/N11) | L2 |
| `storage.spill_dir` | str | `~/.pyharness/spill` | 路径 | spill 私有区 600,随会话生命周期(F039),workspace 外 | L2 |
| `storage.spill.max_per_file_bytes` | int | `10485760` | 1KB-100MB | spill 单文件≤10MB(F039);摘要=头 500 字+行数+大小(固定) | L2 |
| `storage.spill.max_per_session_mb` | int | `100` | 1-10240 | spill≤100MB/会话(N3) | L2 |
| `storage.fts.index_batch_ms` | int | `200` | 10-5000 | FTS 异步攒批 200ms(F057) | L2 |
| `storage.fts.query_timeout_s` | int | `5` | 1-60 | FTS 查询超时 5s(F057) | L2 |
| `storage.fts.result_limit` | int | `20` | 1-100 | ≤20 条;中文≥2 字/英文≥3 字符固定(F057) | L2 |

**存储纪律固定**:事件日志=唯一真源;SQLite/FTS/KV 全为派生视图可重建(F060);KV 禁存凭据与不可重建信息(F056/原则 1)。

## 3.7 插件域(F001-F006;装载序 DIS-SEAM §4.6)

| 键 | 类型 | 默认值 | 取值范围 | 说明 | 覆盖 |
|---|---|---|---|---|---|
| `plugins.enabled` | list[str] | 内置能力(空=只装内置) | 插件/能力 id | 启用名单;名单外不装载;脊柱 8 模块不可配(BUS-002) | L2+ |
| `plugins.dir` | str | `~/.pyharness/plugins` | 路径 | 第三方插件包目录(含 manifest);不存在=忽略 | L2+ |
| `plugins.ctx_lazy` | bool | `true` | true/false | ctx 服务惰性装载(DIS-SEAM §3.3);false=启动期全量 enter | L2 |
| `plugins.pre_activate` | list[str] | `[storage.spill, credentials, storage.kv]` | 基础能力 id | 预激活:启动第⑤步先行 enter(DIS-SEAM §3.4) | L2 |
| `plugins.priority` | dict | `{}` | `{插件id: int}` | 同层无依赖插件次序,小者先;默认 0 按注册序 | L2 |
| `plugins.backpressure_limit` | int | `1000` | 1-100000 | 总线背压阈值,满=拒新不丢旧(F005) | L2 |
| `plugins.deadletter_samples` | int | `100` | 0-10000 | 死信保留最近样本数(F005) | L2 |

**装载顺序固定**(DIS-SEAM §4.6):脊柱→内置→第三方;同层按 requires 拓扑序(循环依赖检出败,F006),无依赖按注册序;卸载逆序;新装不回溯(F004)。运行期装/卸走 install/uninstall API,`plugins.enabled` 增删需重启。

## 3.8 外壳域(补充域;F064-F066)

| 键 | 类型 | 默认值 | 取值范围 | 说明 | 覆盖 |
|---|---|---|---|---|---|
| `shell.web.host` | str | `127.0.0.1` | IP/主机名 | 默认仅回环(F065);绑非回环须已配 token,否则 CFG-601(结构缺失)拒启 | L2+ |
| `shell.web.port` | int | `8000` | 1-65535 | Web 端口 | L2+ |
| `shell.web.token` | secret-ref | 空 | `env:NAME`/`file:PATH` | 远程访问 token(F065);失败→401 | L2 |
| `shell.acp.enabled` | bool | `false` | true/false | ACP 桥(F066)监听 stdio,一次一请求串行 | L2+ |

**headless 默认拒绝固定(R8)**:CLI 管道/后台 job 无审批通道→danger≥high 直接 denied(F064/F051);由 stdin 是否 tty 判定,**无配置开关**。

## 3.9 固定常量(不提供配置项)

事件 payload≤64KB(N3);seq 单调、ts 框架 UTC;事件类型只增不改(PRD §3.8);granted 重入链、approval_id=请求 seq 防重放、critical 不可审批;danger 枚举不可自定义(F008);schedule 最小 1 分钟、深夜 23-7 禁危险模板(F048);subagent 深度≤3/并发≤8/无父级担保(F049);jobs 并发≤4、结果 7 天清理(F051);PTY 单会话≤1(F053);工具输出 stdout 32KB/stderr 8KB 截断(F052);todo≤20(F040);env 前缀 `PH_` 固定。

---

# 4 环境变量映射

## 4.1 两套 env 机制(勿混淆)

- **A 配置值覆盖**:`PH_`+键映射(§4.3)→覆盖 L1/L2,进同一 merge 管线,如 `PH_LOOP_MAX_TURNS=100`。
- **B 秘密来源**:任意名 env 经 `env:NAME` 引用(F016 单口),如 `DEEPSEEK_API_KEY` + `llm.api_key: env:DEEPSEEK_API_KEY`;不进 Settings 明文。

## 4.2 命名规范

`PH_` + 域大写 + `_` + 键路径大写(`llm.timeout.total_s`→`PH_LLM_TIMEOUT_TOTAL_S`);前缀固定(F021)。

## 4.3 映射表(白名单=env 与 CLI 共用唯一键面)

| 环境变量 | 覆盖键 |
|---|---|
| `PH_CFG_PATH` | (文件路径,指向配置文件) |
| `PH_LLM_MODEL`/`PH_LLM_BASE_URL` | `llm.model`/`llm.base_url` |
| `PH_LLM_FALLBACK_MODELS` | `llm.fallback_models`(逗号/JSON 列表) |
| `PH_LLM_TEMPERATURE`/`PH_LLM_MAX_TOKENS` | `llm.temperature`/`llm.max_tokens` |
| `PH_LLM_TIMEOUT_CONNECT_S`/`_FIRST_TOKEN_S`/`_TOTAL_S` | `llm.timeout.*`(F017) |
| `PH_LLM_RETRY_ATTEMPTS` | `llm.retry.attempts` |
| `PH_LLM_DEGRADE_ENABLED`/`PH_LLM_PROBE_INTERVAL_S` | `llm.degrade.enabled`/`llm.probe.interval_s` |
| `PH_LOOP_MAX_TURNS`/`PH_LOOP_STEP_TIMEOUT_S`/`PH_LOOP_TOOL_CONCURRENCY`/`PH_LOOP_MAX_CONTEXT_TOKENS` | `loop.*` 对应键 |
| `PH_LOG_LEVEL`/`PH_LOG_FILE`/`PH_LOG_REDACT_ENABLED` | `log.*` 对应键 |
| `PH_BUDGET_TASK_MAX_IN_TOKENS`/`_MAX_OUT_TOKENS`/`_MAX_COST_YUAN` | `budget.task.*`(N5) |
| `PH_BUDGET_MONTHLY_LIMIT_YUAN`/`_ALERT_RATIO` | `budget.monthly.*` |
| `PH_STORAGE_ROOT`/`PH_STORAGE_SESSIONS_DIR`/`PH_STORAGE_DB_PATH` | `storage.*` 对应键 |
| `PH_PLUGINS_ENABLED`/`PH_PLUGINS_DIR` | `plugins.enabled`/`plugins.dir` |
| `PH_WEB_HOST`/`PH_WEB_PORT` | `shell.web.host`/`shell.web.port`(非回环需 token) |
| `PH_ACP_ENABLED` | `shell.acp.enabled` |

## 4.4 解析规则

bool:`1/0/true/false/yes/no`;int/float 严格解析,失败→**CFG-601(env_parse)中止**;列表:逗号或 JSON 数组,空串=未设置;env 覆盖同走 §5 校验。

## 4.5 禁止面(结构上不可被 env/CLI 放宽)

`security.*` 全部、secret-ref、预算 warn/追加类——安全下调需人类确认/留痕,禁 env/CLI **静默触发**(SEC §7);策略键留在可版本化的 config.yaml。env 属可信部署者,**收紧方向允许**;凡覆盖列为"L2"的键不在上表。

---

# 5 配置校验

## 5.1 校验管线(加载时顺序执行)

①YAML 语法与 `PH_*` 值解析(§4.4);②类型/范围/枚举(pydantic 逐键);③结构约束(时间档序、guard disabled ⊆ 五内置、非回环须 token);④秘密语法(`env:`/`file:` 前缀);⑤安全单调越权(guard 全关/关未注册名/danger 调低/deny 空/通配过宽);⑥未知键收集。①-⑤失败一律 **CFG-601 启动中止**(ctx.fields 列字段,ctx.reason 区分 file_parse/env_parse/type_range/structural/secret_literal/越权);⑥→CFG-607 警告不中止。输出"码+字段+建议"(ADR-011);`config validate` 离线预检。

## 5.2 CFG-6xx 错误码(601-603 与 ERR.md 已登记一致;607/608 为本文提议新登记,编号取 ERR §1 预留空洞,落地同步 ERR)

| 码 | 语义 | 处置 |
|---|---|---|
| CFG-601 | 配置非法/越权:合并校验失败(类型/范围/枚举/文件语法/env 解析/结构缺失/秘密明文)+越权放宽+装配序违反(DIS-SEAM §3.5 同码) | 拒绝启动,列字段;只紧不松(ERR §2.7) |
| CFG-602 | system-prompt 模板缺变量(装配域,ERR 已登记;勿当文件解析错) | 结构化错误,会话继续 |
| CFG-603 | 护栏段构建失败(ERR 已登记;缺护栏不发请求) | 拒绝本次 LLM 调用 |
| CFG-607 | 未知配置键/白名单外 `PH_*` | 警告不中止 |
| CFG-608 | 尝试热更只读键(§6) | 拒绝并提示重启,留事件 |

## 5.3 未知键与向前兼容

未知键→CFG-607 警告(含来源文件)不中止(F021),容忍新版本读旧配置;`config.schema_version`(L1=1,int)记录配置面版本,破坏性变更升版本号给迁移提示。

---

# 6 运行时重载

## 6.1 原则

默认**全部需重启**(C3)。热更新仅限"观测/无害/不触及安全策略"类:显式调用重载 API、逐项留 `config.updated(key, old, new, by)` 事件——可审计运维动作,非配置漂移后门。

## 6.2 策略表

| 键(组) | 热更新 | 生效 | 理由 |
|---|---|---|---|
| `log.level`/`log.file` | ✅ | 即时/重开句柄 | 观测排障、日志轮换 |
| `llm.probe.interval_s` | ✅ | 下一周期 | 纯观测 |
| `llm.usage.unit_price` | ✅ | 下笔记账 | 软约束(N14);硬闸看 token |
| `budget.monthly.alert_ratio` | ✅ | 即时 | 纯提醒 |
| `llm.model`/温度/`max_tokens`/超时/重试/降级 | ❌ 重启 | — | 客户端单点构造(F012) |
| `loop.max_turns`/窗口/收敛/`budget.task.*` | ❌ 重启 | — | 三闸/预算可变=绕过审计(F007/F032) |
| `security.*`(沙箱/deny/danger/guard/审批/信任名单) | ❌ 重启 | — | 单调 L0 防漂移(SEC §4);下调走会话内确认 |
| secret-ref 秘密 | ❌ 重启 | 例外:TTL≤5min 轮换 | SECURITY §6 |
| `plugins.*` | ❌ 重启 | 运行期走 install/uninstall API | 装载序确定性(DIS-SEAM §4.6) |
| `storage.*`/`shell.*` | ❌ 重启 | — | 启动期固定 |

## 6.3 机制与失败

热更键以 `RuntimeMutable` 标记于 Settings;重载 API 校验请求键 ∈ 集合,否则 **CFG-608** 拒绝(不静默忽略、不部分生效);热更值同过 §5 校验;每次热更写 `config.updated`(actor=system)。

---

# 7 配置安全

## 7.1 秘密引用语法

secret-ref 型键(`llm.api_key`/`shell.web.token`/credentials.yaml 内键)只接受:①`env:NAME`(启动读环境变量,F016 单口);②`file:PATH`(读文件首行,须 600)。**字面量密钥拒绝**:值匹配 `sk-` 前缀或 ≥32 位疑似密钥 → CFG-601(secret_literal)拒载并提示改引用。秘密不落 SQLite/KV/FTS/事件日志(SECURITY §6/§8)。

## 7.2 脱敏与出口(INV-09)

`log.redact_enabled=true` 时全出口 redact:事件 payload/错误消息/tool.result summary/spill/PTY io/debug;32+ 位疑似密钥打码 `sk-***last4`。`config show`/错误回显只显示引用不显示值,无 `--show-secrets`。审计例外:guard.rejected 的 policy_ref 无参数原文、args_summary 摘要化(SECURITY §8.3)。

## 7.3 文件权限与安全事件

配置文件与父目录建议 600,gitignore;credentials.yaml 600 强制。配置相关留痕:启动加载摘要(键数/来源层,不含值)、`guard.disabled`、`sandbox.opened`(下调)、`config.updated`(热更)、CFG-607 警告集。错误消息不打印 env 值/完整文件(只给路径+行号)。

---

# 8 完整示例配置文件(中文注释;权威值见 §3)

```yaml
# ~/.pyharness/config.yaml — 只写要改的键;未写的用代码默认值
llm:
  model: deepseek-chat            # 主模型;401 自动降级
  api_key: env:DEEPSEEK_API_KEY   # 秘密引用不写明文;缺时 CRED-701
  fallback_models: [qwen-max]     # 备用降级链;整体替换 L1
  temperature: 0.7                # 0-1.5
  max_tokens: 4096
  timeout: {connect_s: 10, first_token_s: 60, total_s: 180}  # 三档超时
  retry: {attempts: 4, base_delay_s: 1.0, jitter: 0.3}
  degrade: {enabled: true, rate_limit_consecutive: 2, max_per_session: 5}
  probe: {interval_s: 60}
  usage:
    unit_price:                   # 单价表(元/百万 token)只影响估算
      deepseek-chat: {in_per_million: 2.0, out_per_million: 8.0}
      qwen-max:    {in_per_million: 4.0, out_per_million: 12.0}
loop:
  max_turns: 30                   # 轮数上限;只许收紧
  step_timeout_s: 60
  tool_concurrency: 1             # 默认串行
  max_context_tokens: 65536       # 窗口 64k tokens
  compact: {trigger_ratio: 0.75, min_new_rounds: 10, keep_recent_rounds: 12}
security:
  sandbox: {level: strict, proc_wallclock_s: 60}  # strict/basic/off;下调须确认+事件
  network: {allowed_domains: []}  # 空=禁一切外发
  policy: {deny_tools_extra: [], read_extra_dirs: []}
  guards: {disabled: []}          # 关单个才写;禁全关
  approval: {ttl_ms: 120000, merge_window_s: 60}   # 超时=denied
  trustlist: {enabled: false}     # 默认关
  credentials: {file: ~/.pyharness/credentials.yaml, cache_ttl_s: 300}
budget:
  task: {max_in_tokens: 2000000, max_out_tokens: 50000, max_cost_yuan: 1.0}
  warn_ratio: 0.8                 # 80% warn / 100% paused / 超 exhausted
  subagent_ratio: 0.25
  monthly: {limit_yuan: 0, alert_ratio: 0.8}
log:
  level: info                     # 事件 JSONL 不受影响
  redact_enabled: true            # 全出口脱敏
storage:
  root: ~/.pyharness
  sessions_dir: ~/.pyharness/sessions     # 会话 JSONL=唯一真源
  db_path: ~/.pyharness/pyharness.db
  workspaces_dir: ~/.pyharness/workspaces
  spill_dir: ~/.pyharness/spill           # spill 私有区 600
plugins:
  enabled: []                     # 空=只装内置
  ctx_lazy: true
  backpressure_limit: 1000
shell:
  web: {host: 127.0.0.1, port: 8000, token: ""}   # 默认仅回环
  acp: {enabled: false}
```

---

# 9 关联测试(test_f021_config.py + tests/security;GWT)

**G1 四层合并与优先级**:Given L1 `loop.max_turns=30`、文件 100、`PH_LOOP_MAX_TURNS=200`、CLI `--max-turns 300`;When 加载;Then 生效 300,逐层摘除得 200/100/30;字典键级合并、列表整体替换逐项断言(§1.2)。

**G2 类型/范围校验失败→CFG-601**:Given 文件写 `llm.temperature: 3.0`、`loop.max_turns: "abc"`;When 启动加载;Then 抛 CFG-601,ctx.fields 含两键各原因;进程中止退出码非 0;`config validate` 输出同明细。

**G3 字面量密钥拒载**:Given `llm.api_key: "sk-…(32+ 位)"` 明文;When 加载;Then CFG-601(secret_literal)拒载;改 `env:` 引用且环境缺变量→加载通过、使用时 CRED-701。

**G4 热更策略表(CFG-608)**:Given 运行中 Settings;When 热更 `log.level`;Then 生效并写 `config.updated`;When 热更 `security.sandbox.level`/`loop.max_turns`;Then CFG-608 拒绝、值不变、事件留痕;`config show` 秘密全为 `env:NAME` 引用(INV-09 grep 无值)。

**G5 安全单调:收紧放行、放宽拒绝**:Given 工具 `net.upload` 声明 danger=low;When `tool_danger_extra` 写 `{net.upload: high}`;Then 加载通过、调用转审批;When 写 `{fs.delete_file: low}` 或 `guards.disabled` 全五内置;Then CFG-601(越权)拒载。

**G6 未知键/白名单外 env→CFG-607**:Given 文件含拼错键 `llm.temperatur: 0.3`、环境含 `PH_LLM_TEMPERATUR`;When 加载;Then 不中止、两处收警告、生效温度仍 L1 默认 0.7。

---

# 附:与既有文档一致性声明

1. 配置键/默认值/编号溯源 PRD-Core(F007/F010/F012/F013/F015/F017/F021/F023/F026/F028/F029/F032/F033/F034/F037/F038/F039/F043/F049/F051/F052/F054/F055/F056/F057/F058/F061/F063-F066、§2.5/§3.6、§7 NFR)与 SECURITY.md(§5/§6/§7)、DIS-SEAM.md(§3 ctx.lazy/§4.6);冲突以 PRD §7 与 SECURITY.md 为准。
2. 错误码 CFG-601~603 与 ERR.md 已登记语义一致(F019 配置域);CFG-607/608 为本文提议新登记(编号取 ERR 预留空洞),落地同步 ERR;遵守 ADR-011:按码决策、不裸 raise。
3. 本文件为 `pyharness/config.py`、`config` 子命令(F064)与 test_f021_config.py 唯一权威规格;实现阶段以 specs/ 函数级规格为准。
