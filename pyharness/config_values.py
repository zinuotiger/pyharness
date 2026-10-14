"""Config values: complete migrated implementation."""
from __future__ import annotations
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional
import yaml
from pyharness.errors import raise_code


log = logging.getLogger("pyharness.config")


RUNTIME_MUTABLE: frozenset = frozenset({
    "log.level", "log.file", "llm.probe.interval_s",
    "llm.usage.unit_price", "budget.monthly.alert_ratio"})

BUILTIN_GUARDS = frozenset({
    "g-fs-path", "g-credential-read", "g-net-outbound", "g-exec", "g-overwrite"})

_SECRET_RE = re.compile(r"^(env:[A-Za-z_][A-Za-z0-9_]*|file:.+)$")

_SECRET_KEYS = ("llm.api_key", "shell.web.token")

_TOOL_DANGER_BASELINE: dict[str, str] = {"fs.delete_file": "high"}

_DANGER_RANK: dict[str, int] = {"none": 0, "low": 1, "high": 2, "critical": 3}

_SECRET_KEY_HINTS = ("api_key", "token", "secret", "password", "credential")

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

