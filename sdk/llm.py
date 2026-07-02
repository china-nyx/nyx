"""LLM client — HTTP layer for OpenAI-compatible API.

Provides chat() for free-text completion.
"""
import json
import os
import re
import socket
import time
import urllib.request
from typing import Dict, List, Optional

from app.config import config
from app.log import get_logger

logger = get_logger(__name__)


def _strip_think(text: str) -> str:
    """Strip thinking tags and leaked XML fragments from LLM output."""
    if not text:
        return ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
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
        r"<(?:issue_description|reset|task|context)[^>]*>.*?</(?:issue_description|reset|task|context)>",
        "", text, flags=re.DOTALL | re.IGNORECASE,
    )
    m2 = re.match(
        r"^\s*<(?:function=[^>]*|issue_description[^>]*|reset[^>]*|task[^>]*|context[^>]*|\|mask_start\|)>",
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


class LLM:
    """HTTP client for OpenAI-compatible LLM API."""

    def __init__(self, url: str = None, model: str = None):
        base_url = (url or config.LLM_BASE_URL).rstrip("/")
        self.url = base_url + "/chat/completions"
        self.model = model or config.LLM_MODEL
        self.api_key = config.LLM_API_KEY
        self.timeout = config.LLM_TIMEOUT
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

    def chat(self, messages: List[Dict], temperature: float = 0.6, max_tokens: int = 2048,
             response_format: Optional[Dict] = None) -> Dict:
        """Non-streaming chat completion. Returns AssistantMessage dict."""
        body = {"model": self.model, "messages": messages,
                "temperature": temperature, "max_tokens": max_tokens, "stream": False}
        if response_format:
            body["response_format"] = response_format
        resp = self._post(body)
        msg = resp["choices"][0]["message"]
        content = _strip_think(msg.get("content") or msg.get("reasoning_content") or "")
        return {
            "role": "assistant",
            "content": content,
            "stopReason": resp["choices"][0].get("finish_reason", "stop"),
            "usage": {k: resp.get("usage", {}).get(k, 0) for k in ("input","output","cacheRead","cacheWrite","totalTokens")},
            "timestamp": int(time.time() * 1000),
        }

