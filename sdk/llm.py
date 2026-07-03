"""LLM client — HTTP layer for OpenAI-compatible API."""
import json
import logging
import os
import re
import socket
import time
import urllib.request
from typing import Dict, List, Optional

from sdk.schemas import (
    ChatCompletionResponse,
    ChatMessage,
    ChatChoice,
    ChatResponseMessage,
    Usage,
    ResponseFormat,
    JsonSchema,
    ToolDefinition,
)

logger = logging.getLogger(__name__)


# ── Strip thinking tags ─────────────────────────────────────────────

def _strip_think(text: str) -> str:
    """Strip thinking tags and leaked XML fragments from LLM output."""
    if not text:
        return ""
    text = re.sub(r" thinking.*? ", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(
        r"<[^>]*(?:think|anth|antth)[^>]*>.*?</[^>]*(?:think|anth|antth)[^>]*>",
        "", text, flags=re.DOTALL | re.IGNORECASE,
    )
    m = re.match(r"^\s*<[^>]*(?:think|anth|antth)[^>]*>", text, flags=re.IGNORECASE)
    if m:
        rest = text[m.end():]
        if not re.search(r"</[^>]*(?:think|anth|antth)[^>]*>", rest, re.IGNORECASE):
            rest = re.sub(r"^.*?(?=\n\n|\Z)", "", rest, flags=re.DOTALL)
        text = rest
    text = re.sub(r"<function=[^>]*>.*?</function>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(
        r"<(?:issue_description|reset|task|context)[^>]*>.*?</(?:issue_description|reset|task|context)",
        "", text, flags=re.DOTALL | re.IGNORECASE,
    )
    m2 = re.match(
        r"^\s*<(?:function=[^>]*|issue_description[^>]*|reset[^>]*|task[^>]*|context[^>]*|\|mask_start\|)",
        text, flags=re.IGNORECASE,
    )
    if m2:
        text = text[m2.end():]
    text = re.sub(r"<\|mask_(?:start|end)\|>", "", text, flags=re.IGNORECASE)
    return text.strip()


def _prune_tool_output(name: str, content: str, max_chars: int = 8000) -> str:
    """Prune large tool outputs to save context tokens while preserving useful info."""
    if len(content) <= max_chars:
        return content
    half = max_chars // 2 - 100
    kept_lines_start = content[:half].count("\n")
    kept_lines_end = content[-half:].count("\n")
    skipped_lines = content.count("\n") - kept_lines_start - kept_lines_end
    truncated = (content[:half]
                 + f"\n... [{skipped_lines} lines / {len(content) - max_chars:,} chars omitted] ...\n"
                 + content[-half:])
    return truncated


# ── Merged JSON Schema mode ────────────────────────────────────────

def _build_schema(tools: List[ToolDefinition], business_schema: Optional[Dict] = None) -> Dict:
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
        fn = tool.function
        params = fn.parameters
        tool_items.append({
            "type": "object",
            "properties": {
                "name": {"type": "string", "enum": [fn.name]},
                "args": params.model_dump() if hasattr(params, 'model_dump') else params,
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
    }

    # Nest business schema under a "result" field
    if business_schema:
        properties["result"] = {
            "type": "object",
            "description": "Final answer when no more tools are needed. Only provide when tools is empty.",
            **business_schema,
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


def _parse_response(raw_text: str) -> ChatCompletionResponse:
    """Parse JSON text from merged-schema mode into ChatCompletionResponse.

    Merged schema: {thought, tools, <business fields>}.
    - tools non-empty → tool_calls (model needs to call tools)
    - tools empty / missing → final result (business fields are the answer)
    """
    import time
    # OpenAI-compatible ID: chatcmpl-<timestamp>-<random>
    import uuid
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
            id=req_id, object="chat.completion", model="merged",
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
        id=req_id, object="chat.completion", model="merged",
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

    def __init__(self, url: str, model: str, api_key: str = "", timeout: int = 300):
        base_url = url.rstrip("/")
        self.url = base_url + "/chat/completions"
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler())

    def _post(self, body: Dict) -> Dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        last_err = None
        body_json = json.dumps(body)
        for attempt in range(1, 4):
            req = urllib.request.Request(
                self.url, data=body_json.encode(),
                headers=headers, method="POST")
            try:
                with self._opener.open(req, timeout=self.timeout) as r:
                    return json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                err_body = e.read().decode() if hasattr(e, 'read') else ''
                logger.error(f"[llm] HTTP {e.code}: {err_body[:500]}")
                last_err = e
                raise  # don't retry on 400-level errors
            except (socket.timeout, urllib.error.URLError) as e:
                last_err = e
                if attempt < 3:
                    logger.warning(f"[llm] request failed (attempt {attempt}), retrying in {2 ** attempt}s: {e}")
                    time.sleep(2 ** attempt)

        raise last_err

    def chat(self, messages: list[ChatMessage], *, temperature: float = 0.6,
             max_tokens: int = 2048, tools: List[ToolDefinition] = None,
             response_format: Optional[ResponseFormat] = None) -> ChatCompletionResponse:
        """Non-streaming chat completion.

        When both ``tools`` and ``response_format`` are present, enters
        *merged-schema mode*: encodes all tool definitions into the JSON
        schema so the model can emit both tool calls and a final structured
        answer in a single response.  The caller receives a standard
        ``ChatCompletionResponse`` regardless of the internal mode.
        """
        _msgs = []
        for m in messages:
            d = m.model_dump(exclude_none=True) if hasattr(m, 'model_dump') else dict(m)
            # Ensure non-assistant roles always have 'content' (llama-server rejects missing content)
            if d.get("role") != "assistant" and "content" not in d:
                d["content"] = ""
            _msgs.append(d)
        _merged = bool(tools and response_format)

        if _merged:
            business_schema = _extract_schema(response_format)
            merged = _build_schema(tools, business_schema)
            body = {
                "model": self.model, "messages": _msgs,
                "temperature": temperature, "max_tokens": max_tokens,
                "stream": False, "response_format": merged,
            }
            raw = self._post(body)
            msg = raw["choices"][0]["message"]
            return _parse_response(msg.get("content", ""))

        body = {
            "model": self.model, "messages": _msgs,
            "temperature": temperature, "max_tokens": max_tokens,
            "stream": False,
        }
        if tools:
            body["tools"] = tools
        if response_format:
            body["response_format"] = response_format
        raw = self._post(body)
        return ChatCompletionResponse.model_validate(raw)
