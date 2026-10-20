"""Packaged local MCP server; Windows Job containment precedes Node/Chrome.

Run this absolute script with an explicit trusted interpreter. It owns only its
temporary carrier, never another browser profile or a remembered foreign PID.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import queue
import sys
import threading
import tempfile
import time
import subprocess

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from pyharness.core.browser_contract import TOOLS, allowed_origin, validate_call
from pyharness.core.mcp import _mcp_env
from pyharness.core.process_owner import ProcessOwner


class BrowserCarrier:
    def __init__(self, *, node, playwright, executable, origins, timeout=15):
        if os.name != 'nt':
            raise ValueError('browser_platform_not_enabled')
        self.options = {}
        for name, value in [('node', node), ('playwright', playwright), ('executable', executable)]:
            path = Path(value)
            if not path.is_absolute() or not path.is_file():
                raise ValueError('browser_dependency_unavailable')
            self.options[name] = str(path.resolve())
        if not 1 <= len(origins) <= 8 or not 1 <= timeout <= 20:
            raise ValueError('invalid_browser_configuration')
        self.options['origins'] = sorted(set(allowed_origin(v) for v in origins))
        self.options['timeout'] = timeout * 1000
        self.timeout = timeout
        self.owner = None
        self.closed = False
        self.seq = 0
        self.output = queue.Queue(maxsize=4)
        self.readers = []
        self.lock = threading.RLock()
        self.private_root = None

    def _read(self, stream, *, responses):
        try:
            while True:
                line = stream.readline(65537)
                if not line:
                    if responses:
                        self.output.put_nowait(None)
                    break
                if len(line) > 65536 or not line.endswith(b'\n'):
                    raise ValueError('browser_protocol_limit')
                if responses:
                    self.output.put_nowait(json.loads(line))
        except BaseException:
            if responses:
                try:
                    self.output.put_nowait(None)
                except queue.Full:
                    pass
        finally:
            stream.close()

    def _start(self):
        if self.owner:
            return
        argv = [self.options['node'], str(Path(__file__).with_name('browser_runtime.cjs')),
                json.dumps(self.options, separators=(',', ':'))]
        self.private_root = tempfile.TemporaryDirectory(prefix='pyharness-browser-')
        env = _mcp_env()
        root = Path(self.private_root.name)
        # Keep the OS user identity unchanged: native IME hooks may outlive the
        # browser and keep their user-directory log open. Chrome's own profile
        # and TEMP remain private. No persistent browser context is used.
        home = Path(env.get('USERPROFILE') or env.get('HOME') or root / 'home')
        for key, relative in [('APPDATA','AppData/Roaming'),('LOCALAPPDATA','AppData/Local')]:
            path = home / relative
            path.mkdir(parents=True, exist_ok=True)
            env[key] = str(path)
        for key, relative in [('TMP','tmp'),('TEMP','tmp')]:
            path = root / relative
            path.mkdir(parents=True, exist_ok=True)
            env[key] = str(path)
        try:
            self.owner = ProcessOwner(argv, cwd=Path(__file__).parent, env=env, input_open=True)
        except BaseException:
            self.close()
            raise
        for stream, responses in [(self.owner.proc.stdout, True), (self.owner.proc.stderr, False)]:
            thread = threading.Thread(target=self._read, args=(stream,), kwargs={'responses': responses},
                                      name='browser-owned-reader', daemon=True)
            thread.start()
            self.readers.append(thread)

    def call(self, name, arguments):
        with self.lock:
            if self.closed:
                raise ValueError('browser_carrier_closed')
            arguments = validate_call(name, arguments)
            if name == 'close':
                self.close()
                return {'closed': True}
            self.seq += 1
            request = {'id': self.seq, 'name': name, 'arguments': arguments}
            try:
                self._start()
                self.owner.proc.stdin.write((json.dumps(request, ensure_ascii=False) + '\n').encode('utf-8'))
                self.owner.proc.stdin.flush()
                result = self.output.get(timeout=self.timeout + 2)
                if type(result) is not dict or result.get('id') != self.seq or set(result) != {'id', 'result'}:
                    raise ValueError('browser_protocol_failed')
                value = result['result']
                if type(value) is not dict:
                    raise ValueError('browser_protocol_failed')
                if value.get('fatal'):
                    self.close()
                return value
            except BaseException:
                self.close()
                raise ValueError('browser_carrier_unconfirmed') from None

    def close(self):
        with self.lock:
            self.closed = True
            if self.owner:
                for stream in (self.owner.proc.stdin,):
                    if stream and not stream.closed:
                        stream.close()
                try:
                    self.owner.wait_exit(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
                finally:
                    self.owner.terminate()
                for reader in self.readers:
                    reader.join(timeout=5)
                    if reader.is_alive():
                        raise RuntimeError('browser_reader_cleanup_failed')
            if self.private_root:
                # TerminateJobObject initiates all exits but waiting for its
                # leader alone does not wait for every descendant's file handles.
                deadline = time.monotonic() + 5
                while True:
                    try:
                        self.private_root.cleanup()
                        self.private_root = None
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise RuntimeError('browser_private_files_cleanup_failed') from None
                        time.sleep(.1)


def serve(carrier, source, output):
    try:
        while True:
            line = source.readline(16385)
            if not line:
                return
            if len(line) > 16384 or not line.endswith(b'\n'):
                return
            message = None
            try:
                message = json.loads(line)
                if type(message) is not dict or message.get('jsonrpc') != '2.0':
                    return
                if 'id' not in message:
                    if message.get('method') == 'notifications/initialized':
                        continue
                    return
                ident = message['id']
                if type(ident) not in (int, str) or len(str(ident)) > 80:
                    return
                method = message.get('method')
                if method == 'initialize':
                    result = {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},
                              'serverInfo': {'name': 'pyharness-controlled-browser', 'version': '1'}}
                elif method == 'tools/list':
                    result = {'tools': TOOLS}
                elif method == 'tools/call':
                    params = message.get('params', {})
                    if type(params) is not dict or set(params) != {'name', 'arguments'}:
                        raise ValueError('invalid_browser_request')
                    value = carrier.call(params['name'], params['arguments'])
                    text = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
                    if len(text) > 1900:
                        raise ValueError('browser_output_limit')
                    result = {'content': [{'type': 'text', 'text': text}], 'isError': bool(value.get('error'))}
                else:
                    raise ValueError('unknown_browser_method')
                envelope = {'jsonrpc': '2.0', 'id': ident, 'result': result}
            except Exception:
                # Protocol/schema errors contain no page text, local path or args.
                envelope = {'jsonrpc': '2.0', 'id': message.get('id') if type(message) is dict else None,
                            'error': {'code': -32000, 'message': 'browser_request_failed'}}
            output.write((json.dumps(envelope, ensure_ascii=True) + '\n').encode('utf-8'))
            output.flush()
    finally:
        carrier.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--node', required=True)
    parser.add_argument('--playwright', required=True)
    parser.add_argument('--executable', required=True)
    parser.add_argument('--origin', action='append', required=True)
    parser.add_argument('--timeout', type=int, default=15)
    args = parser.parse_args()
    carrier = BrowserCarrier(node=args.node, playwright=args.playwright, executable=args.executable,
                             origins=args.origin, timeout=args.timeout)
    serve(carrier, sys.stdin.buffer, sys.stdout.buffer)


if __name__ == '__main__':
    main()
