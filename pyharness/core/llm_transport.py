"""Llm transport: complete migrated implementation."""
from __future__ import annotations
try:
    import httpx
except ImportError:
    httpx = None

import asyncio
import json
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, AsyncIterator
from pyharness.errors import raise_code
if TYPE_CHECKING:  # 仅类型标注;装配对象鸭子注入(session/bus 经 ctx)
    pass

from pyharness.core.llm_types import ProviderProtocolError, ProviderStatusError

class _OpenAICompatHTTPTransport:
    """httpx OpenAI 兼容传输(仅 e2e 真实网络路径;unit 注入替身,绝不触网)。

    协议:POST {base}/chat/completions(请求线 ADI §1.1);body 剔除 None 字段(无工具整字段
    省略,wire 规则 §1.2);超时 = 三档 httpx.Timeout(连接=connect_s/读=first_token_s);
    max_retries 语义 = 0(零隐式重试,重试预算全归 F028 编排层,单点留痕)。
    """

    def __init__(self, base_url: str, api_key: str, timeout: Any) -> None:
        if httpx is None:
            raise_code("CYC-999", module="llm.transport", detail="httpx 缺失,无法构建客户端")
        # The key is already supplied to transport construction; never resolve or
        # read credentials for diagnostics. Reject reflected values in metadata.
        def safe_metadata(d):
            return {k: ('unknown' if isinstance(v, str) and api_key and api_key in v else v)
                    for k, v in d.items()}
        self._safe_diagnostics = safe_metadata
        from pyharness.core.context_observation import public_request
        self._public_context_view = lambda req: public_request(req,layer='transport_payload',known_secrets=(api_key,))
        self._base_url = base_url.rstrip("/")       # 裸域或 /v1 结尾(禁写 /chat/completions)
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {api_key}",   # 鉴权只走 header,禁拼 URL
                     # #23 请求归属:每请求带本框架标识(单机自用,审计友好)
                     "X-PyHarness-Agent": "pyharness/1.0"},
            timeout=timeout, max_redirects=2,
            trust_env=False)   # 禁读 env 代理:DeepSeek 直连;应用层不随 HTTP_PROXY 劫持
                                # (桌面 exe 曾因此卡死在 Clash 转发上,180s 才超时)
        self._req_path = "/chat/completions"
        self._validate = None
        self._loop = None

    def _check_send(self):
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise_code('CRED-703', reason='model_transport_event_loop_changed')
        self._loop = loop
        if self._validate is not None:
            self._validate()

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _body(req: dict) -> dict:
        """请求体组装:None 字段剔除(tools 无则整字段省略,qwen 对 [] 语义不稳)。"""
        return {k: v for k, v in req.items() if v is not None}

    def _response_metadata(self, response):
        from pyharness.core.llm_diagnostics import response_metadata, response_observation
        metadata = self._safe_diagnostics(response_metadata(response))
        observation = response_observation.get()
        if observation is not None:
            observation.update(metadata)
        return metadata

    async def complete(self, req: dict) -> Any:
        """非流式 POST → raw 命名空间对象(choices/message/tool_calls/usage 鸭子同构)。"""
        # 断网/连接/读超时等 httpx.HTTPError 直传(归一 303);HTTP ≥400 → 状态错(按状态映射)
        self._check_send()
        resp = await self._client.post(self._req_path, json=self._body(req))
        self._response_metadata(resp)
        if resp.status_code >= 400:
            raise ProviderStatusError(resp.status_code, diagnostics=self._response_metadata(resp))
        try:
            data = resp.json()
        except ValueError as e:
            exc = ProviderProtocolError("响应体非 JSON")
            exc.diagnostics = self._response_metadata(resp)
            raise exc from None
        choices = data.get('choices') if isinstance(data, dict) else None
        message = choices[0].get('message') if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
        if (not isinstance(message, dict) or
                not (isinstance(message.get('content'), str) and message['content'] or message.get('tool_calls'))):
            exc = ProviderProtocolError("choices 空/响应结构非法")
            exc.diagnostics = self._response_metadata(resp)
            raise exc
        return _to_ns(data)

    async def stream(self, req: dict) -> AsyncIterator[Any]:
        """SSE 流式(include_usage):逐行 data: 块 → raw chunk 命名空间;断流异常直传。"""
        body = self._body(req)
        self._check_send()
        async with self._client.stream("POST", self._req_path, json=body) as resp:
            self._response_metadata(resp)
            if resp.status_code >= 400:
                metadata = self._response_metadata(resp)
                data = bytearray()
                try:
                    async for chunk in resp.aiter_bytes():
                        if len(data) + len(chunk) > 16384:
                            data.clear()
                            break
                        data.extend(chunk)
                    body = json.loads(data)
                    from pyharness.core.llm_diagnostics import atom
                    if isinstance(body, dict) and isinstance(body.get('error'), dict):
                        metadata['provider_code'] = atom(body['error'].get('code'))
                except (ValueError, httpx.TransportError):
                    pass
                raise ProviderStatusError(resp.status_code, diagnostics=self._safe_diagnostics(metadata))
            try:
                done = False
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload:
                        continue
                    if payload == "[DONE]":
                        done = True
                        break
                    try:
                        data = json.loads(payload)
                    except ValueError:
                        raise ProviderProtocolError("SSE 块非 JSON") from None
                    if not isinstance(data, dict):
                        raise ProviderProtocolError("Invalid stream response")
                    if 'error' in data:
                        from pyharness.core.llm_diagnostics import atom
                        exc = ProviderProtocolError("Provider stream error")
                        error = data['error']
                        exc.diagnostics = {'provider_code': atom(error.get('code')) if isinstance(error, dict) else 'unknown'}
                        raise exc
                    yield _to_ns(data)
                if not done:
                    raise ProviderProtocolError("Incomplete stream")
            except (httpx.TransportError, ProviderProtocolError) as exc:
                metadata = self._response_metadata(resp)
                metadata.update(getattr(exc, 'diagnostics', {}))
                exc.diagnostics = self._safe_diagnostics(metadata)
                raise

    async def ping(self) -> float:
        """F033 探针:GET /models 往返秒(OpenAI 兼容端点均实现);≥400 → 状态错。"""
        t0 = time.perf_counter()
        self._check_send()
        resp = await self._client.get("/models")
        if resp.status_code >= 400:
            raise ProviderStatusError(resp.status_code, diagnostics=self._response_metadata(resp))
        return time.perf_counter() - t0

def _to_ns(data: Any) -> Any:
    """dict → SimpleNamespace(递归;list 逐元素;原子值原样)——SDK 对象形态鸭子替身。"""
    if isinstance(data, dict):
        return SimpleNamespace(**{k: _to_ns(v) for k, v in data.items()})
    if isinstance(data, list):
        return [_to_ns(x) for x in data]
    return data

# Preserve historical import and pickle identities through the compatibility facade.
_OpenAICompatHTTPTransport.__module__ = 'pyharness.core.llm'
