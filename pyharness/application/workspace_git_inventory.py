"""Ephemeral candidate metadata; Git consumes ignore rules, never captures bytes."""
import re
import stat
import os
from dataclasses import dataclass,field
from pathlib import Path
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,model_validator
from pyharness.application.source_preflight import PreflightGrant
from pyharness.application.source_grants import GrantId
from pyharness.application.source_selection import selection_name_reason
from pyharness.application.source_snapshot import LIMIT,check_content
from pyharness.application.workspace_contracts import Digest,ProjectId
from pyharness.application.workspace_git_contracts import GitOid
from pyharness.errors import PyHError,raise_code

MAX_CANDIDATES=2000
MAX_NAMES_BYTES=2*1024*1024
MAX_METADATA_ENTRIES=10000
MAX_RULE_BYTES=512*1024


class GitInventoryCommand(PreflightGrant):
    project_revision:int=Field(ge=1)
    metadata_grant_id:GrantId|None=None
    metadata_grant_revision:int|None=Field(default=None,ge=1)
    include_ignored:bool=False
    limit:int=Field(default=25,ge=1,le=50)
    cursor:str|None=Field(default=None,min_length=1,max_length=160)

    @model_validator(mode='after')
    def paired_metadata(self):
        if (self.metadata_grant_id is None)!=(self.metadata_grant_revision is None):
            raise ValueError('paired metadata grant required')
        return self


class StrictMetadata(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,frozen=True)


class GitMetadataRef(StrictMetadata):
    git_oid:GitOid
    mode:Literal[33188,33261]


class WorkingMetadata(StrictMetadata):
    size_bytes:int=Field(ge=0,le=2**53-1)
    mtime_ns:str=Field(pattern=r'^[0-9]{1,24}$')
    native_mode:int=Field(ge=0,le=511)


class GitInputCandidate(StrictMetadata):
    entry_index:int=Field(ge=0,lt=MAX_CANDIDATES)
    path:str|None=Field(min_length=1,max_length=1024)
    path_digest:Digest
    origin:Literal['tracked','untracked','ignored']
    required:bool
    head:GitMetadataRef|None
    index:GitMetadataRef|None
    working:WorkingMetadata|None
    staged:bool
    deletion_marker:bool
    index_deletion_marker:bool
    status:Literal['candidate','excluded']
    reason:Literal['sensitive_name','path_rejected','duplicate_path','not_regular','hard_link','file_too_large','unsupported_mode','file_unavailable']|None

    @model_validator(mode='after')
    def coherent(self):
        if self.required!=(self.head is not None or self.index is not None) or self.required!=(self.origin=='tracked'):
            raise ValueError('candidate origin mismatch')
        if (self.status=='candidate')!=(self.reason is None) or (self.path is None)!=(self.status=='excluded'):
            raise ValueError('candidate visibility mismatch')
        if self.status=='candidate' and self.working is None and not self.required:raise ValueError('missing untracked candidate')
        return self


def _names(raw):
    if raw and not raw.endswith(b'\0'):raise_code('GRD-403',reason='git_candidates_unavailable')
    try:values=[name.decode('utf-8') for name in raw.split(b'\0')[:-1]]
    except UnicodeError:raise_code('GRD-403',reason='git_candidates_unavailable')
    if len(values)>MAX_CANDIDATES:raise_code('EVT-100',reason='git_candidate_count_limit')
    if len(set(values))!=len(values):raise_code('GRD-403',reason='git_candidates_unavailable')
    return values


def _identity(info):
    return (info.st_dev,info.st_ino,info.st_mode,info.st_size,info.st_mtime_ns,info.st_nlink)


def _ignore_metadata(root,runtime):
    """Anchor rule presence/metadata, including self-ignored rules; no body IO.

    Walk only bounded ordinary directories. Git's private view has no source
    info/exclude or global excludes; only worktree .gitignore rules are consumed.
    Reject unsafe rule files before allowing Git to consume them.
    """
    pending=[root];facts={};count=0;name_bytes=0;rule_bytes=0
    while pending:
        directory=pending.pop();relative=directory.relative_to(root).as_posix()
        if directory!=root:directory=runtime._safe(root,relative)
        facts['dir:'+relative]=_identity(directory.lstat())
        name='.gitignore' if directory==root else relative+'/.gitignore'
        rule=runtime._safe(root,name)
        try:info=rule.lstat()
        except FileNotFoundError:facts['rule:'+name]=None
        else:
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
                raise_code('GRD-403',reason='git_ignore_rules_unavailable')
            if selection_name_reason(name,set()) is not None:
                raise_code('GRD-403',reason='git_ignore_rules_unavailable')
            rule_bytes+=info.st_size
            if rule_bytes>MAX_RULE_BYTES:raise_code('EVT-100',reason='git_ignore_rules_size_limit')
            facts['rule:'+name]=_identity(info)
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.name.casefold()=='.git':
                    if directory!=root:raise_code('GRD-403',reason='git_nested_repository_unsupported')
                    continue
                if os.name=='nt' and entry.name.casefold()=='.gitignore' and entry.name!='.gitignore':
                    raise_code('GRD-403',reason='git_ignore_rules_unavailable')
                count+=1;name_bytes+=len(entry.name.encode('utf-8'))
                if count>MAX_METADATA_ENTRIES or name_bytes>MAX_NAMES_BYTES:
                    raise_code('EVT-100',reason='git_candidate_metadata_walk_limit')
                info=entry.stat(follow_symlinks=False)
                path=Path(entry.path)
                if entry.is_symlink() or (hasattr(path,'is_junction') and path.is_junction()):
                    raise_code('GRD-403',reason='git_ignore_rules_unavailable')
                if stat.S_ISDIR(info.st_mode):
                    pending.append(path)
                facts['entry:'+path.relative_to(root).as_posix()]='directory' if stat.S_ISDIR(info.st_mode) else 'file'
    return facts


def _read_ignore_rule(root,name,expected,runtime):
    """Check the opened descriptor before any rule bytes; never native source IO."""
    path=runtime._safe(root,name)
    if _identity(path.lstat())!=expected:raise_code('EVT-101',reason='git_ignore_rules_changed')
    descriptor=os.open(path,os.O_RDONLY|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
    with os.fdopen(descriptor,'rb') as stream:
        opened=os.fstat(stream.fileno())
        if _identity(opened)!=expected or not stat.S_ISREG(opened.st_mode) or opened.st_nlink!=1:
            raise_code('GRD-403',reason='git_ignore_rules_unavailable')
        raw=stream.read(opened.st_size+1)
        if len(raw)!=opened.st_size or _identity(os.fstat(stream.fileno()))!=expected:
            raise_code('EVT-101',reason='git_ignore_rules_changed')
    if _identity(runtime._safe(root,name).lstat())!=expected:raise_code('EVT-101',reason='git_ignore_rules_changed')
    return raw


def _inventory_tree(root,view,facts,runtime):
    """Native Git sees owned placeholders and validated bounded rule copies only.

    This is a disposable metadata view, never a captured source/context. Ordinary
    candidate bytes are not opened. The caller owns and removes the outer view.
    """
    mirror=view/'candidate-tree';mirror.mkdir()
    entries=sorted(((key[6:],kind) for key,kind in facts.items() if key.startswith('entry:')),
                   key=lambda item:(len(Path(item[0]).parts),item[0]))
    for name,kind in entries:
        destination=runtime._safe(mirror,name)
        if kind=='directory':destination.mkdir()
        else:
            rule=facts.get('rule:'+name)
            raw=_read_ignore_rule(root,name,rule,runtime) if rule is not None else b''
            destination.write_bytes(raw)
    return mirror


@dataclass
class GitInventoryCandidate:
    command:GitInventoryCommand
    actor:str=''
    tenant:str=''
    observation:dict=field(default_factory=dict)
    items:list=field(default_factory=list)
    identity_digest:str=''
    verify:object=None

    async def observe(self,source,metadata,root,metadata_root,common,gitdir,kind,pointer,head,index,view,commit,heads,staged):
        from pyharness.application import workspace_git as runtime
        from pyharness.application.workspace_store import canonical,digest_of
        rules=_ignore_metadata(root,runtime)
        mirror=_inventory_tree(root,view,rules,runtime)
        async def candidates(ignored=False):
            argv=['ls-files','--others',*(['--ignored'] if ignored else []),'--exclude-standard','-z']
            return _names(await runtime._git(view,common,mirror,argv,MAX_NAMES_BYTES))
        untracked=await candidates();ignored=await candidates(True) if self.command.include_ignored else []
        tracked=set(heads)|set(staged);names=sorted(tracked|set(untracked)|set(ignored))
        if len(names)>MAX_CANDIDATES:raise_code('EVT-100',reason='git_candidate_count_limit')
        if len({name.casefold() for name in names})!=len(names):raise_code('GRD-403',reason='git_candidates_case_collision')
        files={};directories={'.':_identity(root.lstat())};rows=[];seen=set()
        for number,name in enumerate(names):
            reason=selection_name_reason(name,seen);seen.add(name.casefold())
            try:check_content(name.encode('utf-8'))
            except PyHError:reason='sensitive_name'
            required=name in tracked;working=None;missing=False
            if len(name)>1024:reason='path_rejected'
            if reason is None:
                try:
                    path=runtime._safe(root,name)
                    try:info=path.lstat()
                    except FileNotFoundError:
                        if not required:raise_code('EVT-101',reason='git_candidates_changed')
                        missing=True;files[name]=None
                    else:
                        files[name]=_identity(info)
                        if not stat.S_ISREG(info.st_mode):reason='not_regular'
                        elif info.st_nlink!=1:reason='hard_link'
                        elif stat.S_IMODE(info.st_mode)>511:reason='unsupported_mode'
                        else:
                            working=WorkingMetadata(size_bytes=info.st_size,mtime_ns=str(info.st_mtime_ns),native_mode=stat.S_IMODE(info.st_mode)).model_dump()
                            if info.st_size>LIMIT:reason='file_too_large'
                    for directory in [path.parent,*path.parent.parents]:
                        if not directory.is_relative_to(root):break
                        relative=directory.relative_to(root).as_posix()
                        directories[relative]=_identity(directory.lstat())
                        if directory==root:break
                except PyHError as error:
                    if error.code=='EVT-101':raise
                    reason='path_rejected'
                except OSError:reason='file_unavailable'
            h,i=heads.get(name),staged.get(name)
            rows.append(GitInputCandidate(entry_index=number,path=None if reason else name,path_digest=digest_of(name.encode('utf-8')),
                origin='tracked' if required else 'ignored' if name in ignored else 'untracked',required=required,head=h,index=i,working=working,
                staged=(h!=i),deletion_marker=required and missing,index_deletion_marker=h is not None and i is None,
                status='excluded' if reason else 'candidate',reason=reason).model_dump())
        def verify():
            if _ignore_metadata(root,runtime)!=rules:raise_code('EVT-101',reason='git_ignore_rules_changed')
            for name,expected in files.items():
                path=runtime._safe(root,name)
                try:actual=_identity(path.lstat())
                except FileNotFoundError:actual=None
                if expected!=actual:raise_code('EVT-101',reason='git_candidates_changed')
            for name,expected in directories.items():
                # '.' is our internal root sentinel, never an admitted input path.
                directory=root if name=='.' else runtime._safe(root,name)
                if _identity(directory.lstat())!=expected:raise_code('EVT-101',reason='git_candidates_changed')
            if (runtime._layout(root,metadata_root)!=(common,gitdir,kind,pointer)
                    or runtime._head(common,gitdir)!=head or runtime._index(common,gitdir)!=index):
                raise_code('EVT-101',reason='git_metadata_changed')
            runtime._metadata_boundary(common)
        if untracked!=await candidates() or (self.command.include_ignored and ignored!=await candidates(True)):
            raise_code('EVT-101',reason='git_candidates_changed')
        verify();self.verify=verify;self.items=rows
        self.identity_digest=digest_of(canonical({'files':files,'directories':directories,'ignore_metadata':rules,'pointer_digest':digest_of(pointer),'gitdir_relative':gitdir.relative_to(common).as_posix()}))
        self.observation={'schema_version':1,'status':'git_candidates_observed','project_id':source['grant']['project_id'],'project_revision':self.command.project_revision,
            'grant_id':source['grant']['grant_id'],'grant_revision':source['grant']['revision'],'grant_source_seq':source['source_seq'],
            'metadata_grant_id':metadata['grant']['grant_id'] if metadata else None,'metadata_grant_revision':metadata['grant']['revision'] if metadata else None,
            'metadata_grant_source_seq':metadata['source_seq'] if metadata else None,'repository_kind':kind,'head_oid':commit.decode('ascii'),'index_digest':digest_of(index or b''),
            'scope':'tracked_and_untracked_candidates','ignored_requested':self.command.include_ignored,'ignore_policy':'worktree_gitignore_only','body_policy':'git_metadata_and_ignore_rules_only','content_safety':'not_evaluated',
            'capture_stability':'stable_observed','persisted':False,'executable':False,'required_tracked_count':len(tracked),
            'excluded_required_count':sum(row['required'] and row['status']=='excluded' for row in rows),'capture_file_limit':200}

    def page(self):
        from pyharness.application.workspace_store import canonical,digest_of
        body=self.command;after=-1
        view=digest_of(canonical({'observation':self.observation,'rows':self.items,'identity':self.identity_digest,'limit':body.limit,'actor':self.actor,'tenant':self.tenant}))
        if body.cursor:
            match=re.fullmatch(r'1:([1-9][0-9]?):(0|[1-9][0-9]{0,3}):([0-9a-f]{64})',body.cursor)
            if not match or int(match[1])!=body.limit:raise_code('EVT-100',reason='git_candidate_cursor_invalid')
            if match[3]!=view:raise_code('EVT-101',reason='git_candidate_view_changed')
            after=int(match[2])
            if not 0<=after<len(self.items)-1:raise_code('EVT-100',reason='git_candidate_cursor_invalid')
        selected=self.items[after+1:after+1+body.limit];more=after+1+len(selected)<len(self.items)
        cursor=f"1:{body.limit}:{selected[-1]['entry_index']}:{view}" if more else None
        page={**self.observation,'items':selected,'total':len(self.items),'page_count':len(selected),'has_more':more,'next_cursor':cursor,'view_digest':view}
        page['page_digest']=digest_of(canonical(page))
        if len(canonical(page))>512*1024:raise_code('EVT-100',reason='git_candidate_response_limit')
        return page
