"""Bind typed public numeric values identically in Python and browser binary64."""
import math,struct
from pyharness.application.workspace_store import canonical,digest_of
from pyharness.errors import raise_code


def public_numbers(value):
    if type(value) in (int,float):
        if (type(value) is int and abs(value)>9007199254740991) or not math.isfinite(value):
            raise_code('PERS-202',reason='continuation_plan_number_unrepresentable')
        return {'$number':struct.pack('>d',float(value)).hex()}
    if isinstance(value,list):return [public_numbers(v) for v in value]
    if isinstance(value,dict):return {k:public_numbers(v) for k,v in value.items()}
    return value


def continuation_plan_digest(value):return digest_of(canonical(public_numbers(value)))
