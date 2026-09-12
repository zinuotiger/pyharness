"""pyharness/config.py — 分层配置引擎 (specs/config.py.md; 字段级权威 CFG.md)

职责:代码默认值(L1) → config.yaml + config.d/*.yaml(L2) → PH_ 环境变量白名单
(L3) → CLI 覆盖(L4) 四层 deep_merge 成不可变 Settings,整体过 pydantic schema
+ 安全单调校验(validate_rules),非法配置 → CFG-601 拒绝启动(启动第 1 步);
秘密只存 env:NAME / file:PATH 引用,全出口脱敏(redact);仅观测键可热更
(hot_update,CFG §6.2 白名单),只读键 → CFG-608 拒绝并提示重启。

设计约束(C3):Settings 加载后不可变;运行期改写唯一入口是 SettingsHolder +
hot_update 的整体快照替换。禁止:热重载策略键、字面量密钥、运行时改写 Settings。
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from pyharness.errors import ConfigError, raise_code

log = logging.getLogger("pyharness.config")


# ===================================================================== 常量
# ---------------------------------------------------------- L1 权威默认值
# CFG.md §3 默认值列的唯一落地;禁读环境/文件生成(可复现);任意键缺省必可加载。
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
                 "network": {"allowed_domains": [], "web_search_per_session": 20,
                            "search_backend": "bing",
                            "search_endpoint": "https://cn.bing.com/search"},
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
    "skills": {"dir": "~/.pyharness/skills", "registry_url": "",
               "max_package_bytes": 5242880, "max_files": 200},
    "plugins": {"enabled": [], "dir": "~/.pyharness/plugins", "ctx_lazy": True,
                "pre_activate": ["storage.spill", "credentials", "storage.kv"],
                "mcp_servers": [],
                "priority": {}, "backpressure_limit": 1000,
                "deadletter_samples": 100},
    "shell": {"web": {"host": "127.0.0.1", "port": 8000, "token": ""},
              "acp": {"enabled": False}},
}

# ------------------------------------------------ PH_ 环境变量白名单(CFG §4.3)
# env/CLI 共用唯一键面;名单外 PH_* → CFG-607 警告不中止;秘密不在白名单。
ENV_WHITELIST: dict[str, str] = {
    "PH_CFG_PATH": "cfg_path",               # 特殊:指向配置文件而非配置值
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
    "PH_SEARCH_BACKEND": "security.network.search_backend",
    "PH_PLUGINS_ENABLED": "plugins.enabled", "PH_PLUGINS_DIR": "plugins.dir",
    "PH_SKILLS_DIR": "skills.dir", "PH_SKILL_REGISTRY_URL": "skills.registry_url",
    "PH_WEB_HOST": "shell.web.host", "PH_WEB_PORT": "shell.web.port",
    "PH_ACP_ENABLED": "shell.acp.enabled",
}

# 热更白名单(CFG §6.2 观测/无害键);其余一律需重启 → CFG-608
RUNTIME_MUTABLE: frozenset = frozenset({
    "log.level", "log.file", "llm.probe.interval_s",
    "llm.usage.unit_price", "budget.monthly.alert_ratio"})

# F023 五内置 guard(F023;禁全关/禁关未注册名)
BUILTIN_GUARDS = frozenset({
    "g-fs-path", "g-credential-read", "g-net-outbound", "g-exec", "g-overwrite"})

# 秘密引用语法:仅 env:NAME / file:PATH(CFG §7.1)
_SECRET_RE = re.compile(r"^(env:[A-Za-z_][A-Za-z0-9_]*|file:.+)$")

# secret-ref 型键(CFG §7.1:llm.api_key / shell.web.token;credentials.yaml 内键归凭据模块)
_SECRET_KEYS = ("llm.api_key", "shell.web.token")

# 工具危险基线(CFG §3.3 明示的高危项);其余工具基线以注册期声明为准,
# 配置加载期未知 → 视同 none(显式标定不构成"调低",运行期由工具注册表复核)。
_TOOL_DANGER_BASELINE: dict[str, str] = {"fs.delete_file": "high"}
_DANGER_RANK: dict[str, int] = {"none": 0, "low": 1, "high": 2, "critical": 3}

# render_show 判定"疑似秘密值"的键名提示(INV-09 脱敏)
_SECRET_KEY_HINTS = ("api_key", "token", "secret", "password", "credential")


# ================================================================= 合并原语
def deep_merge(base: dict, override: dict) -> dict:
    """键级深合并(CFG §1.2):标量后层覆盖、字典键级并集、列表整体替换、null=删键回落 L1。"""
    out = dict(base)
    for k, v in override.items():
        if v is None:                        # 显式 null = 删键,回落低层默认
            out.pop(k, None)
            continue
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)   # 字典:键级深合并,两键并集
        else:
            out[k] = v                       # 标量覆盖;列表整体替换(不逐元素合并)
    return out


def flatten(d: dict, prefix: str = "") -> dict:
    """嵌套 dict → 点分键平面(env/CLI/hot-update 寻址用)。"""
    out: dict = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(flatten(v, key))      # 递归展开
        else:
            out[key] = v                     # 叶子收进平面
    return out


def set_dotted(d: dict, key: str, value: Any) -> None:
    """点分键写入嵌套 dict(逐层建 dict)。"""
    parts = key.split(".")
    node = d
    for p in parts[:-1]:
        node = node.setdefault(p, {})
    node[parts[-1]] = value


def get_dotted(d: dict, key: str) -> Any:
    """点分键读嵌套 dict;路径中断返回 None。"""
    node: Any = d
    for p in key.split("."):
        node = node[p] if isinstance(node, dict) else None
        if node is None:
            return None
    return node


def _set_dotted_new(key: str, value: Any) -> dict:
    """构造仅含单个点分键的嵌套 dict(热更候选快照铺层用)。"""
    out: dict = {}
    set_dotted(out, key, value)
    return out


# ================================================================= 文件层 L2
def yaml_load_file(path: str) -> dict:
    """YAML 安全加载单个文件;文件缺失 → 回落 L1 记 info;语法错 → CFG-601(file_parse)中止。"""
    p = Path(path).expanduser()
    if not p.exists():
        log.info("config file missing, fallback L1: %s", path)
        return {}
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError("root 必须是映射")
        return data
    except Exception as e:                   # 语法/权限/类型错一律启动中止(半套比没配更危险)
        raise_code("CFG-601", reason="file_parse", file=str(p),
                   detail=f"{type(e).__name__}:{e}")


def collect_config_files(cli_path: Optional[str]) -> list[Path]:
    """搜索顺序(CFG §2.1):--config 显式(不再搜其余)→ PH_CFG_PATH → ~/.pyharness/config.yaml → config.d/*.yaml 升序;缺失 → 空列表回落 L1。"""
    if cli_path:
        return [Path(cli_path).expanduser()]            # 显式指定后不再搜其余路径
    env_path = os.environ.get("PH_CFG_PATH")
    if env_path:
        return [Path(env_path).expanduser()]            # 部署环境统一指向
    home = Path("~/.pyharness").expanduser()
    files: list[Path] = []
    main = home / "config.yaml"
    if main.exists():
        files.append(main)
    frag = sorted((home / "config.d").glob("*.yaml"))   # 文件名升序逐文件 merge
    files.extend(frag)
    return files


def load_file_layer(cli_path: Optional[str]) -> dict:
    """L2 层汇总:主文件 + config.d 片段按序 deep_merge,后文件覆盖前文件。"""
    acc: dict = {}
    for f in collect_config_files(cli_path):
        acc = deep_merge(acc, yaml_load_file(str(f)))
    return acc


# ================================================================= env 层 L3
def parse_env_value(raw: str) -> Any:
    """env 值严格解析:bool/int/float/列表(逗号或 JSON 数组);空串 = 未设置。

    偏离点:白名单含字符串键(llm.model/base_url/storage 路径/log.file 等),
    全部按伪码在 int/float 失败后 CFG-601 会让这些键永不可用——故末尾回退原串,
    交由 pydantic schema 收口(字符串落数字字段仍会 CFG-601 type_range,语义不变)。
    """
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
            return raw                       # 原串回退(字符串键;数字键由 pydantic 拒)


def env_subset() -> dict:
    """L3 层:白名单内 PH_* 收集并按映射展开为嵌套键;名单外 PH_* → CFG-607 警告不中止。"""
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
            log.warning("CFG-607 unknown env key: %s (白名单外,忽略)", name)
    return out


# ================================================================= 主管线
def _merge_layers(cli_path: Optional[str],
                  cli_overrides: tuple[tuple[str, Any], ...]) -> dict:
    """四层深合并:L1 ← L2(文件) ← L3(env) ← L4(CLI),返回合并后原始 dict。"""
    raw = deep_merge(DEFAULTS, load_file_layer(cli_path))   # L1 + L2(文件/片段)
    raw = deep_merge(raw, env_subset())                     # L3(PH_ 白名单)
    cli: dict = {}
    for key, val in cli_overrides:
        set_dotted(cli, key, val)                           # L4(最高优先,同管线同校验)
    return deep_merge(raw, cli)


def _validation_fields(e: ValidationError) -> list[str]:
    """pydantic 错误 loc 元组 → 点分字段列表(供 CFG-601 ctx.fields 列字段)。"""
    fields: list[str] = []
    for err in e.errors():
        loc = err.get("loc") or ()
        fields.append(".".join(str(x) for x in loc))
    return fields or ["<unknown>"]


def load_settings(*cli_overrides: tuple[str, Any]) -> "Settings":
    """F021 主管线:merge(L1←L2←L3←L4)→ Settings.model_validate → validate_rules。

    非法 → CFG-601 列字段,拒绝启动(启动第 1 步);未知键 → CFG-607 警告不中止。
    cli_overrides: (点分键, 值) 序列,来自 CLI --key value。
    """
    raw = _merge_layers(None, cli_overrides)
    try:
        settings = Settings.model_validate(raw)             # 类型/范围/枚举(pydantic 逐键)
    except ValidationError as e:
        raise_code("CFG-601", reason="type_range",
                   fields=_validation_fields(e),
                   detail="类型/范围/枚举越界,修四层配置对应字段")
    validate_rules(settings)                                # 结构/秘密/安全单调(§5.1 ③-⑤)
    # 未知键收集:raw 平面键 − 模型键 → CFG-607 警告(只报键名,不含值)
    known = set(flatten(settings.model_dump()))
    for key in sorted(set(flatten(raw)) - known):
        log.warning("CFG-607 unknown config key: %s", key)
    log.info("config loaded: %d keys", len(flatten(settings.model_dump())))
    return settings


def _cfg601_fields_text(fields: list[str]) -> str:
    return "字段:" + ",".join(fields)


# ================================================================= 校验规则
def validate_rules(settings: "Settings") -> None:
    """pydantic 之外的五类规则(CFG §5.1 ③-⑤):超时档序、guard disabled、秘密
    语法/字面量拒载、安全单调越权。任一失败 → CFG-601 启动中止。"""
    t = settings.llm.timeout
    if not (t.connect_s < t.first_token_s < t.total_s):             # ③ 时间档序
        raise_code("CFG-601", reason="structural", fields=["llm.timeout.*"],
                   detail="超时须 connect_s < first_token_s < total_s")
    dis = set(settings.security.guards.disabled)
    if dis and (dis == BUILTIN_GUARDS or not dis <= BUILTIN_GUARDS):  # 单调只紧不松
        raise_code("CFG-601", reason="越权", fields=["security.guards.disabled"],
                   detail="禁全关/未注册 guard 名;只许关单个内置(真子集)")
    for tool, lvl in settings.security.tool_danger_extra.items():   # ⑤ danger 只许调高
        if _DANGER_RANK[lvl] < _DANGER_RANK[baseline_danger(tool)]:
            raise_code("CFG-601", reason="越权",
                       fields=[f"security.tool_danger_extra.{tool}"],
                       detail=f"danger 只许调高收紧,{tool} 基线={baseline_danger(tool)}")
    # 非回环绑定必须已配 token 引用(CFG §3.8);空串 token 视为未配置
    host = settings.shell.web.host
    token = settings.shell.web.token
    if host not in ("127.0.0.1", "localhost", "::1") and (not token or not secret_ref_ok(token)):
        raise_code("CFG-601", reason="structural", fields=["shell.web.token"],
                   detail="绑非回环必须配 token(env:/file: 引用)")
    for loc, ref in secret_fields(settings):                       # ④ 秘密语法
        if not secret_ref_ok(ref):
            raise_code("CFG-601", reason="secret_literal", fields=[loc],
                       detail="字面量密钥拒载,改 env:/file: 引用")


def baseline_danger(tool: str) -> str:
    """工具基线 danger(CFG §3.3 内置项);未知工具视同 none,运行期由注册表复核。"""
    return _TOOL_DANGER_BASELINE.get(tool, "none")


def secret_fields(settings: "Settings") -> list[tuple[str, Any]]:
    """secret-ref 型键清单:返回 (点分定位, 值) 对,供 validate_rules 逐键校验。"""
    data = settings.model_dump()
    return [(k, get_dotted(data, k)) for k in _SECRET_KEYS]


def secret_ref_ok(value: Any) -> bool:
    """secret-ref 语法校验:仅 env:NAME / file:PATH;字面量匹配 sk- 前缀或 ≥32 位
    疑似密钥 → False(空串/None = 未配置,允许;使用时由凭据模块 CRED-701 拒)。"""
    if value in (None, ""):
        return True                              # 未配置允许;使用时 CRED-701
    if not isinstance(value, str):
        return False
    if _SECRET_RE.match(value):
        return True
    return not (value.startswith("sk-") or len(value) >= 32)   # 疑似明文密钥 → 拒载


def redact(text: str) -> str:
    """出口横切脱敏(INV-09):32+ 位疑似密钥打码,与凭据模块 redact_out 同规则(F016)。"""
    text = re.sub(r"(sk-[A-Za-z0-9]{6})[A-Za-z0-9]+", r"\1***", text)  # sk-abc***
    text = re.sub(r"\b([A-Za-z0-9]{28})[A-Za-z0-9]{4,}\b", r"\1****", text)  # ≥32 位
    return text


def looks_like_secret(key: str, value: Any) -> bool:
    """疑似秘密值判定(render_show 出口脱敏用):键名提示 或 sk- 前缀 或 ≥32 位无分隔串。"""
    if not isinstance(value, str):
        return False
    if value.startswith(("env:", "file:")):
        return False                           # 引用本身不是秘密值
    low = key.lower()
    if any(h in low for h in _SECRET_KEY_HINTS):
        return True
    if value.startswith("sk-"):
        return True
    # 长串且不含路径/域名分隔符 → 疑似连续密钥(路径/URL 长值不误伤)
    return len(value) >= 32 and not any(ch in value for ch in "/\\.")


# ================================================================= 热更出口
class SettingsHolder:
    """运行期 Settings 快照持有者 —— 全系统唯一可变点(CFG §1.4/C3)。

    settings 只允许整体快照替换(hot_update),保持不可变语义;bus 为可选事件
    出口(阶段 0 bus 未装配时可为 None,事件降级为日志)。
    """

    def __init__(self, settings: "Settings", bus: Any = None):
        self.settings = settings
        self.bus = bus                         # EventBus 实例(注入;供测试/装配)


def _emit_event(holder: SettingsHolder, event: str, payload: dict) -> None:
    """热更事件出口:经 schedule_emit 排程(保留 task 引用,异常可审计)。"""
    bus = holder.bus
    if bus is None:
        log.warning("config.updated 事件未落:bus 未装配")
        return
    from pyharness.bus import schedule_emit
    try:
        schedule_emit(bus, event, payload)
    except Exception as exc:                       # noqa: BLE001 事件失败不阻断热更生效
        log.warning("config.updated 事件写失败:%s", exc)


def hot_update(holder: SettingsHolder, key: str, value: Any, by: str = "system") -> None:
    """热更唯一入口:key ∈ RUNTIME_MUTABLE 且值过全量校验 → 生效并写 config.updated
    事件;否则 CFG-608 拒绝(不静默、不部分生效)。"""
    if key not in RUNTIME_MUTABLE:               # 策略/预算/安全类 → 拒绝
        raise_code("CFG-608", key=key, hint="该键需重启生效;热更仅观测键")
    old = get_dotted(holder.settings.model_dump(), key)
    candidate = deep_merge(holder.settings.model_dump(), _set_dotted_new(key, value))
    try:
        new_settings = Settings.model_validate(candidate)
    except ValidationError:
        raise_code("CFG-601", reason="type_range", fields=[key],
                   detail="热更值类型/范围非法")
    validate_rules(new_settings)                 # 热更值同过全量校验
    holder.settings = new_settings               # 整体替换快照(不可变语义保持)
    _emit_event(holder, "config.updated",
                {"key": key, "old": redact(str(old)),
                 "new": redact(str(value)), "by": by})   # 可审计运维动作


# ============================================================== config 子命令
def render_show(settings: "Settings") -> dict:
    """config show 后端:生效值扁平化,秘密只回显 env:NAME/file:PATH 引用(--json 支持)。"""
    flat = flatten(settings.model_dump())
    out: dict = {}
    for k, v in flat.items():
        if isinstance(v, str) and (v.startswith("env:") or v.startswith("file:")):
            out[k] = v                           # 秘密回显引用不显示值(无 --show-secrets)
        elif looks_like_secret(k, v):
            out[k] = redact(str(v))
        else:
            out[k] = v
    return out


def _config_error_text(e: ConfigError) -> str:
    """config validate 错误文案:码+处置+字段明细(不吐 env 值/完整文件)。"""
    fields = e.ctx.get("fields")
    suffix = ""
    if fields:
        suffix = " 字段:" + ",".join(str(f) for f in fields)
    tip = e.ctx.get("detail")
    if tip:
        suffix += f" 明细:{tip}"
    return f"{e.code}:{e.spec.advice}{suffix}"


def validate_only(cli_path: Optional[str] = None) -> list[str]:
    """config validate 后端:只走全管线校验返回错误列表,不启动(F064 离线)。"""
    errors: list[str] = []
    try:
        raw = _merge_layers(cli_path, ())
        settings = Settings.model_validate(raw)
        validate_rules(settings)
    except ValidationError as e:
        errors.append(f"CFG-601:配置非法/越权 {_cfg601_fields_text(_validation_fields(e))}")
    except ConfigError as e:
        errors.append(_config_error_text(e))
    return errors                                # 非空 → config validate 退出码非 0


# ================================================================ pydantic 模型
# 字段约束以 CFG.md §3 各键取值范围列 + 默认值为准;范围列给出处均落 Field ge/le。
class ConfigMeta(BaseModel):
    """config.* — 配置面元信息:破坏性变更升 schema_version 给迁移提示。"""
    schema_version: int = Field(1, ge=1, le=999)


class UnitPriceCfg(BaseModel):
    """单价条目(元/百万 token,F029/N14):只影响估算/报表。"""
    in_per_million: float = Field(..., ge=0)
    out_per_million: float = Field(..., ge=0)


def _default_unit_price() -> dict[str, UnitPriceCfg]:
    return {
        "deepseek-chat": UnitPriceCfg(in_per_million=2.0, out_per_million=8.0),
        "qwen-max": UnitPriceCfg(in_per_million=4.0, out_per_million=12.0),
    }


class TimeoutCfg(BaseModel):
    """llm.timeout — 三档超时(F017);档序在 validate_rules ③ 复核。"""
    connect_s: int = Field(10, ge=1, le=300)
    first_token_s: int = Field(60, ge=1, le=600)
    total_s: int = Field(180, ge=1, le=1800)


class RetryCfg(BaseModel):
    """llm.retry — 退避(F028)。"""
    attempts: int = Field(4, ge=0, le=8)
    base_delay_s: float = Field(1.0, ge=0.1, le=60)
    jitter: float = Field(0.3, ge=0, le=0.5)


class DegradeCfg(BaseModel):
    """llm.degrade — 降级链(F013)。"""
    enabled: bool = True
    rate_limit_consecutive: int = Field(2, ge=1, le=5)
    max_per_session: int = Field(5, ge=1, le=20)


class ProbeCfg(BaseModel):
    """llm.probe — 探针周期(F033)。"""
    interval_s: int = Field(60, ge=10, le=3600)


class UsageCfg(BaseModel):
    """llm.usage — 单价表(只影响估算/报表)。"""
    unit_price: dict[str, UnitPriceCfg] = Field(default_factory=_default_unit_price)


class LlmCfg(BaseModel):
    """llm.* — 模型域(F012/F013/F028/F029/F030/F033)。"""
    model: str = "deepseek-chat"
    base_url: str = "https://api.deepseek.com"
    api_key: str = "env:DEEPSEEK_API_KEY"       # secret-ref 型,字面量由 validate_rules 拒载
    temperature: float = Field(0.7, ge=0, le=1.5)
    max_tokens: int = Field(4096, ge=1, le=32768)
    fallback_models: list[str] = Field(default_factory=lambda: ["qwen-max"])
    timeout: TimeoutCfg = Field(default_factory=TimeoutCfg)
    retry: RetryCfg = Field(default_factory=RetryCfg)
    degrade: DegradeCfg = Field(default_factory=DegradeCfg)
    probe: ProbeCfg = Field(default_factory=ProbeCfg)
    usage: UsageCfg = Field(default_factory=UsageCfg)

    @field_validator("base_url")
    @classmethod
    def _url_ok(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("base_url 须 http(s):// 开头(合法 URL)")
        return v


class CompactCfg(BaseModel):
    """loop.compact — 上下文压缩(F058)。"""
    trigger_ratio: float = Field(0.75, ge=0.5, le=0.95)
    min_new_rounds: int = Field(10, ge=1, le=100)
    keep_recent_rounds: int = Field(12, ge=1, le=100)
    summarize_budget_tokens: int = Field(400, ge=50, le=2000)


class ContentCfg(BaseModel):
    """loop.content — 工具内容截断(F034/F037/F038)。"""
    file_spill_bytes: int = Field(65536, ge=1024, le=1048576)
    fetch_max_chars: int = Field(32768, ge=1024, le=1048576)
    search_result_chars: int = Field(8000, ge=512, le=32768)


class LoopCfg(BaseModel):
    """loop.* — 循环域(F007/F010/F017/F022/F026/F043/F058/F063)。"""
    max_turns: int = Field(30, ge=1, le=1000)
    step_timeout_s: int = Field(60, ge=1, le=600)
    input_queue_max: int = Field(10, ge=1, le=100)
    task_queue_max: int = Field(32, ge=1, le=1024)
    convergence_rounds: int = Field(3, ge=1, le=10)
    tool_concurrency: int = Field(1, ge=1, le=3)
    thread_pool_size: int = Field(4, ge=1, le=32)
    max_arg_failures_per_round: int = Field(2, ge=1, le=5)
    max_context_tokens: int = Field(65536, ge=1024, le=1048576)
    streaming: bool = Field(False)   # F027:agent-loop 出网走 chat_stream(桌面 SSE 实时)
    compact: CompactCfg = Field(default_factory=CompactCfg)
    content: ContentCfg = Field(default_factory=ContentCfg)
    message_edit_window_s: int = Field(1800, ge=60, le=86400)


class SandboxCfg(BaseModel):
    """security.sandbox — 沙箱级别(SEC §7)。"""
    level: Literal["strict", "basic", "off"] = "strict"
    proc_wallclock_s: int = Field(60, ge=1, le=3600)
    proc_mem_limit_mb: int = Field(0, ge=0)      # 0=不限;≥1=MB(Windows 尽力而为)


class NetworkCfg(BaseModel):
    """security.network — 外发 allowlist(F023/N13/F037)。"""
    allowed_domains: list[str] = Field(default_factory=list)
    web_search_per_session: int = Field(20, ge=0, le=1000)
    search_backend: Literal["disabled", "bing", "duckduckgo"] = "bing"
    search_endpoint: str = "https://cn.bing.com/search"


class PolicyCfg(BaseModel):
    """security.policy — 权限预设基集上只增的 deny/只读例外(F023/F055)。"""
    deny_tools_extra: list[str] = Field(default_factory=list)
    read_extra_dirs: list[str] = Field(default_factory=list)
    preset: Literal["locked", "readonly", "standard", "strict"] = "strict"
    # 档位语义(engine._apply_preset):strict=默认最小权限;standard=放宽域约束
    # (危险级仍由 guard/审批管);readonly=除只读/自管理外全禁;locked=仅对话


class GuardsCfg(BaseModel):
    """security.guards — 五内置 guard 显式关单个(禁全关/禁未注册名,validate_rules 复核)。"""
    disabled: list[str] = Field(default_factory=list)


class ApprovalCfg(BaseModel):
    """security.approval — 审批 TTL/合并窗(F015)。"""
    ttl_ms: int = Field(120000, ge=1000, le=3600000)   # 120s,超时=denied 安全默认
    merge_window_s: int = Field(60, ge=0, le=600)


class TrustlistCfg(BaseModel):
    """security.trustlist — 信任名单默认关(SEC §5)。"""
    enabled: bool = False


class CredentialsCfg(BaseModel):
    """security.credentials — 凭据文件(F016/SEC §6)。"""
    file: str = "~/.pyharness/credentials.yaml"
    cache_ttl_s: int = Field(300, ge=0, le=3600)       # 秘密 TTL≤5min


class AttachmentCfg(BaseModel):
    """security.attachment — 附件限制(F061)。"""
    max_bytes: int = Field(10485760, ge=1, le=104857600)
    max_per_message: int = Field(5, ge=1, le=20)
    mime_whitelist: list[str] = Field(default_factory=lambda: [
        "image/jpeg", "image/png", "image/webp", "image/gif"])


class SecurityCfg(BaseModel):
    """security.* — 安全域(F014/F015/F023/F037/F038/F054/F061)。"""
    sandbox: SandboxCfg = Field(default_factory=SandboxCfg)
    network: NetworkCfg = Field(default_factory=NetworkCfg)
    policy: PolicyCfg = Field(default_factory=PolicyCfg)
    tool_danger_extra: dict[str, Literal["none", "low", "high", "critical"]] = \
        Field(default_factory=dict)
    guards: GuardsCfg = Field(default_factory=GuardsCfg)
    approval: ApprovalCfg = Field(default_factory=ApprovalCfg)
    trustlist: TrustlistCfg = Field(default_factory=TrustlistCfg)
    credentials: CredentialsCfg = Field(default_factory=CredentialsCfg)
    attachment: AttachmentCfg = Field(default_factory=AttachmentCfg)


class BudgetTaskCfg(BaseModel):
    """budget.task — 单任务硬闸(F032/N5)。"""
    max_in_tokens: int = Field(2_000_000, ge=1, le=100_000_000)
    max_out_tokens: int = Field(50_000, ge=1, le=10_000_000)
    max_cost_yuan: float = Field(1.0, ge=0.01, le=1_000_000)


class MonthlyCfg(BaseModel):
    """budget.monthly — 月度成本估算(跨会话按日志聚合)。"""
    limit_yuan: float = Field(0.0, ge=0)               # 0=不启用;>0=元
    alert_ratio: float = Field(0.8, ge=0.5, le=0.99)


class BudgetCfg(BaseModel):
    """budget.* — 成本域(F032/N5/F029)。"""
    task: BudgetTaskCfg = Field(default_factory=BudgetTaskCfg)
    warn_ratio: float = Field(0.8, ge=0.5, le=0.99)
    subagent_ratio: float = Field(0.25, ge=0.01, le=1)
    monthly: MonthlyCfg = Field(default_factory=MonthlyCfg)


class LogJsonlCfg(BaseModel):
    """log.jsonl — 事件日志攒批(PRD §3.6)。"""
    flush_interval_s: float = Field(0.5, ge=0.05, le=10)
    flush_batch: int = Field(64, ge=1, le=1024)


class LogCfg(BaseModel):
    """log.* — 日志域(F009/F011);事件 JSONL 不受 log.level 影响。"""
    level: Literal["debug", "info", "warning", "error"] = "info"
    file: str = ""                                       # 空 = 仅控制台
    rotate_bytes: int = Field(52_428_800, ge=1_048_576, le=1_073_741_824)
    redact_enabled: bool = True                          # 全出口脱敏(INV-09)
    jsonl: LogJsonlCfg = Field(default_factory=LogJsonlCfg)


class StorageJsonlCfg(BaseModel):
    """storage.jsonl — 会话日志轮转(F011/N11)。"""
    rotate_bytes: int = Field(52_428_800, ge=1_048_576, le=1_073_741_824)


class SpillCfg(BaseModel):
    """storage.spill — spill 私有区限额(F039/N3)。"""
    max_per_file_bytes: int = Field(10_485_760, ge=1024, le=104_857_600)
    max_per_session_mb: int = Field(100, ge=1, le=10240)


class FtsCfg(BaseModel):
    """storage.fts — 会话 FTS 索引(F057)。"""
    index_batch_ms: int = Field(200, ge=10, le=5000)
    query_timeout_s: int = Field(5, ge=1, le=60)
    result_limit: int = Field(20, ge=1, le=100)


class StorageCfg(BaseModel):
    """storage.* — 存储域(F011/F039/F055/F056/F057);~ 路径运行时再展开。"""
    root: str = "~/.pyharness"
    sessions_dir: str = "~/.pyharness/sessions"
    db_path: str = "~/.pyharness/pyharness.db"
    workspaces_dir: str = "~/.pyharness/workspaces"
    archive_days: int = Field(30, ge=0, le=3650)
    jsonl: StorageJsonlCfg = Field(default_factory=StorageJsonlCfg)
    spill_dir: str = "~/.pyharness/spill"
    spill: SpillCfg = Field(default_factory=SpillCfg)
    fts: FtsCfg = Field(default_factory=FtsCfg)


class McpServerCfg(BaseModel):
    """MCP stdio server 配置;command 为 argv,不经过 shell。"""
    name: str = Field(min_length=1, max_length=64)
    command: list[str] = Field(min_length=1, max_length=64)
    enabled: bool = True
    timeout_s: int = Field(30, ge=1, le=600)


class SkillsCfg(BaseModel):
    """skills.* — local skill root and optional remote registry."""
    dir: str = "~/.pyharness/skills"
    registry_url: str = ""
    max_package_bytes: int = Field(5242880, ge=1024, le=104857600)
    max_files: int = Field(200, ge=1, le=10000)


class PluginsCfg(BaseModel):
    """plugins.* — 插件域(F001-F006;装载序 DIS-SEAM §4.6)。"""
    enabled: list[str] = Field(default_factory=list)     # 空 = 只装内置
    dir: str = "~/.pyharness/plugins"
    ctx_lazy: bool = True
    pre_activate: list[str] = Field(
        default_factory=lambda: ["storage.spill", "credentials", "storage.kv"])
    mcp_servers: list[McpServerCfg] = Field(default_factory=list)
    priority: dict[str, int] = Field(default_factory=dict)
    backpressure_limit: int = Field(1000, ge=1, le=100000)   # 总线背压(F005)
    deadletter_samples: int = Field(100, ge=0, le=10000)


class ShellWebCfg(BaseModel):
    """shell.web — Web 外壳(F065);默认仅回环,非回环须配 token(validate_rules)。"""
    host: str = "127.0.0.1"
    port: int = Field(8000, ge=1, le=65535)
    token: str = ""                                        # secret-ref 型


class ShellAcpCfg(BaseModel):
    """shell.acp — ACP 桥(F066)。"""
    enabled: bool = False


class ShellCfg(BaseModel):
    """shell.* — 外壳域(F064-F066)。"""
    web: ShellWebCfg = Field(default_factory=ShellWebCfg)
    acp: ShellAcpCfg = Field(default_factory=ShellAcpCfg)


class Settings(BaseModel):
    """全量 schema 顶层模型:9 域嵌套;未知键 extra=ignore 并收集到 CFG-607 警告。"""
    model_config = ConfigDict(extra="ignore")

    config: ConfigMeta = Field(default_factory=ConfigMeta)
    llm: LlmCfg = Field(default_factory=LlmCfg)
    loop: LoopCfg = Field(default_factory=LoopCfg)
    security: SecurityCfg = Field(default_factory=SecurityCfg)
    budget: BudgetCfg = Field(default_factory=BudgetCfg)
    log: LogCfg = Field(default_factory=LogCfg)
    storage: StorageCfg = Field(default_factory=StorageCfg)
    skills: SkillsCfg = Field(default_factory=SkillsCfg)
    plugins: PluginsCfg = Field(default_factory=PluginsCfg)
    shell: ShellCfg = Field(default_factory=ShellCfg)
