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
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from pyharness.errors import PyHError
from pyharness.events import Envelope, SYNC_TYPES
from pyharness.persistence import (RepairReport, SessionStore, _FileLock,
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


def test_detect_truncation_half_line_over_tail_window(tmp_path):
    """半行超过 4096B 尾窗时不误判为 0(数据事故回归):完整行 + 大半行。

    旧实现窗口内无 \\n → 返回 0 → repair 把含完整行的文件整段截空;现应返回
    最后完整行的切点(= 单行信封长度 + 1),半行内容完整保留待 repair 截断。
    """
    path = tmp_path / f"{SID}.jsonl"
    good = _line(_env(1)).encode("utf-8")
    big_half = b'{"seq": 2, "ts": "' + b"x" * 5000  # > _TAIL_WINDOW 的无 \\n 半行
    path.write_bytes(good + big_half)
    detected = detect_truncation(path)
    assert detected == len(good)                           # 不返回 0
    assert path.read_bytes()[detected:] == big_half        # 大半行仍在(未误伤)


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
    await store.append(_env(1, "session.created", "system", {"title": "", "model": "m"}), sync=True)
    await store.append(_env(2), sync=True)
    good_bytes = store.path.read_bytes()
    # 模拟崩溃:半行残留(无 \n 结尾)
    with open(store.path, "ab") as f:
        f.write(b'{"seq":99,"ts":"2026-09-06T07:00:00Z",')
    store.close()
    assert detect_truncation(store.path) == len(good_bytes)

    store2, _ = _store(tmp_path)
    report = await store2.repair()
    # ① 备份先于修复;② 半行截断;③ recovered 声明(经**权威** repair_session,B1/R24 委托)
    assert report.fixed == ["tail-truncated"]
    assert report.quarantined == []
    assert report.backup_path is not None and report.backup_path.exists()
    assert report.backup_path.name.startswith(f"{SID}.corrupt-")
    assert store2.path.read_bytes().startswith(good_bytes)  # 原子重写:完整行前缀逐字节一致
    assert store2.path.read_bytes().endswith(b"\n")
    rec_evs = [e for e in store2.replay() if e.type == "session.recovered"]
    assert len(rec_evs) == 1                             # 恰一次声明(落**文件**,非 store 回调)
    assert rec_evs[0].payload["fixed"] == ["tail-truncated"]
    assert rec_evs[0].payload["backup"] == str(report.backup_path)
    # 重启续写:截断点后接续(声明自身占一个 seq ⇒ 下一个是 4)
    await store2.append(_env(4), sync=True)
    assert [e.seq for e in store2.replay()] == [1, 2, 3, 4]
    store2.close()

    # 幂等:再次 repair 无修复动作、不重复 recovered、文件字节不变
    store3, _ = _store(tmp_path)
    before = store3.path.read_bytes()
    report2 = await store3.repair()
    assert report2.fixed == [] and report2.quarantined == []
    assert len([e for e in store3.replay()
                if e.type == "session.recovered"]) == 1   # 不重复声明
    assert store3.path.read_bytes() == before
    assert [e.seq for e in store3.replay()] == [1, 2, 3, 4]
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
    """repair:中部坏行**移入隔离档**(fixed=quarantine-N,原文留证),主文件原子重写。

    B1/R24:该路径现经**权威** ``repair_session`` —— 早期面曾把坏行**留在原地**并只记
    行号(``quarantined:[n]``),与 F060 规格("隔离到 quarantine 文件,主文件原子重写")
    不符;本用例随之迁到生产语义。
    """
    p = tmp_path / f"{SID}.jsonl"
    p.write_text(_line(_env(1)) + "not-json\n" + _line(_env(2)) + _line(_env(3)),
                 encoding="utf-8")
    store, _ = _store(tmp_path)
    report = await store.repair()
    assert report.quarantined == [2]
    assert report.fixed == ["quarantine-2"]
    assert "not-json" not in p.read_text(encoding="utf-8")   # 主文件已原子重写
    q = sorted(tmp_path.glob(f"{SID}.quarantine-*"))
    assert q and "not-json" in q[0].read_text(encoding="utf-8")   # 坏行原文留证(不删)
    assert report.backup_path is not None                # 修复前强制备份
    # 后续事件可正常追加(seq 不回填;声明自身占 4 ⇒ 下一个是 5)
    await store.append(_env(5), sync=True)
    assert [e.seq for e in store.replay()] == [1, 2, 3, 4, 5]
    store.close()


async def test_repair_encoding_corrupt_preserved(tmp_path):
    """编码级坏块(完整行内):按**中部坏行**隔离 —— 原文进隔离档,主文件原子重写。

    B1/R24 迁移:早期面把整文件 rename 成 ``.jsonl.corrupt-*`` 并抛 PERS-202(整文件
    让位);权威 ``repair_session`` 的口径是**逐行**处置(编码级坏块 = 单行坏行,入
    quarantine,不整文件让位)—— 与 `scan_session` 的"坏行记跳不中断"同源。
    """
    p = tmp_path / f"{SID}.jsonl"
    p.write_bytes(_line(_env(1)).encode("utf-8") + b"\x00\xff\xfeBROKEN\n" +
                  _line(_env(2)).encode("utf-8"))
    store, d = _store(tmp_path)
    report = await store.repair()
    assert report.fixed == ["quarantine-2"]
    assert report.quarantined == [2]
    assert store.path.exists()                            # 主文件仍在(逐行处置,不让位)
    q = sorted(d.glob(f"{SID}.quarantine-*"))
    assert q and "BROKEN" in q[0].read_bytes().decode("utf-8", "replace")
    assert [e.seq for e in store.replay()] == [1, 2, 3]   # 其余行完好(3=recovered 声明)
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
    """未声明空洞:repair 以 `seq-holes:[…]` 声明(告警深查 F031,不回填)。"""
    p = tmp_path / f"{SID}.jsonl"
    p.write_text(_line(_env(1, "session.created", "system", {"title": "", "model": "m"})) + _line(_env(3)), encoding="utf-8")
    store, _ = _store(tmp_path)
    report = await store.repair()
    assert "seq-holes:[2]" in report.fixed
    rec_evs = [e for e in store.replay() if e.type == "session.recovered"]
    assert len(rec_evs) == 1 and "seq-holes:[2]" in rec_evs[0].payload["fixed"]
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
    # **R14-15**:成功 flush = 写通道确已恢复 ⇒ 必须**就地解除暂停**。此前解除点只有
    # `store.repair()`(旧面,生产零调用者)⇒ 进程内一旦暂停就永不恢复(append 恒被
    # "拒新不丢旧"挡回 PERS-202),而定时 flush 只会反复重写队列、永不解除。
    assert store._suspended is False, "通道恢复后应自动解除暂停(定时 flush 即恢复路径)"
    await store.repair()                                 # 旧面(幂等;生产零调用者,见 L-27)
    assert store._suspended is False
    store.close()


# =====================================================================
# 系统事件注入回调契约
# =====================================================================
async def test_on_system_event_not_injected_logs_only(tmp_path, caplog):
    """收敛路径不再经 store 的 `on_system_event`:声明由权威入口直接落盘。

    B1/R24:早期面经注入回调落 `session.recovered`;改为**委托** `repair_session` 后,
    声明由 `repair.declare_recovered` 自开 store 强同步写入 ⇒ 本用例改为断言
    "修复照常完成且声明**确实落到文件**"(而不是依赖回调)。
    """
    p = tmp_path / f"{SID}.jsonl"
    p.write_text(_line(_env(1, "session.created", "system",
                              {"title": "", "model": "m"})) +
                 "junk-line-no-newline")                 # created + 尾部半行(无 \n)
    store, _ = _store(tmp_path)
    with caplog.at_level("WARNING"):
        report = await store.repair()
    assert report.fixed == ["tail-truncated"]            # 修复本身照常完成
    evs = [e for e in store.replay() if e.type == "session.recovered"]
    assert len(evs) == 1 and evs[0].payload["fixed"] == ["tail-truncated"]
    store.close()


async def test_repair_empty_file_no_fix(tmp_path):
    """空文件(0 字节):无半行无坏行 → **无动作、不备份**(权威口径:健康即不动)。"""
    store, _ = _store(tmp_path)
    report = await store.repair()
    assert report.fixed == [] and report.quarantined == []
    assert report.backup_path is None                    # 健康文件不产生修复备份
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


# =====================================================================
# 跨进程单写者锁(INV-07):进程内可重入 / 跨进程独占
# =====================================================================
def test_file_lock_os_level_mutual_exclusion(tmp_path):
    """_FileLock:第二把锁取不到(OS 级互斥);释放后可再取。"""
    lp = tmp_path / f"{SID}.jsonl.lock"
    a = _FileLock(lp)
    assert a.try_acquire()
    b = _FileLock(lp)
    assert not b.try_acquire()               # 已持有 → 拒绝
    a.release()
    c = _FileLock(lp)
    assert c.try_acquire()                   # 释放后可再取
    c.release()


def test_session_lock_reentrant_same_process(tmp_path):
    """同进程多开同一会话:引用计数复用同一锁,不误判跨进程冲突。"""
    a, d = _store(tmp_path)
    b, _ = _store(tmp_path)                  # 同路径第二次打开:重入,不抛
    assert a.path == b.path
    a.close()
    b.close()


def test_session_lock_released_on_close_allows_reopen(tmp_path):
    """close 释放锁:同会话可再次 open。"""
    a, d = _store(tmp_path)
    a.close()
    b, _ = _store(tmp_path)                  # 锁已释放 → 可开
    b.close()


def test_cross_process_lock_blocks_second_writer(tmp_path):
    """跨进程:父进程持锁时,子进程开同一会话 → PERS-202(单写者 INV-07)。"""
    store, d = _store(tmp_path)
    code = (
        "import sys\n"
        "from pathlib import Path\n"
        "from pyharness.persistence import open_store\n"
        "from pyharness.errors import PyHError\n"
        "try:\n"
        "    open_store(sys.argv[1], dir=Path(sys.argv[2]))\n"
        "    print('OPENED')\n"
        "except PyHError as e:\n"
        "    print('BLOCKED', e.code)\n"
    )
    repo = Path(__file__).resolve().parents[2]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([sys.executable, "-c", code, SID, str(d)],
                       cwd=str(repo), capture_output=True,
                       encoding="utf-8", errors="replace", env=env)
    store.close()
    assert "BLOCKED PERS-202" in r.stdout, (r.stdout, r.stderr)
    # 父进程释放后,子进程可正常打开
    r2 = subprocess.run([sys.executable, "-c", code, SID, str(d)],
                        cwd=str(repo), capture_output=True,
                        encoding="utf-8", errors="replace", env=env)
    assert "OPENED" in r2.stdout, (r2.stdout, r2.stderr)


# =====================================================================
# S2-4:强同步真源收敛(关闭 ADR-019 P-3)
# =====================================================================
def test_s24_sync_set_membership():
    """Q1/Q3 分工在真源上的体现:policy.updated 在册,scope.updated 不在册。"""
    assert "policy.updated" in SYNC_TYPES       # 治理层策略事件:强同步(ADR-020 Q3)
    assert "scope.updated" not in SYNC_TYPES    # 运行时 scope 收紧:非强同步(Q1)


async def test_s24_desktop_adapter_derives_sync_from_vocab():
    """S2-4:desktop 落盘适配器同样**派生自** `SYNC_TYPES`,并保持 sid 过滤。

    以未绑定方法直接驱动 ``_attach_persistence``(只需 ``bus``/``_owners``),
    避免拉起整个桌面会话管理器。
    """
    from types import SimpleNamespace

    from pyharness.bus import EventBus
    from pyharness.desktop.sessions import DesktopSessionManager

    class _Rec:
        def __init__(self) -> None:
            self.calls: list = []

        async def append(self, env, sync=False) -> None:
            self.calls.append((env.type, sync))

    sid = "s-s24desk01"
    rec = _Rec()
    mgr = SimpleNamespace(bus=EventBus(), _owners={})
    log_ = SimpleNamespace(sid=sid, _bus=None)
    DesktopSessionManager._attach_persistence(mgr, log_, rec, sid)

    env = SimpleNamespace(session_id=sid, model_dump_json=lambda: "{}")
    for t, expected in (("policy.updated", True), ("scope.updated", False),
                        ("user.message", True), ("agent.message", False),
                        ("guard.rejected", True)):
        env.type = t
        await mgr.bus.emit(t, env)
        assert rec.calls[-1] == (t, expected), \
            f"{t} 的 sync 取值必须等于 (t in SYNC_TYPES)"

    # 多会话隔离仍生效:异会话事件不落本文件
    n = len(rec.calls)
    env.session_id = "s-other-session"
    await mgr.bus.emit("user.message", env)
    assert len(rec.calls) == n, "异会话事件必须被 sid 过滤挡住"


def test_s24_no_hardcoded_sync_list_in_adapters():
    """**防回归**:适配器源文件不得再出现"硬编码强同步清单"。

    判定:任何元素全为字符串常量的 tuple/set/list,若其**至少 3 个**元素且
    **全体 ⊆ SYNC_TYPES**,即视为第二真源(engine/desktop 曾各有一份 11 名副本)。
    """
    import ast

    repo = Path(__file__).resolve().parents[2]
    files = ("pyharness/engine.py", "pyharness/desktop/sessions.py",
             "pyharness/cli.py", "pyharness/core/orchestration.py")
    offenders: list = []
    for rel in files:
        tree = ast.parse((repo / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Tuple, ast.Set, ast.List)):
                continue
            vals = [e.value for e in node.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            if (len(vals) == len(node.elts) >= 3
                    and set(vals) <= set(SYNC_TYPES)):
                offenders.append((rel, sorted(vals)))
    assert not offenders, (
        f"出现硬编码强同步清单(应统一派生自 events.vocab.SYNC_TYPES):{offenders}")


def test_s24_adapters_reference_sync_types():
    """落盘适配器必须**派生于唯一实现**,不得各自维护强同步清单(ADR-019 P-3)。

    2026-09-21 R8 更新:两处适配器(engine / 桌面壳)现统一**委托**
    ``persistence.session_recorder`` —— 该单点才是引用 ``SYNC_TYPES`` 的地方。
    被测性质**未变且更强**(从"两处各自派生"变成"单点派生"),故断言随之改为:
    ①唯一实现引用 SYNC_TYPES;②两个调用方都委托它(不再各写一份)。
    """
    repo = Path(__file__).resolve().parents[2]
    src_impl = (repo / "pyharness/persistence.py").read_text(encoding="utf-8")
    assert "def session_recorder(" in src_impl
    assert "SYNC_TYPES" in src_impl, "唯一实现必须派生自 events.vocab.SYNC_TYPES"

    for rel in ("pyharness/engine.py", "pyharness/desktop/sessions.py",
                "pyharness/cli.py"):           # R9:CLI 侧原为第三份各自实现
        src = (repo / rel).read_text(encoding="utf-8")
        assert "session_recorder" in src, f"{rel} 未委托唯一实现(应为派生)"
        assert "_SYNC = (" not in src, f"{rel} 仍存在第二真源 `_SYNC`"


# ============================================ F-SYNC-1 强同步 durability 修复
class TestSyncDurability:
    """F-SYNC-1:sync=True 落盘失败必须**不丢行**且**对调用方可⻅**。

    修复点 = ``SessionStore._flush_pending_all`` 的失败语义(原为丢弃 pending_now)。
    铁律:全部走**真实** ``store.append`` / ``store.flush``,只对 ``_fh.write``
    注入故障(不 mock 内部函数制造假成功)。
    """

    @staticmethod
    def _boom(*_a, **_k):
        raise OSError("disk full (simulated)")

    async def test_sync_failure_preserves_batch_and_raises(self, tmp_path,
                                                           monkeypatch):
        """Case B:两次都失败 → 行留在 `_retry_q`;第二次调用方可见 PERS-202。"""
        store, _ = _store(tmp_path)
        monkeypatch.setattr(store._fh, "write", self._boom)
        with pytest.raises(PyHError) as ei:                  # 第一次:适配器路径
            await store.append(_env(10), sync=True)
        assert ei.value.code == "PERS-202"
        assert [s for s, _ in store._retry_q] == [10]        # 不丢
        assert len(store._pending) == 0
        with pytest.raises(PyHError) as ei2:                 # 第二次:步骤 9 flush
            await store.flush(10)
        assert ei2.value.code == "PERS-202"                  # caller-visible
        assert [s for s, _ in store._retry_q] == [10]        # 仍不丢
        assert store.path.read_text(encoding="utf-8") == ""  # 确实未落盘
        monkeypatch.undo()

    async def test_sync_case_a_second_attempt_succeeds(self, tmp_path,
                                                       monkeypatch):
        """Case A:第一次失败、第二次成功 → 数据真实存在且队列清空。"""
        store, _ = _store(tmp_path)
        monkeypatch.setattr(store._fh, "write", self._boom)
        with pytest.raises(PyHError):
            await store.append(_env(10), sync=True)
        assert [s for s, _ in store._retry_q] == [10]
        monkeypatch.undo()                                   # 通道恢复
        await store.flush(10)                                # 第二次真实尝试
        assert len(store._retry_q) == 0
        assert store.path.read_bytes() == _line(_env(10)).encode("utf-8")
        assert [e.seq for e in store.replay()] == [10]       # replay 可读
        store.close()

    async def test_failed_batch_not_split_or_reordered(self, tmp_path,
                                                       monkeypatch):
        """多事件批:10/11/12 整批保留且**保序**,不丢头不丢尾。"""
        store, _ = _store(tmp_path)
        for s in (10, 11, 12):
            store._pending.append((s, _line(_env(s))))
        monkeypatch.setattr(store._fh, "write", self._boom)
        with pytest.raises(OSError):
            await store._flush_pending_all()
        assert [s for s, _ in store._retry_q] == [10, 11, 12]
        monkeypatch.undo()
        await store.flush(12)                                # 恢复后重试
        assert store.path.read_text(encoding="utf-8") == "".join(
            _line(_env(s)) for s in (10, 11, 12))            # 顺序 10→11→12
        assert len(store._retry_q) == 0
        store.close()

    async def test_retry_precedes_new_rows(self, tmp_path, monkeypatch):
        """重试行恒先写:旧 retry(10) 先于新 pending(11),seq 不倒退。"""
        store, _ = _store(tmp_path)
        monkeypatch.setattr(store._fh, "write", self._boom)
        with pytest.raises(OSError):
            store._pending.append((10, _line(_env(10))))
            await store._flush_pending_all()
        monkeypatch.undo()
        store._pending.append((11, _line(_env(11))))         # 新行入队
        await store.flush(11)
        assert store.path.read_text(encoding="utf-8") == (
            _line(_env(10)) + _line(_env(11)))               # 10 → 11
        store.close()

    @pytest.mark.parametrize("type_,payload", [
        ("user.message", {"content": "hi"}),
        ("guard.rejected", {"tool": "t", "guard_id": "g-danger",
                            "reason": "POL-DGR-1", "policy_ref": "POL-DGR-1"}),
        ("decision.issued", {"decision_id": "d1", "verdict": "allow",
                             "principal_kind": "system",
                             "principal_id": "pyharness-runtime"}),
    ])
    async def test_contract_applies_to_all_sync_types(self, tmp_path, type_,
                                                      payload, monkeypatch):
        """修复对全部 SYNC_TYPES 一致生效——无任何 event type 特判。"""
        assert type_ in SYNC_TYPES
        store, _ = _store(tmp_path)
        monkeypatch.setattr(store._fh, "write", self._boom)
        with pytest.raises(PyHError) as ei:
            await store.append(_env(10, type_=type_, payload=payload),
                               sync=True)
        assert ei.value.code == "PERS-202"
        assert [s for s, _ in store._retry_q] == [10]
        monkeypatch.undo()
        await store.flush(10)
        assert [e.type for e in store.replay()] == [type_]
        store.close()

    async def test_async_path_semantics_unchanged(self, tmp_path, monkeypatch):
        """INV-F5:非 sync 路径行为不变(攒批失败回填 `_retry_q`,不因本修复改变)。"""
        store, _ = _store(tmp_path)
        store.flush_batch = 1
        monkeypatch.setattr(store._fh, "write", self._boom)
        await store.append(_env(10))                         # sync=False:不抛
        assert [s for s, _ in store._retry_q] == [10]
        assert store._fail_streak == 1
        monkeypatch.undo()
        await store.flush()
        assert [e.seq for e in store.replay()] == [10]
        store.close()

    async def test_same_seq_replay_does_not_duplicate_line(self, tmp_path,
                                                           monkeypatch):
        """同 seq 重放(失败后原样重试)不得在 JSONL 留下重复 seq 行。"""
        store, _ = _store(tmp_path)
        monkeypatch.setattr(store._fh, "write", self._boom)
        with pytest.raises(PyHError):
            await store.append(_env(10), sync=True)          # 失败 → 行入 retry
        assert [s for s, _ in store._retry_q] == [10]
        monkeypatch.undo()
        await store.append(_env(10), sync=True)              # 同 seq 重放
        text = store.path.read_text(encoding="utf-8")
        assert text == _line(_env(10))                       # 只写一行
        assert [e.seq for e in store.replay()] == [10]       # seq 不重复
        assert len(store._retry_q) == 0
        store.close()

    async def test_t10_same_seq_identical_payload_dedupes(self, tmp_path):
        """T10(A-1):同 seq + **相同内容** → 去重为一行(幂等,不重复写)。"""
        store, _ = _store(tmp_path)
        line = _line(_env(10))
        store._retry_q.append((10, line))
        store._pending.append((10, line))
        await store._flush_pending_all()
        assert store.path.read_text(encoding="utf-8") == line   # 恰一行
        assert len(store._retry_q) == 0 and len(store._pending) == 0
        store.close()

    async def test_t11_same_seq_conflicting_payload_fails_closed(self, tmp_path):
        """T11(A-1)=(c):同 seq + **不同内容** → PERS-202 fail-closed;
        **不 first-wins、不 last-wins、不静默覆盖**——两份数据都保留。"""
        store, _ = _store(tmp_path)
        line_a = _line(_env(10))                             # payload A
        line_b = _line(_env(10, payload={"content": "B"}))   # 同 seq,不同内容
        assert line_a != line_b
        store._retry_q.append((10, line_a))
        store._pending.append((10, line_b))
        with pytest.raises(PyHError) as ei:
            await store._flush_pending_all()
        assert ei.value.code == "PERS-202"
        assert ei.value.ctx.get("op") == "seq_conflict"
        # 两份原始数据均未被覆盖/丢弃(冲突前不改队列)
        assert store._retry_q[0] == (10, line_a)
        assert store._pending[0] == (10, line_b)
        assert store.path.read_text(encoding="utf-8") == ""   # 未写盘
        store.close()

    async def test_t12_seq_unique_after_retry_cycles(self, tmp_path,
                                                    monkeypatch):
        """T12:多轮失败/重试后 seq 仍唯一且升序(replay 无重复 seq)。"""
        store, _ = _store(tmp_path)
        for s in (10, 11, 12):
            store._pending.append((s, _line(_env(s))))
        monkeypatch.setattr(store._fh, "write", self._boom)
        for _ in range(2):                                   # 连续两轮失败
            with pytest.raises(OSError):
                await store._flush_pending_all()
            assert [s for s, _ in store._retry_q] == [10, 11, 12]
        monkeypatch.undo()
        await store.flush(12)                                # 恢复
        seqs = [e.seq for e in store.replay()]
        assert seqs == [10, 11, 12]                          # 唯一 + 升序
        assert len(seqs) == len(set(seqs))                   # 无重复
        assert store.path.read_text(encoding="utf-8").count("\n") == 3
        store.close()


# ============ R13-2:崩溃残片(尾部半行)未修复即重开 + 追加 → 新事件不得损坏
async def test_append_after_torn_tail_keeps_new_event_parseable(tmp_path):
    """**修复前失败**(静默丢事件):崩溃留下的无换行尾半行,会让下一次 append
    与其拼在**同一物理行** ⇒ 新事件不可解析(内存以为已落盘、磁盘读不回)。

    修复:首写前补一个 `\n` —— 残片自成一(坏)行、新事件保持完整;**不删字节**
    (残片留待 repair 隔离),保持"open/回放不修"的既有口径。
    """
    import json

    from pyharness.bus import EventBus
    from pyharness.desktop.sessions import DesktopSessionManager

    bus = EventBus()
    mgr = DesktopSessionManager(dir=tmp_path, bus=bus)
    sid = await mgr.create()
    log_ = await mgr.open_session(sid)
    await log_.append("user.message", {"content": "m2"}, actor="user", sync=True)
    path = tmp_path / f"{sid}.jsonl"
    assert path.read_bytes().endswith(b"\n")

    # ---- 崩溃现场:半行(无换行)
    with open(path, "ab") as fh:
        fh.write(b'{"seq":99,"ts":"2026-09-21T00:00:00Z","type":"agent.mess')
    await mgr.shutdown_all()

    mgr2 = DesktopSessionManager(dir=tmp_path, bus=bus)
    log2 = await mgr2.open_session(sid)
    env = await log2.append("user.message", {"content": "after-crash"},
                            actor="user", sync=True)
    await mgr2.shutdown_all()

    assert env.seq == 3, "seq 应续在最后**完整**事件之后"
    raw = path.read_bytes()
    lines = [ln for ln in raw.split(b"\n") if ln]
    parsed = []
    for ln in lines:
        try:
            parsed.append(json.loads(ln.decode("utf-8")))
        except ValueError:
            parsed.append(None)
    # 新事件必须是一条**独立且可解析**的行(修复前它与残片拼接 ⇒ 不可解析)
    assert any(p is not None and p.get("seq") == 3 for p in parsed), \
        f"新事件被残片吞掉(不可解析):{[l[:40] for l in lines]}"
    # 残片仍在(数据优先:不删字节,留给 repair 隔离)
    assert any(p is None for p in parsed), "残片不得被静默删除"
    # 重放口径:可解析事件数 == 3(created + m2 + after-crash)
    assert len([p for p in parsed if p is not None]) == 3


async def test_torn_tail_healing_is_skipped_for_clean_files(tmp_path):
    """负空间:完整行结尾的文件**不得**多写分隔字节(逐字节不变)。"""
    from pyharness.bus import EventBus
    from pyharness.desktop.sessions import DesktopSessionManager

    mgr = DesktopSessionManager(dir=tmp_path, bus=EventBus())
    sid = await mgr.create()
    await mgr.shutdown_all()
    path = tmp_path / f"{sid}.jsonl"
    before = path.read_bytes()

    mgr2 = DesktopSessionManager(dir=tmp_path, bus=EventBus())
    log2 = await mgr2.open_session(sid)
    await log2.append("user.message", {"content": "x"}, actor="user", sync=True)
    await mgr2.shutdown_all()

    raw = path.read_bytes()
    assert raw.startswith(before), "既有字节必须逐字节保真(prefix 不变)"
    assert b"\n\n" not in raw, "不得插入多余空行"


# ============ R13-3:写中途失败(磁盘满)后,下一轮写不得与残行拼接
async def test_partial_write_failure_does_not_corrupt_next_line(tmp_path,
                                                               monkeypatch):
    """写失败可能停在**一行中间** ⟹ 下一轮写必须先把残行隔断,不得拼接。

    R13-2 的自愈只在"打开时检测到残片";而**本轮写失败**会新造残行且标记已被清除
    ⟹ 若不复武装,下一轮写就与残行拼接(R13-3 二阶)。此处注入"半行后抛 OSError"
    (开关控制、可恢复)验证:失败行回填重试、残行被隔断、重试行可解析。
    """
    import json

    NL = bytes([10])
    store, _ = _store(tmp_path)
    real_write = store._fh.write
    state = {"fail": True}

    def _flaky(text, *a, **kw):
        if state["fail"]:
            real_write(text[: len(text) // 2])   # 只写半行
            raise OSError("disk full (simulated)")
        return real_write(text, *a, **kw)

    monkeypatch.setattr(store._fh, "write", _flaky)
    with pytest.raises(PyHError) as ei:          # 强同步:失败上抛且不丢行
        await store.append(_env(1, "session.created", "system",
                                {"title": "", "model": "m"}), sync=True)
    assert ei.value.code == "PERS-202"
    assert [x for x, _ in store._retry_q] == [1], "失败行必须回填重试(不丢)"

    state["fail"] = False                        # 写通道恢复 → 重试落盘
    await store.flush()
    store.close()

    raw = store.path.read_bytes()
    assert raw.endswith(NL), "落盘应以完整行结尾"
    lines = [ln for ln in raw.split(NL) if ln]
    parsed = []
    for ln in lines:
        try:
            parsed.append(json.loads(ln.decode("utf-8")))
        except ValueError:
            parsed.append(None)
    assert any(x is not None and x.get("seq") == 1 for x in parsed), (
        "重试行必须可解析(修复前与残行拼接):"
        + repr([ln[:40] for ln in lines]))


# ============ R14-2:会话锁占用探针(启动自检不得把活会话当修复候选)
def test_session_lock_held_probe_semantics(tmp_path):
    """``session_lock_held`` 只探测不改语义:未取过锁 → False(**不建锁文件**);
    持锁期间(同进程) → True;释放后 → False;跨进程占用 → True。"""
    from pyharness.persistence import (acquire_session_lock, session_lock_held,
                                       session_lock_path)

    log_path = tmp_path / f"{SID}.jsonl"
    lock_path = session_lock_path(log_path)

    assert session_lock_held(lock_path) is False
    assert not lock_path.exists(), "探针不得为'从未取锁'的会话新建锁文件"

    acquire_session_lock(lock_path)
    try:
        assert session_lock_held(lock_path) is True, "持锁期间必须判为占用"
    finally:
        release = __import__("pyharness.persistence",
                             fromlist=["release_session_lock"])
        release.release_session_lock(lock_path)
    assert session_lock_held(lock_path) is False, "释放后不得再判为占用"

    # 跨进程占用:子进程取锁并保持 → 本进程探针判 True
    code = (
        "import sys, time\n"
        "from pathlib import Path\n"
        "from pyharness.persistence import acquire_session_lock\n"
        "acquire_session_lock(Path(sys.argv[1]))\n"
        "print('LOCKED', flush=True)\n"
        "time.sleep(30)\n"
    )
    proc = subprocess.Popen([sys.executable, "-c", code, str(lock_path)],
                            stdout=subprocess.PIPE, text=True,
                            env={**os.environ, "PYTHONPATH": str(Path.cwd())})
    try:
        assert proc.stdout.readline().strip() == "LOCKED"
        assert session_lock_held(lock_path) is True, "跨进程占用必须判为占用"
    finally:
        proc.kill()
        proc.wait(timeout=10)
    deadline = time.monotonic() + 5.0              # 进程退出→OS 释放锁(平台可能滞后)
    while time.monotonic() < deadline:
        if not session_lock_held(lock_path):
            break
        time.sleep(0.05)
    assert session_lock_held(lock_path) is False, "子进程退出后应可探测"


# ============ R14-3:会话 vs 辅助档的**唯一**判据/枚举点
def test_session_log_paths_excludes_aux_files(tmp_path):
    """``session_log_paths`` 只返回会话**主文件**;轮转段/修复备份/隔离档/旧备份名
    一律排除(它们**属于**某会话但**不是**独立会话),非 ``s-`` 命名同样排除。"""
    from pyharness.persistence import (is_aux_session_file, session_log_paths,
                                       session_data_paths,
                                       rotated_segment_paths)

    for name in ("s-abc12345.jsonl",
                 "s-abc12345.1.jsonl", "s-abc12345.12.jsonl",
                 "s-abc12345.corrupt-20260907T000000000.jsonl",
                 "s-abc12345.quarantine-20260907T000000000.jsonl",
                 "s-abc12345.1.quarantine-20260907T000000000.jsonl",
                 "s-abc12345.jsonl.bak-20260907T000000000",
                 "notes.jsonl"):
        (tmp_path / name).write_text("", encoding="utf-8")

    assert [p.name for p in session_log_paths(tmp_path)] == ["s-abc12345.jsonl"], \
        "只有主文件是会话;其余(段/备份/隔离/非会话命名)全部排除"

    assert is_aux_session_file(tmp_path / "s-abc12345.1.jsonl") is True
    assert is_aux_session_file(tmp_path / "s-abc12345.jsonl") is False

    # 数据面:段(升序)+ 主文件 —— 与 replay 同序,供按会话聚合的调用方使用
    main = tmp_path / "s-abc12345.jsonl"
    assert [p.name for p in rotated_segment_paths(main)] == [
        "s-abc12345.1.jsonl", "s-abc12345.12.jsonl"]
    assert [p.name for p in session_data_paths(main)][-1] == "s-abc12345.jsonl"
    assert len(session_data_paths(main)) == 3

    assert session_log_paths(tmp_path / "nope") == [], "目录不存在 → 空(不抛)"


# ============ R14-5:只读回放源不取 INV-07 会话写锁
def test_session_reader_replays_without_taking_session_lock(tmp_path):
    """``SessionReader`` 回放真源时**不取**会话写锁(否则只读命令会锁住所有会话)。

    实测(R14-5):``search`` 曾借 ``open_store`` 建回放源,而 ``open_store`` 取 INV-07
    锁并持有到查询结束 ⇒ 搜索运行期间**其他进程开会话撞 PERS-202**
    (并发 open 交替 OPEN_OK / BLOCKED)。只读命令不该持有写锁。
    """
    from pyharness import persistence as P

    # 真源:轮转段 + 主文件(只读源须跨段回放)
    (tmp_path / f"{SID}.1.jsonl").write_text(
        _line(_env(1, "session.created", "system",
                   {"title": "", "model": "m"})), encoding="utf-8")
    (tmp_path / f"{SID}.jsonl").write_text(
        _line(_env(2, "user.message", "user", {"content": "hi"})), encoding="utf-8")

    # 前置判别力:写通道(open_store)确实会持**本会话**锁,而只读源不会。
    # (断言按本会话锁路径判定:`_LOCKS` 是进程级全局,可能残留其他用例的条目。)
    key = str(P.session_lock_path(tmp_path / f"{SID}.jsonl"))
    wr = P.open_store(SID, dir=tmp_path)
    assert key in P._LOCKS, "前置:open_store 必须持锁(否则本用例失去判别力)"
    wr.close()
    assert key not in P._LOCKS, "前置:关闭后本会话锁应归还"

    rd = P.SessionReader(SID, dir=tmp_path)
    assert [e.seq for e in rd.replay()] == [1, 2], "只读源须跨段回放(段+主文件)"
    assert key not in P._LOCKS, "只读回放**不得**取会话写锁"
    assert rd.quarantine_info()["count"] == 0

    # 负空间:不可读的单个源只告警跳过,不上抛(离线命令容错)
    bad = "s-badfile01"
    (tmp_path / f"{bad}.jsonl").mkdir()                     # 目录:open 必失败
    assert list(P.SessionReader(bad, dir=tmp_path).replay()) == [], \
        "不可读源应跳过而非上抛"
