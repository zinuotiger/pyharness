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
    """Explicit HTTP contract: POST /query; citations and answer_allowed required.

    No implicit endpoint, fallback, embedding model, or startup dependency.
    """
    def __init__(self, endpoint: str):
        from pyharness.application.service import _validate_registry_url
        self.endpoint = _validate_registry_url(endpoint).rstrip('/')

    async def query(self, query: str, documents: list[dict]) -> dict:
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
        citations = body.get('citations')
        if not isinstance(citations, list) or len(citations) > 10:
            raise ValueError('knowledge_invalid_response')
        allowed = {d['source_id']:d for d in documents}
        if any(not isinstance(c, dict) or c.get('source_id') not in allowed
               or not isinstance(c.get('quote'), str) or not c['quote'] or len(c['quote']) > 1000
               or c['quote'] not in allowed[c['source_id']]['text'] for c in citations):
            raise ValueError('knowledge_invalid_citation')
        return {'provider': 'remote_rag', 'citations': citations,
                'answer_allowed': bool(citations) and body.get('answer_allowed') is True,
                'reason': None if citations else 'no_reliable_evidence'}
