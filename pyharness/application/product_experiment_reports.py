"""Private report operations carry no execution or publication permission."""
from pyharness.application.experiment_report_contracts import (PreviewExperimentReport,SaveExperimentReport,ExperimentReportId,
    PreviewExperimentReportExport,ConfirmExperimentReportExport)
from pyharness.application.run_attempt_contracts import SubmissionKey
from pyharness.core.lifecycle import admitted


class ProductExperimentReports:
    @admitted
    async def experiment_report_preview(self,project,body):
        project=self._experiment_scope(project);command=PreviewExperimentReport.model_validate(body)
        return await self.store.experiment_report_preview(project,command,authorize=lambda:self._authorize(write=True),projects=self)

    @admitted
    async def save_experiment_report(self,project,body,key):
        project=self._experiment_scope(project);key=self._workspace_id(key,SubmissionKey);command=SaveExperimentReport.model_validate(body)
        return await self.store.save_experiment_report(project,command,key,authorize=lambda:self._authorize(write=True),projects=self)

    @admitted
    async def experiment_report(self,project,ident,body=None,*,confirmed=False):
        project=self._experiment_scope(project);ident=self._workspace_id(ident,ExperimentReportId)
        export=(ConfirmExperimentReportExport if confirmed else PreviewExperimentReportExport).model_validate(body) if body is not None else None
        return await self.store.experiment_report(project,ident,authorize=lambda:self._authorize(write=True),projects=self,export=export,confirmed=confirmed)
