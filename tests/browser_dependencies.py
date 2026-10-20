"""Declared browser driver and an installed browser; never download a browser."""
import os
from pathlib import Path
import shutil


def browser_dependencies():
    import playwright

    driver = Path(playwright.__file__).resolve().parent / 'driver'
    node = Path(os.environ.get('PYHARNESS_TEST_BROWSER_NODE', str(driver / ('node.exe' if os.name == 'nt' else 'node'))))
    package = Path(os.environ.get('PYHARNESS_TEST_PLAYWRIGHT_INDEX', str(driver / 'package/index.js')))
    explicit = os.environ.get('PYHARNESS_TEST_BROWSER_EXECUTABLE')
    candidates = [Path(explicit)] if explicit else []
    if not explicit:
        for root in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA'):
            if os.environ.get(root):
                candidates.extend(Path(os.environ[root]) / suffix for suffix in (
                    'Google/Chrome/Application/chrome.exe', 'Microsoft/Edge/Application/msedge.exe'))
        candidates.extend(Path(path) for name in ('google-chrome', 'chromium', 'chromium-browser', 'msedge')
                          if (path := shutil.which(name)))
    browser = next((path for path in candidates if path.is_absolute() and path.is_file()), None)
    if not node.is_absolute() or not node.is_file() or not package.is_absolute() or not package.is_file():
        raise RuntimeError('Declared Playwright driver is unavailable; install the frozen dev dependencies')
    if browser is None:
        raise RuntimeError('Chrome/Edge is required; set PYHARNESS_TEST_BROWSER_EXECUTABLE to an installed browser')
    return node, package, browser
