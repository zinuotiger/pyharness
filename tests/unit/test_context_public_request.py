"""Public request facts must be bounded, private-field-free and non-mutating."""
import copy,json,subprocess,sys
from pathlib import Path
import pytest


def capture(value,**kwargs):
    from pyharness.core.context_observation import public_request
    return public_request(value,layer='transport_payload',**kwargs)


def request(content='Actual selected context'):
    return dict(model='synthetic-model',messages=[dict(role='user',content=content)],tools=None,max_tokens=16,temperature=0,stream=False)


def test_actual_public_message_and_tool_schema_are_inspectable_without_changing_wire_body():
    value=request();value['tools']=[dict(type='function',function=dict(name='file_read',description='Read selected file',parameters=dict(type='object',properties=dict(path=dict(type='string')),required=['path'])))]
    before=copy.deepcopy(value);result=capture(value)
    assert result['messages'][0]['content']=='Actual selected context'
    assert result['tools'][0]['function']['name']=='file_read'
    assert result['tools'][0]['function']['parameters']['properties']['path']['type']=='string'
    assert result['layer']=='transport_payload' and result['counts']['messages']==1
    assert not result['truncated'] and not result['withheld']
    assert value==before


@pytest.mark.parametrize('content',[
    'API_KEY=short-private-value',
    'password: short-private-value',
    'Authorization: Bearer short-private-value',
    '{"access_token":"short-private-value"}',
    'my key sk-abcdefghijklmnopqrstuvwxyzdontpublish',
    'github_pat_abcdefghijklmnopqrstuvwxyz1234567890',
    ('-----BEGIN ' + 'PRIVATE KEY----- short-private-value'),
    '<think>short-private-value</think> public text',
    '<analysis>short-private-value',
    'reasoning_content: short-private-value',
])
def test_recognized_secrets_and_hidden_reasoning_never_enter_public_fact(content):
    result=capture(request(content));text=json.dumps(result)
    assert 'short-private-value' not in text and 'abcdefghijklmnopqrstuvwxyzdontpublish' not in text
    assert 'abcdefghijklmnopqrstuvwxyz1234567890' not in text
    assert result['withheld'] is True


def test_current_transport_credential_is_removed_even_when_short_and_without_a_key_prefix():
    result=capture(request('Report contains a tinycredential for the controlled test'),known_secrets=('tinycredential',))
    assert 'tinycredential' not in json.dumps(result) and result['withheld'] is True


def test_only_public_request_fields_survive_even_when_private_reasoning_headers_and_responses_are_present():
    value=request();value.update(headers=dict(Authorization='Bearer opaque-private-header'),reasoning_content='opaque-hidden-chain',raw_response='opaque-provider-body')
    value['messages'][0].update(reasoning='opaque-hidden-message',analysis='opaque-hidden-analysis')
    result=capture(value);text=json.dumps(result)
    assert not any(s in text for s in ['opaque-private-header','opaque-hidden-chain','opaque-provider-body','opaque-hidden-message','opaque-hidden-analysis'])
    assert result['messages'][0]['content']=='Actual selected context'


def test_nested_tool_arguments_redact_values_without_losing_normal_arguments():
    value=request();value['messages']=[dict(role='assistant',content='Public action',tool_calls=[dict(id='call-1',type='function',function=dict(name='lookup',arguments=json.dumps(dict(query='public query',password='short-private-value',nested=dict(access_token='nested-private-value')))))])]
    result=capture(value);text=json.dumps(result)
    assert 'short-private-value' not in text and 'nested-private-value' not in text
    assert 'public query' in text and result['withheld'] is True


def test_images_only_leave_type_counts_and_never_image_urls_or_base64():
    value=request();value['messages'][0]['content']=[dict(type='text',text='Public image question'),dict(type='image_url',image_url=dict(url='data:image/png;base64,private-image-bytes'))]
    result=capture(value);text=json.dumps(result)
    assert 'Public image question' in text and 'private-image-bytes' not in text and 'data:image' not in text
    assert result['counts']['images']==1 and result['withheld'] is True


def test_redaction_precedes_display_clipping():
    result=capture(request('safe words '*1000+' password=short-private-value'))
    assert result['withheld'] is True and 'short-private-value' not in json.dumps(result)


def test_large_multibyte_request_is_bounded_and_explicit_about_omissions():
    value=request();value['messages']=[dict(role='user',content='汉'*6000) for _ in range(1000)]
    result=capture(value)
    assert len(json.dumps(result,ensure_ascii=False).encode('utf-8'))<=49152
    assert result['counts']['messages']==1000 and len(result['messages'])<1000 and result['truncated'] is True


def test_unsupported_content_never_uses_a_private_object_repr_or_string_fallback():
    class Private:
        def __str__(self):raise AssertionError('Do not stringify an arbitrary private object')
        def __repr__(self):raise AssertionError('Do not repr an arbitrary private object')
    result=capture(request(Private()))
    assert result['withheld'] is True and 'withheld' in result['messages'][0]['content']


def test_malformed_tool_arguments_are_withheld_instead_of_echoed_as_raw_json():
    value=request();value['messages']=[dict(role='assistant',content='Public',tool_calls=[dict(id='call-1',type='function',function=dict(name='lookup',arguments='{"query": "private-unterminated-value'))])]
    result=capture(value)
    assert 'private-unterminated-value' not in json.dumps(result) and result['withheld'] is True


@pytest.mark.controlled_process
def test_projection_does_not_allocate_all_entries_of_an_oversized_schema_mapping():
    # Isolate tracemalloc so this test cannot reset another consumer's tracer.
    code='''import json,tracemalloc,sys
sys.path.insert(0,sys.argv[1])
from pyharness.core.context_observation import public_request
properties={"field-"+str(n):"public" for n in range(200000)}
value=dict(model="synthetic",messages=[],tools=[dict(type="function",function=dict(name="lookup",parameters=properties))])
tracemalloc.start()
result=public_request(value,layer="transport_payload")
peak=tracemalloc.get_traced_memory()[1]
assert result["truncated"] is True
assert peak<4*1024*1024,peak
print(json.dumps(dict(peak_bytes=peak)))
'''
    child=subprocess.run([sys.executable,'-I','-B','-c',code,str(Path(__file__).resolve().parents[2])],capture_output=True,text=True,timeout=20)
    assert child.returncode==0,child.stderr


def test_unrepresentable_unicode_is_withheld_and_never_breaks_public_json_serialization():
    result=capture(request('unpaired-surrogate-\ud800-private'))
    assert result['withheld'] is True
    json.dumps(result,ensure_ascii=False).encode('utf-8')


@pytest.mark.parametrize('shape',['message','content','part','role'])
def test_uninspectable_message_content_never_claims_an_exact_zero_image_count(shape):
    value=request()
    if shape=='message':value['messages']=[object()]
    elif shape=='content':value['messages'][0]['content']=object()
    elif shape=='part':value['messages'][0]['content']=[object()]
    else:value['messages']=[dict(role='unsupported-role',content=[dict(type='image_url',image_url={'url':'private-image'})])]
    result=capture(value)
    assert result['withheld'] and result['counts']['images_exact'] is False


def test_old_log_redaction_switch_cannot_disable_the_public_projection_policy():
    value=request('password=short-private-value');value['redact_enabled']=False
    assert 'short-private-value' not in json.dumps(capture(value))


def test_nested_schema_processing_has_a_total_node_budget_and_marks_withheld_work():
    schema='public'
    for _ in range(3):schema={f'field-{n}':schema for n in range(16)}
    value=request();value['tools']=[dict(type='function',function=dict(name='lookup',parameters=schema))]
    result=capture(value)
    assert result['truncated'] is True and result['withheld'] is True
    assert len(json.dumps(result,ensure_ascii=False).encode('utf-8'))<=49152


def test_string_analysis_budget_is_shared_across_the_whole_request():
    value=request();content='public words '*4000+' password=short-private-value'
    value['messages']=[dict(role='user',content=content) for _ in range(64)]
    result=capture(value)
    assert result['withheld'] is True and result['truncated'] is True
    assert 'short-private-value' not in json.dumps(result)


def test_normal_fixed_task_context_remains_readable_when_opaque_digests_are_hidden():
    # Real product inputs contain long immutable reference digests. Hiding an
    # opaque token must not hide the entire otherwise public task objective.
    content=json.dumps(dict(task=dict(objective='Public task objective'),context_digest='a'*64))
    result=capture(request(content));public=result['messages'][0]['content']
    assert 'Public task objective' in public and 'a'*64 not in public
    assert result['withheld'] is True


@pytest.mark.parametrize('name',['token','cookie','set-cookie','passphrase'])
def test_common_short_session_credentials_are_removed_in_text_and_nested_arguments(name):
    value=request(f'{name}: short-session-material')
    value['messages'].append(dict(role='assistant',content='Public action',tool_calls=[dict(id='call-1',type='function',
        function=dict(name='lookup',arguments=json.dumps({name:'short-session-material','query':'public query'})))]))
    result=capture(value);text=json.dumps(result)
    assert 'short-session-material' not in text and 'public query' in text and result['withheld']
