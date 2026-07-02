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


def _prune_tool_output(tool_name: str, content: str, max_chars: int = 8000) -> str:
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

def _build_merged_schema(tools: List[Dict], business_schema: Optional[Dict] = None) -> Dict:
    """Build a merged JSON Schema encoding both tool calls and business response.

    Uses flat structure (no nested result object) because llama-server does not
    reliably honour oneOf / deeply-nested schemas.
    """
    # Build action items from tool definitions
    tool_items = []
    for tool in tools:
        fn = tool.get("function", {})
        params = fn.get("parameters", {"type": "object"})
        tool_items.append({
            "type": "object",
            "properties": {
                "tool_name": {"type": "string", "enum": [fn["name"]]},
                "tool_arguments": params,
            },
            "required": ["tool_name", "tool_arguments"],
            "additionalProperties": False,
        })

    properties: Dict = {
        "thought": {
            "type": "string",
            "description": "Analyze the current situation and decide what to do next."
        },
        "actions": {
            "type": "array",
            "description": "If you need to use tools, put them here. Otherwise leave empty.",
            "items": tool_items[0] if len(tool_items) == 1 else {"anyOf": tool_items},
        },
    }

    # Nest business schema under a "result" field
    if business_schema:
        properties["result"] = {
            "type": "object",
            "description": "Final answer when no more tools are needed. Only provide when actions is empty.",
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
                "required": ["thought", "actions"],
                "additionalProperties": False,
            }
        }
    }


def _extract_business_schema(response_format: Optional[Dict]) -> Optional[Dict]:
    """Extract inner schema dict from a response_format payload."""
    if not isinstance(response_format, dict):
        return None
    if "json_schema" in response_format:
        inner = response_format["json_schema"]
        return inner.get("schema") if isinstance(inner, dict) else None
    if "schema" in response_format:
        rf_schema = response_format["schema"]
        return rf_schema if isinstance(rf_schema, dict) else None
    return None


def _parse_merged_response(raw_text: str) -> ChatCompletionResponse:
    """Parse JSON text from merged-schema mode into ChatCompletionResponse.

    Merged schema is flat: {thought, actions, <business fields>}.
    - actions non-empty → tool_calls (model needs to call tools)
    - actions empty / missing → final result (business fields are the answer)
    """
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        return ChatCompletionResponse(
            id="merged-fallback", object="chat.completion", model="merged",
            created=0,
            choices=[ChatChoice(
                index=0,
                message=ChatResponseMessage(role="assistant", content=raw_text),
                finish_reason="stop",
            )],
            usage=Usage(completion_tokens=0, prompt_tokens=0, total_tokens=0),
        )

    actions = parsed.get("actions") or []

    if actions:
        # Model wants to call tools
        tool_calls = []
        for i, action in enumerate(actions):
            tool_calls.append({
                "id": f"call_{i}",
                "type": "function",
                "function": {
                    "name": action["tool_name"],
                    "arguments": json.dumps(action.get("tool_arguments", {}))
                }
            })
        content = parsed.get("thought", "")
        return ChatCompletionResponse(
            id="merged-tool-calls", object="chat.completion", model="merged",
            created=0,
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

    # No actions → final result
    result = parsed.get("result")
    if isinstance(result, dict):
        content = json.dumps(result)
    elif result is not None:
        content = str(result)
    else:
        # Fallback: collect non-meta fields
        business = {k: v for k, v in parsed.items()
                    if k not in ("thought", "actions")}
        content = json.dumps(business) if business else parsed.get("thought", "")

    return ChatCompletionResponse(
        id="merged-result", object="chat.completion", model="merged",
        created=0,
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
        for attempt in range(1, 4):
            req = urllib.request.Request(
                self.url, data=json.dumps(body).encode(),
                headers=headers, method="POST")
            try:
                with self._opener.open(req, timeout=self.timeout) as r:
                    return json.loads(r.read().decode())
            except (socket.timeout, urllib.error.URLError) as e:
                last_err = e
                if attempt < 3:
                    logger.warning(f"[llm] request failed (attempt {attempt}), retrying in {2 ** attempt}s: {e}")
                    time.sleep(2 ** attempt)

        raise last_err

    def chat(self, messages: list[ChatMessage], *, temperature: float = 0.6,
             max_tokens: int = 2048, tools: List[Dict] = None,
             response_format: Optional[Dict] = None) -> ChatCompletionResponse:
        """Non-streaming chat completion.

        When both ``tools`` and ``response_format`` are present, enters
        *merged-schema mode*: encodes all tool definitions into the JSON
        schema so the model can emit both tool calls and a final structured
        answer in a single response.  The caller receives a standard
        ``ChatCompletionResponse`` regardless of the internal mode.
        """
        _msgs = [m.model_dump(exclude_none=True) for m in messages]
        _merged_mode = bool(tools and response_format)

        if _merged_mode:
            business_schema = _extract_business_schema(response_format)
            merged = _build_merged_schema(tools, business_schema)
            body = {
                "model": self.model, "messages": _msgs,
                "temperature": temperature, "max_tokens": max_tokens,
                "stream": False, "response_format": merged,
            }
            raw = self._post(body)
            msg = raw["choices"][0]["message"]
            return _parse_merged_response(msg.get("content", ""))

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
