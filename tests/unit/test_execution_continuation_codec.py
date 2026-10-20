"""Public typed JSON numbers bind across Python/browser decimal spellings."""
import pytest
from pyharness.application.continuation_plan_codec import public_numbers,continuation_plan_digest
from pyharness.errors import PyHError

def test_binary64_protocol_vectors_and_boolean_separation():
    assert public_numbers(0.0)=={'$number':'0000000000000000'}
    assert public_numbers(1.0)==public_numbers(1)=={'$number':'3ff0000000000000'}
    assert public_numbers(-0.0)=={'$number':'8000000000000000'}
    assert public_numbers(True) is True and public_numbers(None) is None
    assert continuation_plan_digest(dict(cost=0.0))==continuation_plan_digest(dict(cost=0))
    assert continuation_plan_digest(dict(cost=1e-7))!=continuation_plan_digest(dict(cost=0.0))

@pytest.mark.parametrize('value',[float('inf'),float('nan'),9007199254740992])
def test_unrepresentable_public_numbers_are_not_silently_rounded(value):
    with pytest.raises(PyHError,match='PERS-202'):public_numbers(value)
