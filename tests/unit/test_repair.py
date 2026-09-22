"""pyharness/repair.py 单测 — 契约:specs/repair.py.md(F060 权威)+ OPS.md §4
S-03/S-05 + PRD §3.4(空洞合法化)+ EVENT-SCHEMA(session.recovered 载荷)。

覆盖面(任务要求 + 规格模块级测试):
    auto_scan:全会话自检命中截断会话;跳过轮转/备份/隔离辅助文件;目录不存在 → []
    scan_session:人工截断(尾部偏移)/注入坏行/制造空洞 → 各字段断言;compacted/
        recovered 声明覆盖空洞不告警;fts_last_seq 提供者落后标记;无提供者不算落后
    repair_session 管线:截断+备份+recovered 声明(lost/fixed/backup)→ 二次 repair
        幂等(无动作不追加);中部坏行隔离(隔离档可查/主文件其余事件完整/二次幂等);
        seq 空洞无声明 → seq-holes 声明后幂等收敛;交互分支(monkeypatch 输入);
        整文件半行 → 截空 + 声明跳过(EVT-106 语义)但备份留存
    backup_file:命名规范 .corrupt-{ts}.jsonl / 字节一致 / 同 ts 幂等
    truncate_tail:截断后逐事件可解析、seq 连续;offset=None 不动
    quarantine_lines:坏行抽档+主文件原子重写;keep_quarantine=False 保留原地(不删);
        隔离档写失败 → 行保留原地 archived=False(数据优先,隔离≠删除)
    locate_holes:无声明告警 / declared 覆盖不告警(纯函数)
    rebuild_derived_views:索引落后 → 重建一致;未装配降级 False;对账不一致 → PERS-201
    declare_recovered:事件含 fixed/lost/backup、返回 seq;空动作不追加(幂等);
        空文件未 created → 声明跳过返回 None
    interactive_confirm:K/n 决策 / EOF 回退安全默认
    view_quarantine:隔离后可查(行号/原文/原因/归档);无隔离文件 → []
    错误纪律:活会话双开 → PERS-202;对账不一致 → PERS-201;全走 raise_code

铁律:全部用 pytest tmp_path,绝不写真实 ~/.pyharness。
"""
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from pyharness import repair as R
from pyharness.errors import PyHError
from pyharness.events import Envelope
from pyharness.persistence import detect_truncation

SID = "s-repair01"
TS = "2026-09-07T06:00:00.000000Z"
FIXED_TS = "20260907T000000000"          # now_ts 打桩值(命名断言确定性)
_CORRUPT_NAME = re.compile(r"^\d{8}T\d{9}$")
_PAYLOAD_DEFAULT = {
    "session.created": {"title": "", "model": "m"},
    "user.message": {"content": "hi"},
    "agent.message": {"content": "ok"},
    "session.finished": {"reason": "idle"},
}


def _env(seq, type_="agent.message", actor="agent", payload=None, sid=SID,
         ts=TS) -> Envelope:
    """直接构造合法信封(payload 为信封层 dict,无需二次模型校验)。"""
    if payload is None:
        payload = _PAYLOAD_DEFAULT.get(type_, {})
    return Envelope(seq=seq, ts=ts, type=type_, session_id=sid, actor=actor,
                    payload=payload)


def _line(env: Envelope) -> str:
    return env.model_dump_json() + "\n"


def _session_events(n, sid=SID) -> list[Envelope]:
    """真实会话头:session.created(seq=1) + n-1 条 user.message。"""
    evs = [_env(1, "session.created", "system")]
    for i in range(2, n + 1):
        evs.append(_env(i, "user.message", "user", {"content": f"m{i}"}))
    return evs


def _write(d: Path, sid: str, envs: list[Envelope], tail: bytes = b"",
           split_after: int | None = None) -> Path:
    """写会话文件:逐行 JSONL;\n 收尾 + 可选尾部半行 / 在 split_after 行后插坏行。"""
    path = d / f"{sid}.jsonl"
    chunks = []
    for env in envs:
        chunks.append(_line(env))
        if split_after is not None and len(chunks) == split_after:
            chunks.append('{"garbage": "not-an-envelope"}\n')
    path.write_bytes("".join(chunks).encode("utf-8") + tail)
    return path


def _ctx(d, *, session=None, session_query=None, storage=None, **kw) -> SimpleNamespace:
    """ctx 装配门面(与 cli.assemble_ctx 同形:storage.sessions_dir 锚点)。"""
    ns = SimpleNamespace(
        storage=storage or SimpleNamespace(sessions_dir=Path(d)),
        session=session,
        session_query=session_query)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def _seqs_of(path: Path) -> list[int]:
    """文件内全部信封 seq(逐行解析,坏行跳过)。"""
    out = []
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            try:
                out.append(Envelope.model_validate_json(raw).seq)
            except Exception:                     # noqa: BLE001
                continue
    return out


class _ViewStub:
    """FTS 视图桩:attach_source/rebuild(async)/max_seq(对账提供者)。
    auto_catchup=True(默认):rebuild 成功后视图追平日志(真实现语义),供
    repair_session 全管线测试;False:重建不追平(测对账 PERS-201 防御路径)。
    """

    def __init__(self, max_seq: int = 0, fail_rebuild: bool = False,
                 auto_catchup: bool = True):
        self.max = max_seq
        self.rebuilt: list[str] = []
        self.sources: dict[str, object] = {}
        self._fail = fail_rebuild
        self._auto_catchup = auto_catchup

    def max_seq(self, sid: str) -> int:
        return self.max

    def attach_source(self, sid: str, store: object) -> None:
        self.sources[sid] = store

    async def rebuild(self, *, session_id=None) -> int:
        self.rebuilt.append(session_id or "*")
        if self._auto_catchup:
            # 真实现:重建追平视图到内容日志末 seq(排除 recovered 声明,与对账同口径)
            for sid, store in self.sources.items():
                if session_id in (None, sid):
                    seqs = [e.seq for e in store.replay()
                            if e.type != "session.recovered"] \
                        if hasattr(store, "replay") else []
                    if seqs:
                        self.max = max(self.max, max(seqs))
        return 1


# ===================================================================== auto_scan
class TestAutoScan:
    async def test_hit_truncated_session_only(self, tmp_path):
        """全会话自检:命中截断会话;健康/轮转/备份/隔离文件不报。"""
        _write(tmp_path, SID, _session_events(3),
               tail=b'{"seq": 4, "ts": "2026-09-07T0')          # 崩溃现场
        good = "s-good-001"
        _write(tmp_path, good, _session_events(2))              # 健康会话
        # 辅助文件(含损坏内容)一律跳过:备份/轮转/隔离
        (tmp_path / f"{SID}.corrupt-{FIXED_TS}.jsonl").write_bytes(
            b'{"seq": 1' + _line(_env(1)).encode())
        (tmp_path / f"{SID}.2.jsonl").write_bytes(b'{"seq": 2')
        (tmp_path / f"{SID}.quarantine-{FIXED_TS}.jsonl").write_bytes(b'junk')
        hits = await R.auto_scan(tmp_path)
        assert [h.sid for h in hits] == [SID]
        assert hits[0].tail_truncated is True and hits[0].healthy is False

    async def test_missing_dir_returns_empty(self, tmp_path):
        assert await R.auto_scan(tmp_path / "nope") == []
        assert await R.auto_scan(Path("/definitely-not-exist-xyz")) == []

    async def test_scan_only_unhealthy_returned(self, tmp_path):
        _write(tmp_path, "s-good-002", _session_events(2))
        hits = await R.auto_scan(tmp_path)
        assert hits == []


# ===================================================================== scan_session
class TestScanSession:
    async def test_healthy_session(self, tmp_path):
        path = _write(tmp_path, SID, _session_events(3))
        h = await R.scan_session(path)
        assert h.sid == SID and h.healthy is True
        assert h.tail_truncated is False and h.bad_lines == []
        assert h.holes == [] and h.holes_declared == []
        assert h.index_stale is False

    async def test_tail_half_line_fields(self, tmp_path):
        full = "".join(_line(e) for e in _session_events(3)).encode("utf-8")
        path = _write(tmp_path, SID, _session_events(3),
                      tail=b'{"seq": 4, "ts": "2026-09-07T0')
        h = await R.scan_session(path)
        assert h.tail_truncated is True
        assert h.tail_offset == len(full)                    # 半行起始 = 最后完整行末
        assert detect_truncation(path) == len(full)
        assert h.healthy is False

    async def test_bad_line_and_mid_hole_fields(self, tmp_path):
        path = _write(tmp_path, SID, _session_events(3), split_after=2)
        raw = path.read_bytes().decode("utf-8")              # 中部坏行(第 3 物理行)
        bad_no = raw.splitlines().index('{"garbage": "not-an-envelope"}') + 1
        h = await R.scan_session(path)
        assert bad_no == 3 and h.bad_lines == [3]
        assert h.healthy is False

    async def test_undeclared_hole_detected(self, tmp_path):
        evs = [_env(1, "session.created", "system"), _env(2), _env(4), _env(5)]
        path = _write(tmp_path, SID, evs)                    # seq3 物理缺失
        h = await R.scan_session(path)
        assert h.holes == [3] and h.healthy is False
        assert h.holes_declared == []

    async def test_declared_hole_is_legal(self, tmp_path):
        """recovered 声明(seq-holes)覆盖空洞 = 合法,不再告警(§3.4)。"""
        evs = [_env(1, "session.created", "system"), _env(2), _env(4), _env(5),
               _env(6, "session.recovered", "system",
                    {"fixed": ["seq-holes:[3]"], "backup": "b.jsonl"})]
        path = _write(tmp_path, SID, evs)
        h = await R.scan_session(path)
        assert h.holes == [] and h.holes_declared == [(3, 3)]
        assert h.healthy is True

    async def test_compacted_declared_hole_is_legal(self, tmp_path):
        evs = [_env(1, "session.created", "system"), _env(2),
               _env(3, "context.compacted", "system",
                    {"ranges": [[1, 2]], "summary": "s"}),
               _env(4), _env(5)]
        # 空洞:声明区间不覆盖缺失 → 仍告警
        path = _write(tmp_path, SID, evs)
        h = await R.scan_session(path)
        assert h.holes == []
        assert h.healthy is True and h.holes_declared == [(1, 2)]

    async def test_index_stale_with_provider(self, tmp_path):
        path = _write(tmp_path, SID, _session_events(3))
        stale = await R.scan_session(path, fts_last_seq=lambda sid: 1)
        assert stale.index_stale is True and stale.healthy is False
        synced = await R.scan_session(path, fts_last_seq=lambda sid: 3)
        assert synced.index_stale is False and synced.healthy is True
        # 无提供者(视图未装配)= 不算落后(偏离 4)
        no_provider = await R.scan_session(path)
        assert no_provider.index_stale is False

    async def test_index_ghost_row_detected(self, tmp_path):
        """幽灵行(视图末 seq 超前日志)也判 stale:dispatch 先于 flush,落盘失败时
        事件已入总线被 FTS 索引、日志却无该 seq。"""
        path = _write(tmp_path, SID, _session_events(3))
        ghost = await R.scan_session(path, fts_last_seq=lambda sid: 9)
        assert ghost.index_stale is True and ghost.healthy is False

    async def test_index_not_stale_when_tail_is_non_content(self, tmp_path):
        """CND-06/08 水位同口径:日志尾部为非内容事件(如 session.finished)时,
        不误判 stale(修复前 last=全部非 recovered 末 seq,含 finished → 恒 stale)。"""
        evs = [_env(1, "session.created", "system"),
               _env(2, "user.message", "user", {"content": "m2"}),
               _env(3, "session.finished", "system", {"reason": "idle"})]
        path = _write(tmp_path, SID, evs)
        h = await R.scan_session(path, fts_last_seq=lambda sid: 2)  # 内容末 seq = 2
        assert h.index_stale is False      # finished(seq3)非内容 → 不计入水位

    async def test_missing_file_healthy(self, tmp_path):
        h = await R.scan_session(tmp_path / f"{SID}.jsonl")
        assert h.healthy is True


# ===================================================================== backup/truncate
class TestBackupFile:
    def test_backup_naming_and_bytes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(R, "now_ts", lambda: FIXED_TS)
        evs = _session_events(3)
        path = _write(tmp_path, SID, evs)
        dst = R.backup_file(path)
        assert dst.name == f"{SID}.corrupt-{FIXED_TS}.jsonl"
        ts_part = dst.name.replace(f"{SID}.corrupt-", "").replace(".jsonl", "")
        assert _CORRUPT_NAME.fullmatch(ts_part)
        assert dst.read_bytes() == path.read_bytes()         # 字节一致(元数据保留)
        assert _seqs_of(dst) == [1, 2, 3]                    # 备份完整可回放

    def test_backup_same_ts_idempotent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(R, "now_ts", lambda: FIXED_TS)
        path = _write(tmp_path, SID, _session_events(2))
        d1 = R.backup_file(path)
        d2 = R.backup_file(path)                             # 同 ts 已备 → 幂等不重备
        assert d1 == d2 and d1.exists()

    def test_backup_missing_file_raises_pers202(self, tmp_path):
        with pytest.raises(PyHError) as e:
            R.backup_file(tmp_path / f"{SID}.jsonl")
        assert e.value.code == "PERS-202"


class TestTruncateTail:
    def test_truncates_and_keeps_events_parseable(self, tmp_path):
        path = _write(tmp_path, SID, _session_events(3),
                      tail=b'{"seq": 4, "ts": "2026-09-07T0')
        off = detect_truncation(path)
        R.truncate_tail(path, off)
        assert _seqs_of(path) == [1, 2, 3]                   # 截断后逐事件可解析
        assert path.read_bytes().endswith(b"\n")             # 完整行收尾
        assert detect_truncation(path) is None               # 无半行残留

    def test_offset_none_noop(self, tmp_path):
        path = _write(tmp_path, SID, _session_events(2))
        before = path.read_bytes()
        R.truncate_tail(path, None)
        assert path.read_bytes() == before

    def test_whole_file_partial_truncates_to_zero(self, tmp_path):
        """整文件即一条未完成半行(无换行)→ 截到 0(偏离 2:未完成即未发生)。"""
        path = _write(tmp_path, SID, [],
                      tail=b'{"seq": 1, "ts": "2026-09-07T00:00:00.000000Z", "ty')
        R.truncate_tail(path, 0)
        assert path.read_bytes() == b""


# ===================================================================== 修复管线
class TestRepairSession:
    async def test_tail_repair_backup_recovered(self, tmp_path):
        """截断修复:备份存在+字节一致、fixed/lost、recovered 事件(审计落点)。"""
        path = _write(tmp_path, SID, _session_events(3),
                      tail=b'{"seq": 4, "ts": "2026-09-07T0')
        report = await R.repair_session(_ctx(tmp_path), SID)
        assert report.fixed == ["tail-truncated"]
        assert report.lost == 1                               # 半行含 1 个未完成事件
        assert report.recovered_seq == 4                      # 续写 seq=max+1
        backup = report.backup_path
        assert backup is not None and backup.exists()
        assert backup.read_bytes().endswith(b'{"seq": 4, "ts": "2026-09-07T0')
        # recovered 事件载荷(fixed/lost/backup)
        lines = path.read_text(encoding="utf-8").splitlines()
        rec = Envelope.model_validate_json(lines[-1])
        assert rec.type == "session.recovered" and rec.actor == "system"
        assert rec.payload["fixed"] == ["tail-truncated"]
        assert rec.payload["lost"] == [4]
        assert rec.payload["backup"] == str(backup)
        assert _seqs_of(path) == [1, 2, 3, 4]                 # seq 连续可续跑
        assert detect_truncation(path) is None

    async def test_second_repair_idempotent_no_actions(self, tmp_path):
        """DEP G6 幂等:已修复文件二次 repair → 无动作、不追加事件、无 lost。"""
        path = _write(tmp_path, SID, _session_events(3),
                      tail=b'{"seq": 4, "ts": "2026-09-07T0')
        r1 = await R.repair_session(_ctx(tmp_path), SID)
        r2 = await R.repair_session(_ctx(tmp_path), SID)
        assert r1.recovered_seq == 4
        assert r2.fixed == [] and r2.lost == 0
        assert r2.recovered_seq is None
        assert r2.backup_path is None
        assert _seqs_of(path) == [1, 2, 3, 4]                 # 未重复截断/追加
        assert len([e for e in open(path, encoding="utf-8")
                    if "session.recovered" in e]) == 1

    async def test_missing_session_empty_report(self, tmp_path):
        report = await R.repair_session(_ctx(tmp_path), "s-nope-001")
        assert report.sid == "s-nope-001"
        assert report.fixed == [] and report.lost == 0
        assert report.backup_path is None

    async def test_live_session_conflict_pers202(self, tmp_path):
        """ctx.session 持有同 sid(双开)→ PERS-202 拒修(DEP §6 禁双开)。"""
        _write(tmp_path, SID, _session_events(3),
               tail=b'{"seq": 4, "ts": "2026-09-07T0')
        sess = SimpleNamespace(sid=SID)
        with pytest.raises(PyHError) as e:
            await R.repair_session(_ctx(tmp_path, session=sess), SID)
        assert e.value.code == "PERS-202"

    async def test_quarantine_repair_isolates_bad_line(self, tmp_path):
        """中部坏行:隔离不删(隔离档可查),主文件其余事件完整,二次幂等。"""
        path = _write(tmp_path, SID, _session_events(3), split_after=2)
        report = await R.repair_session(_ctx(tmp_path), SID)
        assert report.fixed == ["quarantine-3"]
        assert [e.line_no for e in report.quarantined] == [3]
        assert all(e.archived for e in report.quarantined)
        assert _seqs_of(path) == [1, 2, 3, 4]  # 好行 1-3 + recovered 声明(seq4)
        # 隔离档可查:行号/原文/原因/归档
        entries = R.view_quarantine(SID, tmp_path)
        assert len(entries) == 1
        e = entries[0]
        assert e.line_no == 3 and e.archived is True
        assert e.reason == "parse-fail"
        assert '{"garbage": "not-an-envelope"}' in e.raw
        # 幂等:二次 repair 无动作(坏行已不在主文件)
        r2 = await R.repair_session(_ctx(tmp_path), SID)
        assert r2.fixed == [] and r2.recovered_seq is None

    async def test_interactive_keep_in_place_leaves_file(self, tmp_path):
        """交互选"保留原地"(keep_quarantine=False):不重写不删除,只读报警。"""
        path = _write(tmp_path, SID, _session_events(3), split_after=2)
        before = path.read_bytes()
        pol = R.RepairPolicy(mode="interactive", drop_tail=True,
                             keep_quarantine=False)
        kept = await R.quarantine_lines(path, [3], pol)
        assert [e.line_no for e in kept] == [3]
        assert kept[0].reason == "user-keep" and kept[0].archived is False
        assert path.read_bytes() == before                    # 一行未动(数据优先)
        assert list(tmp_path.glob(f"{SID}.quarantine-*")) == []

    async def test_interactive_end_to_end(self, tmp_path, monkeypatch):
        """交互管线:monkeypatch 输入 → T 截断 + Y 隔离(坏行隔离 + recovered)。"""
        path = _write(tmp_path, SID, _session_events(3),
                      tail=b'{"seq": 4, "ts": "2026-09-07T0')
        monkeypatch.setattr("builtins.input",
                            lambda _prompt="": iter(["t", "y"]).__next__())
        report = await R.repair_session(_ctx(tmp_path), SID, interactive=True)
        assert "tail-truncated" in report.fixed
        assert report.recovered_seq is not None
        assert detect_truncation(path) is None

    async def test_whole_partial_session_backup_kept(self, tmp_path):
        """整文件半行(created 未完成):截空 + 无事件可声明(EVT-106 语义)但备份留存。"""
        path = _write(tmp_path, SID, [],
                      tail=b'{"seq": 1, "ts": "2026-09-07T00:00:00.000000Z", "ty')
        report = await R.repair_session(_ctx(tmp_path), SID)
        assert "tail-truncated" in report.fixed
        assert report.backup_path is not None and report.backup_path.exists()
        assert report.backup_path.read_bytes() == b'{"seq": 1, "ts": "2026-09-07T00:00:00.000000Z", "ty'
        assert report.recovered_seq is None                   # 空文件未 created:声明跳过
        assert path.read_bytes() == b""                       # 截空(重建由 session 层引导)

    async def test_undeclared_hole_repair_declares_and_converges(self, tmp_path):
        """无声明 seq 空洞 → fixed seq-holes:[3] 声明 → 二次扫描合法收敛(幂等)。"""
        evs = [_env(1, "session.created", "system"), _env(2), _env(4), _env(5)]
        path = _write(tmp_path, SID, evs)
        report = await R.repair_session(_ctx(tmp_path), SID)
        assert report.fixed == ["seq-holes:[3]"]
        assert report.recovered_seq == 6                      # 声明续写在 max+1
        assert report.holes_alert == [3]
        h2 = await R.scan_session(path)                       # 声明覆盖 → 无空洞
        assert h2.holes == [] and h2.healthy is True
        r2 = await R.repair_session(_ctx(tmp_path), SID)      # 幂等:无动作
        assert r2.fixed == [] and r2.recovered_seq is None

    async def test_index_stale_triggers_views_rebuild(self, tmp_path):
        """索引落后(日志末 3 > 视图末 1)→ 派生视图整体重建 + 审计词条。"""
        _write(tmp_path, SID, _session_events(3))
        view = _ViewStub(max_seq=1)
        ctx = _ctx(tmp_path, session_query=view)
        report = await R.repair_session(ctx, SID)
        assert view.rebuilt == [SID]
        assert report.fixed == ["views-rebuilt"]
        assert report.recovered_seq == 4
        # 视图追上内容日志(排除 recovered 声明,声明不被 FTS 索引)→ 二次幂等
        assert view.max == 3, "auto_catchup 应追平到内容日志末(声明不参与对账)"
        r2 = await R.repair_session(ctx, SID)
        assert r2.fixed == [] and r2.recovered_seq is None

    async def test_ghost_row_triggers_rebuild_and_purge(self, tmp_path):
        """幽灵行(视图末 9 > 日志末 3)→ 触发重建,视图从源重置、幽灵行清除。"""
        _write(tmp_path, SID, _session_events(3))

        class _GhostView:
            """幽灵行视图桩:末 seq 超前日志;rebuild 从源重置(真实现语义)。"""
            def __init__(self) -> None:
                self.m = 9
                self.rebuilt: list = []
                self._store = None

            def max_seq(self, sid: str) -> int:
                return self.m

            def attach_source(self, sid: str, store: object) -> None:
                self._store = store

            async def rebuild(self, *, session_id=None) -> int:
                self.rebuilt.append(session_id)
                seqs = [e.seq for e in self._store.replay()
                        if e.type != "session.recovered"]
                self.m = max(seqs) if seqs else 0     # 重置到内容日志末(幽灵行剔除)
                return len(seqs)

        view = _GhostView()
        ctx = _ctx(tmp_path, session_query=view)
        report = await R.repair_session(ctx, SID)
        assert view.rebuilt == [SID]                  # 幽灵行触发整体重建
        assert report.fixed == ["views-rebuilt"]
        assert view.max_seq(SID) == 3                 # 幽灵行已清除,视图追平日志


# ===================================================================== quarantine_lines
class TestQuarantineLines:
    async def test_archive_failure_keeps_line_in_place(self, tmp_path, monkeypatch):
        """隔离档写失败 → 行保留原地 archived=False(数据优先:隔离≠丢数据)。"""
        monkeypatch.setattr(R, "now_ts", lambda: FIXED_TS)
        path = _write(tmp_path, SID, _session_events(3), split_after=2)
        before = path.read_bytes()
        (tmp_path / f"{SID}.quarantine-{FIXED_TS}.jsonl").mkdir()  # 档位被目录占
        pol = R.RepairPolicy()                                # auto:隔离
        kept = await R.quarantine_lines(path, [3], pol)
        assert [e.line_no for e in kept] == [3]
        assert kept[0].archived is False
        assert path.read_bytes() == before                    # 主文件原样(行未丢)
        (tmp_path / f"{SID}.quarantine-{FIXED_TS}.jsonl").rmdir()

    async def test_quarantine_raw_capped_8k(self, tmp_path):
        huge = "x" * 20000
        chunks = [_line(_env(1, "session.created", "system")), huge + "\n",
                  _line(_env(2))]
        path = tmp_path / f"{SID}.jsonl"
        path.write_text("".join(chunks), encoding="utf-8")
        pol = R.RepairPolicy()
        kept = await R.quarantine_lines(path, [2], pol)
        assert len(kept[0].raw) <= R._QUARANTINE_RAW_CAP
        assert _seqs_of(path) == [1, 2]                       # 重写后好行完整

    async def test_quarantine_preserves_crlf(self, tmp_path):
        """P3:隔离重写保行尾——好行 CRLF 原样保留,不被归一为 LF。"""
        path = tmp_path / f"{SID}.jsonl"
        l1 = _line(_env(1)).encode("utf-8").replace(b"\n", b"\r\n")
        bad = b'{"garbage": "not-an-envelope"}\r\n'
        l3 = _line(_env(3)).encode("utf-8").replace(b"\n", b"\r\n")
        path.write_bytes(l1 + bad + l3)
        kept = await R.quarantine_lines(path, [2], R.RepairPolicy())
        assert [e.line_no for e in kept] == [2]
        out = path.read_bytes()
        assert out.count(b"\r\n") == 2            # 两条好行行尾仍 CRLF
        assert b"garbage" not in out              # 坏行已抽离


# ===================================================================== locate_holes / declare
class TestLocateHoles:
    def test_undeclared_holes_alerted(self):
        h = R.SessionHealth(sid=SID, path=Path("x"),
                            holes=[3, 7], holes_declared=[(1, 2)])
        assert R.locate_holes(h) == [3, 7]

    def test_declared_holes_legal_no_alert(self):
        h = R.SessionHealth(sid=SID, path=Path("x"),
                            holes=[3, 7], holes_declared=[(3, 3), (7, 7)])
        assert R.locate_holes(h) == []

    def test_no_holes_empty(self):
        h = R.SessionHealth(sid=SID, path=Path("x"))
        assert R.locate_holes(h) == []


class TestDeclareRecovered:
    async def test_empty_actions_no_declare(self, tmp_path):
        """无修复动作 → 不追加(幂等;注册载荷 fixed min_length=1,偏离 3)。"""
        _write(tmp_path, SID, _session_events(2))
        seq = await R.declare_recovered(_ctx(tmp_path), SID, [], lost=[], kept=[])
        assert seq is None
        assert "session.recovered" not in open(
            tmp_path / f"{SID}.jsonl", encoding="utf-8").read()

    async def test_declare_appends_sync_event(self, tmp_path):
        """声明 = 强同步落盘:recovered 事件含 fixed/lost/backup,seq=max+1。"""
        path = _write(tmp_path, SID, _session_events(2),
                      tail=b'{"seq": 3, "ts": "2026-09-07T0')
        backup = R.backup_file(path)
        # 完整管线语义:先截半行(truncate_tail 是 repair_session 的一步),再声明
        off = detect_truncation(path)
        R.truncate_tail(path, off)
        seq = await R.declare_recovered(_ctx(tmp_path), SID,
                                        ["tail-truncated"], [3], [], backup)
        assert seq == 3                                      # 续写 max(2)+1
        lines = path.read_text(encoding="utf-8").splitlines()
        rec = Envelope.model_validate_json(lines[-1])
        assert rec.type == "session.recovered"
        assert rec.payload == {"fixed": ["tail-truncated"], "lost": [3],
                               "backup": str(backup)}

    async def test_empty_file_evt106_skip(self, tmp_path):
        """空文件未 created → append 语义 EVT-106,声明跳过返回 None(审计=备份)。"""
        (tmp_path / f"{SID}.jsonl").write_text("", encoding="utf-8")
        seq = await R.declare_recovered(_ctx(tmp_path), SID,
                                        ["tail-truncated"], [], [])
        assert seq is None


# ===================================================================== 派生视图重建
class TestRebuildViews:
    async def test_mounted_rebuild_and_reconcile(self, tmp_path):
        """FTS 挂载:重建走单会话入口 + attach_source;对账一致不抛。"""
        _write(tmp_path, SID, _session_events(3))
        view = _ViewStub(max_seq=3)
        ok = await R.rebuild_derived_views(_ctx(tmp_path, session_query=view), SID)
        assert ok is True and view.rebuilt == [SID]
        assert SID in view.sources

    async def test_unmounted_degrades_false(self, tmp_path):
        """视图未装配 → 日志降级 False(派生可弃;装配后自动生效,偏离 4)。"""
        _write(tmp_path, SID, _session_events(2))
        ok = await R.rebuild_derived_views(_ctx(tmp_path), SID)
        assert ok is False

    async def test_reconcile_mismatch_pers201(self, tmp_path):
        """重建后对账不一致(视图末 seq < 日志末 seq)→ PERS-201 不静默。"""
        _write(tmp_path, SID, _session_events(3))
        view = _ViewStub(max_seq=2, auto_catchup=False)   # 落后于日志末 3 且重建不追平
        with pytest.raises(PyHError) as e:
            await R.rebuild_derived_views(_ctx(tmp_path, session_query=view), SID)
        assert e.value.code == "PERS-201"

    async def test_owned_handle_reconcile_reads_inside_lifetime(self, tmp_path):
        """R14-1 二阶回归:自建句柄(CLI/桌面外壳 ctx 无 ``session_query``)重建后,
        对账读数必须在句柄**有效期内**取 —— 否则 ``finally`` 里 detach 关库后
        ``max_seq`` 恒返 0,而日志水位 > 0 ⇒ **假 PERS-201**(修复自身引出的缺陷)。

        真 ``SessionQueryIndex`` + 真 JSONL,走 ``owned=True`` 路径:修复后应返回
        True 且不抛;索引水位与内容日志一致。
        """
        from pyharness.core.session_query import read_max_seq
        _write(tmp_path, SID, _session_events(3))       # created + user.message×2
        db = tmp_path / "index.db"
        cfg = SimpleNamespace(storage=SimpleNamespace(sessions_dir=tmp_path,
                                                      db_path=str(db)))
        ctx = _ctx(tmp_path, settings=cfg, config=cfg)  # 外壳形态:无 session_query
        ok = await R.rebuild_derived_views(ctx, SID)
        assert ok is True, "自建句柄重建应成功(不因 detach 后读数而假报 PERS-201)"
        assert read_max_seq(db, SID) == 3, "重建后索引应追平内容日志水位"

    async def test_rotated_session_watermark_is_session_level(self, tmp_path):
        """R14-1 二阶:轮转会话的水位是**会话级**的(replay 合并读段+主文件)。

        复现路径:轮转段持全部内容(seq 1..3);主文件只剩一条被截断的半行 ⇒ repair
        截到空 ⇒ rebuild 重放"段+主文件"→ 索引水位 3。若对账按**主文件单文件**算
        内容水位 → 0 != 3 ⇒ **假 PERS-201**(repair 已成功却被判失败,违反 R14-1
        "repair 后 healthy/stale 收敛")。判据须与 ``scan_session`` 的会话级水位单源
        (CND-06/08)。
        """
        from pyharness.core.session_query import read_max_seq
        seg = tmp_path / f"{SID}.1.jsonl"                # 轮转段:真源的一部分
        seg.write_bytes("".join(_line(e) for e in _session_events(3)).encode())
        (tmp_path / f"{SID}.jsonl").write_bytes(       # 主文件:整文件半行(崩溃现场)
            b'{"seq": 4, "ts": "2026-09-07T0')
        db = tmp_path / "index.db"
        cfg = SimpleNamespace(storage=SimpleNamespace(sessions_dir=tmp_path,
                                                      db_path=str(db)))
        ctx = _ctx(tmp_path, settings=cfg, config=cfg)
        rep = await R.repair_session(ctx, SID)          # 不得假报 PERS-201
        assert "tail-truncated" in rep.fixed
        assert read_max_seq(db, SID) == 3, "索引应含轮转段内容(会话级水位 3)"

    async def test_kv_rebuild_duck_called(self, tmp_path):
        """storage.kv_rebuild 鸭子面(装配后有 KV 派生缓存 → 一并重建)。"""
        calls = []
        _write(tmp_path, SID, _session_events(2))
        storage = SimpleNamespace(sessions_dir=tmp_path,
                                  kv_rebuild=lambda sid: calls.append(sid))
        ctx = _ctx(tmp_path, storage=storage)
        ok = await R.rebuild_derived_views(ctx, SID)
        assert ok is True and calls == [SID]


# ===================================================================== 交互决策
class TestInteractiveConfirm:
    def test_keep_tail_and_keep_bad_lines(self, tmp_path, monkeypatch):
        evs = _session_events(3)
        path = _write(tmp_path, SID, evs, tail=b'{"seq": 4, "ts": "2026-09-07T0')
        health = R.SessionHealth(sid=SID, path=path, tail_truncated=True,
                                 tail_offset=10, bad_lines=[9], holes=[8])
        answers = iter(["k", "n"])
        monkeypatch.setattr("builtins.input", lambda _p="": next(answers))
        pol = R.interactive_confirm(health)
        assert pol.mode == "interactive"
        assert pol.drop_tail is False                         # K:保留退出
        assert pol.keep_quarantine is False                   # n:保留原地

    def test_defaults_when_no_damage(self, tmp_path, monkeypatch):
        health = R.SessionHealth(sid=SID, path=Path("x"))
        pol = R.interactive_confirm(health)
        assert pol.drop_tail is True and pol.keep_quarantine is True


def _raise_eof(_prompt=""):
    raise EOFError


# ===================================================================== view_quarantine
class TestViewQuarantine:
    def test_empty_when_none(self, tmp_path):
        assert R.view_quarantine(SID, tmp_path) == []

    def test_reads_entries_and_skips_corrupt_line(self, tmp_path):
        q = tmp_path / f"{SID}.quarantine-{FIXED_TS}.jsonl"
        q.write_text(json.dumps({"line_no": 3, "raw": "bad", "reason": "parse-fail",
                                 "archived": True}) + "\n"
                     + "not-json\n", encoding="utf-8")
        entries = R.view_quarantine(SID, tmp_path)
        assert len(entries) == 1                             # 损坏行跳过(隔离档非真源)
        assert entries[0].line_no == 3
        assert entries[0].raw == "bad" and entries[0].reason == "parse-fail"
        assert entries[0].archived is True


# ============ CND-06 保真:隔离重写后**未被隔离的字节逐字节不变**(2026-09-21)
async def test_quarantine_rewrite_preserves_kept_bytes_exactly(tmp_path):
    """隔离重写 = 只删坏行:**其余字节原样往返**(含 CRLF、末行无换行、CRLF 混合)。

    代码注释声明"好行原样保留(含原行尾,字节往返)",此前**无测试锁定** ⇒ 未来
    若把读改写改成文本模式(universal newlines)会把 CRLF 静默归一为 LF(丢 \r),
    属 CND-06"未改动数据字节级保真"违约。本用例以**字节相等**断言钉死。
    """
    path = tmp_path / f"{SID}.jsonl"
    good1 = _env(1, "session.created", "system").model_dump_json().encode() + b"\r\n"
    bad = b'{"seq": 2, "broken": \n'                     # 半行 JSON(坏行)
    good2 = _env(3, "user.message", "user").model_dump_json().encode() + b"\n"
    good3 = _env(4, "agent.message").model_dump_json().encode()   # 末行**无换行**
    path.write_bytes(good1 + bad + good2 + good3)

    pol = R.RepairPolicy(mode="auto", keep_quarantine=True)
    entries = await R.quarantine_lines(path, [2], pol)

    assert [e.line_no for e in entries] == [2] and entries[0].archived is True
    assert path.read_bytes() == good1 + good2 + good3, \
        "未隔离字节必须逐字节不变(CRLF/无尾换行/顺序全保真)"
    # 隔离档含坏行原文(≤8KB 截断),行号可查
    view = R.view_quarantine(SID, tmp_path)
    assert any(e.line_no == 2 for e in view)


async def test_quarantine_line_judgement_single_source(tmp_path):
    """**判据单源**:`_iter_text` 判定的坏行集合 == 重写时被摘除的行集合。

    两处若各用一套行切分(文本模式 vs 字节模式)会出现"判坏第 N 行、删掉另一行";
    现共用 `enumerate(fh, 1)` 同一基座,本用例以"逐行重放后字节全等"验证。
    """
    path = tmp_path / f"{SID}.jsonl"
    lines = []
    for seq in range(1, 7):
        if seq in (2, 5):
            lines.append(b'{"seq": %d, "oops"\n' % seq)     # 坏行
        else:
            lines.append(_env(seq, "user.message", "user").model_dump_json()
                         .encode() + b"\n")
    original = b"".join(lines)
    path.write_bytes(original)

    bad = [no for no, text in R._iter_text(path) if R._parse_or_none(text) is None]
    assert bad == [2, 5], bad
    pol = R.RepairPolicy(mode="auto", keep_quarantine=True)
    await R.quarantine_lines(path, bad, pol)

    expect = lines[0] + lines[2] + lines[3] + lines[5]
    assert path.read_bytes() == expect, "删掉的行必须与判坏集合完全一致"


# ============ R11-2:索引落后对账**真正可达**(db_path 兜底 + ctx-less 入口)
def _mk_index_db(path: Path, rows: list[tuple[str, int]]) -> None:
    """按**契约表结构**直建最小索引库(fts_rows(session_id,seq) 主键)——只测"读数口"。"""
    import sqlite3
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE IF NOT EXISTS fts_rows("
                 "session_id TEXT NOT NULL, seq INTEGER NOT NULL, "
                 "PRIMARY KEY (session_id, seq)) WITHOUT ROWID")
    conn.executemany("INSERT OR REPLACE INTO fts_rows VALUES(?,?)", rows)
    conn.commit()
    conn.close()


def test_read_max_seq_handles_missing_db_and_rows(tmp_path):
    """`read_max_seq`:库不存在 → 0(未索引);有行 → 该会话最大 seq;无表 → 0。"""
    from pyharness.core.session_query import read_max_seq

    missing = tmp_path / "nope.db"
    assert read_max_seq(missing, SID) == 0

    db = tmp_path / "index.db"
    _mk_index_db(db, [(SID, 1), (SID, 2), ("s-other-000002", 9)])
    assert read_max_seq(db, SID) == 2, "只数本会话(跨会话隔离)"
    assert read_max_seq(db, "s-other-000002") == 9
    assert read_max_seq(db, "s-none-000003") == 0   # 无行

    empty = tmp_path / "empty.db"                   # 库在但无表 ⇒ 未索引
    import sqlite3
    sqlite3.connect(str(empty)).close()
    assert read_max_seq(empty, SID) == 0


def test_fts_provider_falls_back_to_cfg_db_path(tmp_path):
    """**修复前失败**:provider 只认 ctx.session_query/fts_last_seq ⇒ 从未启用。

    现补第三兜底:由 ``cfg.storage.db_path`` 构造只读读数口 —— repair 只需"库在哪"。
    """
    from types import SimpleNamespace as _SN

    cfg = _SN(storage=_SN(db_path=str(tmp_path / "index.db")))
    ctx = _SN(settings=cfg)
    fn = R._fts_last_seq_provider(ctx)
    assert callable(fn), "有 db_path 时必须能给出读数口(否则对账永不启用)"
    assert fn(SID) == 0                              # 库未建:未索引
    # 显式注入优先(不因兜底而改变既有语义)
    explicit = R._fts_last_seq_provider(_SN(fts_last_seq=lambda sid: 7,
                                            settings=cfg))
    assert explicit(SID) == 7
    # 什么都没有 → None(保守:不算落后)
    assert R._fts_last_seq_provider(_SN()) is None


async def test_auto_scan_reconciles_index_when_given_db_path(tmp_path):
    """**ctx-less 入口**(CLI 启动自检)**修复前失败**:`auto_scan` 不收 db_path ⇒ 对账无从启用。"""
    evs = _session_events(3)
    _write(tmp_path, SID, evs)                       # 日志到 seq=3(内容事件)
    db = tmp_path / "index.db"
    _mk_index_db(db, [(SID, 1)])                     # 索引只到 1 ⇒ 落后

    hits = await R.auto_scan(tmp_path, db_path=str(db))
    stale = [h for h in hits if h.sid == SID]
    assert stale and stale[0].index_stale is True, "应检出索引落后"
    assert stale[0].healthy is False

    # 索引追平 → 不报
    _mk_index_db(db, [(SID, 3)])
    hits2 = await R.auto_scan(tmp_path, db_path=str(db))
    assert all(not h.index_stale for h in hits2 if h.sid == SID)


async def test_auto_scan_without_db_path_keeps_legacy_semantics(tmp_path):
    """负空间:不给 db_path ⇒ 行为与修复前一致(不判落后),不误报。"""
    _write(tmp_path, SID, _session_events(3))
    hits = await R.auto_scan(tmp_path)
    assert all(not h.index_stale for h in hits if h.sid == SID)


# ============ R13-1:失败恢复闭环 —— 幽灵索引行 → 检出 → 重建 → 干净
async def test_ghost_index_row_detected_and_rebuilt_end_to_end(tmp_path):
    """崩溃后"索引超前日志"(dispatch 先于 flush)必须**可检出且可自愈**。

    链路(全真):真 JSONL(repair 测试既有 `_write` 直落盘)+ 真 `SessionQueryIndex`
    建索引 → 注入"索引超前"行(等价崩溃现场)→ 经 **R11-2 的 ctx-less 读数口**
    `auto_scan(db_path=…)` 检出 `index_stale` → `rebuild`(派生服从真源)清除 → 复扫干净。
    """
    from types import SimpleNamespace as _SN

    from pyharness.core.session import SessionLog
    from pyharness.core.session_query import SessionQueryIndex, read_max_seq

    evs = _session_events(3)                       # created + user.message ×2
    path = _write(tmp_path, SID, evs)              # 真文件(磁盘真源)
    db = tmp_path / "index.db"
    log_ = SessionLog(sid=SID)                     # rebuild 的回放源(内存同源事件)
    for e in evs:
        await log_.append(e.type, e.payload, actor=e.actor)

    ix = SessionQueryIndex(db_path=db, sources={SID: log_}, batch_ms=60_000)
    await ix.enter(_SN())
    for e in evs:
        await ix.on_event(e.type, e)
    await ix.flush()
    assert read_max_seq(db, SID) == 3, "前置:索引追平日志内容水位"

    # ---- 崩溃现场:索引里多出一行(seq=9),日志没有(dispatch 先于 flush 后崩溃)
    _mk_index_db(db, [(SID, 9)])
    cfg = _SN(settings=_SN(storage=_SN(db_path=str(db))))
    h = await R.scan_session(path, fts_last_seq=R._fts_last_seq_provider(cfg))
    assert h.index_stale is True and h.healthy is False,         "索引超前(幽灵行)必须被检出(R11-2 的读数口使其可达)"

    # ---- 恢复:重建派生索引(服从真源)⇒ 复扫干净
    await ix.rebuild(session_id=SID)
    assert read_max_seq(db, SID) == 3, "重建后索引末 seq 应回到日志水位"
    h2 = await R.scan_session(path, fts_last_seq=R._fts_last_seq_provider(cfg))
    assert h2.index_stale is False and h2.healthy is True, "自愈后应健康"
    await ix.detach(None)


# ============ R13-4:中部坏行 三面一体(不自动删 / 追加不破结构 / repair 恰隔离)
async def test_middle_bad_line_append_then_repair_quarantines_only_bad_line(tmp_path):
    """① 坏行**不被自动删** ② 运行期追加落在其后、行结构完好
    ③ repair 恰隔离坏行且**其余行逐字节不变** ④ 可解析事件集**不丢** ⑤ 对账水位不受影响。
    """
    from pyharness.bus import EventBus
    from pyharness.desktop.sessions import DesktopSessionManager

    evs = _session_events(3)                       # seq 1/2/3
    path = _write(tmp_path, SID, evs)
    raw = path.read_bytes()
    garbage = b'{"broken": nope-not-json}\n'
    lines = raw.split(b"\n")
    # 把坏行插到**中部**(最后一条完整行之前)
    path.write_bytes(b"\n".join(lines[:-2]) + b"\n" + garbage + lines[-2] + b"\n")
    assert _seqs_of(path) == [1, 2, 3], "前置:坏行是**新增**第 3 物理行,不顶掉原行"

    # ---- ② 运行期追加(生产形态:经管理器装配落盘订阅):坏行由 replay 记跳
    mgr = DesktopSessionManager(dir=tmp_path, bus=EventBus())
    log_ = await mgr.open_session(SID)
    env = await log_.append("user.message", {"content": "after-bad"}, actor="user",
                            sync=True)
    assert env.seq == 4, "seq 续在可解析水位(max=3)之后"
    await mgr.shutdown_all()

    after = path.read_bytes()
    # ① 坏行原样还在(不自动删 ⇒ 数据优先)
    assert garbage in after, "坏行被自动删除:违反'坏行不自动删'约定"
    # ② 行结构完好:除坏行外每行都可解析,且新事件是**独立完整**的一行
    parsed = []
    for ln in after.split(b"\n"):
        if not ln:
            continue
        try:
            parsed.append(Envelope.model_validate_json(ln))
        except Exception:                         # noqa: BLE001 坏行
            parsed.append(None)
    good = [e for e in parsed if e is not None]
    assert sorted(e.seq for e in good) == [1, 2, 3, 4], f"行结构被破坏:{parsed}"
    assert sum(1 for e in parsed if e is None) == 1, "坏行不得影响其他行的可解析性"

    # ---- ③ repair:恰隔离坏行;其余行**逐字节**不变
    before_bytes_lines = after.split(b"\n")
    report = await R.repair_session(_ctx(tmp_path), SID)
    assert report.quarantined, "中部坏行应被隔离"
    assert [e.line_no for e in report.quarantined] == [3], report.quarantined
    assert garbage.rstrip(b"\n") in report.quarantined[0].raw.encode("utf-8"), \
        "隔离档须留坏行原文(证据)"
    fixed_lines = path.read_bytes().split(b"\n")
    # ④ 可解析事件集不丢(repair 只动坏行)
    posted = []
    for ln in fixed_lines:
        if not ln:
            continue
        try:
            posted.append(Envelope.model_validate_json(ln))
        except Exception:                         # noqa: BLE001
            posted.append(None)
    good_post = [e for e in posted if e is not None]
    assert sorted(e.seq for e in good_post) == [1, 2, 3, 4, 5], good_post
    assert good_post[-1].type == "session.recovered", "repair 应追加 recovered 声明"
    # 序号连续无洞(可续跑;坏行不占 seq)
    assert _seqs_of(path) == [1, 2, 3, 4, 5]
    # 其余行字节保真:去掉坏行后,与修复前"去掉坏行"的字节序列一致
    keep_before = [ln for ln in before_bytes_lines if ln and ln != garbage.rstrip(b"\n")]
    keep_after = [ln for ln in fixed_lines if ln and ln != garbage.rstrip(b"\n")]
    assert keep_after[: len(keep_before)] == keep_before,         "未改动的行必须字节保真(repair 只在尾部追加 recovered 声明)"
    # ⑤ 对账水位:坏行不影响内容水位
    assert R._log_last_seq(path) >= 5


# ============ R13-5:轮转段(真源的一部分)损坏必须**可见且可修**
async def _rotated_damaged(tmp_path):
    """构造:一次轮转产生 .1.jsonl,并把其中的**整行**换成垃圾。返回 (sid, seg)。"""
    from pyharness.bus import EventBus
    from pyharness.desktop.sessions import DesktopSessionManager

    mgr = DesktopSessionManager(dir=tmp_path, bus=EventBus())
    sid = await mgr.create()
    log_ = await mgr.open_session(sid)
    await log_.append("user.message", {"content": "a"}, actor="user", sync=True)
    mgr._stores[sid].rotate_bytes = 1                # 强制下一次写触发轮转
    await log_.append("user.message", {"content": "b"}, actor="user", sync=True)
    await mgr.shutdown_all()
    seg = sorted(tmp_path.glob(f"{sid}.*.jsonl"))[0]
    assert seg.name.endswith(".1.jsonl"), seg.name
    lines = seg.read_bytes().split(b"\n")
    seg.write_bytes(b"\n".join([lines[0], b'{"corrupted": true}', lines[2], b'']))
    return sid, seg


def _repair_ctx(tmp_path):
    from types import SimpleNamespace as _SN
    return _SN(storage=_SN(sessions_dir=str(tmp_path)))


async def test_rotated_segment_damage_is_repaired_and_reported(tmp_path):
    """**修复前失败(静默丢事件)**:轮转段内整行损坏此前**零告警零修复**
    (replay 少一条、`repair`/`auto_scan` 都不报)。

    修后:段级**备份** → 坏行**隔离**(不删,留证)→ 报告出现 `seg1-quarantine-N`
    → `session.recovered` 落盘声明 → 其余段行**字节保真**;二次 repair 幂等。
    """
    sid, seg = await _rotated_damaged(tmp_path)
    keep_before = [ln for ln in seg.read_bytes().split(b"\n") if ln and b"corrupt" not in ln]

    report = await R.repair_session(_repair_ctx(tmp_path), sid)
    assert "seg1-quarantine-2" in report.fixed, report.fixed
    assert [(e.line_no, e.archived) for e in report.quarantined] == [(2, True)]
    # 段内坏行已移出主序列(不删字节:隔离档留原文)
    assert b'{"corrupted": true}' not in seg.read_bytes()
    q = sorted(tmp_path.glob(f"{sid}.1.quarantine-*"))
    assert q and b"corrupted" in q[0].read_bytes(), "隔离档须留坏行原文(证据)"
    # 段级**备份**(修复前强制)
    assert sorted(tmp_path.glob(f"{sid}.1.corrupt-*")), "轮转段修复同样必须先备份"
    # 其余段行字节保真
    keep_after = [ln for ln in seg.read_bytes().split(b"\n") if ln]
    assert keep_after == keep_before

    # 主文件侧:recovered 声明已落(丢失事实**显式**记录,而非静默)
    from pyharness.events import Envelope as _Env
    main_lines = (tmp_path / f"{sid}.jsonl").read_text(encoding="utf-8").splitlines()
    rec = _Env.model_validate_json(main_lines[-1])
    assert rec.type == "session.recovered"
    assert any("seg1-quarantine-2" == f for f in rec.payload["fixed"])

    # 幂等:二次 repair 无动作
    again = await R.repair_session(_repair_ctx(tmp_path), sid)
    assert again.fixed == [] and again.quarantined == []


async def test_auto_scan_discovers_damage_via_segment_aware_holes(tmp_path):
    """**R13-6**:空洞判定跨段(会话级)⇒ 段内**真实丢失**能被启动自检发现。

    两个方向都钉住:
      ① 干净的轮转会话**不再**被误判(此前主文件从 seq N+1 起 ⇒ 假空洞
         `1..N` ⇒ 每次 repair 都报假空洞并追加一条 session.recovered);
      ② 段内**整行损坏导致真实缺 seq** ⇒ 自检报 `holes=[该 seq]` + 不健康。
    """
    from pyharness.bus import EventBus
    from pyharness.desktop.sessions import DesktopSessionManager

    # ① 干净轮转会话:healthy(修复前为假空洞 ⇒ unhealthy)
    mgr = DesktopSessionManager(dir=tmp_path, bus=EventBus())
    sid = await mgr.create()
    log_ = await mgr.open_session(sid)
    await log_.append("user.message", {"content": "a"}, actor="user", sync=True)
    mgr._stores[sid].rotate_bytes = 1
    await log_.append("user.message", {"content": "b"}, actor="user", sync=True)
    await mgr.shutdown_all()
    hits = await R.auto_scan(tmp_path)
    assert hits == [], f"干净的轮转会话不得被判不健康:{hits}"

    # ② 段内真实损坏(整行替换 ⇒ 缺 seq)⇒ 自检必须报出来
    seg = sorted(tmp_path.glob(f"{sid}.*.jsonl"))[0]
    lines = seg.read_bytes().split(bytes([10]))
    seg.write_bytes(bytes([10]).join(
        [lines[0], b'{"corrupted": true}', lines[2], b'']))
    hits2 = await R.auto_scan(tmp_path)
    assert hits2 and hits2[0].sid == sid and hits2[0].healthy is False, hits2
    assert hits2[0].holes, f"应报出缺失的 seq:{hits2}"


# ============ R14-1:repair × 派生视图重建 × replay/index/watermark 一致性
def _idx_ctx(tmp_path: Path, db: Path):
    """外壳形态 ctx:真 sessions_dir + 真 ``db_path`` ⇒ 走 ``owned`` 句柄的真实重建路径。

    刻意**不给** ``session_query`` —— 这正是 CLI/桌面传进来的形态(修前 rebuild 不可达),
    也是 R14-1 两处二阶缺陷(读数在 detach 后 / 水位只算主文件)唯一能被触发的形态。
    """
    cfg = SimpleNamespace(storage=SimpleNamespace(sessions_dir=tmp_path,
                                                  db_path=str(db)))
    return SimpleNamespace(storage=SimpleNamespace(sessions_dir=Path(tmp_path)),
                           session=None, session_query=None,
                           settings=cfg, config=cfg)


def _fts_seqs(db: Path, sid: str) -> set:
    """索引里该会话已有的行键 seq 集合(fts_rows 契约表)。"""
    import sqlite3
    if not db.exists():
        return set()
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute("SELECT seq FROM fts_rows WHERE session_id=?",
                            (sid,)).fetchall()
    except sqlite3.Error:
        return set()
    finally:
        conn.close()
    return {int(r[0]) for r in rows}


def _state_of(tmp_path: Path, sid: str, db: Path) -> dict:
    """不变量快照:声明条数 / 隔离档数 / 索引行 / 水位(二次修复不得改动)。"""
    from pyharness.core.session_query import read_max_seq
    main = tmp_path / f"{sid}.jsonl"
    body = main.read_text(encoding="utf-8") if main.exists() else ""
    segs = sorted(tmp_path.glob(f"{sid}.[0-9]*.jsonl"))
    seg_body = "".join(s.read_text(encoding="utf-8") for s in segs)
    return {
        "recovered": (body + seg_body).count("session.recovered"),
        "quarantine": len(list(tmp_path.glob(f"{sid}*.quarantine-*.jsonl"))),
        "fts_rows": _fts_seqs(db, sid),
        "watermark": read_max_seq(db, sid),
    }


async def test_case_a_segment_bad_line_never_enters_index(tmp_path):
    """**R14-1 Case A**:被隔离的段级坏行 → repair → rebuild → **不进入 FTS** →
    replay/index 一致(复扫健康);且二次 repair 幂等(无声明/隔离/索引/水位变更)。
    """
    sid, seg = await _rotated_damaged(tmp_path)     # 段:created(1) / 坏行(原 seq2) / user.message(3)
    db = tmp_path / "index.db"
    ctx = _idx_ctx(tmp_path, db)

    rep = await R.repair_session(ctx, sid)
    assert "seg1-quarantine-2" in rep.fixed, rep.fixed
    assert _fts_seqs(db, sid) == {3}, \
        "索引只能含幸存的**可索引**事件(user.message seq=3);坏行不得进入 FTS"
    h = await R.scan_session(tmp_path / f"{sid}.jsonl",
                             fts_last_seq=R._fts_last_seq_provider(ctx))
    assert h.healthy is True and h.index_stale is False, \
        f"repair 后应收敛为健康:stale={h.index_stale} holes={h.holes}"

    before = _state_of(tmp_path, sid, db)
    again = await R.repair_session(ctx, sid)         # 二次 repair
    assert again.fixed == [] and again.quarantined == []
    assert _state_of(tmp_path, sid, db) == before, \
        "二次 repair 不得重复产生 recovered/隔离/索引行/水位变更"


async def test_case_b_truncated_tail_no_ghost_watermark_consistent(tmp_path):
    """**R14-1 Case B**:被截断的尾部(轮转会话,主文件整文件半行)→ repair →
    rebuild → **不产生幽灵索引** → 会话级水位一致(段内容在,主文件空)。
    """
    sid = "s-repair01"
    (tmp_path / f"{sid}.1.jsonl").write_bytes(      # 段:真源的一部分
        "".join(_line(e) for e in _session_events(3)).encode())
    (tmp_path / f"{sid}.jsonl").write_bytes(b'{"seq": 4, "ts": "2026-09-07T0')
    db = tmp_path / "index.db"
    ctx = _idx_ctx(tmp_path, db)

    await R.repair_session(ctx, sid)
    assert _fts_seqs(db, sid) == {2, 3}, \
        "索引 = 段内可索引事件(session.created 不索引);无幽灵行"
    h = await R.scan_session(tmp_path / f"{sid}.jsonl",
                             fts_last_seq=R._fts_last_seq_provider(ctx))
    assert h.healthy is True and h.index_stale is False, \
        f"水位应一致(会话级):stale={h.index_stale}"

    before = _state_of(tmp_path, sid, db)
    await R.repair_session(ctx, sid)
    assert _state_of(tmp_path, sid, db) == before


async def test_case_c_ghost_row_purged_and_index_matches_replay(tmp_path):
    """**R14-1 Case C**:幽灵行(索引里有、真日志里没有)→ repair/rebuild →
    索引**不保留**不存在于真日志的记录 → replay/index 一致。
    """
    sid = "s-repair01"
    _write(tmp_path, sid, _session_events(3))       # 真日志:可索引到 seq 3
    db = tmp_path / "index.db"
    _mk_index_db(db, [(sid, 2), (sid, 3), (sid, 99)])   # 99 = 幽灵行(日志无此 seq)
    ctx = _idx_ctx(tmp_path, db)

    h0 = await R.scan_session(tmp_path / f"{sid}.jsonl",
                              fts_last_seq=R._fts_last_seq_provider(ctx))
    assert h0.index_stale is True, "前置:幽灵行必须被判 stale"

    await R.repair_session(ctx, sid)
    assert _fts_seqs(db, sid) == {2, 3}, "幽灵行 99 必须被清除,且不多不少"
    h1 = await R.scan_session(tmp_path / f"{sid}.jsonl",
                              fts_last_seq=R._fts_last_seq_provider(ctx))
    assert h1.healthy is True and h1.index_stale is False, \
        f"重建后 replay/index 必须一致:stale={h1.index_stale}"

    before = _state_of(tmp_path, sid, db)
    await R.repair_session(ctx, sid)
    assert _state_of(tmp_path, sid, db) == before


async def test_owned_handle_is_released_no_leak_no_double_close(tmp_path):
    """**R14-1 二阶(句柄生命周期)**:自建句柄必须在 ``finally`` 里如实摘除关库——
    既不泄漏(库文件仍被占用)也不 double-close。

    Windows 上未关闭的 sqlite 连接会**占住库文件**(unlink 报 PermissionError),
    故"重建后能删除 index.db"是确定性的泄漏探针;重复调用亦不得累积句柄。
    """
    sid = "s-repair01"
    _write(tmp_path, sid, _session_events(3))
    db = tmp_path / "index.db"
    ctx = _idx_ctx(tmp_path, db)

    assert await R.rebuild_derived_views(ctx, sid) is True
    assert await R.rebuild_derived_views(ctx, sid) is True     # 二次:新句柄,旧句柄已摘
    try:
        db.unlink()                                            # 泄漏则 PermissionError
    except PermissionError as e:                               # pragma: no cover
        pytest.fail(f"自建 FTS 句柄未释放(库文件仍被占用):{e}")


# ============ R14-9:声明的空洞在回放里不得被重报(判据唯一 + 判定时机)
async def test_repaired_seq_hole_is_not_rewarned_on_open(tmp_path):
    """**R14-9 回归**:repair 已声明的空洞,在随后回放里**不得**再报"未声明空洞"。

    根因两层:① ``SessionLog`` 只把 ``context.compacted.ranges`` 当合法声明,**不认**
    ``session.recovered``(``lost`` / ``seq-holes:[…]``);② 空洞**边读边判**,而
    ``session.recovered`` 由 repair **追加在流尾** ⇒ 判定时声明尚未吸收。
    实测:一次合法修复被 ``open_store`` + ``open_session`` 各报一次"疑丢事件"(假 F031)。
    """
    from pyharness.core.session import open_session
    from pyharness.persistence import open_store

    # seq 3 的**真实事件**被替换为垃圾 ⇒ 修复隔离后形成真实的 seq 空洞 3
    body = (_line(_env(1, "session.created", "system",
                       {"title": "", "model": "m"}))
            + _line(_env(2))
            + '{"garbage": true}\n'
            + _line(_env(4)))
    (tmp_path / f"{SID}.jsonl").write_text(body, encoding="utf-8")

    ctx = _ctx(tmp_path)
    rep = await R.repair_session(ctx, SID)
    assert "seq-holes:[3]" in rep.fixed, rep.fixed
    assert rep.holes_alert == [3]

    log_ = await open_session(SID, open_store(SID, dir=tmp_path))
    stats = log_.stats()
    assert stats["holes_declared"] == [[3, 3]], "声明区间必须被吸收(含 recovered)"
    assert stats["holes_warned"] == [], \
        "已声明的修复空洞不得再报为未声明空洞(假 F031 线索 + 日志噪声)"
    # 负空间:真未声明的空洞仍必须报出来(不是把告警整个关掉)
    assert R._declared_ranges(_env(2)) == [] and R._declared_ranges(
        Envelope(seq=2, ts=TS, type="session.recovered", session_id=SID,
                 actor="system", payload={"lost": [7]})) == [(7, 7)]
