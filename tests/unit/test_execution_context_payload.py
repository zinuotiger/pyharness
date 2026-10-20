"""A new observation type must preserve the old vocabulary and reject raw fields."""
import copy
import pytest
from pyharness.events import EVENT_TYPES,SYNC_TYPES
from pyharness.events.vocab import validate_payload
from pyharness.errors import PyHError
from pyharness.application.workspace_store import canonical,digest_of
from pyharness.core.context_observation import public_request


def payload():
    from pyharness.events.payload_context import PublicRequestView
    view=PublicRequestView.model_validate(public_request(dict(model='synthetic',messages=[dict(role='user',content='Public')]),layer='adapter_input')).model_dump()
    return dict(schema_version=1,observation_id='co-'+'1'*16,execution_id='ex-'+'1'*16,
        authorization_digest='a'*64,authorization_seq=2,prefix_seq=5,phase='prepared',layer='adapter_input',
        model='synthetic',model_digest=digest_of(canonical('synthetic')),method='chat',role='other',attempt=1,
        request_seq=None,checkpoint_ref=None,messages_digest='b'*64,public_view_digest=digest_of(canonical(view)),public_view=view,
        derivation=dict(history_window=dict(available=False),system_window=dict(available=False),final_messages=1),
        source_content_state='not_confirmed',execution_authorized=False)


def test_registered_observation_is_a_new_independent_model_and_old_sync_types_stay_unchanged():
    value=payload();result=validate_payload('execution.context',value)
    assert result['public_view']==value['public_view'] and result['phase']=='prepared'
    assert len(EVENT_TYPES)==90 and len(SYNC_TYPES)==22 and 'execution.context' not in SYNC_TYPES
    assert not result['execution_authorized']


@pytest.mark.parametrize('field',['headers','reasoning_content','raw_response'])
def test_private_extra_fields_are_rejected_inside_public_messages(field):
    value=payload();value['public_view']['messages'][0][field]='Private data'
    with pytest.raises(PyHError) as error:validate_payload('execution.context',value)
    assert error.value.code=='EVT-100'


def test_modified_public_copy_cannot_keep_the_old_observation_digest():
    value=payload();value['public_view']['messages'][0]['content']='Altered public copy'
    with pytest.raises(PyHError) as error:validate_payload('execution.context',value)
    assert error.value.code=='EVT-100'


def test_main_request_requires_its_actual_checkpoint_reference():
    value=payload();value['role']='main_agent'
    with pytest.raises(PyHError) as error:validate_payload('execution.context',value)
    assert error.value.code=='EVT-100'


@pytest.mark.parametrize('patch',[dict(schema_version=True),dict(attempt=True),dict(request_seq=5),dict(execution_authorized=True)])
def test_observation_cannot_forge_version_attempt_transport_identity_or_execution_permission(patch):
    value=payload();value.update(copy.deepcopy(patch))
    with pytest.raises(PyHError) as error:validate_payload('execution.context',value)
    assert error.value.code=='EVT-100'
