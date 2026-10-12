"""Evidence-first document retrieval. Keyword search is the default provider."""
from __future__ import annotations
from typing import Protocol
from dataclasses import dataclass
import re
import httpx
from pyharness.core.url_policy import validate_service_url

@dataclass(frozen=True)
class KnowledgeProviderCapabilities:
    max_query_length: int
    supports_sync: bool = False


def validate_query(query, capabilities):
    if not isinstance(query, str) or not query.strip():
        raise KnowledgeError('invalid_query')
    if len(query) > capabilities.max_query_length:
        raise KnowledgeError('invalid_query_length')


class KnowledgeError(ValueError):
    """Safe typed error: never embeds transport headers, response or secrets."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class KnowledgeProvider(Protocol):
    capabilities: KnowledgeProviderCapabilities
    async def query(self, query: str, documents: list[dict]) -> dict: ...


class ExactKeywordKnowledgeProvider:
    capabilities = KnowledgeProviderCapabilities(max_query_length=4000)

    async def query(self, query: str, documents: list[dict]) -> dict:
        validate_query(query, self.capabilities)
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
    capabilities = KnowledgeProviderCapabilities(max_query_length=500, supports_sync=True)

    def __init__(self, endpoint: str, *, secret_ref=None, secret_resolver=None, source_owner=None):
        self.endpoint = validate_service_url(endpoint).rstrip('/')
        self.secret_ref = secret_ref
        self.secret_resolver = secret_resolver
        self.source_owner = source_owner

    async def _request(self, query: str, documents: list[dict]) -> dict:
        validate_query(query, self.capabilities)
        return await self.request('POST', '/query', {
            'query':query, 'documents':documents, 'source_ids':[d['source_id'] for d in documents], 'limit':10,
            **({'source_owner':self.source_owner} if self.source_owner else {})})

    async def request(self, method, path, body=None):
        headers = {}
        if self.secret_ref:
            if self.secret_resolver is None:
                raise KnowledgeError('authentication_error')
            try:
                headers['Authorization'] = 'Bearer ' + self.secret_resolver(self.secret_ref, self.endpoint)
            except Exception:
                raise KnowledgeError('authentication_error') from None
        try:
            return await self._http(method, path, body, headers)
        except KnowledgeError:
            raise
        except (TimeoutError, httpx.TimeoutException):
            raise KnowledgeError('knowledge_timeout') from None
        except httpx.HTTPStatusError as error:
            code = error.response.status_code
            raise KnowledgeError('authentication_error' if code in (401,403)
                else 'invalid_query' if code in (400,422) else 'provider_unavailable') from None
        except Exception:
            raise KnowledgeError('provider_unavailable') from None

    async def _http(self, method, path, body, headers):
        async with httpx.AsyncClient(timeout=5, trust_env=False, follow_redirects=False) as client:
            async with client.stream(method, self.endpoint + path, json=body, headers=headers) as response:
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
        validate_query(query, self.capabilities)
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
        citations = [{'source_id':c['source_id'], 'name':allowed[c['source_id']]['name'],
                      'version':allowed[c['source_id']].get('version', 1),
                      'line':c['line'], 'quote':c['quote']} for c in citations]
        return {'provider': 'remote_rag', 'citations': citations,
                'answer_allowed': bool(citations) and (body.get('match_status')=='matched' if native else body.get('answer_allowed') is True),
                'reason': None if citations and (body.get('match_status')=='matched' if native else body.get('answer_allowed') is True) else 'no_reliable_evidence'}
