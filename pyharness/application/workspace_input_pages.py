"""Bounded, stable pages of currently authorized input metadata.

A cursor selects a position in a checked view; it is not authorization or a
secret capability. Every request replays control facts and reauthorizes rows.
Changes to that view require a refresh instead of silently omitting rows.
"""
import re
from pydantic import BaseModel,ConfigDict,Field
from pyharness.errors import raise_code


class WorkspaceInputQuery(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    limit:int=Field(default=25,ge=1,le=50)
    cursor:str|None=Field(default=None,max_length=160)


def parse_workspace_query(pairs):
    parameters=dict(pairs)
    if len(parameters)!=len(pairs) or set(parameters)-{'limit','cursor'}:
        raise_code('EVT-100',reason='workspace_page_query_invalid')
    if 'limit' in parameters:
        if not re.fullmatch(r'[0-9]{1,2}',parameters['limit']):
            raise_code('EVT-100',reason='workspace_page_query_invalid')
        parameters['limit']=int(parameters['limit'])
    return WorkspaceInputQuery.model_validate(parameters)


def workspace_input_page(rows,query,*,kind,project,actor,watermark):
    from pyharness.application.workspace_store import canonical,digest_of
    after=0;expected=None
    if query.cursor is not None:
        match=re.fullmatch(r'1:([1-9][0-9]{0,15}):([1-9][0-9]{0,15}):([1-9][0-9]?):([0-9a-f]{64})',query.cursor)
        if not match: raise_code('EVT-100',reason='workspace_page_cursor_invalid')
        bound,after,limit=map(int,match.groups()[:3]);expected=match[4]
        if limit!=query.limit or not after<=bound<=watermark:
            raise_code('EVT-100',reason='workspace_page_cursor_invalid')
        watermark=bound
    rows=sorted((row for row in rows if row['source_seq']<=watermark),key=lambda row:row['source_seq'])
    view=digest_of(canonical({'kind':kind,'project':project,'actor':actor,'watermark':watermark,'limit':query.limit,'rows':rows}))
    if expected is not None:
        if expected!=view: raise_code('EVT-101',reason='workspace_page_view_changed_refresh')
        if after not in {row['source_seq'] for row in rows[:-1]}:
            raise_code('EVT-100',reason='workspace_page_cursor_invalid')
    selected=[row for row in rows if row['source_seq']>after][:query.limit]
    more=bool(selected and selected[-1]['source_seq']<rows[-1]['source_seq'])
    cursor=f"1:{watermark}:{selected[-1]['source_seq']}:{query.limit}:{view}" if more else None
    page={'schema_version':1,kind:selected,'total':len(rows),'page_count':len(selected),
          'has_more':more,'next_cursor':cursor,'source_watermark':watermark,'view_digest':view}
    if len(canonical(page))>512*1024: raise_code('EVT-100',reason='workspace_page_bytes_limit')
    return page
