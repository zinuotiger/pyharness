"""Dispatch configuration metadata to each existing tenant-owned authority."""
from pyharness.application.agent_configuration_history import AgentConfigurationHistory
from pyharness.core import configuration_revision_contracts as c


class ConfigurationHistory:
    def __init__(self, service):
        self.service = service
        self.models = service.credentials
        self.agents = AgentConfigurationHistory(service.platform)

    @staticmethod
    def _kind(kind):
        if kind not in ('model', 'agent'):
            c.reject('configuration_kind_not_found')
        return kind

    def current(self, kind, resource, *, authorize):
        if self._kind(kind) == 'model':
            return self.models.model_configuration_current(self.service.tenant_id, resource, authorize=authorize)
        return self.agents.current(resource, authorize=authorize)

    def history(self, kind, resource, *, authorize):
        if self._kind(kind) == 'model':
            return self.models.model_configuration_history(self.service.tenant_id, resource, authorize=authorize)
        return self.agents.history(resource, authorize=authorize)

    def capture(self, kind, resource, body, *, authorize):
        if self._kind(kind) == 'model':
            return self.models.capture_model_configuration(self.service.tenant_id, resource, body, authorize=authorize)
        return self.agents.capture(resource, body, authorize=authorize)

    def preview(self, kind, resource, revision, *, authorize):
        if self._kind(kind) == 'model':
            return self.models.preview_model_configuration(self.service.tenant_id, resource, revision, authorize=authorize)
        return self.agents.preview(resource, revision, authorize=authorize)

    def apply(self, kind, resource, revision, body, *, authorize):
        if self._kind(kind) == 'model':
            return self.models.apply_model_configuration(self.service.tenant_id, resource, revision, body, authorize=authorize)
        return self.agents.apply(resource, revision, body, authorize=authorize)

    def command(self, kind, resource, command_key, *, authorize):
        if self._kind(kind) == 'model':
            result = self.models.model_configuration_command(self.service.tenant_id, command_key, authorize=authorize)
        else:
            result = self.agents.command(command_key, authorize=authorize)
        revision = result.get('revision')
        if type(revision) is not dict or revision.get('resource_kind') != kind or revision.get('resource_id') != resource:
            c.reject('configuration_command_not_found')
        return result
