"""Persistence policy: complete migrated implementation."""
from __future__ import annotations
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from pyharness.config import DEFAULTS
from pyharness.errors import raise_code


log = logging.getLogger("pyharness.persistence")

_ROTATE_BYTES: int = DEFAULTS["storage"]["jsonl"]["rotate_bytes"]     # 50MB

_FLUSH_BATCH: int = DEFAULTS["log"]["jsonl"]["flush_batch"]           # 64 条

_FLUSH_INTERVAL_S: float = DEFAULTS["log"]["jsonl"]["flush_interval_s"]  # 0.5s

_DEFAULT_SESSIONS_DIR: str = DEFAULTS["storage"]["sessions_dir"]      # ~/.pyharness/sessions

_RETRY_Q_LIMIT = 192

_FAIL_STREAK_LIMIT: int = 3

_TAIL_WINDOW = 4096

def _resolve_by_seq(rows: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """写前按 seq 归并(保序)并做**一致性校验**(fail-closed)。

    ``seq`` 是事件身份的一部分——框架经 ``SeqState`` 单调分配、``SessionLog``
    另有 EVT-101 失步闸,故同一 seq 只可能代表同一事件。据此:

    - 同 seq + **相同行** ⇒ 去重(同 seq 重放同一事件,幂等);
    - 同 seq + **不同行** ⇒ **数据一致性冲突** → ``PERS-202`` 上抛;
      **不 first-wins、不 last-wins、不静默覆盖**(丢哪一份都是丢事件)。

    返回按 seq 升序的写序列(物理写序 = seq 序)。**本函数无副作用**:在任何
    队列状态变更之前调用,冲突时原队列保持不动。
    """
    seen: dict[int, str] = {}
    for seq, line in rows:
        prev = seen.get(seq)
        if prev is None:
            seen[seq] = line
        elif prev != line:
            raise_code("PERS-202", op="seq_conflict", seq=seq,
                       why="同一 seq 出现不同内容:seq 是事件身份的一部分,"
                           "同一 seq 只能是同一事件",
                       advice="拒绝静默覆盖(不 first/last-wins);查是否绕过"
                              "框架自报 seq 或存在重复写入")
    return [(s, seen[s]) for s in sorted(seen)]

def now_ts() -> str:
    """UTC 时间戳(备份/损坏文件命名用):YYYYMMDDTHHMMSSZ。"""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

def default_sessions_dir() -> Path:
    """默认会话日志目录(可配):L3 env 优先(PH_STORAGE_SESSIONS_DIR),否则 L1 锚点。

    L2(config.yaml) 定制由装配层阶段 2 统一解析后经 dir= 注入——本模块不在运行期
    读用户配置文件(避免隐藏副作用),此处只落地 L1/L3 快捷解析。
    """
    raw = os.environ.get("PH_STORAGE_SESSIONS_DIR", _DEFAULT_SESSIONS_DIR)
    return Path(raw).expanduser()
