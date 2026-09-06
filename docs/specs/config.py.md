# specs/config.py.md — 编码规格

> 目标代码:pyharness/config.py,PRD-Core F021(配置管理)的唯一编码规格,字段级权威为 CFG.md(默认值/键目录/校验/重载边界以 CFG.md §3/§5/§6 为准)。AI 编码 Agent 只读本文件即可写出配置系统全量代码。权威源:CFG.md(全量规范)、PRD-Core.md F021、ERR.md §2.7(CFG-6xx)、DIS-SEAM.md §3.2(ctx.config 只读门面)。全中文,仅 Python,禁 TS。禁止:热重载策略键、字面量密钥、运行时改写 Settings(原则:C3 启动只读、默认即安全)。

## 模块职责
一句话:分层配置引擎——代码默认值(L1)→ config.yaml(L2)→ PH_ 环境变量(L3)→ CLI 覆盖(L4)四层 deep_merge 成不可变 Settings,经 pydantic schema + 安全单调校验(非法 → CFG-601 启动中止),秘密只存 `env:NAME`/`file:PATH` 引用、全出口脱敏,仅观测键可热更。

## 依赖
| import | 用途 |
|---|---|
| os、pathlib | 配置文件搜索顺序、环境变量读取、路径展开(~) |
| yaml(safe_load) | YAML 解析(禁 unsafe_load);语法错 → CFG-601(file_parse) |
| pydantic(BaseModel、Field、ConfigDict、model_validator) | 全量 schema 校验(类型/范围/枚举/结构) |
| typing(Any、Optional、Literal) | 键类型标注 |
| dotmap/自研 flatten | 点分键 ↔ 嵌套 dict 互转(禁第三方配置框架) |
| pyharness.errors(raise_code、PyHError) | CFG-601/602/603/607/608 唯一出口 |
| pyharness.bus(EventBus) | 热更成功写 config.updated 事件(actor=system) |
| logging | 启动加载摘要/警告(不含值) |

## 常量与键面

### DEFAULTS(L1 权威默认,代码字面量)
**功能**:CFG.md §3 默认值列的唯一落地;禁读环境/文件生成(可复现);任意键缺省必可加载。
**伪代码**:
```python
DEFAULTS: dict = {
    "config": {"schema_version": 1},
    "llm": {"model": "deepseek-chat", "base_url": "https://api.deepseek.com",
            "api_key": "env:DEEPSEEK_API_KEY", "temperature": 0.7, "max_tokens": 4096,
            "fallback_models": ["qwen-max"],
            "timeout": {"connect_s": 10, "first_token_s": 60, "total_s": 180},
            "retry": {"attempts": 4, "base_delay_s": 1.0, "jitter": 0.3},
            "degrade": {"enabled": True, "rate_limit_consecutive": 2, "max_per_session": 5},
            "probe": {"interval_s": 60},
            "usage": {"unit_price": {"deepseek-chat": {"in_per_million": 2.0,
                                                       "out_per_million": 8.0},
                                     "qwen-max": {"in_per_million": 4.0,
                                                  "out_per_million": 12.0}}}},
    "loop": {"max_turns": 30, "step_timeout_s": 60, "input_queue_max": 10,
             "task_queue_max": 32, "convergence_rounds": 3, "tool_concurrency": 1,
             "thread_pool_size": 4, "max_arg_failures_per_round": 2,
             "max_context_tokens": 65536,
             "compact": {"trigger_ratio": 0.75, "min_new_rounds": 10,
                         "keep_recent_rounds": 12, "summarize_budget_tokens": 400},
             "content": {"file_spill_bytes": 65536, "fetch_max_chars": 32768,
                         "search_result_chars": 8000},
             "message_edit_window_s": 1800},
    "security": {"sandbox": {"level": "strict", "proc_wallclock_s": 60,
                             "proc_mem_limit_mb": 0},
                 "network": {"allowed_domains": [], "web_search_per_session": 20},
                 "policy": {"deny_tools_extra": [], "read_extra_dirs": []},
                 "tool_danger_extra": {},
                 "guards": {"disabled": []},
                 "approval": {"ttl_ms": 120000, "merge_window_s": 60},
                 "trustlist": {"enabled": False},
                 "credentials": {"file": "~/.pyharness/credentials.yaml",
                                 "cache_ttl_s": 300},
                 "attachment": {"max_bytes": 10485760, "max_per_message": 5,
                                "mime_whitelist": ["image/jpeg", "image/png",
                                                   "image/webp", "image/gif"]}},
    "budget": {"task": {"max_in_tokens": 2_000_000, "max_out_tokens": 50_000,
                        "max_cost_yuan": 1.0},
               "warn_ratio": 0.8, "subagent_ratio": 0.25,
               "monthly": {"limit_yuan": 0, "alert_ratio": 0.8}},
    "log": {"level": "info", "file": "", "rotate_bytes": 52_428_800,
            "redact_enabled": True,
            "jsonl": {"flush_interval_s": 0.5, "flush_batch": 64}},
    "storage": {"root": "~/.pyharness", "sessions_dir": "~/.pyharness/sessions",
                "db_path": "~/.pyharness/pyharness.db",
                "workspaces_dir": "~/.pyharness/workspaces",
                "archive_days": 30,
                "jsonl": {"rotate_bytes": 52_428_800},
                "spill_dir": "~/.pyharness/spill",
                "spill": {"max_per_file_bytes": 10_485_760,
                          "max_per_session_mb": 100},
                "fts": {"index_batch_ms": 200, "query_timeout_s": 5,
                        "result_limit": 20}},
    "plugins": {"enabled": [], "dir": "~/.pyharness/plugins", "ctx_lazy": True,
                "pre_activate": ["storage.spill", "credentials", "storage.kv"],
                "priority": {}, "backpressure_limit": 1000,
                "deadletter_samples": 100},
    "shell": {"web": {"host": "127.0.0.1", "port": 8000, "token": ""},
              "acp": {"enabled": False}},
}
```
**关联测试**:TC-F021(四层合并,逐层摘除得 200/100/30)→ tests/acceptance/test_f021_config.py(§8)。

### ENV_WHITELIST 环境变量映射(PH_ 前缀,CFG §4.3)
**功能**:env/CLI 共用唯一键面;名单外 PH_* → CFG-607 警告不中止;秘密不在白名单。
**伪代码**:
```python
ENV_WHITELIST: dict[str, str] = {          # 环境变量 → 覆盖键(点分)
    "PH_CFG_PATH": "cfg_path",             # 特殊:指向配置文件而非配置值
    "PH_LLM_MODEL": "llm.model", "PH_LLM_BASE_URL": "llm.base_url",
    "PH_LLM_FALLBACK_MODELS": "llm.fallback_models",
    "PH_LLM_TEMPERATURE": "llm.temperature", "PH_LLM_MAX_TOKENS": "llm.max_tokens",
    "PH_LLM_TIMEOUT_CONNECT_S": "llm.timeout.connect_s",
    "PH_LLM_TIMEOUT_FIRST_TOKEN_S": "llm.timeout.first_token_s",
    "PH_LLM_TIMEOUT_TOTAL_S": "llm.timeout.total_s",
    "PH_LLM_RETRY_ATTEMPTS": "llm.retry.attempts",
    "PH_LLM_DEGRADE_ENABLED": "llm.degrade.enabled",
    "PH_LLM_PROBE_INTERVAL_S": "llm.probe.interval_s",
    "PH_LOOP_MAX_TURNS": "loop.max_turns", "PH_LOOP_STEP_TIMEOUT_S": "loop.step_timeout_s",
    "PH_LOOP_TOOL_CONCURRENCY": "loop.tool_concurrency",
    "PH_LOOP_MAX_CONTEXT_TOKENS": "loop.max_context_tokens",
    "PH_LOG_LEVEL": "log.level", "PH_LOG_FILE": "log.file",
    "PH_LOG_REDACT_ENABLED": "log.redact_enabled",
    "PH_BUDGET_TASK_MAX_IN_TOKENS": "budget.task.max_in_tokens",
    "PH_BUDGET_TASK_MAX_OUT_TOKENS": "budget.task.max_out_tokens",
    "PH_BUDGET_TASK_MAX_COST_YUAN": "budget.task.max_cost_yuan",
    "PH_BUDGET_MONTHLY_LIMIT_YUAN": "budget.monthly.limit_yuan",
    "PH_BUDGET_MONTHLY_ALERT_RATIO": "budget.monthly.alert_ratio",
    "PH_STORAGE_ROOT": "storage.root", "PH_STORAGE_SESSIONS_DIR": "storage.sessions_dir",
    "PH_STORAGE_DB_PATH": "storage.db_path",
    "PH_PLUGINS_ENABLED": "plugins.enabled", "PH_PLUGINS_DIR": "plugins.dir",
    "PH_WEB_HOST": "shell.web.host", "PH_WEB_PORT": "shell.web.port",
    "PH_ACP_ENABLED": "shell.acp.enabled",
}
RUNTIME_MUTABLE = frozenset({              # CFG §6.2 热更白名单(观测/无害)
    "log.level", "log.file", "llm.probe.interval_s",
    "llm.usage.unit_price", "budget.monthly.alert_ratio"})
```
**关联测试**:TC-F021、TC-G6(未知键/白名单外 env → CFG-607 警告生效值仍 L1)→ test_f021_config.py(§8)。

## 类与函数清单

### deep_merge(base: dict, override: dict) -> dict
**功能**:键级深合并(CFG §1.2):标量后层覆盖、字典键级并集、列表整体替换、null=删键回落 L1。
**参数表**:
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| base | dict | 是 | 低优先级层(L1/L2) |
| override | dict | 是 | 高优先级层(L2/L3/L4) |
**伪代码**:
```python
def deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if v is None:                        # 显式 null = 删键,回落低层默认
            out.pop(k, None); continue
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)   # 字典:键级深合并,两键并集
        else:
            out[k] = v                       # 标量覆盖;列表整体替换(不逐元素合并)
    return out
```
**关联测试**:TC-G1(字典键级合并/列表整体替换逐项断言)→ test_f021_config.py(§8)。

### flatten(d: dict, prefix: str = "") -> dict
**功能**:嵌套 dict → 点分键平面(env/CLI/hot-update 寻址用);含同名冲突检测。
**伪代码**:
```python
def flatten(d: dict, prefix: str = "") -> dict:
    out: dict = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(flatten(v, key))      # 递归展开
        else:
            out[key] = v                     # 叶子收进平面
    return out
```

### yaml_load_file(path: str) -> dict
**功能**:YAML 安全加载单个文件;文件缺失 → 回落 L1 记 info;语法错 → CFG-601(file_parse)中止(半套配置比没配置危险)。
**参数表**:
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| path | str | 是 | 配置文件路径 |
**伪代码**:
```python
def yaml_load_file(path: str) -> dict:
    p = Path(path).expanduser()
    if not p.exists():
        log.info(f"config file missing, fallback L1: {path}")
        return {}
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError("root 必须是映射")
        return data
    except Exception as e:                   # 语法/权限/类型错一律启动中止
        raise_code("CFG-601", reason="file_parse", file=str(p),
                   detail=f"{type(e).__name__}:{e}")
```
**异常表**:
| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| ConfigError | YAML 语法错/根非映射/读权限 | CFG-601(file_parse) | 修文件;config validate 离线预检 |
**关联测试**:TC-G2(CFG-601 列字段,进程退出非 0)→ test_f021_config.py、test_f064_cli.py(§8)。

### collect_config_files(cli_path: Optional[str]) -> list[Path]
**功能**:搜索顺序(CFG §2.1):--config 显式(不再搜其余)→ PH_CFG_PATH → ~/.pyharness/config.yaml → config.d/*.yaml 升序;缺失 → 空列表回落 L1。
**伪代码**:
```python
def collect_config_files(cli_path: Optional[str]) -> list[Path]:
    if cli_path:
        return [Path(cli_path).expanduser()]            # 显式指定后不再搜其余路径
    env_path = os.environ.get("PH_CFG_PATH")
    if env_path:
        return [Path(env_path).expanduser()]            # 部署环境统一指向
    home = Path("~/.pyharness").expanduser()
    files = []
    main = home / "config.yaml"
    if main.exists():
        files.append(main)
    frag = sorted((home / "config.d").glob("*.yaml"))   # 文件名升序逐文件 merge
    files.extend(frag)
    return files
```
**关联测试**:TC-F021(路径优先级)→ test_f021_config.py(§8)。

### load_file_layer(cli_path: Optional[str]) -> dict
**功能**:L2 层汇总:主文件 + config.d 片段按序 deep_merge;片段校验失败 → CFG-601 列来源文件+字段。
**伪代码**:
```python
def load_file_layer(cli_path: Optional[str]) -> dict:
    acc: dict = {}
    for f in collect_config_files(cli_path):
        acc = deep_merge(acc, yaml_load_file(f))        # 逐文件 merge,后文件覆盖前文件
    return acc
```

### parse_env_value(raw: str) -> Any
**功能**:env 值严格解析:bool(1/0/true/false/yes/no)/int/float/列表(逗号或 JSON 数组,空串=未设置);失败 → CFG-601(env_parse)中止。
**参数表**:
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| raw | str | 是 | 环境变量原值 |
**伪代码**:
```python
def parse_env_value(raw: str) -> Any:
    if raw == "":
        return None                          # 空串 = 未设置
    low = raw.strip().lower()
    if low in {"1", "true", "yes", "on"}:
        return True
    if low in {"0", "false", "no", "off"}:
        return False
    if "," in raw or raw.startswith("["):    # 列表:逗号或 JSON 数组
        try:
            return json.loads(raw) if raw.startswith("[") else [s.strip() for s in raw.split(",")]
        except json.JSONDecodeError as e:
            raise_code("CFG-601", reason="env_parse", detail=str(e))
    try:
        return int(raw)                      # int 优先
    except ValueError:
        try:
            return float(raw)                # float
        except ValueError:
            raise_code("CFG-601", reason="env_parse", value=raw)   # 严格解析,不静默
```
**异常表**:
| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| ConfigError | bool/int/float/list 解析失败 | CFG-601(env_parse) | 修环境变量格式后重启 |
**关联测试**:TC-G2(类型/范围失败 → CFG-601)→ test_f021_config.py(§8)。

### env_subset() -> dict
**功能**:L3 层:白名单内 PH_* 收集并按映射展开为嵌套键;名单外 PH_* → CFG-607 警告不中止(向前兼容)。
**伪代码**:
```python
def env_subset() -> dict:
    out: dict = {}
    for name, dotted in ENV_WHITELIST.items():
        raw = os.environ.get(name)
        if raw is None:
            continue
        if name == "PH_CFG_PATH":
            continue                          # cfg_path 已在 collect_config_files 消费
        val = parse_env_value(raw)
        if val is not None:
            set_dotted(out, dotted, val)      # 点分键写入嵌套 dict
    for name in os.environ:                   # 名单外:警告不中止(CFG §4.5 禁止面除外)
        if name.startswith("PH_") and name not in ENV_WHITELIST:
            log.warning(f"CFG-607 unknown env key: {name}")
    return out
```
**异常表**:
| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| ConfigError | 白名单键值解析失败 | CFG-601(env_parse) | 修值/删变量 |
| CFG-607 警告(不抛) | 名单外 PH_* | CFG-607 | 核对 §4.3 键面拼写 |
**关联测试**:TC-G6(白名单外 env → CFG-607,生效值仍 L1 默认)→ test_f021_config.py(§8)。

### set_dotted / get_dotted(d: dict, key: str, value: Any)
**功能**:点分键 ↔ 嵌套 dict 读写辅助(env/CLI/hot-update 共用)。
**伪代码**:
```python
def set_dotted(d: dict, key: str, value: Any) -> None:
    parts = key.split(".")
    node = d
    for p in parts[:-1]:
        node = node.setdefault(p, {})          # 逐层建 dict
    node[parts[-1]] = value
def get_dotted(d: dict, key: str) -> Any:
    node: Any = d
    for p in key.split("."):
        node = node[p] if isinstance(node, dict) else None
        if node is None:
            return None
    return node
```

### load_settings(*cli_overrides: tuple[str, Any]) -> Settings
**功能**:F021 主管线:merge(L1←L2←L3←L4)→ Settings.model_validate → validate_rules;非法 → CFG-601 列字段,拒绝启动(启动第 1 步,PRD §2.5)。
**参数表**:
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| *cli_overrides | tuple[str, Any] | 否 | (点分键, 值) 序列,来自 CLI --key value |
**伪代码**:
```python
def load_settings(*cli_overrides: tuple[str, Any]) -> "Settings":
    raw = deep_merge(DEFAULTS, load_file_layer(None))   # L1 + L2(文件/片段)
    raw = deep_merge(raw, env_subset())                 # L3(PH_ 白名单)
    cli = {}
    for key, val in cli_overrides:
        set_dotted(cli, key, val)                       # L4(最高优先,同管线同校验)
    raw = deep_merge(raw, cli)
    try:
        settings = Settings.model_validate(raw)         # 类型/范围/枚举(pydantic 逐键)
    except ValidationError as e:
        raise_code("CFG-601", reason="type_range",
                   fields=[err["loc"] for err in e.errors()])   # 列非法字段,启动中止
    validate_rules(settings)                            # 结构/秘密/安全单调(§5.1 ③-⑤)
    log.info(f"config loaded: {len(flatten(raw))} keys")        # 摘要不含值
    return settings
```
**异常表**:
| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| ConfigError | 类型/范围/枚举失败 | CFG-601 | 修四层配置对应字段 |
| ConfigError | 结构/秘密/越权失败 | CFG-601(见 validate_rules) | 见各 reason 分支 |
**关联测试**:TC-F021(四层合并/CFG-601)、TC-G2 → test_f021_config.py(§8)。

### validate_rules(settings) -> None
**功能**:pydantic 之外的五类规则(CFG §5.1 ③-⑤):超时时间档序、guard disabled 约束、秘密语法/字面量拒载、安全单调越权;未知键收集 CFG-607 警告。
**参数表**:
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| settings | Settings | 是 | 已通过模型校验的配置 |
**伪代码**:
```python
BUILTIN_GUARDS = {"g-fs-path", "g-credential-read", "g-net-outbound",
                  "g-exec", "g-overwrite"}                          # F023 五内置
def validate_rules(settings) -> None:
    t = settings.llm.timeout
    if not (t.connect_s < t.first_token_s < t.total_s):             # ③ 时间档序
        raise_code("CFG-601", reason="structural", fields=["llm.timeout.*"])
    dis = set(settings.security.guards.disabled)
    if dis and (dis == BUILTIN_GUARDS or not dis <= BUILTIN_GUARDS):
        raise_code("CFG-601", reason="越权", fields=["security.guards.disabled"],
                   detail="禁全关/未注册 guard 名")                   # 单调只紧不松
    for tool, lvl in settings.security.tool_danger_extra.items():   # ⑤ danger 只许调高
        if lvl < baseline_danger(tool):                             # 调低(如 high→low)
            raise_code("CFG-601", reason="越权", fields=[f"security.tool_danger_extra.{tool}"])
    if settings.shell.web.host not in ("127.0.0.1", "localhost") and not secret_ref_ok(
            settings.shell.web.token):
        raise_code("CFG-601", reason="structural", fields=["shell.web.token"],
                   detail="绑非回环必须配 token")                     # §3.8
    for ref in secret_fields(settings):                             # ④ 秘密语法
        if not secret_ref_ok(ref):
            raise_code("CFG-601", reason="secret_literal", fields=ref_loc(ref),
                       detail="字面量密钥拒载,改 env:/file: 引用")     # §7.1
```
**异常表**:
| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| ConfigError | 超时档序错乱 | CFG-601(structural) | 修 llm.timeout.* |
| ConfigError | guard 全关/关未注册名 | CFG-601(越权) | 只许关单个内置(真子集) |
| ConfigError | tool_danger_extra 调低 danger | CFG-601(越权) | danger 只许调高收紧 |
| ConfigError | 字面量密钥(sk-/≥32 位) | CFG-601(secret_literal) | 改 env:NAME/file:PATH 引用 |
| ConfigError | 非回环绑定无 token | CFG-601(structural) | 配 shell.web.token 引用 |
**关联测试**:TC-G3(字面量密钥拒载)、TC-G5(收紧放行/放宽拒绝)→ test_f021_config.py + tests/security(§8)。

### secret_ref_ok(value: Any) -> bool
**功能**:secret-ref 语法校验:仅 `env:NAME`/`file:PATH`;字面量匹配 sk- 前缀或 ≥32 位疑似密钥 → False(调用方据此 CFG-601 secret_literal 拒载)。
**参数表**:
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| value | Any | 是 | 配置键值(空串视为未配置,允许) |
**伪代码**:
```python
_SECRET_RE = re.compile(r"^(env:[A-Za-z_][A-Za-z0-9_]*|file:.+)$")
def secret_ref_ok(value: Any) -> bool:
    if value in (None, ""):
        return True                              # 未配置允许;使用时 CRED-701
    if not isinstance(value, str):
        return False
    if _SECRET_RE.match(value):
        return True
    return not (value.startswith("sk-") or len(value) >= 32)   # 疑似明文密钥 → 拒载
```
**关联测试**:TC-G3 → test_f021_config.py(§8)。

### redact(text: str) -> str
**功能**:出口横切脱敏(INV-09):32+ 位疑似密钥打码;与凭据模块 redact_out 同规则(F016)。
**参数表**:
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| text | str | 是 | 待脱敏文本(日志/事件/错误消息) |
**伪代码**:
```python
def redact(text: str) -> str:
    text = re.sub(r"(sk-[A-Za-z0-9]{6})[A-Za-z0-9]+", r"\1***", text)  # sk-abc*** 
    text = re.sub(r"\b([A-Za-z0-9]{28})[A-Za-z0-9]{4,}\b", r"\1****", text)  # ≥32 位
    return text
```
**关联测试**:TC-F016 脱敏 INV-09、TC-G4(config show 秘密全为 env:NAME 引用)→ test_f016_credentials.py、test_f021_config.py(§8)。

### hot_update(holder, key: str, value: Any, by: str = "system") -> None
**功能**:热更唯一入口:key ∈ RUNTIME_MUTABLE 且值过全量校验 → 生效并写 config.updated 事件;否则 CFG-608 拒绝(不静默、不部分生效),留事件。
**参数表**:
| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| holder | SettingsHolder | 是 | 运行期 Settings 持有者(唯一可变点) |
| key | str | 是 | 点分键 |
| value | Any | 是 | 新值(同过 §5 校验) |
| by | str | 否 | 操作者,默认 system |
**伪代码**:
```python
def hot_update(holder, key: str, value: Any, by: str = "system") -> None:
    if key not in RUNTIME_MUTABLE:               # 策略/预算/安全类 → 拒绝
        raise_code("CFG-608", key=key, hint="该键需重启生效;热更仅观测键")
    old = get_dotted(holder.settings.model_dump(), key)
    candidate = deep_merge(holder.settings.model_dump(), set_dotted_new(key, value))
    try:
        new_settings = Settings.model_validate(candidate)
        validate_rules(new_settings)             # 热更值同过全量校验
    except ValidationError:
        raise_code("CFG-601", reason="type_range", fields=[key])
    holder.settings = new_settings               # 整体替换快照(不可变语义保持)
    bus.emit("config.updated", {"key": key, "old": redact(str(old)),
                                "new": redact(str(value)), "by": by})   # 可审计运维动作
```
**异常表**:
| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| ConfigError | 热更只读键(security.*/loop.max_turns/budget.task.* 等) | CFG-608 | 重启生效;非配置漂移后门 |
| ConfigError | 热更值校验失败 | CFG-601 | 修值重试 |
**关联测试**:TC-G4(热更 log.level 生效;热更 sandbox.level 被 CFG-608 拒且事件留痕)→ test_f021_config.py(§8)。

### render_show(settings) -> dict / validate_only(cli_path) -> list[str]
**功能**:config 子命令后端(F064 离线):show=生效值扁平化,秘密只回显 `env:NAME` 引用(--json 支持);validate=只校验返回错误列表,不启动。
**伪代码**:
```python
def render_show(settings) -> dict:
    flat = flatten(settings.model_dump())
    out = {}
    for k, v in flat.items():
        if isinstance(v, str) and (v.startswith("env:") or v.startswith("file:")):
            out[k] = v                             # 秘密回显引用不显示值(无 --show-secrets)
        elif looks_like_secret(k, v):
            out[k] = redact(str(v))
        else:
            out[k] = v
    return out
def validate_only(cli_path: Optional[str]) -> list[str]:
    errors = []
    try:
        load_settings() if not cli_path else None  # 走全管线;异常被收集
    except ConfigError as e:
        errors.append(e.to_user_message())
    return errors                                  # config validate 退出码非 0 当 errors 非空
```
**关联测试**:TC-F064(config 子命令/退出码)→ test_f064_cli.py、TC-G2(config validate 输出同明细)→ test_f021_config.py(§8)。

### class Settings(BaseModel)
**功能**:全量 schema 顶层模型(config.schema_version 记录配置面版本,破坏性变更升版本号);嵌套模型逐域对应 §3(此处仅示意嵌套结构,字段约束以 CFG.md §3 各键取值范围列+默认值为准)。
**伪代码**:
```python
class TimeoutCfg(BaseModel):
    connect_s: int = Field(10, ge=1, le=300)
    first_token_s: int = Field(60, ge=1, le=600)
    total_s: int = Field(180, ge=1, le=1800)        # 档序在 validate_rules ③ 复核
class LlmCfg(BaseModel):
    model: str = "deepseek-chat"
    api_key: str = "env:DEEPSEEK_API_KEY"           # secret-ref 型,禁字面量
    temperature: float = Field(0.7, ge=0, le=1.5)
    max_tokens: int = Field(4096, ge=1, le=32768)
    fallback_models: list[str] = ["qwen-max"]
    timeout: TimeoutCfg = TimeoutCfg()
    ...
class SecurityCfg(BaseModel):
    sandbox: SandboxCfg = SandboxCfg()              # level: Literal["strict","basic","off"]
    ...
class Settings(BaseModel):
    model_config = ConfigDict(extra="ignore")       # 未知键收集到 CFG-607 警告集,不中止
    config: ConfigMeta = ConfigMeta()
    llm: LlmCfg = LlmCfg()
    loop: LoopCfg = LoopCfg()
    security: SecurityCfg = SecurityCfg()
    budget: BudgetCfg = BudgetCfg()
    log: LogCfg = LogCfg()
    storage: StorageCfg = StorageCfg()
    plugins: PluginsCfg = PluginsCfg()
    shell: ShellCfg = ShellCfg()
```
**异常表**:
| 异常 | 触发 | 错误码 | 恢复 |
|---|---|---|---|
| ValidationError | 任一键类型/范围/枚举越界 | CFG-601(type_range) | load_settings 收集 fields 列明细 |
**关联测试**:TC-F021、TC-G2 → test_f021_config.py(§8)。

## 关联文档
- CFG.md(全量权威:§1 分层模型、§3 配置项目录与默认值、§4 env 白名单、§5 校验管线与 CFG-6xx、§6 热更策略表、§7 秘密引用与脱敏、§9 G1-G6)
- PRD-Core.md(F021 配置管理、§2.5 启动 6 步第 1 步加载配置、§8.2 TC-F021)
- ERR.md(§2.7 CFG-6xx 语义与处置、§1.3 致命度、§9.2 注 3 CFG-602/603 归属)
- SECURITY.md(§4 L0 策略键不漂移、§6 配置安全、§7.5 POL 策略)
- DIS-SEAM.md(§3.2 ctx.config 只读门面、§4.6 插件装载顺序依赖 plugins.* 配置)
