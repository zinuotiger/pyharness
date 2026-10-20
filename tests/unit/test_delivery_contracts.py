"""Delivery commands identify fixed evidence, never grant their own authority."""
import pytest
from pydantic import ValidationError


def output(kind='artifact'):
    return dict(kind=kind,execution_id='ex-'+'1'*16,session_id='s-pa-'+'2'*32,
                task_id='p-'+'3'*32,source_seq=12,record_digest='a'*64,
                sha256='b'*64,size_bytes=42,
                **({'artifact_id':'f-'+'4'*32} if kind=='artifact' else {}))


def reference(key,prefix):
    return {key:prefix+'-'+'5'*16,'revision':1,'digest':'c'*64,'source_seq':10}


def package():
    return dict(schema_version=1,package_id='dp-'+'6'*16,project_revision=1,
                execution_ref=reference('execution_id','ex'),delivery_ref=reference('delivery_id','del'),
                bindings=[dict(item_id='report',outputs=[output()])])


def test_package_binds_exact_actual_output_without_client_actor_or_verdict():
    from pyharness.application.delivery_contracts import DeliveryPackageCommand
    command=DeliveryPackageCommand.model_validate(package())
    assert command.bindings[0].outputs[0].size_bytes==42
    assert not {'created_by','accepted','tenant_id','budget'} & DeliveryPackageCommand.model_fields.keys()


@pytest.mark.parametrize('change',['actor','verdict','boolean','duplicate-item','duplicate-output','empty','path','message-artifact','empty-message','large'])
def test_package_rejects_forgery_or_ambiguous_output(change):
    from pyharness.application.delivery_contracts import DeliveryPackageCommand
    data=package(); entry=data['bindings'][0]['outputs'][0]
    if change=='actor':data['created_by']='administrator'
    if change=='verdict':data['accepted']=True
    if change=='boolean':data['schema_version']=True
    if change=='duplicate-item':data['bindings']*=2
    if change=='duplicate-output':data['bindings'][0]['outputs']*=2
    if change=='empty':data['bindings']=[]
    if change=='path':entry['session_id']='../../other'
    if change=='message-artifact':entry['kind']='message'
    if change=='empty-message':data['bindings'][0]['outputs']=[dict(output('message'),size_bytes=0)]
    if change=='large':entry['size_bytes']=32*1024*1024+1
    with pytest.raises(ValidationError):DeliveryPackageCommand.model_validate(data)


def role(kind='reviewer'):
    data=dict(schema_version=1,role_id='rc-'+'7'*16,expected_revision=0,project_revision=1,
              title='Review',parent_execution_ref=reference('execution_id','ex'),
              recipient_username='reviewer',role=kind)
    if kind=='consumer':data['consumer_agent_ref']=dict(agent_id='a-'+'8'*32,version=1,sha256='d'*64)
    return data


def test_role_does_not_create_execution_or_budget_authority():
    from pyharness.application.delivery_contracts import RoleContractCommand
    reviewer=RoleContractCommand.model_validate(role())
    consumer=RoleContractCommand.model_validate(role('consumer'))
    assert reviewer.consumer_agent_ref is None and consumer.consumer_agent_ref is not None
    for mutation in [dict(created_by='owner'),dict(role='reviewer',consumer_agent_ref=consumer.consumer_agent_ref.model_dump()),dict(role='consumer'),dict(max_context_bytes=98305),dict(budget={'cost':100})]:
        with pytest.raises(ValidationError):RoleContractCommand.model_validate(dict(role(),**mutation))


def test_handoff_and_ack_only_bind_exact_refs():
    from pyharness.application.delivery_contracts import HandoffCommand,HandoffAckCommand
    command=HandoffCommand(schema_version=1,handoff_id='ho-'+'9'*16,
        role_ref=reference('role_id','rc'),package_ref=reference('package_id','dp'),
        outputs=[output('message')],minimum_context='Read the selected final result.')
    ack=HandoffAckCommand(schema_version=1,ack_id='ha-'+'a'*16,handoff_ref=reference('handoff_id','ho'))
    assert command.minimum_context and ack.handoff_ref.revision==1
    with pytest.raises(ValidationError):HandoffCommand.model_validate(dict(command.model_dump(),execution_authorized=True))


def test_acceptance_requires_explicit_unique_criterion_decisions():
    from pyharness.application.delivery_contracts import DeliveryAcceptanceCommand
    data=dict(schema_version=1,acceptance_id='av-'+'b'*16,handoff_ref=reference('handoff_id','ho'),
        package_ref=reference('package_id','dp'),decisions=[dict(item_id='report',criterion_index=0,verdict='unknown',note='Need to inspect')])
    assert DeliveryAcceptanceCommand.model_validate(data).decisions[0].verdict=='unknown'
    for changed in [dict(decisions=data['decisions']*2),dict(accepted=True),dict(decisions=[dict(data['decisions'][0],criterion_index=True)])]:
        with pytest.raises(ValidationError):DeliveryAcceptanceCommand.model_validate(dict(data,**changed))


def test_delivery_export_requires_acceptance_and_review_export_is_distinct():
    from pyharness.application.delivery_contracts import DeliveryExportCommand
    data=dict(schema_version=1,export_id='xp-'+'c'*16,purpose='review',
        handoff_ref=reference('handoff_id','ho'),package_ref=reference('package_id','dp'),outputs=[output()])
    assert DeliveryExportCommand.model_validate(data).acceptance_ref is None
    with pytest.raises(ValidationError):DeliveryExportCommand.model_validate(dict(data,purpose='delivery'))
    accepted=dict(data,purpose='delivery',acceptance_ref=reference('acceptance_id','av'))
    assert DeliveryExportCommand.model_validate(accepted).purpose=='delivery'
    with pytest.raises(ValidationError):DeliveryExportCommand.model_validate(dict(accepted,purpose='review'))


def test_fixed_reference_rejects_revision_change_and_extra_authority():
    from pyharness.application.delivery_contracts import DeliveryPackageReference,RoleContractReference
    data=reference('package_id','dp')
    with pytest.raises(ValidationError):DeliveryPackageReference.model_validate(dict(data,revision=2))
    assert RoleContractReference.model_validate(reference('role_id','rc')).revision==1
    with pytest.raises(ValidationError):RoleContractReference.model_validate(dict(reference('role_id','rc'),revision=201))
