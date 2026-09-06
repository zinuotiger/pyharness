"""persistence 模块单测 — 契约:specs/persistence.py.md + EVENT-SCHEMA.md §5

覆盖面:GWT-P8-01 强弱同步分级(append 后文件内容/攒批/up_to_seq 部分刷)、
重启可读(open_store 重放一致)、原子写(repair 截断 tmp+rename 后字节级完整)、
GWT-P8-02 中部坏行隔离(记跳不中断、quarantine_info 可查、repair 后隔离区保留)、
GWT-P8-03 崩溃恢复(备份→截断→session.recovered,重复 repair 幂等)、
GWT-P8-04 写失败暂停(OSError×3 → PERS-202 + 会话暂停,重试队列不丢事件)、
seq 空洞检测(未声明标记/compacted 声明合法)、轮转序号合并、Windows 路径与
\\n 行尾。

铁律:全部用 pytest tmp_path fixture,绝不写真实 ~/.pyharness。
"""
import re
from pathlib import Path

import pytest

from pyharness.errors import PyHError
from pyharness.events import Envelope, SYNC_TYPES
from pyharness.persistence import (RepairReport, SessionStore,
                                   default_sessions_dir, detect_truncation,
                                   open_store)

SID = "s-abc12345"
TS = "2026-09-06T07:00:00.000000Z"
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T.*Z$")


def _env(seq, type_="agent.message", actor="agent", payload=None, sid=SID,
         ts=TS) -> Envelope:
    """直接构造合法信封(payload 为信封层 dict,无需二次模型校验)。"""
    if payload is None:
        payload = {"content": "msg"} if type_ == "agent.message" else {}
    return Envelope(seq=seq, ts=ts, type=type_, session_id=sid, actor=actor,
                    payload=payload)


def _line(env: Envelope) -> str:
    """信封 → JSONL 物理单行(与 append 同规则)。"""
    return env.model_dump_json() + "\n"


def _store(tmp_path, sid=SID, sub=None):
    """open_store 便捷封装:目录 tmp_path(/sub),返回 (store, dir)。"""
    d = tmp_path / sub if sub else tmp_path
    return open_store(sid, dir=d), d


class _Recorder:
    """on_system_event 注入回调记录器(async cb(type_, payload))。"""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def cb(self, type_: str, payload: dict) -> None:
        self.calls.append((type_, payload))


# =====================================================================
# 打开/目录/路径
# =====================================================================
def test_open_store_creates_dir_and_main_file(tmp_path):
    """open_store:目录 mkdir + 主文件 {sid}.jsonl 就位,内容为空。"""
    d = tmp_path / "sessions" / "nested"
    store, _ = _store(tmp_path, sub="sessions/nested")
    assert d.is_dir()
    assert store.path == d / f"{SID}.jsonl"
    assert store.path.exists()
    assert store.path.read_bytes() == b""
    assert store.session_id == SID
    store.close()


def test_default_sessions_dir_anchor(tmp_path, monkeypatch):
    """PARAMETER-ANCHOR:~/.pyharness/sessions/*.jsonl(不创建,仅路径解析)。"""
    monkeypatch.delenv("PH_STORAGE_SESSIONS_DIR", raising=False)
    assert default_sessions_dir() == Path("~/.pyharness/sessions").expanduser()
    # L3 env 覆盖可配(PH_STORAGE_SESSIONS_DIR)
    monkeypatch.setenv("PH_STORAGE_SESSIONS_DIR", str(tmp_path / "s"))
    assert default_sessions_dir() == (tmp_path / "s").expanduser()


def test_open_store_warns_tail_half_line_but_does_not_fix(tmp_path, caplog):
    """open_store:检测到尾部半行只告警(PERS-201 域),绝不自动截断(修复前备份)。"""
    path = tmp_path / f"{SID}.jsonl"
    path.write_bytes(_line(_env(1)).encode("utf-8") + b'{"seq":99')
    with caplog.at_level("WARNING"):
        store, _ = _store(tmp_path)
    assert any("PERS-201" in r.message for r in caplog.records)
    assert path.read_bytes().endswith(b'{"seq":99')      # 原样未动
    assert detect_truncation(path) == len(_line(_env(1)).encode("utf-8"))
    store.close()


# =====================================================================
# 双速 flush:append 后文件内容(GWT-P8-01)
# =====================================================================
async def test_append_sync_immediate_content(tmp_path):
    """强同步:append(sync=True) 返回即落盘,文件恰为 单行信封+\\n(无 \\r\\n)。"""
    store, _ = _store(tmp_path)
    env = _env(1)
    await store.append(env, sync=True)
    assert store.path.read_bytes() == (env.model_dump_json() + "\n").encode("utf-8")
    assert b"\r\n" not in store.path.read_bytes()       # JSONL 统一 \n 行尾
    store.close()


async def test_append_batch_pending_then_flush(tmp_path):
    """攒批:普通事件不即落盘;flush() 全量落;满 flush_batch 自动整批刷。"""
    store, _ = _store(tmp_path)
    await store.append(_env(1))
    assert store.path.read_bytes() == b""               # 未 flush:文件仍空
    await store.flush()
    assert store.path.read_bytes() == _line(_env(1)).encode("utf-8")
    # 满 flush_batch 自动刷(不显式 flush):攒满 3 条即整批落盘
    store.flush_batch = 3
    await store.append(_env(2))
    await store.append(_env(3))
    assert store.path.read_bytes() == _line(_env(1)).encode("utf-8")   # 未满:仍积压
    await store.append(_env(4))                          # 攒满 3 条 → 自动批量落盘
    text = store.path.read_text(encoding="utf-8")
    assert text == "".join(_line(_env(i)) for i in (1, 2, 3, 4))
    store.close()


async def test_flush_up_to_seq_partial(tmp_path):
    """flush(up_to_seq):只刷 ≤ 该 seq 的行,之后的保留积压。"""
    store, _ = _store(tmp_path)
    for i in (1, 2, 3):
        await store.append(_env(i))
    await store.flush(up_to_seq=2)
    text = store.path.read_text(encoding="utf-8")
    assert text == _line(_env(1)) + _line(_env(2))      # 前两条已落
    assert store._pending and store._pending[0][0] == 3  # seq3 仍在攒批
    await store.flush()                                  # 全量收尾
    assert store.path.read_text(encoding="utf-8") == (
        _line(_env(1)) + _line(_env(2)) + _line(_env(3)))
    store.close()


# =====================================================================
# 重启可读(open_store 重放一致)
# =====================================================================
async def test_reopen_replay_roundtrip(tmp_path):
    """模拟重启:落盘(强同步+攒批 flush)→ 关闭 → 新 store 重放,事件一致。"""
    store, d = _store(tmp_path)
    events = [_env(1, "session.created", "system", {"title": "", "model": "m"}),
              _env(2, "user.message", "user", {"content": "你好"}),
              _env(3, "agent.message", "agent", {"content": "收到"})]
    await store.append(events[0], sync=True)             # 强同步
    await store.append(events[1])                        # 攒批 + flush
    await store.flush()
    await store.append(events[2], sync=True)
    store.close()                                        # 模拟进程退出
    store2, _ = _store(tmp_path)                         # 重启
    got = list(store2.replay())
    assert [(e.seq, e.type, e.actor) for e in got] == [
        (1, "session.created", "system"),
        (2, "user.message", "user"),
        (3, "agent.message", "agent")]
    assert got[1].payload["content"] == "你好"
    assert store2.quarantine_info()["count"] == 0        # 干净重启无隔离
    store2.close()


async def test_replay_skips_blank_lines(tmp_path):
    """空行跳过(轮转残留容忍):连续空行不隔离不产出。"""
    p = tmp_path / f"{SID}.jsonl"
    p.write_text("\n\n" + _line(_env(1)) + "\n\n" + _line(_env(2)) + "\n\n",
                 encoding="utf-8")
    store, _ = _store(tmp_path)
    assert [e.seq for e in store.replay()] == [1, 2]
    assert store.quarantine_info()["count"] == 0
    store.close()


# =====================================================================
# 原子写(repair 截断:tmp+fsync+rename,字节级完整,无半行窗口)
# =====================================================================
async def test_repair_tail_half_line_and_idempotent(tmp_path):
    """GWT-P8-03:尾部半行 → 备份 + 原子截断 + recovered;重复 repair 幂等。"""
    store, d = _store(tmp_path)
    await store.append(_env(1), sync=True)
    await store.append(_env(2), sync=True)
    good_bytes = store.path.read_bytes()
    # 模拟崩溃:半行残留(无 \n 结尾)
    with open(store.path, "ab") as f:
        f.write(b'{"seq":99,"ts":"2026-09-06T07:00:00Z",')
    store.close()
    assert detect_truncation(store.path) == len(good_bytes)

    rec = _Recorder()
    store2, _ = _store(tmp_path)
    store2.on_system_event = rec.cb
    report = await store2.repair()
    # ① 备份先于修复;② 半行截断;③ recovered 声明
    assert report.fixed == ["tail-truncated"]
    assert report.quarantined == []
    assert report.backup_path is not None and report.backup_path.exists()
    assert report.backup_path.name.startswith(f"{SID}.jsonl.bak-")
    assert store2.path.read_bytes() == good_bytes       # 原子重写:与完整行前缀逐字节一致
    assert store2.path.read_bytes().endswith(b"\n")
    (t, payload), = rec.calls                            # 恰一次 recovered
    assert t == "session.recovered"
    assert payload["fixed"] == ["tail-truncated"]
    assert payload["backup"] == str(report.backup_path)
    # 重启续写:截断点后从最后完整点接续
    await store2.append(_env(3), sync=True)
    assert [e.seq for e in store2.replay()] == [1, 2, 3]
    store2.close()

    # 幂等:再次 repair 无修复动作、不重复 recovered、文件字节不变
    rec2 = _Recorder()
    store3, _ = _store(tmp_path)
    store3.on_system_event = rec2.cb
    before = store3.path.read_bytes()
    report2 = await store3.repair()
    assert report2.fixed == [] and report2.quarantined == []
    assert rec2.calls == []                              # 无修复动作 → 不产 recovered
    assert store3.path.read_bytes() == before
    assert [e.seq for e in store3.replay()] == [1, 2, 3]
    store3.close()


async def test_rewrite_atomic_no_tmp_leftover(tmp_path):
    """原子重写:repair 截断后目录无 .tmp-* 残留(临时文件清理)。"""
    store, d = _store(tmp_path)
    await store.append(_env(1), sync=True)
    with open(store.path, "ab") as f:
        f.write(b'{"seq":9')
    store.close()
    store2, _ = _store(tmp_path)
    await store2.repair()
    assert not list(d.glob(f"{SID}.jsonl.tmp-*"))
    store2.close()


# =====================================================================
# 中部坏行隔离(GWT-P8-02:记跳不中断,repair 后隔离区可查,坏行不删)
# =====================================================================
async def test_middle_bad_line_quarantine_not_interrupt(tmp_path):
    """回放绝不因单行损坏中断:好行照常产出,坏行记 PERS-201 隔离,不自动删。"""
    p = tmp_path / f"{SID}.jsonl"
    p.write_text(_line(_env(1)) + "THIS IS NOT JSON{{\n" + _line(_env(2)),
                 encoding="utf-8")
    store, _ = _store(tmp_path)
    got = list(store.replay())                           # 中间坏行:继续,不中断
    assert [e.seq for e in got] == [1, 2]
    assert store.quarantine_info() == {
        "path": str(p), "line_nos": [2], "count": 1}
    # 隔离不删:坏行仍在文件里(人类决策)
    assert "THIS IS NOT JSON{{" in p.read_text(encoding="utf-8")
    store.close()


async def test_repair_quarantine_marked_bad_line_kept(tmp_path):
    """repair:中部坏行进隔离区(fixed=quarantined:[n])并随 recovered 声明,不删。"""
    p = tmp_path / f"{SID}.jsonl"
    p.write_text(_line(_env(1)) + "not-json\n" + _line(_env(2)) + _line(_env(3)),
                 encoding="utf-8")
    rec = _Recorder()
    store, _ = _store(tmp_path)
    store.on_system_event = rec.cb
    report = await store.repair()
    assert report.quarantined == [2]
    assert report.fixed == ["quarantined:[2]"]
    (t, payload), = rec.calls
    assert t == "session.recovered"
    assert payload["quarantined"] == [2] and payload["fixed"] == ["quarantined:[2]"]
    # 坏行不删;后续事件可正常追加(seq 不回填,从 4 续)
    assert "not-json" in p.read_text(encoding="utf-8")
    await store.append(_env(4), sync=True)
    assert [e.seq for e in store.replay()] == [1, 2, 3, 4]
    assert store.quarantine_info()["line_nos"] == [2]    # 隔离区仍可查
    store.close()


async def test_repair_encoding_corrupt_preserved(tmp_path):
    """编码级坏块(完整行内):原文件保留 .corrupt-{ts} + PERS-202,不覆盖不静默。"""
    p = tmp_path / f"{SID}.jsonl"
    p.write_bytes(_line(_env(1)).encode("utf-8") + b"\x00\xff\xfeBROKEN\n" +
                  _line(_env(2)).encode("utf-8"))
    store, d = _store(tmp_path)
    with pytest.raises(PyHError) as ei:
        await store.repair()
    assert ei.value.code == "PERS-202"
    corrupt = list(d.glob(f"{SID}.jsonl.corrupt-*"))
    assert len(corrupt) == 1                             # 原文件保留
    assert not store.path.exists()                       # 主文件已让位 .corrupt
    assert "BROKEN" in corrupt[0].read_bytes().decode("latin-1")
    store.close()


# =====================================================================
# seq 空洞检测与声明合法性
# =====================================================================
async def test_seq_holes_detected(tmp_path):
    """seq 空洞:相邻跳号(1,2,4,5 → 缺 3);连续无空洞。"""
    p = tmp_path / f"{SID}.jsonl"
    p.write_text("".join(_line(_env(s)) for s in (1, 2, 4, 5)), encoding="utf-8")
    store, _ = _store(tmp_path)
    assert store.seq_holes(store.path) == [3]
    store.close()


async def test_repair_seq_holes_undeclared_marked(tmp_path):
    """未声明空洞:repair 标记 seq-holes(告警深查 F031,不回填)。"""
    p = tmp_path / f"{SID}.jsonl"
    p.write_text(_line(_env(1)) + _line(_env(3)), encoding="utf-8")
    rec = _Recorder()
    store, _ = _store(tmp_path)
    store.on_system_event = rec.cb
    report = await store.repair()
    assert "seq-holes:[2]" in report.fixed
    (t, payload), = rec.calls
    assert payload["fixed"] == ["seq-holes:[2]"]
    store.close()


async def test_repair_seq_holes_declared_by_compaction(tmp_path):
    """已声明空洞(context.compacted.ranges 覆盖)→ 合法,不标记不告警。"""
    p = tmp_path / f"{SID}.jsonl"
    compact = _env(4, "context.compacted", "system",
                   {"ranges": [[2, 2]], "summary": "压缩", "tokens_before": 1,
                    "tokens_after": 1})
    p.write_text(_line(_env(1)) + _line(_env(3)) + _line(compact),
                 encoding="utf-8")
    rec = _Recorder()
    store, _ = _store(tmp_path)
    store.on_system_event = rec.cb
    report = await store.repair()
    assert report.fixed == []                            # 空洞已被声明覆盖
    assert rec.calls == []                               # 无修复 → 不产 recovered
    store.close()


async def test_seq_holes_across_rotated_files(tmp_path):
    """空洞定位跨文件合并:seq1 已轮转,seq3 在主文件 → 缺 2 仍可检出。"""
    store, _ = _store(tmp_path)
    store.rotate_bytes = 50                                # 单行即超限 → 触发轮转
    await store.append(_env(1), sync=True)                 # → sid.1.jsonl
    assert len(store._rotated_paths()) == 1
    store.rotate_bytes = 10 ** 9                           # 抬高阈值:后续留主文件
    await store.append(_env(3), sync=True)                 # 2 未写(模拟丢失)
    assert [e.seq for e in store.replay()] == [1, 3]
    assert store.seq_holes(store.path) == [2]              # 空洞跨 轮转+主文件
    store.close()


# =====================================================================
# 轮转(序号合并/重放有序)
# =====================================================================
async def test_rotation_sequential_files_and_replay_order(tmp_path):
    """轮转:主文件原子 rename 为 {sid}.{n}.jsonl,重放按 1..n→main 序号合并。"""
    store, _ = _store(tmp_path)
    store.rotate_bytes = 50                                # 单行(~190B)即触发轮转
    for i in range(1, 6):
        await store.append(_env(i), sync=True)
    rotated = store._rotated_paths()
    assert len(rotated) == 5
    assert [p.name for p in rotated] == [f"{SID}.{n}.jsonl"
                                         for n in range(1, 6)]  # 序号从 1 连续
    assert store.path.exists() and store.path.stat().st_size == 0  # 主文件轮转后为空
    store.rotate_bytes = 10 ** 9                           # 抬高阈值:主文件续写
    await store.append(_env(6), sync=True)
    assert [e.seq for e in store.replay()] == [1, 2, 3, 4, 5, 6]   # 合并有序无丢失
    assert store.seq_holes(store.path) == []               # 跨文件无空洞
    store.close()
    # 重启后重放:轮转文件仍在、顺序一致
    store2, _ = _store(tmp_path)
    assert [e.seq for e in store2.replay()] == [1, 2, 3, 4, 5, 6]
    store2.close()


# =====================================================================
# 写失败暂停(GWT-P8-04:OSError×3 → PERS-202 + 会话暂停,拒新不丢旧)
# =====================================================================
async def test_sync_write_failure_raises_pers202(tmp_path, monkeypatch):
    """强同步写失败:直接 PERS-202;通道恢复后重试成功。"""
    store, _ = _store(tmp_path)

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(store._fh, "write", boom)
    with pytest.raises(PyHError) as ei:
        await store.append(_env(1), sync=True)
    assert ei.value.code == "PERS-202"
    assert ei.value.ctx.get("seq") == 1
    assert store._fail_streak == 1
    monkeypatch.undo()
    await store.append(_env(1), sync=True)               # 修复后重试
    assert store.path.read_bytes() == _line(_env(1)).encode("utf-8")
    store.close()


async def test_async_three_failures_suspend_retry_q_kept(tmp_path, monkeypatch):
    """异步路径 3 次连续失败 → system.error(PERS-202) + 会话暂停;拒新不丢旧。"""
    store, _ = _store(tmp_path)
    store.flush_batch = 1                                # 每条即刷,加速触达阈值
    rec = _Recorder()
    store.on_system_event = rec.cb

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(store._fh, "write", boom)
    await store.append(_env(1))                          # 1 败(未达阈值:静默重试窗)
    assert store._fail_streak == 1 and len(store._retry_q) == 1
    await store.append(_env(2))                          # 2 败
    assert store._fail_streak == 2 and len(store._retry_q) == 2
    with pytest.raises(PyHError) as ei:
        await store.append(_env(3))                      # 3 败 → PERS-202 + 暂停
    assert ei.value.code == "PERS-202"
    assert store._suspended is True
    assert len(store._retry_q) == 3                      # 拒新不丢旧:3 条全在
    (t, payload), = rec.calls
    assert t == "system.error" and payload["code"] == "PERS-202"
    monkeypatch.undo()

    with pytest.raises(PyHError) as ei2:
        await store.append(_env(4))                      # 暂停期拒新
    assert ei2.value.code == "PERS-202"
    assert len(store._retry_q) == 3                      # 新事件未入队(拒新)
    await store.flush()                                  # 通道恢复:重试队列全落盘
    assert store.path.read_text(encoding="utf-8") == "".join(
        _line(_env(i)) for i in (1, 2, 3))
    assert len(store._retry_q) == 0
    await store.repair()                                 # repair → recovering → normal
    assert store._suspended is False
    store.close()


# =====================================================================
# 系统事件注入回调契约
# =====================================================================
async def test_on_system_event_not_injected_logs_only(tmp_path, caplog):
    """on_system_event 未注入:降级日志不抛(repair 主路径不被打断)。"""
    p = tmp_path / f"{SID}.jsonl"
    p.write_text("junk-line-no-newline")                 # 尾部半行,无 \n
    store, _ = _store(tmp_path)
    with caplog.at_level("WARNING"):
        report = await store.repair()
    assert report.fixed == ["tail-truncated"]            # 修复本身照常完成
    assert any("on_system_event 未注入" in r.message for r in caplog.records)
    store.close()


async def test_repair_empty_file_no_fix(tmp_path):
    """空文件(0 字节):无半行无坏行 → 空修复报告(备份仍建,幂等)。"""
    store, _ = _store(tmp_path)
    report = await store.repair()
    assert report.fixed == [] and report.quarantined == []
    assert report.backup_path is not None
    store.close()


async def test_repair_missing_file_empty_report(tmp_path):
    """文件不存在(空会话):返回空报告,不备份不抛错。"""
    store, _ = _store(tmp_path)
    store.close()
    store.path.unlink()
    report = await store.repair()
    assert report == RepairReport()
    assert report.backup_path is None


def test_repair_report_dataclass_defaults():
    """RepairReport 形状(阶段 6 pyharness/repair 消费契约)。"""
    r = RepairReport()
    assert r.fixed == [] and r.quarantined == [] and r.backup_path is None
    assert "fixed" in r.__dataclass_fields__             # fixed/quarantined/backup 三件套


# =====================================================================
# SYNC_TYPES / 事件类常量
# =====================================================================
def test_store_sync_types_aligns_vocab():
    """SYNC_TYPES 单一真源在词表:user.message/guard.rejected/approval.* 全覆盖。"""
    assert SessionStore.SYNC_TYPES == SYNC_TYPES
    for t in ("user.message", "guard.rejected",
              "approval.requested", "approval.granted",
              "approval.denied", "approval.timeout",
              "session.finished", "session.recovered"):
        assert t in SessionStore.SYNC_TYPES


# =====================================================================
# Windows 路径处理
# =====================================================================
async def test_windows_style_backslash_paths(tmp_path):
    """Windows 反斜杠路径:open_store/append/重放全链路可用。"""
    d = Path(str(tmp_path).replace("/", "\\"))           # 模拟 Windows 风格串
    store = open_store(SID, dir=d)
    assert store.path == d / f"{SID}.jsonl"
    assert store.path.name == f"{SID}.jsonl"
    assert isinstance(store.path.parent, Path)
    await store.append(_env(1), sync=True)
    await store.append(_env(2), sync=True)
    assert [e.seq for e in store.replay()] == [1, 2]
    store.close()


async def test_replay_tolerates_crlf_leftover(tmp_path):
    """兼容遗留 \\r\\n 行尾(非本模块写出):replay 不隔离、内容一致。"""
    p = tmp_path / f"{SID}.jsonl"
    p.write_text(_line(_env(1)).replace("\n", "\r\n") +
                 _line(_env(2)).replace("\n", "\r\n"), encoding="utf-8")
    store, _ = _store(tmp_path)
    assert [e.seq for e in store.replay()] == [1, 2]
    assert store.quarantine_info()["count"] == 0
    store.close()
