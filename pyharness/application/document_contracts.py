"""Exact private document, citation and delivery-definition contracts."""
from typing import Annotated, Literal
from pydantic import Field, field_validator, model_validator
from pyharness.application.workspace_manifest import StrictModel
from pyharness.application.workspace_contracts import ProjectId, Digest
from pyharness.application.product_task_contracts import InputReference
from pyharness.application.context_manifest import TaskVersionReference
from pyharness.application.source_grants import GrantId
from pyharness.application.source_snapshot import SnapshotFile

DocumentId = Annotated[str, Field(pattern=r'^doc-[0-9a-f]{16}$')]
CitationId = Annotated[str, Field(pattern=r'^cit-[0-9a-f]{16}$')]
DeliveryId = Annotated[str, Field(pattern=r'^del-[0-9a-f]{16}$')]
BlockId = Annotated[str, Field(pattern=r'^b-[0-9]{4}$')]


class VersionOne(StrictModel):
    schema_version: Literal[1] = 1

    @field_validator('schema_version', mode='before')
    @classmethod
    def exact_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError('document contract requires integer version one')
        return value


class DocumentReference(StrictModel):
    document_id: DocumentId
    revision: int = Field(ge=1, le=200)
    digest: Digest
    source_seq: int = Field(ge=1)


class CitationReference(StrictModel):
    citation_id: CitationId
    revision: int = Field(ge=1, le=1)
    digest: Digest
    source_seq: int = Field(ge=1)


class PrivateInputSource(StrictModel):
    kind: Literal['workspace_input']
    input_ref: InputReference
    file_index: int = Field(ge=0, le=199)


class PdfPreview(VersionOne):
    project_revision: int = Field(ge=1)
    grant_id: GrantId
    grant_revision: int = Field(ge=1)
    path: str = Field(min_length=1, max_length=1024)


class PdfImportSource(StrictModel):
    kind: Literal['pdf_grant']
    grant_id: GrantId
    grant_revision: int = Field(ge=1)
    path: str = Field(min_length=1, max_length=1024)
    confirmation_digest: Digest


class PdfCapturedSource(StrictModel):
    kind: Literal['pdf_grant']
    grant_id: GrantId
    grant_revision: int = Field(ge=1)
    grant_digest: Digest
    grant_source_seq: int = Field(ge=1)
    root_identity: Digest
    file: SnapshotFile
    confirmation_digest: Digest


class DocumentCommand(VersionOne):
    document_id: DocumentId
    expected_revision: int = Field(ge=0, le=199)
    project_revision: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=100)
    source: Annotated[PrivateInputSource | PdfImportSource, Field(discriminator='kind')]
    retired: bool = False

    @field_validator('title')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('blank document title')
        return value


class DocumentBlock(StrictModel):
    block_id: BlockId
    page: int | None = Field(ge=1, le=200)
    paragraph: int = Field(ge=1, le=400)
    start: int = Field(ge=0)
    end: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=98304)
    quote_digest: Digest

    @model_validator(mode='after')
    def coherent(self):
        import hashlib
        if self.end - self.start != len(self.text) or hashlib.sha256(self.text.encode()).hexdigest() != self.quote_digest:
            raise ValueError('block interval or digest mismatch')
        return self


class DocumentSourceMetadata(StrictModel):
    path: str = Field(min_length=1, max_length=1024)
    size_bytes: int = Field(ge=0, le=10 * 1024 * 1024)
    mtime_ns: str = Field(pattern=r'^[0-9]{1,24}$')
    mode: int = Field(ge=0, le=511)
    device: int = Field(ge=0)
    inode: int = Field(gt=0)


class DocumentRevision(VersionOne):
    document_id: DocumentId
    revision: int = Field(ge=1, le=200)
    project_id: ProjectId
    project_revision: int = Field(ge=1)
    created_by: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=100)
    source: Annotated[PrivateInputSource | PdfCapturedSource, Field(discriminator='kind')]
    source_metadata: DocumentSourceMetadata | None = None
    content_digest: Digest
    size_bytes: int = Field(ge=0, le=10 * 1024 * 1024)
    format: Literal['text', 'pdf']
    parser_version: str = Field(min_length=1, max_length=100)
    offset_unit: Literal['unicode-codepoint'] = 'unicode-codepoint'
    page_count: int = Field(ge=0, le=200)
    unlocatable_pages: list[int] = Field(max_length=200)
    capability: Literal['locatable', 'unlocatable']
    capability_reason: Literal['text', 'no_text_layer', 'empty_text', 'encrypted_pdf', 'invalid_pdf', 'unsupported_format']
    blocks: list[DocumentBlock] = Field(max_length=400)
    retired: bool = False

    @model_validator(mode='after')
    def coherent(self):
        if len({b.block_id for b in self.blocks}) != len(self.blocks):
            raise ValueError('duplicate block')
        if sum(len(b.text.encode()) for b in self.blocks) > 98304:
            raise ValueError('document text limit')
        if self.unlocatable_pages != sorted(set(self.unlocatable_pages)) or any(not 1 <= p <= self.page_count for p in self.unlocatable_pages):
            raise ValueError('invalid unlocatable pages')
        if (self.capability == 'locatable') != bool(self.blocks):
            raise ValueError('capability/block mismatch')
        if self.format == 'text' and (self.page_count or self.unlocatable_pages or any(b.page is not None for b in self.blocks)):
            raise ValueError('plain text has no physical pages')
        if self.format == 'pdf' and any(b.page is None or b.page > self.page_count or b.page in self.unlocatable_pages for b in self.blocks):
            raise ValueError('invalid physical page')
        return self


class CitationLocator(StrictModel):
    document_ref: DocumentReference
    block_id: BlockId
    start: int = Field(ge=0)
    end: int = Field(ge=1)
    quote_digest: Digest

    @model_validator(mode='after')
    def nonempty(self):
        if self.end <= self.start:
            raise ValueError('empty citation interval')
        return self


class CitationCommand(VersionOne):
    citation_id: CitationId
    expected_revision: int = Field(ge=0, le=0)
    project_revision: int = Field(ge=1)
    locator: CitationLocator


class CitationRevision(VersionOne):
    citation_id: CitationId
    revision: int = Field(ge=1, le=1)
    project_id: ProjectId
    project_revision: int = Field(ge=1)
    created_by: str = Field(min_length=1, max_length=128)
    locator: CitationLocator


class DeliveryItem(StrictModel):
    item_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,40}$')
    title: str = Field(min_length=1, max_length=200)
    required: bool = True
    acceptance_criteria: list[str] = Field(min_length=1, max_length=20)
    citation_refs: list[CitationReference] = Field(default_factory=list, max_length=20)

    @field_validator('title')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('blank delivery title')
        return value

    @field_validator('acceptance_criteria')
    @classmethod
    def bounded_criteria(cls, values):
        if any(not v.strip() or len(v) > 1000 for v in values):
            raise ValueError('invalid delivery criterion')
        return values


class DeliveryCommand(VersionOne):
    delivery_id: DeliveryId
    expected_revision: int = Field(ge=0, le=199)
    project_revision: int = Field(ge=1)
    task_ref: TaskVersionReference
    items: list[DeliveryItem] = Field(min_length=1, max_length=30)
    retired: bool = False

    @field_validator('items')
    @classmethod
    def unique_items(cls, values):
        if len({x.item_id for x in values}) != len(values):
            raise ValueError('duplicate delivery item')
        return values


class DeliveryRevision(VersionOne):
    delivery_id: DeliveryId
    revision: int = Field(ge=1, le=200)
    project_id: ProjectId
    project_revision: int = Field(ge=1)
    created_by: str = Field(min_length=1, max_length=128)
    task_ref: TaskVersionReference
    items: list[DeliveryItem] = Field(min_length=1, max_length=30)
    retired: bool = False
