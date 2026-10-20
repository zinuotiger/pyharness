"""Scan closed report data while distinguishing bound IDs/digests from free text."""
from pyharness.application.experiment_report_contracts import ExperimentReportData


def public_report_bytes(data):
    from pyharness.application.workspace_store import canonical
    from pyharness.application.source_snapshot import check_content
    # Extra fields and unconstrained text are forbidden by this versioned model.
    # The original proof is rechecked by the caller before reaching this encoder.
    value=ExperimentReportData.model_validate(data).model_dump()
    raw=canonical(value)
    def reference(row,key):
        if row is None:return
        row[key]='verified-reference';row['digest']='verified-digest'
    value['checker_implementation']='verified-digest'
    for field,key in [('experiment_run_ref','experiment_run_id'),('experiment_ref','experiment_id'),
            ('graph_run_ref','graph_run_id'),('dataset_ref','dataset_id')]:reference(value[field],key)
    value['basis']['control']['prefix_digest']='verified-digest'
    for entries in value['basis']['nodes'].values():
        for entry in entries or []:
            entry['session_id']='verified-session';entry['prefix_digest']='verified-digest'
    for case in value['cases']:
        reference(case['case_ref'],'case_id')
        for side in ('left','right'):
            measurement=case[side];reference(measurement['execution_ref'],'execution_id')
            measurement['agent_ref']['agent_id']='verified-agent';measurement['agent_ref']['sha256']='verified-digest'
            for key in ('input_digest','validator_digest'):
                if measurement['basis'][key] is not None:measurement['basis'][key]='verified-digest'
            if measurement['output_digest'] is not None:measurement['output_digest']='verified-digest'
    for regression in value['regressions']:reference(regression['case_ref'],'case_id')
    # Only exact schema paths for already-verified references are substituted.
    # Static enums, node names and all other canonical content still face today's
    # original rule, including future rule changes. No global redactor is relaxed.
    check_content(canonical(value))
    return raw
