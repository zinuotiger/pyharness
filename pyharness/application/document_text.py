"""Deterministic text blocks; all offsets are Unicode codepoints in decoded page text."""
import hashlib
from pyharness.application.document_contracts import DocumentBlock
from pyharness.application.source_snapshot import check_content
from pyharness.errors import raise_code

MAX_TEXT_BYTES = 98304
TEXT_PARSER = 'plain-utf8-bom-codepoint-v1'


def parser_version(kind):
    if kind == 'text':
        return TEXT_PARSER
    from importlib.metadata import version
    return 'physical-page-text-v1:pypdf-' + version('pypdf')


def text_blocks(pages, *, pdf):
    blocks = []
    if sum(len(t.encode()) for t in pages) > MAX_TEXT_BYTES:
        raise_code('EVT-100', reason='document_text_limit')
    for page, text in enumerate(pages, 1):
        check_content(text.encode())
        offset = 0; start = None; end = None; paragraph = 0
        # Sentinel flushes the final paragraph; CRLF within a block stays intact.
        for line in [*text.splitlines(keepends=True), '']:
            body = line.rstrip('\r\n')
            if body.strip():
                if start is None:
                    start = offset
                end = offset + len(body)
            elif start is not None:
                paragraph += 1
                value = text[start:end]
                blocks.append(DocumentBlock(block_id=f'b-{len(blocks)+1:04d}', page=page if pdf else None,
                    paragraph=paragraph, start=start, end=end, text=value,
                    quote_digest=hashlib.sha256(value.encode()).hexdigest()))
                start = end = None
                if len(blocks) > 400:
                    raise_code('EVT-100', reason='document_block_limit')
            offset += len(line)
    return blocks


def parse_text(raw):
    check_content(raw)
    text = raw.decode('utf-8-sig')
    blocks = text_blocks([text], pdf=False)
    return {'format': 'text', 'parser_version': parser_version('text'), 'page_count': 0,
            'unlocatable_pages': [], 'blocks': blocks, 'capability': 'locatable' if blocks else 'unlocatable',
            'capability_reason': 'text' if blocks else 'empty_text'}
