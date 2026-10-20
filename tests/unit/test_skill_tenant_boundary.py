"""An actual nondefault engine must never scan the default user's skill root."""
from pyharness import cli
from pyharness.application.service import ApplicationService
from tests.support import isolated_settings


async def test_effective_engine_settings_isolate_private_skill_roots(tmp_path):
    cfg=isolated_settings(tmp_path)
    default=tmp_path/'skills'/'default-private';default.mkdir(parents=True)
    (default/'SKILL.md').write_text('---\nname: default-private\ndescription: private\n---\nDEFAULT-PRIVATE',encoding='utf-8')
    service=ApplicationService(cli.assemble_ctx(cfg),tenant_id='team-a')
    own=service.tenant_root()/'skills'/'own';own.mkdir(parents=True)
    (own/'SKILL.md').write_text('---\nname: own\ndescription: own\n---\nOWN-ONLY',encoding='utf-8')
    try:
        from pyharness.engine import build_spine
        spine=await build_spine(service.effective_settings(),sid='s-tenant-skill',
            sessions_dir=service._tenant_sessions_dir(),bus=None)
        try:
            names={r['name'] for r in spine.skills.list()}
            assert 'default-private' not in names
            assert 'own' in names
        finally:await spine.close()
    finally:await service.shutdown()
