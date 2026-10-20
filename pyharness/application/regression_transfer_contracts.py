"""Bounded data transfer; a self-reported bundle never confers source authority."""
from typing import Literal
from pydantic import Field,field_validator,model_validator
from pyharness.application.document_contracts import VersionOne
from pyharness.application.workspace_contracts import ProjectId,Digest
from pyharness.application.workspace_manifest import StrictModel
from pyharness.application.regression_contracts import (CaseReference,DatasetId,DatasetReference,public_manual_text)
from pyharness.application.regression_records import CaseRevision,DatasetRevision


class DatasetExportPreview(VersionOne):
    revision:int=Field(ge=1,le=200)


class ConfirmedData(VersionOne):
    confirmed:bool

    @field_validator('confirmed')
    @classmethod
    def explicit(cls,value):
        if value is not True:raise ValueError('explicit data transfer confirmation required')
        return value


class ExportDataset(ConfirmedData):
    revision:int=Field(ge=1,le=200)
    confirmation_digest:Digest


class DatasetImportPreview(VersionOne):
    dataset_id:DatasetId
    expected_revision:int=Field(ge=0,le=199)
    project_revision:int=Field(ge=1)
    title:str=Field(min_length=1,max_length=100)
    bundle_canonical:str=Field(min_length=1,max_length=65536)

    @field_validator('title')
    @classmethod
    def public_title(cls,value):return public_manual_text(value)


class ImportDataset(DatasetImportPreview,ConfirmedData):
    confirmation_digest:Digest


class ExportedCase(StrictModel):
    reference:CaseReference
    record:CaseRevision


class RegressionBundle(VersionOne):
    format:Literal['pyharness-regression-data-v1']='pyharness-regression-data-v1'
    tenant_id:str=Field(min_length=1,max_length=128)
    subject_ref:str=Field(min_length=1,max_length=128)
    project_id:ProjectId
    dataset_ref:DatasetReference
    dataset:DatasetRevision
    cases:list[ExportedCase]=Field(min_length=1,max_length=32)
    assertion_version:Literal['regression-assertions-v1']='regression-assertions-v1'

    @model_validator(mode='after')
    def self_consistent(self):
        from pyharness.application.workspace_store import canonical,digest_of
        if (self.dataset.dataset_id!=self.dataset_ref.dataset_id or self.dataset.revision!=self.dataset_ref.revision or
                digest_of(canonical(self.dataset.model_dump()))!=self.dataset_ref.digest or
                self.dataset.project_id!=self.project_id or self.dataset.created_by!=self.subject_ref or
                [v.reference for v in self.cases]!=self.dataset.selection.case_refs):
            raise ValueError('bundle dataset differs from fixed reference')
        for case in self.cases:
            if (case.record.case_id!=case.reference.case_id or case.record.revision!=case.reference.revision or
                    digest_of(canonical(case.record.model_dump()))!=case.reference.digest or
                    case.record.project_id!=self.project_id or case.record.created_by!=self.subject_ref):
                raise ValueError('bundle case differs from fixed reference')
        return self
