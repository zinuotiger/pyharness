"""Read-only real CLI handshake; never starts a model turn or sends a message."""
import asyncio
import json
from pyharness.integrations.codex_transport import CodexTransport


async def main():
    transport = CodexTransport(timeout=15)
    try:
        init = await transport.connect()
        models = await transport.request('model/list', {'limit':20})
        account = await transport.request('account/read', {})
        print(json.dumps({'initialize':bool(init), 'model_count':len(models.get('data',[])),
                          'account_present':bool(account.get('account'))}, ensure_ascii=False))
    finally:
        await transport.close()
        print(json.dumps({'process_closed':transport.process is None or transport.process.returncode is not None}))


if __name__ == '__main__':
    asyncio.run(main())
