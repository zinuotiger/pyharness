"""PersistenceRecovery operations, separated from facade assembly."""
from __future__ import annotations
import os
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from pyharness.errors import raise_code
from pyharness.events import declared_ranges
from pyharness.persistence_policy import log
from pyharness.persistence_reader import detect_truncation, RepairReport

class PersistenceRecovery:
    """PersistenceRecovery responsibility; state is owned by the composing facade."""

    @staticmethod
    def detect_truncation(path: Path) -> Optional[int]:
        """同模块函数(静态门面,兼容既有调用形态)。"""
        return detect_truncation(path)



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

        大小取 OS 文件字节数(fstat),不用 `_fh.tell()`——文本模式 tell() 返回的是
        不透明游标(cookie)而非字节偏移,与 rotate_bytes(字节)不可比(P3:_rotate 判定)。
        轮转失败只记日志不抛(数据无损:事件已先落主文件;rename 原子性保证主文件
        仍在),下次 append 再触发——避免在 append 成功路径上叠加二次异常。
        """
        if self._retry_q or self._pending:
            return
        try:
            size = os.fstat(self._fh.fileno()).st_size
            if size > self.rotate_bytes:
                self._rotate()
        except (OSError, ValueError) as e:
            log.error("PERS-202 域:轮转失败(数据无损,下次重试) why=%s", e)


    def seq_holes(self, path: Path) -> list[int]:
        """对照连续序列找缺失号(含轮转合并回放;path 参数保留 spec 形态)。

        只找相邻差 >1 的缺口;seq 只前进,空洞只解释不回填(§3.4)。重复/倒退
        (range 空)不入洞——append 保证单调,此处只定位缺失,声明判定在 repair 内。
        """
        seqs = [env.seq for env in self.replay()]     # 复用坏行隔离读取
        holes: list[int] = []
        if seqs and seqs[0] > 1:
            # 前缀段连续缺失(轮转文件丢失/坏文件):seq 1..(seqs[0]-1) 整体缺失,
            # 旧实现只查相邻差、对首段失明(P1-5,与 repair.check_seq_gap 口径对齐)
            holes.extend(range(1, seqs[0]))
        for expect, got in zip(seqs, seqs[1:]):
            if got != expect + 1:
                holes.extend(range(expect + 1, got))  # 相邻差 >1 = 空洞
        return holes

    def _declared_by_compaction(self, holes: list[int]) -> bool:
        """空洞合法性判定:对照 context.compacted.ranges / session.recovered 声明。

        覆盖来源:compacted.ranges 闭区间、recovered.lost 截断 seq 清单、既往
        recovered.fixed 中 "seq-holes:[…]" 声明(重复 repair 不重复告警)。
        全部被声明覆盖 → 合法空洞(压缩/截断/修复事实),不告警不回填。

        2026-09-21 R14-9:判据**唯一来源** ``events.declared_ranges``(此前本处、
        ``repair``、``session``、``governance.audit`` 各写一份 ⇒ 漂移)。
        """
        covered: set[int] = set()
        for env in self.replay():
            for lo, hi in declared_ranges(env):
                covered.update(range(lo, hi + 1))
        return all(h in covered for h in holes)

    def quarantine_info(self) -> dict:
        """坏行隔离区只读暴露(repair 决策/用户查看):{path, line_nos, count}。"""
        nos = sorted(self._quarantine)
        return {"path": str(self.path), "line_nos": nos, "count": len(nos)}

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

