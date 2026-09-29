"""Persistent loopback Web service. Closing a browser never owns server shutdown."""
from __future__ import annotations
import argparse
import asyncio
from copy import deepcopy
from pathlib import Path
import socket
import signal


def settings_for(root: Path):
    from pyharness.config import DEFAULTS, Settings
    root = root.resolve()
    data = deepcopy(DEFAULTS)
    data['storage'].update(root=str(root), sessions_dir=str(root/'sessions'),
        workspaces_dir=str(root/'workspaces'), spill_dir=str(root/'spill'), db_path=str(root/'index.db'))
    data['plugins'].update(dir=str(root/'plugins'), enabled=[], mcp_servers=[])
    data['skills']['dir'] = str(root/'skills')
    data['security']['credentials']['file'] = str(root/'credentials.yaml')
    # Only explicitly saved profiles can resolve credentials. Do not inherit
    # shell environment, the operator's global config or a default provider.
    data['llm'].update(model='unconfigured',base_url='http://127.0.0.1:1',
        api_key='tenant:default:unconfigured',fallback_models=[])
    data['llm']['retry']['attempts'] = 0
    return Settings.model_validate(data)


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--data-dir',required=True,type=Path)
    result.add_argument('--port',type=int,default=0)
    result.add_argument('--open-browser',action='store_true')
    return result


async def serve(args):
    import uvicorn
    from pyharness import cli
    from pyharness.desktop.app import DesktopApp
    app = DesktopApp(cli.assemble_ctx(settings_for(args.data_dir)))
    sock = socket.socket()
    try:
        sock.bind(('127.0.0.1',args.port))
        port = sock.getsockname()[1]
        app.publish_operator_token()
        class Server(uvicorn.Server):
            async def startup(self, sockets=None):
                await super().startup(sockets=sockets)
                if self.started:
                    print(f'PyHarness Web: http://127.0.0.1:{port}/login',flush=True)
                    if args.open_browser:
                        import webbrowser
                        webbrowser.open(app.bootstrap_url('127.0.0.1',port))
        server = Server(uvicorn.Config(app.api,host='127.0.0.1',port=port,
            access_log=False,log_level='warning'))
        await server.serve(sockets=[sock])
    finally:
        sock.close()
        for service in app._service_registry.all():
            await service.shutdown()


def main(argv=None):
    arguments = parser()
    args = arguments.parse_args(argv)
    if not 0 <= args.port <= 65535:
        arguments.error('--port must be between 0 and 65535')
    # Uvicorn re-raises the original signal after closing HTTP connections.
    # Default SIGTERM/SIGBREAK would terminate Python before serve's finally
    # can close the owned queues/services. Convert only these graceful-stop
    # signals to the same unwinding path as Ctrl+C, and restore caller state.
    previous = {}
    try:
        for name in ('SIGTERM', 'SIGBREAK'):
            if hasattr(signal, name):
                sig = getattr(signal, name)
                previous[sig] = signal.signal(sig, signal.default_int_handler)
        asyncio.run(serve(args))
    except KeyboardInterrupt:
        pass
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
