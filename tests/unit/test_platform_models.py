"""Platform contracts: invalid input must fail before side effects."""
import pytest
from pydantic import ValidationError

from pyharness.application.platform_models import AgentDefinition, SandboxProfile, Run


def test_defaults_are_fail_closed():
    assert AgentDefinition(name='assistant').sandbox_profile.mode == 'disabled'
    assert SandboxProfile(mode='isolated').network == 'none'


@pytest.mark.parametrize('value', ['automatic', 'docker', '', None])
def test_unknown_execution_modes_rejected(value):
    with pytest.raises(ValidationError):
        SandboxProfile(mode=value)


def test_network_allowlist_not_silently_accepted():
    with pytest.raises(ValidationError):
        SandboxProfile(network='allowlist')


def test_invalid_run_status_rejected():
    with pytest.raises(ValidationError):
        Run(run_id='r', session_id='s', status='success')


@pytest.mark.parametrize('field,value', [('max_rounds', 0), ('timeout', 10000)])
def test_agent_limits_bounded(field, value):
    with pytest.raises(ValidationError):
        AgentDefinition(name='a', **{field: value})
