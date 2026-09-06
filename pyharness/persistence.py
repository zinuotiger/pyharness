"""pyharness/persistence.py — 会话日志持久化 (specs/persistence.py.md; F011/F060)

会话真源的物理形态:事件逐行 JSONL append(UTF-8 单行信封+payload)、攒批/强同步
双速 flush、原子写(同目录临时文件 + fsync + rename)、轮转(>50MB)、截断检测与
repair 崩溃恢复入口(备份 → 修复 → recovered 声明)。

不变式(INV-01 只追加,INV-07 单写者,INV-08 无回边):
- 正常写路径纯 append 句柄,无就地改写;一切重写(repair 截断、轮转 rename)
  走同目录临时文件/rename 原子路径,且修复前强制备份。
- 事件信封只经本模块写盘,不自造信封(seq/ts 只能框架分配,EVENT-SCHEMA §1.1-4):
  本模块产出的 system.error / session.recovered 一律经注入的 on_system_event 回调
  上抛(由装配层/bus 日志订阅者注入),保拓扑无回边、不反向 import session。

损坏标记 / 隔离约定(供阶段 6 pyharness/repair 模块消费,本模块先行落地):
- 隔离区:_quarantine:set[int] 行号(repair 决策/用户查看),经 quarantine_info()
  暴露;坏行只记跳隔离、绝不自动删(中部坏行 = 人类决策)。
- 备份命名:{sid}.jsonl.bak-{ts}(repair 修复前强制,ts=UTC YYYYMMDDTHHMMSSZ)。
- 不可修复(编码级坏块):原文件 rename 为 {sid}.jsonl.corrupt-{ts} 保留并
  PERS-202 明确报错,不覆盖。
- 轮转命名:{sid}.{n}.jsonl(n 从 1 递增),重放按 {sid}.1..n → {sid}.jsonl 序号合并。
- RepairReport(fixed/quarantined/backup_path) = session.recovered 事件的
  fixed/quarantined/backup 载荷镜像(F060)。

文件布局偏离:spec 头部标注 pyharness/core/persistence.py(8 脊柱归位计划);
当前仓库 spine 模块平铺包根(events/config/errors 同层),故落地
pyharness/persistence.py,待 core/ 归位阶段整体迁移(契约不变)。

错误全走 raise_code(PERS-2xx);外部依赖:events.Envelope、errors.raise_code、
config.DEFAULTS(轮转/攒批参数)、log。
"""
from __future__ import annotations

import logging
import os
import re
import shutil
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Iterator, Optional

from pyharness.config import DEFAULTS
from pyharness.errors import raise_code
from pyharness.events import Envelope, SYNC_TYPES

log = logging.getLogger("pyharness.persistence")

# ===================================================================== 常量
# L1 权威默认值(config.DEFAULTS 唯一落地;全量 config 解析在装配层完成后再注入)
_ROTATE_BYTES: int = DEFAULTS["storage"]["jsonl"]["rotate_bytes"]     # 50MB
_FLUSH_BATCH: int = DEFAULTS["log"]["jsonl"]["flush_batch"]           # 64 条
_FLUSH_INTERVAL_S: float = DEFAULTS["log"]["jsonl"]["flush_interval_s"]  # 0.5s
_DEFAULT_SESSIONS_DIR: str = DEFAULTS["storage"]["sessions_dir"]      # ~/.pyharness/sessions

# 重试队列上限(PERS-202 暂停判据之二;PARAMETER-ANCHOR 无锁死,沿用 DIS 伪码 192)
_RETRY_Q_LIMIT = 192

# 只读检测尾部窗口(半行定位范围)
_TAIL_WINDOW = 4096

# ================================================================= 工具函数
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


def detect_truncation(path: Path) -> Optional[int]:
    """截断检测(只读):文件非空且末字节不是 \\n → 返回半行起始字节偏移;否则 None。

    崩溃遗留的尾部半行 = 未完成写的事实,不假装发生:open/回放不修,repair 先备份
    再截断(F060)。实现按 spec 注释意图读取尾部窗口——伪码 seek(0,2) 后未回退
    read 为空,实际以 seek(-window, 2) 读窗口再定位最后一个换行。
    """
    try:
        if not path.exists() or path.stat().st_size == 0:
            return None
    except OSError:
        return None
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        window = min(size, _TAIL_WINDOW)
        f.seek(-window, 2)                       # 回退读尾部窗口(修正伪码遗漏)
        tail = f.read(window)
        if tail.endswith(b"\n"):
            return None                          # 完整行结尾:无截断
    last_nl = tail.rfind(b"\n")
    # 偏移 = 文件大小 - 窗口内最后一个换行之后的字节数(字节级切点,保完整行)
    return size - (len(tail) - last_nl - 1) if last_nl >= 0 else 0


def _read_clean(path: Path) -> list[tuple[int, str]]:
    """整文件解码为 (行号, 文本) 列表;编码级坏块 → 抛 UnicodeDecodeError。

    repair 中部坏行扫描专用:先整读保证\"中部编码坏块 = 不可修复\"可整体暴露
    (逐行容错会把编码坏块降级成单行隔离,违背 spec PERS-202 处置表)。
    """
    out: list[tuple[int, str]] = []
    with open(path, "rb") as f:
        for no, raw in enumerate(f, 1):
            line = raw.rstrip(b"\r\n")
            if not line:
                continue
            out.append((no, line.decode("utf-8")))   # 坏块在此处整体抛出
    return out


# ================================================================= RepairReport
@dataclass(frozen=True)
class RepairReport:
    """repair 产出(F060):fixed/quarantined/backup_path —— session.recovered 载荷镜像。

    fixed: 修复动作清单(如 "tail-truncated" / "quarantined:[2]" / "seq-holes:[3]");
    quarantined: 坏行行号(隔离不删,人类决策);
    backup_path: 修复前强制备份(.bak-{ts});文件不存在 → None(空会话)。
    幂等:对已修复文件重复 repair,结果一致(无修复动作)。
    """

    fixed: list[str] = field(default_factory=list)
    quarantined: list[int] = field(default_factory=list)
    backup_path: Optional[Path] = None


# ================================================================= SessionStore
class SessionStore:
    """单个会话的 JSONL 存储(单写者纪律 INV-07)。

    字段(spec 关键数据结构):session_id / path / _fh(append 句柄)/ _pending
    (攒批缓冲)/ _retry_q(失败重试,拒新不丢旧)/ _fail_streak / _quarantine /
    SYNC_TYPES。写通道状态机:攒批 →(满 batch/定时器)→ flush;强同步三类立即
    write+flush;OSError 重试 ≤3,3 败 → system.error(PERS-202)+ 会话暂停;
    repair → recovering → normal。
    """

    # 强同步事件族(与 events.vocab 同一 frozenset,单一真源在词表)
    SYNC_TYPES: frozenset = SYNC_TYPES

    def __init__(self, session_id: str, path: Path, fh, *,
                 rotate_bytes: Optional[int] = None,
                 flush_batch: Optional[int] = None,
                 flush_interval_s: Optional[float] = None,
                 on_system_event: Optional[Callable[[str, dict], Awaitable]] = None):
        self.session_id = session_id
        self.path = path
        self._fh = fh                             # 追加句柄(append,UTF-8,单写者)
        self.rotate_bytes = rotate_bytes or _ROTATE_BYTES
        self.flush_batch = flush_batch or _FLUSH_BATCH
        self.flush_interval_s = flush_interval_s or _FLUSH_INTERVAL_S
        self.on_system_event = on_system_event    # 注入回调(装配层/bus 订阅者)
        self._pending: deque[tuple[int, str]] = deque()   # 攒批缓冲(seq, line)
        self._retry_q: deque[tuple[int, str]] = deque()   # 写失败重试队列
        self._fail_streak = 0                     # 连续失败计数(≥3 暂停)
        self._quarantine: set[int] = set()        # 坏行行号隔离区(repair/查看)
        self._suspended = False                   # 暂停中:拒新不丢旧

    # ------------------------------------------------------------ 关闭
    def close(self) -> None:
        """收尾:flush + 关追加句柄(会话终态/进程退出前调用)。"""
        if self._fh.closed:                       # 已关(如编码坏块 .corrupt 分支)
            return
        try:
            self._fh.flush()
        finally:
            self._fh.close()

    # ------------------------------------------------------------ 落盘主路径
    async def append(self, env: Envelope, sync: bool = False) -> None:
        """Envelope → 单行落盘(F011 双速写)。

        sync=True(强同步三类:user.message/guard.rejected/approval.* 等):立即
        write+flush,成功才返回(崩溃一致性锚点);否则入 _pending 攒批(满 batch
        即刷,0.5s 定时器兜底,见 flush)。写入后检查句柄位置触发轮转(>50MB)。
        异常:强同步 OSError → PERS-202 直接抛;异步 3 次重试仍败 → PERS-202 +
        会话暂停;暂停中(拒新不丢旧)→ PERS-202。
        """
        if self._suspended:                       # 拒新不丢旧:暂停期拒绝新事件
            raise_code("PERS-202", hint="会话暂停(落盘通道故障),repair 后恢复;拒新不丢旧")
        line = env.model_dump_json() + "\n"       # 信封+payload 拍平单行(§3.6)
        if sync:                                  # 强同步点:成功才返回
            try:
                self._fh.write(line)
                self._fh.flush()
            except OSError as e:
                self._fail_streak += 1
                raise_code("PERS-202", seq=env.seq, why=str(e),
                           advice="落盘通道故障;跑 repair(F060)")
        else:                                     # 普通事件:攒批(内存即对订阅者可见)
            self._pending.append((env.seq, line))
            if len(self._pending) >= self.flush_batch:   # ≥64 条 → 立即批量 flush
                await self._flush_batch()
            # 0.5s 定时器兜底:由独立定时任务调 flush()(append 内不 sleep)
        self._maybe_rotate()                      # >50MB → 轮转(§8.3.4)

    async def flush(self, up_to_seq: Optional[int] = None) -> None:
        """公开 flush:把 _pending 中 seq ≤ up_to_seq 的行(连同重试队列)写盘+flush。

        up_to_seq=None = 全量(定时器/关闭前);成功才返回(强同步契约);OSError →
        行回重试队列(PERS-202,拒新不丢旧)。调用方:session 强同步点、定时任务。
        """
        if not self._pending and not self._retry_q:
            return                                # 无积压:空操作
        take, keep = [], []
        for item in self._pending:                # 仅刷 up_to_seq 之前;之后保留积压
            (keep if (up_to_seq is not None and item[0] > up_to_seq) else take).append(item)
        self._pending = deque(keep)
        retry = list(self._retry_q)               # 先重试旧行再新取(seq 升序)
        to_write = retry + take
        if not to_write:
            return
        try:
            for _seq, line in to_write:
                self._fh.write(line)
            self._fh.flush()
            self._fail_streak = 0                 # 成功:复位连续失败计数
            self._retry_q.clear()
        except OSError as e:
            self._retry_q = deque(retry)          # 旧行原样保留,新取入队:不重不漏
            self._retry_q.extend(take)
            self._fail_streak += 1
            raise_code("PERS-202", n=len(take), why=str(e),
                       advice="行已入重试队列不丢;repair 后恢复")

    async def _flush_batch(self) -> None:
        """内部:攒批写 + 失败重试(摘批先行:并发 append 不阻塞)。"""
        batch, self._pending = self._pending, deque()
        retry = list(self._retry_q)
        to_write = retry + list(batch)
        if not to_write:
            return
        try:
            for _seq, line in to_write:
                self._fh.write(line)
            self._fh.flush()
            self._fail_streak = 0                 # 成功:复位失败计数
            self._retry_q.clear()
        except OSError:
            self._retry_q = deque(retry)
            self._retry_q.extend(batch)           # 拒新不丢旧:全部回重试队列
            self._fail_streak += 1
            if self._fail_streak >= 3 or len(self._retry_q) > _RETRY_Q_LIMIT:
                # 注入回调(不反向 import session);通道坏时由装配层/bus 侧兜底
                await self._on_system_event("system.error", {
                    "code": "PERS-202",
                    "advice": "落盘通道故障,会话暂停;跑 repair(F060)"})
                self._suspended = True            # 暂停会话(拒新不丢旧)
                raise_code("PERS-202", advice="会话暂停,repair 后恢复")
            # 未达阈值:保留重试队列,等下一次 flush 定时器重试

    # ------------------------------------------------------------ 读取(隔离,不中断)
    def replay(self) -> Iterator[Envelope]:
        """行迭代器(轮转文件按序号合并:先 {sid}.1..n 后主文件)。

        空行跳过;坏行(解码失败/JSON 非法/信封校验失败)记 PERS-201 进 _quarantine
        隔离区,继续下一行——回放 = 恢复 = 审计同一路径,绝不因单行损坏中断
        (§3.8)。行号按文件从 1 起(多文件时可能重号,隔离判定以 repair 主文件
        扫描为准)。
        """
        paths = self._rotated_paths() + [self.path]
        for p in paths:
            if not p.exists():
                continue
            with open(p, "rb") as f:
                for no, raw in enumerate(f, 1):
                    line = raw.rstrip(b"\r\n")   # 容忍 \r\n 遗留(Windows 兼容)
                    if not line:
                        continue                 # 空行跳过(轮转残留容忍)
                    try:
                        text = line.decode("utf-8")
                        yield Envelope.model_validate_json(text)   # 信封一次校验
                    except Exception:                            # noqa: BLE001
                        # 解码坏块/JSON 非法/信封校验失败统一记 PERS-201 隔离
                        self._quarantine.add(no)  # 中部坏行隔离(记跳不中断)
                        log.warning("PERS-201 域:坏行隔离(记跳不中断,删否经 repair) "
                                    "file=%s line_no=%s", p, no)

    # ------------------------------------------------------------ 截断检测(只读)
    @staticmethod
    def detect_truncation(path: Path) -> Optional[int]:
        """同模块函数(静态门面,兼容既有调用形态)。"""
        return detect_truncation(path)

    # ------------------------------------------------------------ repair 崩溃恢复(F060)
    async def repair(self, session_id: Optional[str] = None) -> RepairReport:
        """校验 + 修复流水线,幂等(F060):备份 → 截断 → 隔离 → 空洞定位 → recovered。

        ①备份(copy2 → {sid}.jsonl.bak-{ts},修复前强制);②尾部半行原子截断;
        ③编码级坏块(完整行内)→ 原文件 .corrupt-{ts} 保留 + PERS-202 不覆盖;
        ④中部坏行隔离(不自动删);⑤seq 空洞定位(对照 compacted/recovered 声明,
        未声明只告警标记不回填);⑥on_system_event 强同步写 session.recovered
        (fixed 非空才写——载荷模型 fixed min_length=1,空修复不产声明);⑦置
        recovering→normal 恢复写通道。文件不存在 → 空报告(新会话)。
        """
        sid = session_id or self.session_id
        path = self.path
        if not path.exists():
            return RepairReport()                 # 空会话:空报告
        backup = shutil.copy2(path, path.with_name(f"{sid}.jsonl.bak-{now_ts()}"))
        fixed: list[str] = []
        self._quarantine.clear()                  # 隔离区以本次主文件扫描为准(幂等)
        # ② 尾部半行(崩溃未完成的事实不假装发生):原子截断,先备份后动刀
        off = detect_truncation(path)
        if off is not None:
            self._rewrite_without_tail(off)
            fixed.append("tail-truncated")
        # ③ 编码级坏块(截断后仍不可整解码 = 中部坏块):保留原样,明确报错
        try:
            lines = _read_clean(path)
        except UnicodeDecodeError:
            corrupt = path.with_name(f"{sid}.jsonl.corrupt-{now_ts()}")
            try:
                # Windows:持开句柄无法 rename,先关;此后本存储需重新 open_store
                self._fh.close()
                os.replace(path, corrupt)         # 原文件保留为 .corrupt,不覆盖
            except OSError as e:
                raise_code("PERS-202", op="corrupt-rename", why=str(e),
                           advice="原文件保留 .corrupt 失败,查磁盘/权限")
            raise_code("PERS-202",
                       advice="编码级坏块不可修复;原文件保留 .corrupt,不覆盖",
                       backup=str(backup), corrupt=str(corrupt))
        # ④ 中部坏行隔离(不自动删——人类决策,repair 只隔离)
        for no, text in lines:
            try:
                Envelope.model_validate_json(text)
            except Exception:                    # noqa: BLE001
                self._quarantine.add(no)
        if self._quarantine:
            fixed.append(f"quarantined:{sorted(self._quarantine)}")
        # ⑤ seq 空洞定位:有 compacted/recovered 声明 → 合法;未声明 → 标记告警(F031 深查)
        holes = self.seq_holes(path)
        if holes and not self._declared_by_compaction(holes):
            fixed.append(f"seq-holes:{holes}")
        # ⑥ recovered 声明(fixed 非空才写:载荷 fixed min_length=1,空不产事件)
        if fixed:
            await self._on_system_event("session.recovered", {
                "fixed": fixed,
                "quarantined": sorted(self._quarantine),
                "backup": str(backup)})
        else:
            log.info("repair 无修复动作(幂等),skip session.recovered: %s", path)
        # ⑦ recovering→normal:写通道恢复(派生视图整体重建在阶段 4/6 装配)
        self._suspended = False
        return RepairReport(fixed=fixed,
                            quarantined=sorted(self._quarantine),
                            backup_path=backup)

    # ------------------------------------------------------------ 原子截断重写(repair 专用)
    def _rewrite_without_tail(self, cut_offset: int) -> None:
        """把主文件截至 cut_offset 的内容原子重写(临时文件 + fsync + rename)。

        同目录 tmp → fsync → os.replace 原子替换 → 重开追加句柄;rename 原子性
        保证任何时刻磁盘上要么旧完整文件要么新完整文件,杜绝半写。Windows 先关
        句柄再 rename(持开句柄 replace 会 PermissionError),失败不留临时残留。
        """
        tmp = self.path.with_name(self.path.name + f".tmp-{os.getpid()}")
        try:
            self._fh.close()                      # Windows:先关旧句柄再替换
            with open(self.path, "rb") as src, open(tmp, "wb") as dst:
                src.seek(0)
                dst.write(src.read(cut_offset))   # 保留全部完整行(字节级切点)
                dst.flush()
                os.fsync(dst.fileno())            # 数据落盘再换名
            os.replace(tmp, self.path)            # 原子 rename:旧完整或新完整
        except OSError as e:
            raise_code("PERS-202", op="rewrite_without_tail", why=str(e),
                       advice="原子截断失败;备份仍在 .bak-{ts},重跑 repair")
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)       # 失败清理,不留临时残留
            if self._fh.closed:
                # 成败皆重开追加句柄:成功续写;失败也不留坏句柄(异常照常上抛)
                self._fh = open(self.path, "a", encoding="utf-8", newline="\n")

    # ------------------------------------------------------------ 轮转(内部)
    def _rotated_paths(self) -> list[Path]:
        """主目录下 {sid}.{n}.jsonl(n 从 1)按序号升序(重放合并顺序)。"""
        pat = re.compile(r"^" + re.escape(self.session_id) + r"\.(\d+)\.jsonl$")
        found: list[tuple[int, Path]] = []
        try:
            children = list(self.path.parent.iterdir())
        except OSError:
            return []
        for child in children:
            m = pat.match(child.name)
            if m:
                found.append((int(m.group(1)), child))
        return [p for _n, p in sorted(found)]

    def _maybe_rotate(self) -> None:
        """轮转检查(append 写入后调用):>rotate_bytes → _rotate。

        轮转失败只记日志不抛(数据无损:事件已先落主文件;rename 原子性保证主文件
        仍在),下次 append 再触发——避免在 append 成功路径上叠加二次异常。
        """
        try:
            if self._fh.tell() > self.rotate_bytes:
                self._rotate()
        except (OSError, ValueError) as e:
            log.error("PERS-202 域:轮转失败(数据无损,下次重试) why=%s", e)

    def _rotate(self) -> None:
        """主文件 >50MB → 原子 rename 为 {sid}.{n}.jsonl → 开新主句柄。"""
        self._fh.flush()
        n = len(self._rotated_paths()) + 1
        target = self.path.with_name(f"{self.session_id}.{n}.jsonl")
        self._fh.close()                          # Windows:先关句柄再 rename
        try:
            os.replace(self.path, target)         # 原子 rename(同文件系统)
        except OSError:
            self._fh = open(self.path, "a", encoding="utf-8", newline="\n")
            raise                                # 主文件未动,恢复句柄
        self._fh = open(self.path, "a", encoding="utf-8", newline="\n")
        log.info("jsonl rotated: %s (size_mb=%d)",
                 target, self.rotate_bytes // 1_048_576)

    # ------------------------------------------------------------ seq 空洞
    def seq_holes(self, path: Path) -> list[int]:
        """对照连续序列找缺失号(含轮转合并回放;path 参数保留 spec 形态)。

        只找相邻差 >1 的缺口;seq 只前进,空洞只解释不回填(§3.4)。重复/倒退
        (range 空)不入洞——append 保证单调,此处只定位缺失,声明判定在 repair 内。
        """
        seqs = [env.seq for env in self.replay()]     # 复用坏行隔离读取
        holes: list[int] = []
        for expect, got in zip(seqs, seqs[1:]):
            if got != expect + 1:
                holes.extend(range(expect + 1, got))  # 相邻差 >1 = 空洞
        return holes

    def _declared_by_compaction(self, holes: list[int]) -> bool:
        """空洞合法性判定:对照 context.compacted.ranges / session.recovered 声明。

        覆盖来源:compacted.ranges 闭区间、recovered.lost 截断 seq 清单、既往
        recovered.fixed 中 "seq-holes:[…]" 声明(重复 repair 不重复告警)。
        全部被声明覆盖 → 合法空洞(压缩/截断/修复事实),不告警不回填。
        """
        covered: set[int] = set()
        for env in self.replay():
            if env.type == "context.compacted":
                for lo, hi in (env.payload.get("ranges") or []):
                    covered.update(range(lo, hi + 1))
            elif env.type == "session.recovered":
                covered.update(env.payload.get("lost") or [])
                for item in (env.payload.get("fixed") or []):
                    m = re.match(r"^seq-holes:\[(.*)\]$", str(item))
                    if m and m.group(1).strip():
                        covered.update(int(x) for x in m.group(1).split(",")
                                       if x.strip().isdigit())
        return all(h in covered for h in holes)

    # ------------------------------------------------------------ 隔离区查询
    def quarantine_info(self) -> dict:
        """坏行隔离区只读暴露(repair 决策/用户查看):{path, line_nos, count}。"""
        nos = sorted(self._quarantine)
        return {"path": str(self.path), "line_nos": nos, "count": len(nos)}

    # ------------------------------------------------------------ 系统事件上抛(注入回调)
    async def _on_system_event(self, type_: str, payload: dict) -> None:
        """向上写 system.error / session.recovered(INV-08 无回边)。

        回调签名:async cb(type_: str, payload: dict) -> None,由装配层/bus 日志
        订阅者注入;装配层负责打 seq/ts 成信封并 append(sync=True)——本模块不自造
        信封(seq/ts 只能框架分配,EVENT-SCHEMA §1.1-4)。未注入 → 降级日志
        (阶段 2 装配后必有);回调失败不掩盖主路径错误。
        """
        cb = self.on_system_event
        if cb is None:
            log.warning("on_system_event 未注入,type=%s 仅日志留痕(装配层阶段注入)",
                        type_)
            return
        try:
            await cb(type_, payload)
        except Exception as exc:                 # noqa: BLE001 — 不掩盖主错误
            log.error("on_system_event(type=%s) 失败:%s", type_, exc)


# ================================================================= 工厂
def open_store(session_id: str, *, dir: Optional[Path] = None) -> SessionStore:
    """创建/打开会话存储(工厂):目录 mkdir(700) → 主文件 append 句柄。

    文件已存在(重启恢复):由 session.open_session 重放取 _seq 基线;检测到尾部
    半行 → 告警提示先跑 repair(open 不自动截断——修复前强制备份,F060)。
    异常:目录/文件不可写 → PERS-202。
    """
    d = dir or default_sessions_dir()
    try:
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as e:
        raise_code("PERS-202", op="mkdir", path=str(d), why=str(e),
                   advice="查磁盘/权限;repair 后恢复")
    path = d / f"{session_id}.jsonl"              # 会话日志锚点 ~/.pyharness/sessions
    trunc = detect_truncation(path)               # 只读检测:崩溃遗留尾部半行?
    if trunc is not None:
        log.warning("PERS-201 域:尾部半行待 repair(open 不修,先备份再修 F060) "
                    "file=%s offset=%s", path, trunc)
    try:
        # newline="\n":JSONL 统一 \n 行尾,Windows 下禁 \r\n 翻译(半行检测按字节)
        fh = open(path, "a", encoding="utf-8", newline="\n")
    except OSError as e:
        raise_code("PERS-202", op="open", path=str(path), why=str(e),
                   advice="查磁盘/权限;repair 后恢复")
    return SessionStore(session_id=session_id, path=path, fh=fh)


__all__ = [
    "SessionStore", "RepairReport", "open_store", "detect_truncation",
    "default_sessions_dir", "now_ts",
]
