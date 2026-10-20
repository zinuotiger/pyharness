"""Strict server-authored versions on the original control journal."""
from pydantic import Field,model_validator
from pyharness.application.document_contracts import VersionOne
from pyharness.application.run_attempt_contracts import SubmissionKey
from pyharness.application.workspace_contracts import ProjectId
from pyharness.application.regression_contracts import CaseId,DatasetId,SaveCase,SaveDataset,ImportedSaveDataset,CandidateEvidence


class RegressionRecord(VersionOne):
    project_id:ProjectId
    project_revision:int=Field(ge=1)
    revision:int=Field(ge=1,le=200)
    created_by:str=Field(min_length=1,max_length=128)
    submission_key:SubmissionKey

    @model_validator(mode='after')
    def selected_revision(self):
        if self.selection.expected_revision+1!=self.revision or self.selection.project_revision!=self.project_revision:
            raise ValueError('regression revision differs from confirmed selection')
        return self


class CaseRevision(RegressionRecord):
    case_id:CaseId
    selection:SaveCase
    evidence:CandidateEvidence

    @model_validator(mode='after')
    def confirmed_evidence(self):
        from pyharness.application.workspace_store import canonical,digest_of
        if (self.case_id!=self.selection.case_id or self.project_id!=self.evidence.project_id or
                self.selection.origin!=self.evidence.origin or
                digest_of(canonical(self.evidence.model_dump()))!=self.selection.candidate_digest):
            raise ValueError('case differs from confirmed source evidence')
        return self


class DatasetRevision(RegressionRecord):
    dataset_id:DatasetId
    selection:ImportedSaveDataset|SaveDataset

    @model_validator(mode='after')
    def selected_identity(self):
        if self.dataset_id!=self.selection.dataset_id:raise ValueError('dataset identity differs')
        return self


REGRESSION_MODELS={'regression_case':(CaseRevision,SaveCase,'case_id'),
    'regression_dataset':(DatasetRevision,SaveDataset,'dataset_id')}
