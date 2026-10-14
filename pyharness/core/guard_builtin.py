"""Guard builtin: complete migrated implementation."""
from __future__ import annotations
from typing import Any, Callable, Iterable, Optional
from pyharness.core.guard_models import Guard
from pyharness.core.guard_rules import g_schema_match, g_schema_check, g_danger_match, g_danger_check, g_fs_path_match, g_fs_path_check, g_credential_read_match, g_credential_read_check, g_net_outbound_match, g_net_outbound_check, g_exec_match, g_exec_check, g_overwrite_match, g_overwrite_check


_BUILTIN_IDS: tuple[str, ...] = (
    "g-schema",            # g1 schema 复查(恒在)
    "g-fs-path",           # g3 路径几何
    "g-credential-read",   # g4 凭据读拦截
    "g-exec",              # g6 exec 约束
    "g-overwrite",         # g7 覆写转审批
    "g-net-outbound",      # g5 外发域名 allowlist
    "g-danger",            # g2 danger 分级(恒在,殿后求值)
)

def build_builtin_chain(*, credential_paths: Optional[Iterable[str]] = None,
                        validator: Optional[Callable[..., dict]] = None,
                        approval_channel: Optional[bool] = None,
                        path_exists: Optional[Callable[[str], bool]] = None,
                        link_resolver: Optional[Callable[[str], str]] = None
                        ) -> list[Guard]:
    """装配内置 g1-g7(固定序 = 求值序;DIS-SEAM §2.5 第④步)。

    g1 恒在首;动作形状 guard(g3/g4/g6/g7/g5)先于危险分级 g-danger 求值,分级
    只决定是否需要审批、不短路形状拒(见 _BUILTIN_IDS 注释);插件 guard 一律
    按注册序追加链尾(本函数即固定序源)。validator/credential_paths/
    approval_channel/path_exists/link_resolver 为注入式依赖(见模块 docstring
    偏离 1/2/3/5):链实例构造时一次注入,运行期只读(防策略漂移,F021)。
    """
    paths = list(credential_paths) if credential_paths else None
    return [
        _FuncGuard("g-schema", g_schema_match,
                   lambda c, s: g_schema_check(c, s, validator=validator)),
        _FuncGuard("g-fs-path", g_fs_path_match,
                   lambda c, s: g_fs_path_check(c, s, link_resolver=link_resolver)),
        _FuncGuard("g-credential-read", g_credential_read_match,
                   lambda c, s: g_credential_read_check(
                       c, s, credential_paths=paths,
                       link_resolver=link_resolver)),
        _FuncGuard("g-exec", g_exec_match, g_exec_check),
        _FuncGuard("g-overwrite", g_overwrite_match,
                   lambda c, s: g_overwrite_check(c, s, path_exists=path_exists)),
        _FuncGuard("g-net-outbound", g_net_outbound_match, g_net_outbound_check),
        _FuncGuard("g-danger", g_danger_match, g_danger_check),
    ]

class _FuncGuard(Guard):
    """函数对 → Guard 实例适配器(内置 guard 实现即模块函数对,spec 清单)。"""

    def __init__(self, guard_id: str,
                 match_fn: Callable[[Any], bool],
                 check_fn: Callable[[Any, Any],
                                    Any]) -> None:
        self.id = guard_id
        self._match_fn = match_fn
        self._check_fn = check_fn

    def match(self, call: Any) -> bool:
        return bool(self._match_fn(call))

    async def check(self, call: Any, scope: Any) -> tuple[str, Optional[str]]:
        return await self._check_fn(call, scope)

# Preserve historical import and pickle identities through the compatibility facade.
_FuncGuard.__module__ = 'pyharness.core.tools_guard'
