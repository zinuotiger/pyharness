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
from pyharness.persistence import detect_truncation, open_store

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
        path = _write(tmp_path, SID, _session_events(3))
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
