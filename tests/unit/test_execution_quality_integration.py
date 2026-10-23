from types import SimpleNamespace as NS
import pytest
from tests.unit.test_product_execution_start import running_product,wait_terminal
from tests.unit.test_source_grants import PID
from pyharness.errors import PyHError


async def test_truncated_real_loop_receipt_preserves_draft_and_fails_readiness(running_product):
    env=running_product;projects=env.app.service.projects
    async def truncate(ctx,response):
        response.finish_reason='length'
        event=await ctx.session.append('llm.response',dict(model=response.model,
            finish_reason='length',content=response.content),actor='llm')
        response.seq=event.seq
    env.controls.after_chat=truncate
    preview=await projects.execution_preview(PID,env.selection)
    command=dict(env.selection,execution_id='ex-'+'6'*16,confirmation_digest=preview['confirmation_digest'],confirm_host_boundary=False)
    await projects.start_execution(PID,command,'length-repro')
    result=await wait_terminal(projects,command['execution_id'])
    assert result['status']=='failed' and result['output_completeness']=='incomplete'
    assert result['result_summary']=='done' and result['result_truncated'] is False
    assert result['delivery_readiness']=='needs_review'
    from pyharness.application.execution_quality import require_package_quality
    row=projects.store._grant_state()[0][command['execution_id']]
    with pytest.raises(PyHError) as caught:require_package_quality(env.app.service,row)
    assert caught.value.ctx['reason']=='delivery_quality_not_ready'
    outputs=await projects.delivery_outputs(PID,command['execution_id'])
    assert outputs['outputs'][0]['delivery_label']=='待复核材料，不能作为合格交付'
