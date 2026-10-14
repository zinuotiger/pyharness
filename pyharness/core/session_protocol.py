"""Session protocol: complete migrated implementation."""
from __future__ import annotations
from typing import Iterator, Protocol, TYPE_CHECKING
from pyharness.events import Envelope
if TYPE_CHECKING:  # 总线仅作类型标注;构造未接线时跳过分发(不硬依赖装配)
    pass


class SessionStoreProtocol(Protocol):
    """persistence.SessionStore 最小契约(注入用;core/persistence 后续实现)。

    session → persistence 单向依赖(INV-08):只读 replay + 强同步 flush;
    不反向 import persistence 模块,避免阶段装配期硬依赖。
    """

    def replay(self) -> Iterator[Envelope]:
        """严格行解析回放,坏行记 PERS-201 跳隔离,不中断(§3.8/§5.3)。"""
        ...

    async def flush(self, seq: int) -> None:
        """强同步落盘:立即 write+flush,成功才返回;失败抛 PERS-202。"""
        ...

# Preserve historical import and pickle identities through the compatibility facade.
SessionStoreProtocol.__module__ = 'pyharness.core.session'
