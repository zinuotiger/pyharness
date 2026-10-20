"""Real delayed navigation must finish readback without replaying the action."""
import os
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from pyharness.core.browser_mcp import BrowserCarrier

pytestmark = [pytest.mark.controlled_process,
              pytest.mark.skipif(os.name != 'nt', reason='Windows Job browser carrier only')]


@contextmanager
def navigation_page(mode, *, blocked=False):
    effects = []
    release = threading.Event()
    visits = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            visits.append(self.path)
            receipt = self.path == '/receipt' or (mode == 'same-url' and visits.count('/') == 2)
            if self.path == '/redirect':
                self.send_response(302)
                self.send_header('Location', '/receipt')
                self.end_headers()
                return
            if receipt:
                effects.append('accepted')
                if blocked:
                    release.wait(8)
                else:
                    time.sleep(.3)
                body = '<body>DELAYED EXACT READBACK</body>'
            elif mode == 'same-url':
                body = '<body><button onclick="location.reload()">Exact target</button></body>'
            elif mode == 'fill':
                body = '<body><input name="exact" onchange="location.href=\'/receipt\'"></body>'
            elif mode == 'no-navigation':
                body = '<body><button onclick="this.textContent=\'UPDATED EXACT READBACK\'">Exact target</button></body>'
            else:
                route = '/redirect' if mode == 'redirect' else '/receipt'
                body = '<body><button onclick="location.href=\'' + route + '\'">Exact target</button></body>'
            try:
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(body.encode('utf-8'))
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield 'http://127.0.0.1:' + str(server.server_port), effects, release
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(3)


def browser(origin, *, timeout=10):
    from tests.browser_dependencies import browser_dependencies
    node, package, executable = browser_dependencies()
    return BrowserCarrier(node=str(node), playwright=str(package), executable=str(executable),
                          origins=[origin], timeout=timeout)


def exact_action(observed, mode):
    row = observed['targets'][0]
    value = dict(session=observed['session'], generation=observed['generation'],
                 target=row['target'], label=row['label'], action='click')
    if mode == 'fill':
        value.update(action='fill', text='approved local value')
    return value


@pytest.mark.parametrize('mode', ['click', 'same-url', 'redirect', 'fill'])
def test_delayed_navigation_reads_new_document_once(mode):
    with navigation_page(mode) as (origin, effects, _):
        carrier = browser(origin)
        try:
            observed = carrier.call('open', {'url': origin})
            args = exact_action(observed, mode)
            result = carrier.call('act', args)
            assert not result.get('error'), result
            assert result['readback']['text'] == 'DELAYED EXACT READBACK'
            assert result['readback']['document'] != observed['document']
            assert effects == ['accepted']
            assert carrier.call('act', args)['error'] == 'browser_target_stale'
            assert effects == ['accepted']
        finally:
            carrier.close()


def test_non_navigation_dom_action_remains_bounded_and_consumed():
    with navigation_page('no-navigation') as (origin, effects, _):
        carrier = browser(origin)
        try:
            observed = carrier.call('open', {'url': origin})
            args = exact_action(observed, 'click')
            result = carrier.call('act', args)
            assert result['readback']['text'] == 'UPDATED EXACT READBACK'
            assert result['readback']['document'] == observed['document']
            assert effects == []
            assert carrier.call('act', args)['error'] == 'browser_target_stale'
        finally:
            carrier.close()


def test_unconfirmed_delayed_navigation_retires_carrier_without_replay():
    with navigation_page('click', blocked=True) as (origin, effects, release):
        carrier = browser(origin, timeout=3)
        try:
            observed = carrier.call('open', {'url': origin})
            result = carrier.call('act', exact_action(observed, 'click'))
            release.set()
            assert result['fatal'] and result['error'] == 'browser_carrier_unconfirmed'
            assert carrier.closed and carrier.owner.poll() is not None
            assert effects == ['accepted']
            with pytest.raises(ValueError, match='closed'):
                carrier.call('observe', {})
            assert effects == ['accepted']
        finally:
            release.set()
            carrier.close()
