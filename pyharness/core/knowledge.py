"""Evidence-first document retrieval. Keyword search is the default provider."""
from __future__ import annotations
from typing import Protocol
import re
import httpx


class KnowledgeProvider(Protocol):
    async def query(self, query: str, documents: list[dict]) -> dict: ...


class ExactKeywordKnowledgeProvider:
    async def query(self, query: str, documents: list[dict]) -> dict:
        words = set(re.findall(r'[\w-]+', query.casefold()))
        hits = []
        for document in documents:
            for number, line in enumerate(document['text'].splitlines(), 1):
                score = sum(word in line.casefold() for word in words)
                if score:
                    hits.append({'source_id': document['source_id'], 'name': document['name'],
                        'line': number, 'quote': line[:1000], 'score': score,
                        'version': document.get('version', 1)})
        hits.sort(key=lambda h: (-h['score'], h['name'], h['line']))
        return {'provider': 'exact_keyword', 'citations': hits[:10],
                'answer_allowed': bool(hits), 'reason': None if hits else 'no_reliable_evidence'}


class RemoteRagKnowledgeProvider:
    """Explicit POST /query: canonical or typed retrieval-envelope citations.

    No implicit endpoint, fallback, embedding model, or startup dependency.
    """
    def __init__(self, endpoint: str):
        from pyharness.application.service import _validate_registry_url
        self.endpoint = _validate_registry_url(endpoint).rstrip('/')

    async def _request(self, query: str, documents: list[dict]) -> dict:
        async with httpx.AsyncClient(timeout=5, trust_env=False, follow_redirects=False) as client:
            async with client.stream('POST', self.endpoint + '/query', json={
                'query': query, 'documents':documents, 'source_ids': [d['source_id'] for d in documents], 'limit': 10}) as response:
                response.raise_for_status()
                raw=bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw)>65536:
                        raise ValueError('knowledge_response_too_large')
                import json
                body=json.loads(raw)
        if not isinstance(body,dict):
            raise ValueError('knowledge_invalid_response')
        return body

    @staticmethod
    def _envelope(body):
        if 'status' not in body:
            if not isinstance(body.get('answer_allowed'),bool):
                raise ValueError('knowledge_invalid_response')
            return body,False
        data=body.get('data')
        if body['status']!='ok' or not isinstance(data,dict) or data.get('match_status') not in ('matched','no_reliable_match','blocked'):
            raise ValueError('knowledge_invalid_response')
        return data,True

    async def probe(self):
        """Explicit bounded protocol check; it is never answer evidence."""
        body,native=self._envelope(await self._request('health',[]))
        if not isinstance(body.get('citations'),list) or len(body['citations'])>10:
            raise ValueError('knowledge_invalid_response')
        return {'provider':'remote_rag','status':'available'}

    async def query(self, query: str, documents: list[dict]) -> dict:
        if not documents:
            return {'provider':'remote_rag','citations':[],'answer_allowed':False,'reason':'no_reliable_evidence'}
        body,native=self._envelope(await self._request(query,documents))
        citations = body.get('citations')
        if not isinstance(citations, list) or len(citations) > 10:
            raise ValueError('knowledge_invalid_response')
        allowed = {d['source_id']:d for d in documents}
        if native:
            converted=[]
            for citation in citations:
                if not isinstance(citation,dict):raise ValueError('knowledge_invalid_citation')
                source=citation.get('source');quote=citation.get('text')
                if (not isinstance(source,str) or source not in allowed or not isinstance(quote,str) or not quote or len(quote)>1000
                        or quote not in allowed[source]['text']):
                    raise ValueError('knowledge_invalid_citation')
                # Bind exact contiguous remote snippet to this authorized current
                # document, never remote answer prose or a filename guess.
                text=allowed[source]['text'];offset=text.index(quote)
                start=text[:offset].count('\n')+1
                for number,line in enumerate(quote.splitlines(),start):
                    if line:
                        converted.append({'source_id':source,'name':allowed[source]['name'],'line':number,
                                          'quote':line,'version':allowed[source].get('version',1)})
            if len(converted)>10:raise ValueError('knowledge_invalid_response')
            citations=converted
        if any(not isinstance(c, dict) or not isinstance(c.get('source_id'),str) or c.get('source_id') not in allowed
               or not isinstance(c.get('quote'), str) or not c['quote'] or len(c['quote']) > 1000
               or c['quote'] not in allowed[c['source_id']]['text'] for c in citations):
            raise ValueError('knowledge_invalid_citation')
        for citation in citations:
            lines=allowed[citation['source_id']]['text'].splitlines()
            number=citation.get('line')
            if (not isinstance(number,int) or isinstance(number,bool) or not 1<=number<=len(lines)
                    or citation['quote'] not in lines[number-1]):
                raise ValueError('knowledge_invalid_citation_line')
        return {'provider': 'remote_rag', 'citations': citations,
                'answer_allowed': bool(citations) and (body.get('match_status')=='matched' if native else body.get('answer_allowed') is True),
                'reason': None if citations and (body.get('match_status')=='matched' if native else body.get('answer_allowed') is True) else 'no_reliable_evidence'}
