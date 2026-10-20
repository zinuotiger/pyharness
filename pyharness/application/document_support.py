"""Shared exact-version, ownership and replay rules for S05 control resources."""
from pathlib import Path
import stat
import time
from pyharness.application.source_selection import selection_name_reason
from pyharness.application.source_snapshot import checked_private_bytes
from pyharness.core.sandbox import safe_path
from pyharness.errors import PyHError, raise_code

FILE_METADATA_KEYS = ('path', 'size_bytes', 'mtime_ns', 'mode', 'device', 'inode')


class DocumentSupport:
    def _document_grants(self, resources, value, actor):
        source = value['source']
        if source['kind']=='handoff_output':return self._handoff_document_grants(resources,value,actor)
        if source['kind'] == 'pdf_grant':
            return (source['grant_id'],)
        wi, _ = self._document_input(resources, value['project_id'], source, actor)
        return self._input_grant_ids(wi['workspace_input'])

    @staticmethod
    def _s05_ref(row, key, kind):
        value = row[kind]
        return {key: value[key], 'revision': value['revision'], 'digest': row['digest'], 'source_seq': row['source_seq']}

    def _s05_exact(self, resources, project, kind, ref, key, actor):
        row = self._context_owned(resources, ref[key], kind, project, actor)
        if self._s05_ref(row, key, kind) != ref:
            raise_code('EVT-101', reason=kind + '_revision_changed')
        return row

    def _s05_history(self, resources, project, ident, revision, kind, actor):
        self._context_owned(resources, ident, kind, project, actor)
        for event in self._history_events(resources):
            p = event.payload
            if event.type == 'workspace.resource_committed' and p['resource_id'] == ident and p['revision'] == revision:
                return {kind: self._read_definition(p).model_dump(), 'digest': p['digest'],
                        'source_seq': event.seq, 'created_at': event.ts, 'created_by': p['actor_ref']}
        raise_code('GRD-403', reason=kind + '_unavailable')

    def _s05_base(self, value, resources, previous, event, kind):
        self._context_project(resources, value['project_id'], value['project_revision'])
        self._context_quota(resources, kind, value['project_id'])
        if value['created_by'] != event.payload['actor_ref']:
            raise ValueError('creator mismatch')
        if previous:
            prior = previous[kind]
            if (prior['created_by'] != value['created_by'] or prior['project_id'] != value['project_id']
                    or prior.get('retired') or kind == 'citation'):
                raise ValueError('resource ownership/retirement/immutability')

    def _document_input(self, resources, project, source, actor):
        row = self._s05_exact(resources, project, 'workspace_input', source['input_ref'], 'input_id', actor)
        files = row['workspace_input']['files']
        if source['file_index'] >= len(files):
            raise_code('EVT-100', reason='document_file_index_invalid')
        return row, files[source['file_index']]

    @staticmethod
    def _document_metadata(root, name):
        if selection_name_reason(name, set()):
            raise_code('GRD-403', reason='document_source_path_rejected')
        path = safe_path(Path(root), name)
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not info.st_ino:
            raise_code('GRD-403', reason='document_source_path_rejected')
        return {'path': name, 'size_bytes': info.st_size, 'mtime_ns': str(info.st_mtime_ns),
                'mode': stat.S_IMODE(info.st_mode), 'device': info.st_dev, 'inode': info.st_ino}

    @staticmethod
    def _pdf_confirmation(project, revision, actor, grant, metadata):
        from pyharness.application.workspace_store import canonical, digest_of
        g = grant['grant']
        return digest_of(canonical({'project_id': project, 'project_revision': revision, 'subject': actor,
            'grant_id': g['grant_id'], 'grant_revision': g['revision'], 'grant_digest': grant['digest'],
            'grant_source_seq': grant['source_seq'], 'root_identity': g['root_identity'], 'file': metadata,
            'policy': 'private-pdf-text-only-v1'}))

    def _document_raw(self, value, gate):
        from pyharness.application.workspace_store import digest_of
        gate()
        raw = checked_private_bytes(self._snapshot_content(value['content_digest']), before_read=gate)
        if len(raw) != value['size_bytes'] or digest_of(raw) != value['content_digest']:
            raise_code('PERS-202', reason='document_content_digest')
        gate()
        return raw

    def _validate_document_definition(self, definition, resources, previous, event):
        if definition.schema_version==2:return self._validate_handoff_document(definition,resources,previous,event)
        if previous and previous['document']['schema_version']==2:raise_code('PERS-202',reason='handoff_document_immutable')
        value = definition.model_dump(); source = value['source']
        try:
            self._s05_base(value, resources, previous, event, 'document')
            if source['kind'] == 'workspace_input':
                wi, item = self._document_input(resources, value['project_id'], source, value['created_by'])
                if wi['workspace_input']['project_revision'] != value['project_revision']:
                    raise ValueError('project binding')
                if value['content_digest'] != item['content_digest'] or value['size_bytes'] != item['size_bytes']:
                    raise ValueError('private bytes binding')
                if value['source_metadata'] is not None and value['source_metadata']['path'] != item['path']:
                    raise ValueError('source metadata path binding')
                if wi['workspace_input']['source_ref']['source_kind'] == 'source_snapshot' and value['source_metadata'] is not None:
                    snapshot = resources[wi['workspace_input']['source_ref']['snapshot_id']]['snapshot']
                    captured = next(f for f in snapshot['files'] if f['path'] == item['path'])
                    if value['source_metadata'] != {k: captured[k] for k in FILE_METADATA_KEYS}:
                        raise ValueError('captured source metadata binding')
                if any(resources[g]['grant']['revoked'] for g in self._input_grant_ids(wi['workspace_input'])):
                    raise ValueError('withdrawn input')
            else:
                grant = resources.get(source['grant_id'], {})
                g = grant.get('grant', {})
                if (g.get('project_id') != value['project_id'] or g.get('revoked')
                        or g.get('revision') != source['grant_revision'] or grant.get('digest') != source['grant_digest']
                        or grant.get('source_seq') != source['grant_source_seq'] or g.get('root_identity') != source['root_identity']):
                    raise ValueError('PDF grant binding')
                meta = {k: source['file'][k] for k in FILE_METADATA_KEYS}
                if self._pdf_confirmation(value['project_id'], value['project_revision'], value['created_by'], grant, meta) != source['confirmation_digest']:
                    raise ValueError('PDF confirmation binding')
                if (source['file']['sha256'] != value['content_digest'] or source['file']['size_bytes'] != value['size_bytes']
                        or selection_name_reason(source['file']['path'], set())):
                    raise ValueError('PDF byte binding')
                if value['source_metadata'] is not None and value['source_metadata'] != meta:
                    raise ValueError('PDF metadata binding')
        except (ValueError, KeyError, PyHError):
            raise_code('PERS-202', reason='document_definition_binding')

    def _document_gate(self, resources, row, actor, authorize, started):
        if row['document']['schema_version']==2:return self._handoff_document_gate(resources,row,actor,authorize,started)
        value = row['document']; source = value['source']; project = value['project_id']
        self._context_owned({value['document_id']: row}, value['document_id'], 'document', project, actor)
        self._context_project(resources, project)
        if authorize() != actor:
            raise_code('GRD-403', reason='document_subject_changed')
        reasons = []
        if resources[project]['project']['revision'] != value['project_revision']:
            reasons.append('project_revision_changed')
        latest = resources[value['document_id']]['document']
        if latest['revision'] != value['revision']:
            reasons.append('document_revision_superseded')
        if latest['retired']:
            reasons.append('document_retired')
        from pyharness.application.document_text import parser_version
        if value['parser_version'] != parser_version(value['format']):
            reasons.append('parser_version_changed')
        if source['kind'] == 'workspace_input':
            wi, item = self._document_input(resources, project, source, actor)
            reasons.extend(self._input_gate(resources, wi['workspace_input'], actor, authorize, started, current=False))
            grants = self._input_grant_ids(wi['workspace_input'])
            metadata = value['source_metadata']; source_name = item['path']
        else:
            grant = self._snapshot_grant(resources, project, source['grant_id'])
            self._workspace_storage_geometry(grant, None)
            if grant['grant']['revision'] != source['grant_revision']:
                raise_code('GRD-403', reason='document_grant_withdrawn')
            grants = (source['grant_id'],); metadata = value['source_metadata']; source_name = source['file']['path']
        try:
            live = self._document_metadata(resources[grants[0]]['grant']['canonical_root'], source_name)
            if metadata is None or live != metadata or live['size_bytes'] != value['size_bytes']:
                reasons.append('source_file_changed')
        except FileNotFoundError:
            reasons.append('source_file_missing')
        except OSError:
            raise_code('GRD-403', reason='document_source_unavailable')
        self._context_finish(authorize, actor, resources, grants)
        if time.monotonic() - started >= self.MAX_WORKSPACE_CAPTURE_SECONDS:
            raise_code('EVT-104', reason='document_observation_time_limit')
        return list(dict.fromkeys(reasons))

    def _document_summary(self, row, reasons, *, detail=True):
        value = row['document']
        missing = 'source_file_missing' in reasons or 'document_retired' in reasons
        status = 'missing' if missing else 'stale' if reasons else 'unlocatable' if value['capability'] == 'unlocatable' else 'current'
        result = {k: v for k, v in value.items() if k not in {'created_by', 'source_metadata'}}
        if value['schema_version']==2:result['persisted']=True
        if not detail:
            result.pop('blocks')
        elif missing:
            result['blocks'] = []
        return {**result, 'block_count': len(value['blocks']), 'status': status, 'stale_reasons': reasons,
                'digest': row['digest'], 'source_seq': row['source_seq'], 'created_at': row['created_at'],
                'executable': False, 'export_authorized': False, 'live_source_freshness': 'metadata_only'}
