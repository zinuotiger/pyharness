"""TenantService operations, separated from facade assembly."""
from __future__ import annotations
from typing import TYPE_CHECKING
import copy
from pathlib import Path
from typing import Any
from pyharness.application.session_access import _session_module
if TYPE_CHECKING:
    pass
from pyharness.core.tenant_settings import TenantSettingsStore

class TenantService:
    """TenantService responsibility; state is owned by the composing facade."""

    def _storage_root(self) -> Path:
        cfg = _session_module()._cfg_of(self.ctx)
        root = getattr(getattr(cfg, "storage", None), "root", None)
        return Path(str(root or "~/.pyharness")).expanduser()

    @property
    def credentials(self) -> TenantSettingsStore:
        """Tenant-owned secret interface for application adapters."""
        return self._settings_store

    @property
    def storage_root(self) -> Path:
        return self._storage_root()

    @property
    def platform(self):
        if not hasattr(self, '_platform'):
            from pyharness.application.platform_service import PlatformService
            self._platform = PlatformService(self)
        return self._platform

    @property
    def channels(self):
        if not hasattr(self, '_channels'):
            from pyharness.application.channels import Channels
            self._channels = Channels(self)
        return self._channels

    def tenant_root(self) -> Path:
        return self._storage_root() / "tenants" / self.tenant_id

    @property
    def skill_catalog_root(self) -> Path:
        """Actual tenant skill root, without acquiring model/secret state."""
        if self.tenant_id != 'default':
            return self.tenant_root() / 'skills'
        cfg = _session_module()._cfg_of(self.ctx)
        return Path(cfg.skills.dir).expanduser()

    def _tenant_sessions_dir(self, ctx: Any = None) -> Path:
        """本租户的**会话目录**—— 唯一派生点(R24 收口)。

        `default` 租户沿用装配 ctx 的 `storage.sessions_dir`(L1 锚点);其余租户落在
        自己的租户根下,与 `effective_settings()` 里 `storage.db_path` 的租户分域
        **配套**(索引库与会话目录同域,见 R14-8)。此前该分支在**三处**各写一遍
        (三份相同且正确,但"同源规则多份实现"必然漂移 —— 见 R14-3 方法论)。
        """
        c = ctx if ctx is not None else self.ctx
        if self.tenant_id == "default":
            return _session_module()._sessions_dir_of(c)
        return self.tenant_root() / "sessions"

    def effective_settings(self) -> Any:
        """Settings overridden by this tenant's active model profile **and storage scope**.

        ``2026-09-21 R14-8``:非 ``default`` 租户的**会话目录**本就按租户分
        （调用点一律用 ``tenant_root()/sessions``），但其**派生索引库**
        （``storage.db_path``）仍是**全局**的 ⇒ 两个租户共用一个 FTS 库：
        ① ``query`` 没有任何租户维度 ⇒ A 的 agent 能检索到 B 的会话内容
           （实测：A 侧不带 ``session_id`` 查询直接命中 B 的条目，`session.fts_query`
           的 ``session_id`` 又取自 LLM 自报参数）；
        ② 同名 sid 跨租户在 ``fts_rows`` 主键 ``(session_id, seq)`` 上**碰撞**
           ⇒ 后写者被 ``INSERT OR IGNORE`` 静默丢弃、其内容根本不入索引（实测）。
        **派生视图必须与真源同域**：索引库随会话目录一起按租户分。
        """
        cfg = _session_module()._cfg_of(self.ctx)
        profile = self._settings_store.active_profile(self.tenant_id)
        scoped = self.tenant_id != "default"
        if (profile is None and not scoped) or not hasattr(cfg, "model_dump"):
            return copy.deepcopy(cfg)       # Runtime registration must not mutate caller settings.
        raw = cfg.model_dump()
        if scoped:                          # 索引库随会话目录同域（R14-8）
            storage = dict(raw.get("storage") or {})
            storage["db_path"] = str(self.tenant_root() / "index.db")
            raw["storage"] = storage
            skills = dict(raw.get('skills') or {})
            skills['dir'] = str(self.skill_catalog_root)
            raw['skills'] = skills
        if profile is not None:
            llm = dict(raw.get("llm") or {})
            from pyharness.application.model_profile_configuration import profile_values
            llm.update(profile_values(profile, self.tenant_id, llm))
            raw["llm"] = llm
        from pyharness.config import Settings
        return Settings.model_validate(raw)

    def tenant_state(self) -> dict:
        state = self._settings_store.state(self.tenant_id)
        if self.tenant_id != 'default':
            state['tenants'] = [self.tenant_id]
        return state

    def save_model_profile(self, body: dict) -> dict:
        self._ensure_open()
        result = self._settings_store.upsert_profile(self.tenant_id, body)
        result["restart_required"] = bool(self._engines)
        return result

    def activate_model_profile(self, profile_id: str) -> dict:
        self._ensure_open()
        result = self._settings_store.activate_profile(self.tenant_id, profile_id)
        result["restart_required"] = bool(self._engines)
        return result

    def delete_model_profile(self, profile_id: str) -> dict:
        self._ensure_open()
        result = self._settings_store.delete_profile(self.tenant_id, profile_id)
        result["restart_required"] = bool(self._engines)
        return result

