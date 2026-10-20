"""Operator-only metadata: original managed MCP remains the configuration owner."""
from pathlib import Path
import sys
import uuid
from pydantic import BaseModel, ConfigDict, Field
from pyharness.config import McpServerCfg
from pyharness.core.browser_mcp import BrowserCarrier
from pyharness.core.browser_contract import TOOLS


class BrowserSetup(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    node: str = Field(min_length=1, max_length=500)
    playwright: str = Field(min_length=1, max_length=500)
    executable: str = Field(min_length=1, max_length=500)
    origins: list[str] = Field(min_length=1, max_length=8)


def browser_configuration(body):
    setup = BrowserSetup.model_validate(body)
    carrier = BrowserCarrier(**setup.model_dump())
    try:
        # The real interpreter is the direct retained MCP child: a Windows venv
        # redirector could exit without killing the server that owns the Job.
        library = str(Path(sys.prefix) / 'Lib/site-packages')
        bootstrap = ('import runpy,sys;sys.path.insert(0,' + repr(library) +
                     ');p=sys.argv.pop(1);runpy.run_path(p,run_name="__main__")')
        from pyharness.core import browser_mcp
        command = [sys._base_executable, '-I', '-B', '-X', 'utf8', '-c', bootstrap,
                   str(Path(browser_mcp.__file__).resolve()),
                   '--node', carrier.options['node'], '--playwright', carrier.options['playwright'],
                   '--executable', carrier.options['executable']]
        for origin in carrier.options['origins']:
            command += ['--origin', origin]
        config = McpServerCfg(name='browser_' + uuid.uuid4().hex[:12], command=command,
                             enabled=False, allowed_tools=[row['name'] for row in TOOLS],
                             timeout_s=30).model_dump()
        return {'connection': {'type': 'mcp', 'config': config}, 'status': 'configuration_only',
                'process_started': False, 'execution_authorized': False,
                'supported_platform': 'Windows', 'action_input': 'DOM click/fill; no physical input',
                'required_path': 'original managed MCP → published host_approved Agent → formal task → Guard approval → effect receipt'}
    finally:
        carrier.close()
