"""Explicit local, one-call model smoke. CI uses MockTransport, never paid keys.

Only the bundled sample is read. No tools or host execution are offered. The
cost ceiling uses operator-supplied per-million token rates and a conservative
UTF-8-byte input bound; it cannot guarantee a provider's billing practices.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlparse
import httpx

ROOT=Path(__file__).resolve().parents[1]

def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute',action='store_true')
    p.add_argument('--base-url')
    p.add_argument('--model')
    p.add_argument('--key-env',help='Explicit name of one environment variable; never a literal key')
    p.add_argument('--max-tokens',type=int,default=128)
    p.add_argument('--max-cost',type=float,default=0.02)
    p.add_argument('--input-price',type=float,default=None,help='USD per million input tokens')
    p.add_argument('--output-price',type=float,default=None,help='USD per million output tokens')
    p.add_argument('--timeout',type=float,default=15)
    p.add_argument('--sandbox',choices=['disabled'],default='disabled')
    return p

async def smoke(args, *, transport=None):
    refusal={'status':'refused','real_model':False,'calls':0,'sandbox':'disabled'}
    if not args.execute: return {**refusal,'reason':'explicit_execute_required'}
    if not args.base_url or not args.model or not args.key_env: return {**refusal,'reason':'explicit_connection_required'}
    url=urlparse(args.base_url)
    if url.username or url.password or url.query or url.fragment or not url.hostname:
        return {**refusal,'reason':'invalid_endpoint'}
    if url.scheme!='https' and not (url.scheme=='http' and url.hostname in {'localhost','127.0.0.1','::1'}):
        return {**refusal,'reason':'https_or_loopback_required'}
    if not (1<=args.max_tokens<=256 and 1<=args.timeout<=30 and 0<args.max_cost<=0.05):
        return {**refusal,'reason':'budget_out_of_bounds'}
    import math
    if any(v is None or not math.isfinite(v) or v<=0 for v in (args.input_price,args.output_price)):
        return {**refusal,'reason':'explicit_positive_prices_required'}
    sample='\n'.join((ROOT/'examples/platform-code-change'/n).read_text(encoding='utf-8') for n in ('add.py','check.py'))
    prompt='Explain the bug in this sample in one sentence. Do not call tools.\n'+sample
    input_bound=len(prompt.encode('utf-8'))+256
    ceiling=(input_bound*args.input_price+args.max_tokens*args.output_price)/1_000_000
    if ceiling>args.max_cost: return {**refusal,'reason':'estimated_budget_exceeded'}
    key=os.environ.get(args.key_env)
    if not key: return {**refusal,'reason':'credential_missing'}
    result={'status':'failed','real_model':transport is None,'transport':'real' if transport is None else 'fake',
            'calls':1,'sandbox':'disabled','max_output_tokens':args.max_tokens,'cost_ceiling_usd':ceiling,
            'billing_basis':'operator supplied rates; one call; conservative input bound',
            'response_content_logged':False}
    try:
        async with httpx.AsyncClient(transport=transport,timeout=args.timeout,trust_env=False,follow_redirects=False) as client:
            async with asyncio.timeout(args.timeout):
                async with client.stream('POST',args.base_url.rstrip('/')+'/chat/completions',
                    headers={'Authorization':'Bearer '+key},json={'model':args.model,'messages':[{'role':'user','content':prompt}],
                    'max_tokens':args.max_tokens,'stream':False}) as response:
                    response.raise_for_status()
                    body=bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body)>65536: raise ValueError('response_limit')
                    data=json.loads(body)
                    usage=data.get('usage',{})
                    if not data.get('choices') or not isinstance(data['choices'][0].get('message',{}).get('content'),str):
                        raise ValueError('invalid_response')
                    if data['choices'][0]['message'].get('tool_calls'): raise ValueError('unexpected_tools')
                    tokens={k:int(usage.get(k,0)) for k in ('prompt_tokens','completion_tokens')}
                    if tokens['completion_tokens']>args.max_tokens or tokens['prompt_tokens']>input_bound:
                        raise ValueError('provider_budget_violation')
                    return {**result,'status':'passed','usage':tokens}
    except (httpx.HTTPError,ValueError,TypeError,KeyError,IndexError,TimeoutError):
        # Never emit exception bodies, request headers, endpoint or model output.
        return {**result,'reason':'request_or_contract_failed'}

def main(argv=None):
    result=asyncio.run(smoke(parser().parse_args(argv)))
    print(json.dumps(result))
    return 0 if result['status']=='passed' else 2

if __name__=='__main__':raise SystemExit(main())
