"""probe_multisession.py — 验证桌面多会话不串文件。"""
import asyncio, os, sys, tempfile
from pathlib import Path
sys.path.insert(0, r'C:/Users/LENOVO/Desktop/mini-harness')
from pyharness.bus import EventBus
from pyharness.core.session import SessionLog, open_session
from pyharness.core.approval import ApprovalProvider
from pyharness.persistence import open_store
from pyharness.desktop import DesktopSessionManager


async def main():
    tmp = Path(tempfile.mkdtemp(prefix='ph-multi-'))
    bus = EventBus()
    mgr = DesktopSessionManager(dir=tmp, bus=bus)
    s1 = await mgr.create()      # async create?看签名——DesktopSessionManager.create 是 async
    s2 = await mgr.create()
    print(f'会话1={s1} 会话2={s2}')

    # 各会话写消息
    log1 = await mgr.open_session(s1)
    log2 = await mgr.open_session(s2)
    # open_session 后已 attach persistence(create 时已 attach)
    await log1.append('user.message', {'content': 'hello one'}, actor='user', sync=True)
    await log2.append('user.message', {'content': 'hello two'}, actor='user', sync=True)
    await log1.append('user.message', {'content': 'hello one again'}, actor='user', sync=True)

    # 关 flush
    for sid, store in mgr._stores.items():
        await store.flush()

    # 检查文件内容
    for sid in (s1, s2):
        p = tmp / f'{sid}.jsonl'
        import json
        types = []
        for line in p.read_text(encoding='utf-8').splitlines():
            e = json.loads(line)
            assert e['session_id'] == sid, f'{sid} 文件混入 {e["session_id"]} 事件!'
            types.append(e['type'])
        print(f'{sid}: {len(types)} 事件 全为自身 sid ✅ {types}')


if __name__ == '__main__':
    asyncio.run(main())
