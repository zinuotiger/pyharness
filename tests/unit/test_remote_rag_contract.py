"""Untrusted retrieval responses must bind to current authorized documents."""
import copy
import pytest
from pyharness.core.knowledge import RemoteRagKnowledgeProvider

DOCS=[{'source_id':'k-owned','name':'rules.txt','text':'Header\nThe synthetic limit is seven.\nNext line.','version':2}]


def provider(monkeypatch,body):
    p=RemoteRagKnowledgeProvider('http://127.0.0.1:12345/api/v1')
    calls=[]
    async def request(query,documents):calls.append((query,documents));return body
    monkeypatch.setattr(p,'_request',request)
    return p,calls


def native(text='The synthetic limit is seven.',source='k-owned',status='matched'):
    return {'status':'ok','data':{'match_status':status,'answer':'untrusted answer is never returned',
                                'citations':[{'source':source,'text':text,'document_id':999,'chunk_id':999}]}}


async def test_native_exact_source_quote_and_line(monkeypatch):
    p,calls=provider(monkeypatch,native('The synthetic limit is seven.\nNext line.'))
    result=await p.query('synthetic limit',DOCS)
    assert result['answer_allowed'] and len(result['citations'])==2 and len(calls)==1
    assert result['citations'][0]=={'source_id':'k-owned','name':'rules.txt','line':2,'quote':'The synthetic limit is seven.','version':2}
    assert result['citations'][1]['line']==3 and 'answer' not in result


@pytest.mark.parametrize('body',[native(source='rules.txt'),native(source='foreign'),native(text='stale quote'),
    {'status':'ok','data':{'citations':[]}}, {'status':'error','data':{}},
    {'citations':[]}, native(status='unknown'), native(text='')])
async def test_reject_unbound_and_ambiguous_responses(monkeypatch,body):
    p,_=provider(monkeypatch,body)
    with pytest.raises(ValueError):await p.query('limit',DOCS)


@pytest.mark.parametrize('status',['no_reliable_match','blocked'])
async def test_remote_denial_cannot_be_overridden_by_citation(monkeypatch,status):
    p,_=provider(monkeypatch,native(status=status))
    result=await p.query('limit',DOCS)
    assert not result['answer_allowed'] and result['reason']=='no_reliable_evidence'


async def test_empty_authorization_is_local_but_probe_uses_transport(monkeypatch):
    p,calls=provider(monkeypatch,{'status':'ok','data':{'match_status':'no_reliable_match','citations':[]}})
    assert not (await p.query('query',[]))['answer_allowed'] and not calls
    assert (await p.probe())['status']=='available' and len(calls)==1


@pytest.mark.parametrize('line',[0,99,True,None])
async def test_canonical_rejects_invalid_line(monkeypatch,line):
    p,_=provider(monkeypatch,{'answer_allowed':True,'citations':[{'source_id':'k-owned','quote':'Header','line':line}]})
    with pytest.raises(ValueError):await p.query('Header',DOCS)


async def test_quote_in_other_document_does_not_authorize_source(monkeypatch):
    p,_=provider(monkeypatch,native())
    docs=copy.deepcopy(DOCS);docs[0]['text']='replacement'
    docs.append({**DOCS[0],'source_id':'other'})
    with pytest.raises(ValueError):await p.query('limit',docs)
