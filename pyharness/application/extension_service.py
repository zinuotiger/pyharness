"""ExtensionService operations, separated from facade assembly."""
from __future__ import annotations
from typing import TYPE_CHECKING
import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from pyharness.application.session_access import _session_module
if TYPE_CHECKING:
    pass
from pyharness.core.skill import SkillManager
from pyharness.errors import raise_code
from pyharness.core.url_policy import validate_service_url as _validate_registry_url
from pyharness.core.lifecycle import admitted

class ExtensionService:
    """ExtensionService responsibility; state is owned by the composing facade."""

    @admitted
    async def budget_dashboard(self, sid: str) -> dict:
        log_ = await self.require_session(sid)
        usages = [e for e in log_.events_after() if e.type == "llm.usage"]
        used_in = sum(int(u.payload.get("in_tokens", 0)) for u in usages)
        used_out = sum(int(u.payload.get("out_tokens", 0)) for u in usages)
        base = {"sid": sid, "used_in_tokens": used_in,
                "used_out_tokens": used_out,
                "recent": [dict(u.payload) for u in usages[-20:]]}
        budget = None
        try:                                       # 优先本会话自己装配的预算门面
            spine, _log2 = await self.public_spine_for(sid)
            budget = getattr(spine, "budget", None)
        except Exception:                          # noqa: BLE001 读面降级到 ctx
            budget = None
        if budget is None:
            # 共享 ctx.budget 作 fallback:owner 为空(外部单会话注入)或确属本会话
            # 才用;owner 指向别的会话(多会话最后装配者)→ 拒用,防串场。
            owner = getattr(self.ctx, "_budget_owner_sid", None)
            if owner is None or owner == sid:
                budget = getattr(self.ctx, "budget", None)
        snap = getattr(budget, "snapshot", None)
        if not callable(snap):
            return {**base, "disabled": True,
                    "reason": "budget-gate-unassembled", "limit_cny": None,
                    "used_cny": None, "warned": False, "ratio": 0.0}
        st = snap(sid)
        if inspect.isawaitable(st):
            st = await st
        limit = float(getattr(st, "limit_cny", 0) or 0)
        used = float(getattr(st, "used_cny", 0) or 0)
        ratio = (used / limit) if limit else 0.0
        cfg = _session_module()._cfg_of(self.ctx)
        warn = float(getattr(getattr(cfg, "budget", None), "warn_ratio", 0.8))
        return {**base, "disabled": False, "limit_cny": limit, "used_cny": used,
                "warned": ratio >= warn, "ratio": round(ratio, 4)}

    def plugin_roots(self) -> list[Path]:
        roots = ([Path(__file__).resolve().parents[2] / "examples" / "plugins"]
                 if self.tenant_id == "default" else [self.tenant_root() / "plugins"])
        if self.tenant_id == "default":
            cfg = _session_module()._cfg_of(self.ctx)
            try:
                extra = getattr(getattr(cfg, "plugins", None), "dir", None)
                if extra:
                    roots.append(Path(str(extra)).expanduser())
            except Exception:                          # noqa: BLE001
                pass
        return roots

    @staticmethod
    def _plug_ctx_for(spine: Any) -> Any:
        return SimpleNamespace(session=getattr(spine, "session", None),
                               bus=getattr(spine, "bus", None))

    @admitted
    async def list_plugins(self) -> dict:
        out: list[dict] = []
        for sid, spine in self._engines.items():
            mgr = getattr(spine, "plugins", None)
            if mgr is None:
                continue
            for pid, rec in dict(getattr(spine, "plugin_state", {})).items():
                spec = rec.get("spec")
                out.append({
                    "id": pid,
                    "state": mgr.state(pid),
                    "version": getattr(spec, "manifest", {}).get("version", "?"),
                    "tools": list(rec.get("tool_names", ())),
                    "session": str(sid),
                })
        return {"plugins": out, "count": len(out)}

    @admitted
    async def plugin_action(self, kind: str, body: dict) -> dict:
        self._ensure_open()
        pid = str((body or {}).get("id") or "")
        if not pid:
            return {"ok": False, "error": "缺插件 id"}
        if not self._engines:
            return {"ok": False, "error": "无活动会话(先新建/打开会话)"}
        from pyharness.core import plugin_loader as _pl
        from pyharness.application.session_store import _plugin_within
        pdir = ((Path(__file__).resolve().parents[2] / "examples" / "plugins")
                if self.tenant_id == "default" else (self.tenant_root() / "plugins")) / pid
        results: list[dict] = []
        for sid, spine in list(self._engines.items()):
            if getattr(spine, 'platform_runtime', None) is not None:
                results.append({'session': str(sid), 'ok': False,
                                'error': 'Platform Agent 禁止宿主插件生命周期操作'})
                continue
            mgr = spine.plugins
            try:
                if kind == "load":
                    d = Path(str(body.get("dir") or pdir)).expanduser().resolve()
                    if not d.exists():
                        return {"ok": False, "error": f"插件目录不存在:{d}"}
                    if not any(_plugin_within(d, r) for r in self.plugin_roots()):
                        return {"ok": False,
                                "error": "插件目录必须在配置的插件根目录内"}
                    spec = _pl.load_spec(d)
                    await _pl.load_plugin(mgr, spec, self._plug_ctx_for(spine),
                                          spine.tool_registry,
                                          state=spine.plugin_state)
                elif kind == "unload":
                    rec = spine.plugin_state.get(pid)
                    if rec is None:
                        results.append({"session": str(sid), "ok": False,
                                        "error": "未安装"})
                        continue
                    await _pl.unload_plugin(mgr, rec["spec"],
                                            self._plug_ctx_for(spine),
                                            spine.tool_registry,
                                            state=spine.plugin_state)
                elif kind == "reload":
                    rec = spine.plugin_state.get(pid)
                    if rec is None:
                        results.append({"session": str(sid), "ok": False,
                                        "error": "未安装"})
                        continue
                    await _pl.reload_plugin(
                        mgr, rec["spec"], self._plug_ctx_for(spine),
                        spine.tool_registry, state=spine.plugin_state,
                        pkg_dir=Path(rec.get("path", "")).parent or pdir)
                else:
                    return {"ok": False, "error": f"未知操作:{kind}"}
                results.append({"session": str(sid), "ok": True,
                                "state": mgr.state(pid)})
            except Exception as e:                     # noqa: BLE001
                results.append({"session": str(sid), "ok": False,
                                "error": f"{type(e).__name__}:{e}"[:200]})
        ok_all = all(r.get("ok") for r in results) and bool(results)
        return {"ok": ok_all, "results": results,
                "hint": "新会话(重启程序)自动预载 examples/plugins/ 全部插件"}

    @staticmethod
    def _reset_preset_state(spine: Any) -> None:
        from pyharness import engine as _eng
        cfg, policy = _eng.compile_preset(spine, 'strict')
        spine.settings.security = cfg.security
        spine.scope.policy = policy

    @admitted
    async def set_preset(self, body: dict) -> dict:
        self._ensure_open()
        preset = str((body or {}).get('preset') or '')
        valid = ('strict', 'standard', 'readonly', 'locked')
        if preset not in valid:
            return {'ok':False,'error':'invalid_preset','valid':list(valid)}
        if not self._engines:
            return {'ok':False,'error':'no_active_sessions'}
        from pyharness import engine as _eng
        # Validate every setter against detached copies first. A failed reset
        # must never partially clear the live deny set or claim success.
        try:
            prepared=[(spine,*_eng.compile_preset(spine,preset)) for spine in self._engines.values()]
        except Exception:
            return {'ok':False,'error':'preset_configuration_failed'}
        for spine,cfg,policy in prepared:
            spine.settings.security=cfg.security
            spine.scope.policy=policy
        return {'ok':True,'preset':preset,'sessions':len(prepared),
                'hint':'exec.* 等危险工具仍由治理与审批约束'}

    def skill_installer(self) -> Any:
        from pyharness.core.skill_registry import SkillInstaller
        cfg = _session_module()._cfg_of(self.ctx)
        sk = getattr(cfg, "skills", None)
        root = (Path(str(getattr(sk, "dir", "~/.pyharness/skills"))).expanduser()
                if self.tenant_id == "default"
                else self.tenant_root() / "skills")
        return SkillInstaller(
            root,
            max_package_bytes=int(getattr(sk, "max_package_bytes", 5 * 1024 * 1024)),
            max_files=int(getattr(sk, "max_files", 200)))

    @admitted
    async def search_skills(self, query: str,
                            registry_url: str = "") -> dict:
        cfg = _session_module()._cfg_of(self.ctx)
        url = str(registry_url or getattr(getattr(cfg, "skills", None),
                                          "registry_url", "") or "").strip()
        if not url:
            raise_code("CFG-601", field="skills.registry_url",
                       advice="未配置 Skill Registry URL")
        url = _validate_registry_url(url)
        rows = await self.skill_installer().search(query, url)
        return {"registry_url": url, "skills": rows, "count": len(rows)}

    @admitted
    async def install_skill(self, sid: str, name: str, *, version: str = "",
                            registry_url: str = "",
                            approved_by: str = "user") -> dict:
        self._ensure_open()
        cfg = _session_module()._cfg_of(self.ctx)
        url = str(registry_url or getattr(getattr(cfg, "skills", None),
                                          "registry_url", "") or "").strip()
        if not url:
            raise_code("CFG-601", field="skills.registry_url",
                       advice="未配置 Skill Registry URL")
        url = _validate_registry_url(url)
        result = await self.skill_installer().install(
            name, registry_url=url, version=version or None,
            approved_by=approved_by)
        log_ = await self.require_session(sid)
        await log_.append("skill.installed", {
            "name": result["name"], "version": result["version"],
            "sha256": result["sha256"], "source": result["source"],
            "approved_by": approved_by}, actor="user", sync=True)
        self.skills_mgr().scan()
        return result

    @admitted
    async def remove_skill(self, sid: str, name: str, *,
                           approved_by: str = "user") -> dict:
        self._ensure_open()
        result = self.skill_installer().remove(name, approved_by=approved_by)
        if result.get("removed"):
            log_ = await self.require_session(sid)
            await log_.append("skill.removed", {
                "name": name, "version": result.get("version", ""),
                "approved_by": approved_by}, actor="user", sync=True)
            self.skills_mgr().scan()
        return result

    @admitted
    async def rollback_skill(self, sid: str, name: str, version: str, *,
                             approved_by: str = "user") -> dict:
        self._ensure_open()
        result = self.skill_installer().rollback(
            name, version, approved_by=approved_by)
        log_ = await self.require_session(sid)
        await log_.append("skill.rollback", {
            "name": name, "from_version": result.get("from_version", ""),
            "to_version": version, "approved_by": approved_by},
            actor="user", sync=True)
        self.skills_mgr().scan()
        return result

    def skill_versions(self, name: str) -> list[str]:
        return self.skill_installer().versions(name)

    def skills_mgr(self) -> SkillManager:
        for spine in self._engines.values():
            mgr = getattr(spine, "skills", None)
            if mgr is not None and getattr(spine, 'platform_runtime', None) is None:
                return mgr
        if self.tenant_id != "default":
            return SkillManager([self.tenant_root() / "skills"])
        roots = [Path(__file__).resolve().parents[2] / "skills"]
        cfg = _session_module()._cfg_of(self.ctx)
        try:
            extra = getattr(getattr(cfg, "skills", None), "dir", None)
            if extra:
                roots.append(Path(str(extra)).expanduser())
        except Exception:                              # noqa: BLE001
            pass
        return SkillManager(roots)

    @admitted
    async def list_skills(self) -> dict:
        mgr = self.skills_mgr()
        rows = mgr.list()
        return {"skills": rows, "count": len(rows), "catalog": mgr.render_catalog()}

    @admitted
    async def skill_detail(self, name: str) -> dict:
        data = self.skills_mgr().load(str(name))
        return {"ok": True, "name": data["name"],
                "description": data["description"], "dir": data["dir"],
                "body": data["body"]}

    @admitted
    async def reload_skills(self) -> dict:
        counts = []
        for spine in self._engines.values():
            mgr = getattr(spine, "skills", None)
            if mgr is not None:
                counts.append({"session": str(getattr(
                    getattr(spine, "session", None), "sid", "?")),
                    "count": mgr.scan(), "frozen": bool(getattr(mgr, '_frozen', False))})
        if not counts:
            counts.append({"direct": True, "count": self.skills_mgr().scan()})
        return {"ok": True, "results": counts,
                "hint": "经典会话已显式刷新；正式平台任务保留其装配时固定技能，新任务使用新快照"}

