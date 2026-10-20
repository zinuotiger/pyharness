"""An engine-generated connection key is an alias, never relaxed model authority."""
import pytest
from pyharness.config import Settings
from pyharness.engine import register_default_llm


def connection():
    cfg=Settings();cfg.llm.model='public-identity-fixture';cfg.llm.api_key='env:MODEL_IDENTITY_FIXTURE'
    cfg.llm.base_url='http://127.0.0.1:1';cfg.llm.fallback_models=[]
    expected=cfg.llm.model_copy(deep=True);registry={}
    key=register_default_llm(cfg,registry=registry)
    return cfg.llm,expected,registry,key


@pytest.mark.parametrize('caller',['registry','public'])
def test_exact_original_engine_connection_is_admitted_under_both_actual_core_call_names(caller):
    from pyharness.application.execution_model_identity import model_configuration_admission
    actual,expected,registry,key=connection()
    before=actual.model_dump()
    assert model_configuration_admission(actual,expected,registry,key if caller=='registry' else expected.model)==(True,True)
    assert actual.model_dump()==before and registry[key]._transport is None


@pytest.mark.parametrize('change',['endpoint','credential','model','temperature','timeout','unregistered','lookalike','closed'])
def test_alias_does_not_hide_real_connection_or_configuration_changes(change):
    from pyharness.application.execution_model_identity import model_configuration_admission
    actual,expected,registry,key=connection()
    if change=='endpoint':expected.base_url='http://127.0.0.1:2'
    elif change=='credential':expected.api_key='env:ANOTHER_REFERENCE'
    elif change=='model':expected.model='another-model'
    elif change=='temperature':actual.temperature=0.123
    elif change=='timeout':actual.timeout.total_s=181
    elif change=='unregistered':registry.clear()
    elif change=='lookalike':registry['__connection_forged']=registry.pop(key);actual.model='__connection_forged'
    elif change=='closed':registry[key]._closed=True
    assert model_configuration_admission(actual,expected,registry,expected.model)[0] is False


def test_unknown_actual_attempt_and_changed_fallback_order_are_rejected():
    from pyharness.application.execution_model_identity import model_configuration_admission
    actual,expected,registry,key=connection()
    assert model_configuration_admission(actual,expected,registry,'unrelated-model')==(True,False)
    actual.fallback_models=['unregistered-fallback']
    assert model_configuration_admission(actual,expected,registry,expected.model)[0] is False


def test_old_explicit_adapter_exact_configuration_remains_valid():
    from pyharness.application.execution_model_identity import model_configuration_admission
    cfg=Settings().llm
    assert model_configuration_admission(cfg,cfg.model_copy(deep=True),{},cfg.model)==(True,True)
