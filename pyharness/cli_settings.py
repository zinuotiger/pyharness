"""Cli settings: complete migrated implementation."""
from __future__ import annotations
from typing import Any, Optional
from pyharness import config as config
from pyharness.errors import raise_code


def _load_settings(cfg_path: Optional[str]) -> Any:
    """配置四层加载(L1→L2 文件→L3 env→L4 CLI 路径),CFG-601 失败即中止不装配。

    config.load_settings 只收 CLI 覆盖元组、不收路径 → 显式 --config 路径经公开
    原语(deep_merge/load_file_layer/env_subset/Settings/validate_rules)重走同一
    管线,语义与 load_settings 一致(见偏离 11)。
    """
    if not cfg_path:
        return config.load_settings()
    raw = config.deep_merge(config.load_file_layer(cfg_path), config.env_subset())
    try:
        settings = config.Settings.model_validate(raw)
    except Exception as e:                     # noqa: BLE001 与 config 主管线同错面
        if isinstance(e, config.ValidationError):
            fields = [".".join(str(x) for x in err.get("loc") or ())
                      for err in e.errors()]
            raise_code("CFG-601", reason="type_range", fields=fields,
                       detail="类型/范围/枚举越界,修配置文件对应字段")
        raise
    config.validate_rules(settings)
    return settings
