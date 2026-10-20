"""GIVEN synthetic project definitions WHEN committed THEN immutable strict contracts."""
import pytest
from pydantic import ValidationError


def test_project_revision_is_strict_and_immutable():
    from pyharness.application.workspace_contracts import ProjectRevision
    project = ProjectRevision(project_id='p-0123456789abcdef', revision=1,
                              name='合成项目', description='no host root')
    assert project.schema_version == 1
    assert project.archived is False
    with pytest.raises(ValidationError):
        ProjectRevision(**{**project.model_dump(), 'host_root': 'C:/private'})
    with pytest.raises(ValidationError):
        project.name = 'changed'
    with pytest.raises(ValidationError):
        ProjectRevision(**{**project.model_dump(), 'schema_version': 2})
    with pytest.raises(ValidationError):
        ProjectRevision(**{**project.model_dump(), 'revision': True})
    with pytest.raises(ValidationError):
        ProjectRevision(**{**project.model_dump(), 'project_id': '../escape'})
