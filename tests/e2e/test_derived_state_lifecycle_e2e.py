"""派生状态生命周期(e2e,R8-3):create → use → close → **release** 的最后一环。

覆盖两处"创建了但从不释放"的派生状态:
- **FTS 索引行**:`SessionQueryIndex.delete_session` 此前**无生产调用者** ⇒ 会话删除后
  索引行仍在 ⇒ **已删内容仍可被 `session.fts_query` 搜到**(且结果指向不存在的会话)。
- **每会话串行锁**:桌面管理器的 `_locks` 只增不减(见过的每个 sid 留一个 Lock)。
"""
from __future__ import annotations

from types import SimpleNamespace

from pyharness.application import ApplicationService
from pyharness.bus import EventBus
from pyharness.config import DEFAULTS, Settings
from pyharness.desktop.sessions import DesktopSessionManager


def _cfg(tmp_path) -> Settings:
    raw = Settings.model_validate(DEFAULTS).model_dump()
    raw["storage"]["root"] = str(tmp_path)
    raw["storage"]["sessions_dir"] = str(tmp_path / "sessions")
    raw["storage"]["workspaces_dir"] = str(tmp_path / "workspaces")
    raw["storage"]["db_path"] = str(tmp_path / "index.db")
    raw["llm"]["api_key"] = "env:PH_R8_LIFECYCLE_TEST"   # 过 CRED-701 引用检查
    return Settings.model_validate(raw)


async def test_fts_rows_are_purged_when_session_is_deleted(tmp_path):
    """删除会话必须**级联清 FTS 索引行**,且必须发生在关库(**close**)之前。

    修复前实测:`SessionQueryIndex.delete_session` 零生产调用者 ⇒ `/api/sessions/{sid}`
    DELETE 只删了文件,索引行留库 ⇒ 已删内容仍可被搜索到。
    """
    cfg = _cfg(tmp_path)
    ctx = SimpleNamespace(bus=EventBus(), settings=cfg)
    svc = ApplicationService(ctx, channel="desktop", tenant_id="default")
    sid = await svc.create_session()
    log_ = await svc.require_session(sid)
    await svc._engine_runner_for(sid, log_)               # 建引擎并登记 _engines[sid]
    spine = svc._engines.get(sid)
    assert spine is not None, "前置:服务持有该会话的引擎"

    order: list[str] = []

    class _Fts:
        async def detach(self, *args):
            order.append("detach")

        async def delete_session(self, session_id: str) -> None:
            order.append(f"fts:{session_id}")

    fts = _Fts()
    spine.fts = fts
    orig_close = spine.close

    async def _close() -> None:
        order.append("close")
        await orig_close()

    spine.close = _close                                # 实例属性遮蔽(=生产调用同一方法)
    await svc.delete_session(sid)

    assert f"fts:{sid}" in order, "删除会话未级联清 FTS 索引行"
    assert order.index(f"fts:{sid}") < order.index("close"), \
        "必须在关库之前清(FTS detach 会关库)"


async def test_manager_releases_per_session_lock_on_delete(tmp_path):
    """桌面管理器按会话串行锁随会话删除释放(create → … → release)。"""
    cfg = _cfg(tmp_path)
    mgr = DesktopSessionManager(dir=tmp_path / "sessions", bus=EventBus(),
                               config=cfg)
    sid = await mgr.create()
    await mgr.open_session(sid)                         # open 路径会登记每会话锁
    assert sid in mgr._locks, "前置:会话锁已登记"
    await mgr.delete_session(sid)
    assert sid not in mgr._locks, "删除后必须释放每会话锁(否则随会话数线性增长)"
    await mgr.shutdown_all()
