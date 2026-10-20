"""Version two is explicit: historical version-one document digests stay exact."""
from typing import Literal
from pydantic import Field,field_validator,model_validator
from pyharness.application.workspace_manifest import StrictModel
from pyharness.application.workspace_contracts import Digest
from pyharness.application.document_contracts import DocumentRevision,DocumentId
from pyharness.application.delivery_contracts import HandoffReference,DeliveryOutputReference,DeliveryAcceptanceReference


class TextSelection(StrictModel):
    start:int=Field(ge=0)
    end:int=Field(gt=0)
    quote_digest:Digest

    @model_validator(mode='after')
    def nonempty(self):
        if self.end<=self.start:raise ValueError('empty handoff selection')
        return self


class HandoffDocumentCommand(StrictModel):
    schema_version:Literal[2]
    document_id:DocumentId
    project_revision:int=Field(ge=1)
    title:str=Field(min_length=1,max_length=100)
    handoff_ref:HandoffReference
    output_ref:DeliveryOutputReference
    format:Literal['text','pdf']
    text_selection:TextSelection|None=None
    confirmation_digest:Digest|None=None

    @field_validator('schema_version',mode='before')
    @classmethod
    def exact_version(cls,value):
        if type(value) is not int or value!=2:raise ValueError('handoff document requires integer version two')
        return value

    @field_validator('title')
    @classmethod
    def nonblank(cls,value):
        if not value.strip():raise ValueError('blank document title')
        return value

    @model_validator(mode='after')
    def format_selection(self):
        if self.format=='pdf' and self.text_selection is not None:raise ValueError('PDF uses physical pages and citation locators')
        if self.format=='text' and self.confirmation_digest is not None:raise ValueError('plain text has no PDF confirmation')
        return self


class HandoffOutputSource(StrictModel):
    kind:Literal['handoff_output']='handoff_output'
    handoff_ref:HandoffReference
    output_ref:DeliveryOutputReference
    text_selection:TextSelection|None=None
    acceptance_ref:DeliveryAcceptanceReference|None=None
    confirmation_digest:Digest|None=None


class HandoffDocumentRevision(DocumentRevision):
    schema_version:Literal[2]=2
    revision:int=Field(default=1,ge=1,le=1)
    source:HandoffOutputSource
    handoff_context:str=Field(max_length=98304)

    @field_validator('schema_version',mode='before')
    @classmethod
    def exact_version(cls,value):
        if type(value) is not int or value!=2:raise ValueError('handoff document requires version two')
        return value
