"""Bounded read-only requests; no caller-selected authority or executable hooks."""
import json
from pydantic import Field,JsonValue,model_validator
from pyharness.application.execution_contracts import PreviewExecution
from pyharness.application.execution_contracts import ExecutionId
from pyharness.application.document_contracts import VersionOne
from typing import Literal


class CompareExecutions(VersionOne):
    schema_version:Literal[1]=Field(...)
    left_execution_id:ExecutionId
    right_execution_id:ExecutionId


class PermissionPreview(PreviewExecution):
    tool_name:str=Field(pattern=r'^[a-z][a-z0-9_.]{1,63}$')
    arguments:dict[str,JsonValue]

    @model_validator(mode='after')
    def bounded_arguments(self):
        nodes=0;stack=[(self.arguments,0)]
        while stack:
            value,depth=stack.pop();nodes+=1
            if nodes>2048 or depth>8:raise ValueError('permission_argument_limit')
            if isinstance(value,dict):stack.extend((v,depth+1) for v in value.values())
            elif isinstance(value,list):stack.extend((v,depth+1) for v in value)
        if len(json.dumps(self.arguments,ensure_ascii=True,allow_nan=False).encode())>32768:
            raise ValueError('permission_argument_bytes')
        return self
