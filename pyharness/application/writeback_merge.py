"""Bounded byte-preserving three-way merge; fixed validators never execute code."""
import ast
import difflib
import json
from pathlib import PurePosixPath
from pyharness.application.source_snapshot import LIMIT,check_content
from pyharness.errors import PyHError

MERGE_VERSION='exact-lines-three-way-v1'
VALIDATOR_VERSION='text-policy-syntax-v1'


def _conflict(reason,regions=None):
    return dict(status='conflict',merged=None,conflicts=regions or [dict(reason=reason)])


def _edits(base,changed):
    return [(i,j,tuple(changed[a:b])) for tag,i,j,a,b in
        difflib.SequenceMatcher(a=base,b=changed,autojunk=False).get_opcodes() if tag!='equal']


def _overlap(a,b):
    i,j,_=a;k,l,_=b
    if i==j:return k<=i<=l
    if k==l:return i<=k<=j
    return max(i,k)<min(j,l)


def merge_bytes(base,current,delivery):
    """None denotes absence; conflict results deliberately contain no merged bytes."""
    if any(v is not None and not isinstance(v,bytes) for v in (base,current,delivery)):
        raise TypeError('merge inputs must be exact bytes or absence')
    if any(v is not None and len(v)>LIMIT for v in (base,current,delivery)):
        return _conflict('merge_size_limit')
    if current==delivery:return dict(status='mergeable',merged=current,conflicts=[])
    if current==base:return dict(status='mergeable',merged=delivery,conflicts=[])
    if delivery==base:return dict(status='mergeable',merged=current,conflicts=[])
    if any(v is None for v in (base,current,delivery)):
        return _conflict('creation_or_deletion_conflict')
    try:versions=[v.decode('utf-8').splitlines(keepends=True) for v in (base,current,delivery)]
    except UnicodeDecodeError:return _conflict('binary_concurrent_change')
    if any(len(v)>4096 for v in versions) or any(len(v)>1024*1024 for v in (base,current,delivery)):
        return _conflict('concurrent_text_comparison_limit')
    original,left,right=versions;mine=_edits(original,left);theirs=_edits(original,right)
    conflicts=[]
    for a in mine:
        for b in theirs:
            if a!=b and _overlap(a,b):
                conflicts.append(dict(reason='overlapping_changes',base_start_line=min(a[0],b[0])+1,
                    base_end_line=max(a[1],b[1])+1))
    if conflicts:return _conflict('overlapping_changes',conflicts[:100])
    edits=sorted(set(mine+theirs),key=lambda e:(e[0],e[1]));out=[];cursor=0
    for start,end,replacement in edits:
        out.extend(original[cursor:start]);out.extend(replacement);cursor=end
    out.extend(original[cursor:]);raw=''.join(out).encode('utf-8')
    if len(raw)>LIMIT:return _conflict('merged_size_limit')
    return dict(status='mergeable',merged=raw,conflicts=[])


def validate_result(path,raw):
    """Only versioned builtin checks: no imports, shell commands or user predicates."""
    result=dict(version=VALIDATOR_VERSION,status='passed',checks=['absence' if raw is None else 'bounded-text-policy'])
    if raw is None:return result
    try:
        if len(raw)>LIMIT:raise ValueError('size')
        suffix=PurePosixPath(path).suffix.lower()
        if suffix in ('.py','.json') and len(raw)>128*1024:raise ValueError('syntax byte limit')
        check_content(raw)
        text=raw.decode('utf-8-sig')
        if suffix=='.py':ast.parse(text);result['checks'].append('python-syntax')
        if suffix=='.json':
            def reject_constant(value):raise ValueError('nonfinite JSON')
            json.loads(text,parse_constant=reject_constant);result['checks'].append('json-syntax')
    except (ValueError,SyntaxError,PyHError,RecursionError):
        result.update(status='failed',reason='fixed_content_or_syntax_validation_failed')
    return result
