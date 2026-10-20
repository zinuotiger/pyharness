"""Versioned, finite data checks. Nothing here evaluates code or invokes a tool."""
import json
import math
import re
from typing import Literal
from pydantic import Field,model_validator
from pyharness.application.workspace_manifest import StrictModel

VERSION='regression-assertions-v1'


class JsonLimitError(ValueError):
    """Valid-looking data outside the finite checker's representable limits."""


class Assertion(StrictModel):
    kind:Literal['output_contains','output_excludes','output_equals','json_pointer_equals','execution_status']
    expected:str|int|float|bool|None
    pointer:str|None=Field(default=None,max_length=512)

    @model_validator(mode='after')
    def finite_contract(self):
        if type(self.expected) is float and not math.isfinite(self.expected):raise ValueError('nonfinite expectation')
        if type(self.expected) is str and len(self.expected)>2048:raise ValueError('expectation limit')
        if self.kind.startswith('output_') and (type(self.expected) is not str or not self.expected):
            raise ValueError('nonempty text expectation required')
        if self.kind=='execution_status' and self.expected not in ('completed','failed'):
            raise ValueError('terminal noncancelled expectation required')
        if self.kind=='json_pointer_equals':
            if self.pointer is None or (self.pointer and not self.pointer.startswith('/')):
                raise ValueError('literal JSON pointer required')
            if self.pointer.count('/')>8 or re.search(r'~(?![01])',self.pointer):raise ValueError('pointer limit')
        elif self.pointer is not None:raise ValueError('unexpected pointer')
        return self


def _unique_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('duplicate JSON key')
        result[key]=value
    return result


def strict_json(text,*,max_nodes=2048,max_depth=16):
    def invalid(_):raise ValueError('nonfinite JSON')
    value=json.loads(text,object_pairs_hook=_unique_object,parse_constant=invalid)
    pending=[(value,0)];count=0
    while pending:
        item,depth=pending.pop();count+=1
        if depth>max_depth or count>max_nodes:raise JsonLimitError('JSON tree limit')
        if type(item) is float and not math.isfinite(item):raise JsonLimitError('JSON numeric range')
        if type(item) is dict:pending.extend((v,depth+1) for v in item.values())
        elif type(item) is list:pending.extend((v,depth+1) for v in item)
    return value


def _pointer(value,pointer):
    if not pointer:return value
    for raw in pointer[1:].split('/'):
        key=raw.replace('~1','/').replace('~0','~')
        if type(value) is dict:value=value[key]
        elif type(value) is list:
            if not re.fullmatch(r'0|[1-9][0-9]{0,5}',key):raise KeyError(key)
            value=value[int(key)]
        else:raise KeyError(key)
    return value


def _scalar_equal(observed,expected):
    if type(observed) in (int,float) and type(expected) in (int,float):
        # JSON has one number type. Python compares int/float without first
        # rounding both arbitrary integers to a JavaScript double.
        return observed==expected
    return type(observed) is type(expected) and observed==expected


def evaluate_assertions(assertions,*,output,execution_status,output_complete=True):
    """Missing, cancelled or filtered evidence never becomes a passing check."""
    if not assertions or len(assertions)>16:raise ValueError('assertion count limit')
    try:available=type(output) is str and output_complete is True and len(output.encode('utf-8'))<=65536
    except UnicodeError:available=False
    results=[]
    for assertion in assertions:
        assertion=Assertion.model_validate(assertion)
        verdict='unknown';reason='evidence_unavailable'
        if assertion.kind=='execution_status':
            if execution_status in ('completed','failed'):
                verdict='passed' if execution_status==assertion.expected else 'failed';reason='terminal_status'
        elif available:
            expected=assertion.expected;kind=assertion.kind
            if kind=='output_contains':passed=expected in output
            elif kind=='output_excludes':passed=expected not in output
            elif kind=='output_equals':passed=expected==output
            else:
                try:
                    observed=_pointer(strict_json(output),assertion.pointer)
                    passed=_scalar_equal(observed,expected)
                except (JsonLimitError,RecursionError):
                    results.append(dict(kind=assertion.kind,status='unknown',reason='checker_limit'));continue
                except (ValueError,KeyError,IndexError):passed=False
            verdict='passed' if passed else 'failed';reason='finite_comparison'
        results.append(dict(kind=assertion.kind,status=verdict,reason=reason))
    verdict='failed' if any(r['status']=='failed' for r in results) else 'unknown' if any(r['status']=='unknown' for r in results) else 'passed'
    return dict(version=VERSION,verdict=verdict,assertions=results)
