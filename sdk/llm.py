"""LLM client — HTTP layer for OpenAI-compatible API."""
import json
import logging
import socket
import time
import urllib.request
import uuid
from typing import Dict, List, Optional

from sdk.schemas import (
    ChatCompletionResponse,
    ChatMessage,
    ChatChoice,
    ChatResponseMessage,
    Usage,
    ResponseFormat,
    JsonSchema,
)

logger = logging.getLogger(__name__)



# ── Merged JSON Schema mode ────────────────────────────────────────

def _build_schema(tools: List[Dict], business_schema: Dict) -> Dict:
    """Build a merged JSON Schema encoding both tool calls and business response.

    Schema structure:
    {
      "thought": "string",          // Reasoning process
      "tools": [{...}],           // Tool calls (empty when done)
      "result": {...}               // Final answer when no more tools needed
    }

    llama-server supports nested schemas via json_schema, so we keep the result
    object nested. The description fields guide the model to use the schema correctly.
    """
    # Build tool items from tool definitions
    tool_items = []
    for tool in tools:
        fn = tool["function"]
        params = fn["parameters"]
        tool_items.append({
            "type": "object",
            "properties": {
                "name": {"type": "string", "enum": [fn["name"]]},
                "args": params,
            },
            "required": ["name", "args"],
            "additionalProperties": False,
        })

    properties: Dict = {
        "thought": {
            "type": "string",
            "description": "Analyze the current situation and decide what to do next."
        },
        "tools": {
            "type": "array",
            "description": "If you need to use tools, put them here. Otherwise leave empty.",
            "items": tool_items[0] if len(tool_items) == 1 else {"anyOf": tool_items},
        },
        "result": {
            "type": "object",
            "description": "Final answer when no more tools are needed. Only provide when tools is empty.",
            **business_schema,
        },
    }

    return {
        "type": "json_schema",
        "json_schema": {
            "name": "agent_response",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": properties,
                "required": ["thought", "tools"],
                "additionalProperties": False,
            }
        }
    }


def _extract_schema(response_format: ResponseFormat) -> Dict:
    """Extract inner schema dict from ResponseFormat."""
    return response_format.json_schema.schema if response_format.json_schema else {}


def _parse_response(raw_text: str, model: str) -> ChatCompletionResponse:
    """Parse JSON text from merged-schema mode into ChatCompletionResponse.

    Merged schema: {thought, tools, <business fields>}.
    - tools non-empty → tool_calls (model needs to call tools)
    - tools empty / missing → final result (business fields are the answer)
    """
    req_id = f"chatcmpl-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        # Invalid JSON — raise to trigger retry
        raise ValueError(f"Invalid JSON in merged-schema response: {raw_text}")

    tools = parsed.get("tools") or []
    if tools:
        # Model wants to call tools
        tool_calls = []
        for i, tool in enumerate(tools):
            tool_calls.append({
                "id": f"call_{req_id[-6:]}_{i}",
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "args": json.dumps(tool.get("args", {}), ensure_ascii=False)
                }
            })
        content = parsed.get("thought", "")
        return ChatCompletionResponse(
            id=req_id, object="chat.completion", model=model,
            created=int(time.time()),
            choices=[ChatChoice(
                index=0,
                message=ChatResponseMessage(
                    role="assistant",
                    content=content,
                    tool_calls=tool_calls,
                ),
                finish_reason="tool_calls",
            )],
            usage=Usage(completion_tokens=0, prompt_tokens=0, total_tokens=0),
        )

    # No tools → final result (thought is required, result should be present)
    if "result" not in parsed:
        raise ValueError(f"Schema violation: missing 'result' field. Full response: {raw_text}")
    result = parsed["result"]
    if isinstance(result, dict):
        content = json.dumps(result, ensure_ascii=False)
    else:
        content = str(result)

    return ChatCompletionResponse(
        id=req_id, object="chat.completion", model=model,
        created=int(time.time()),
        choices=[ChatChoice(
            index=0,
            message=ChatResponseMessage(
                role="assistant",
                content=content,
            ),
            finish_reason="stop",
        )],
        usage=Usage(completion_tokens=0, prompt_tokens=0, total_tokens=0),
    )


# ── LLM class ────────────────────────────────────────────────────

class LLM:
    """HTTP client for OpenAI-compatible LLM API."""

    def __init__(self, url: str, api_key: str = "", timeout: int = 300):
        base_url = url.rstrip("/")
        self.url = base_url + "/chat/completions"
        self.api_key = api_key
        self.timeout = timeout
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler())

    def _post(self, body: Dict) -> Dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        last_err = None
        body_json = json.dumps(body)
        msgs = body.get("messages", [])
        logger.debug(f"[llm] POST {len(msgs)} msgs, body={len(body_json):,} chars, keys={sorted(body.keys())}")
        for attempt in range(1, 4):
            req = urllib.request.Request(
                self.url, data=body_json.encode(),
                headers=headers, method="POST")
            try:
                with self._opener.open(req, timeout=self.timeout) as r:
                    raw = r.read().decode()
                    resp = json.loads(raw)
                    usage = resp.get("usage", {})
                    if usage:
                        logger.debug(f"[llm] OK usage={usage}")
                    return resp
            except urllib.error.HTTPError as e:
                err_body = e.read().decode() if hasattr(e, 'read') else ''
                logger.error(f"[llm] HTTP {e.code}: {err_body[:500]}")
                last_err = e
                if e.code < 500:
                    raise  # don't retry on client errors (4xx)
            except (socket.timeout, urllib.error.URLError) as e:
                last_err = e
                if attempt < 3:
                    logger.warning(f"[llm] request failed (attempt {attempt}), retrying in {2 ** attempt}s: {e}")
                    time.sleep(2 ** attempt)

        raise last_err

    def chat(self, messages: list[ChatMessage], *, model: str,
             temperature: float = 0.6,
             max_tokens: int = 2048, tools: List[Dict] = None,
             response_format: Optional[ResponseFormat] = None) -> ChatCompletionResponse:
        """Non-streaming chat completion.

        When both ``tools`` and ``response_format`` are present, enters
        *merged-schema mode*: encodes all tool definitions into the JSON
        schema so the model can emit both tool calls and a final structured
        answer in a single response.  The caller receives a standard
        ``ChatCompletionResponse`` regardless of the internal mode.
        """
        # Serialize messages — ensure non-assistant roles always have 'content'
        _msgs = []
        for m in messages:
            d = m.model_dump(exclude_none=True)
            if d.get("role") != "assistant" and "content" not in d:
                d["content"] = ""
            _msgs.append(d)

        # Build request body
        body: Dict = {
            "model": model, "messages": _msgs,
            "temperature": temperature, "max_tokens": max_tokens,
            "stream": False,
        }

        if tools and response_format:
            # Merged-schema mode: encode tools into the JSON schema
            business_schema = _extract_schema(response_format)
            body["response_format"] = _build_schema(tools, business_schema)
        else:
            if tools:
                body["tools"] = tools
            if response_format:
                body["response_format"] = response_format.model_dump(exclude_none=True)

        raw = self._post(body)

        if tools and response_format:
            # Parse merged-schema response
            msg = raw["choices"][0]["message"]
            resp = _parse_response(msg.get("content", ""), model)
            content_len = len(resp.choices[0].message.content or "")
            logger.debug(f"[llm] merged response: {content_len} chars")
            return resp

        # Standard OpenAI-compatible response
        resp = ChatCompletionResponse.model_validate(raw)
        usage = (resp.usage.model_dump() if resp.usage else {})
        logger.debug(f"[llm] response: {usage}")
        return resp
