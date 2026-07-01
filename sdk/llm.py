"""Local LLM client (llama-server, OpenAI-compatible) + agentic tool loop.

Kernel component. Provides chat() and run_agent() (a multi-round loop with tools).
"""
import hashlib
import json
import os
import re
import socket
import urllib.request
from collections import deque
from typing import Callable, Dict, List, Optional, Set

from core import config
from core.log import get_logger

logger = get_logger(__name__)

from sdk.tools import ALL_TOOLS

# ── Token-aware compaction config (all env-overridable) ──
_CONTEXT_WINDOW = int(os.environ.get("_CONTEXT_WINDOW", "128000"))
_COMPACTION_RESERVE = int(os.environ.get("_COMPACTION_RESERVE", "16384"))
_KEEP_RECENT_TOKENS = int(os.environ.get("_KEEP_RECENT_TOKENS", "20000"))


def estimate_tokens(text: str) -> int:
    """Estimate token count from text using chars/4 heuristic (same as pi)."""
    return max(1, len(text) // 4) if text else 0


def estimate_context_tokens(messages: List[Dict]) -> int:
    """Sum estimated tokens across all messages in the conversation."""
    total = 0
    for msg in messages:
        content = msg.get("content", "") or ""
        total += estimate_tokens(content)
        # Account for tool_calls payload tokens (rough estimate from args JSON)
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            total += estimate_tokens(fn.get("arguments", "") or "")
    return total


def clamp_max_tokens(requested: int, context_tokens: int) -> int:
    """Clamp max_tokens so the total (context + output) stays within the window.

    Leaves a 4096-token safety margin beyond the reserve to avoid OOM / truncation.
    Returns at least 256 so we never request zero tokens.
    """
    headroom = _CONTEXT_WINDOW - context_tokens - 4096
    return max(256, min(requested, headroom))


def _extract_file_paths(messages: List[Dict]) -> Dict[str, Set[str]]:
    """Extract read/modified file paths from tool calls in the given messages.

    Returns {'read': {paths}, 'modified': {paths}} for inclusion in compaction summaries.
    """
    read_files: Set[str] = set()
    modified_files: Set[str] = set()
    for msg in messages:
        role = msg.get("role", "")
        if role != "assistant":
            continue
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            tool_name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except (json.JSONDecodeError, TypeError):
                args = {}
            path = args.get("path", "")
            if path:
                if tool_name == "read":
                    read_files.add(path)
                elif tool_name in ("write", "edit"):
                    modified_files.add(path)
    return {"read": read_files, "modified": modified_files}


def _strip_think(text: str) -> str:
    """Strip thinking tags and leaked XML fragments from LLM output.

    Handles standard ``<thinking>...</thinking>`` plus common variants and
    truncated/corrupted tag names that appear in practice
    (``<antthi>``, ``<anth thinking>``, etc.).  Any opening tag whose name
    contains the substring "think" is matched, paired with a corresponding
    closing tag.

    Also strips leaked XML-like fragments from tool-call context:
    ``<function=...>``, ``<issue_description>``, ``<reset>``, ``<|mask_start|>``, etc.
    These appear when the model's output is truncated mid-tool-call or leaks
    internal formatting tokens.
    """
    if not text:
        return ""
    # Standard <thinking>...</thinking> (case-insensitive, greedy-safe)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Paired thinking-like tags — handles truncated / mangled variants like
    # <antthi>, <anth thinking>, <antThinking>, etc.  The alternation
    # (think|anth|antth) covers both complete and truncated tag names.
    text = re.sub(
        r"<[^>]*(?:think|anth|antth)[^>]*>.*?</[^>]*(?:think|anth|antth)[^>]*>",
        "", text, flags=re.DOTALL | re.IGNORECASE,
    )
    # Unclosed thinking-like tag at the very start — strip it so we don't
    # leave a dangling "<antthi>" fragment that confuses conclusion parsing.
    m = re.match(r"^\s*<[^>]*(?:think|anth|antth)[^>]*>", text, flags=re.IGNORECASE)
    if m:
        rest = text[m.end():]
        # Only strip to end-of-content when there's truly no closing tag
        if not re.search(r"</[^>]*(?:think|anth|antth)[^>]*>", rest, re.IGNORECASE):
            rest = re.sub(r"^.*?(?=\n\n|\Z)", "", rest, flags=re.DOTALL)
        text = rest
    # Leaked XML-like fragments from tool-call context:
    # <function=write>, <function=task>, <issue_description>, <reset>, <|mask_start|>
    # Paired tags: strip the whole block
    text = re.sub(r"<function=[^>]*>.*?</function>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(
        r"<(?:issue_description|reset|task|context)[^>]*>.*?</(?:issue_description|reset|task|context)>",
        "", text, flags=re.DOTALL | re.IGNORECASE,
    )
    # Unclosed leaked tags at the start (e.g. output starts with <function=write>)
    m2 = re.match(
        r"^\s*<(?:function=[^>]*|issue_description[^>]*|reset[^>]*|task[^>]*|context[^>]*|\|mask_start\|)>",
        text, flags=re.IGNORECASE,
    )
    if m2:
        text = text[m2.end():]
    # Stray single tokens like <|mask_start|>, <|mask_end|>
    text = re.sub(r"<\|mask_(?:start|end)\|>", "", text, flags=re.IGNORECASE)
    return text.strip()


def _prune_tool_output(tool_name: str, content: str, max_chars: int = 8000) -> str:
    """Prune large tool outputs to save context tokens while preserving useful info.

    For very long outputs, keeps the first and last portions with a gap summary in
    between so the model can still see structure (headers, errors, final results).
    Small outputs are returned unchanged.
    """
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


def _summarize_tool_result(tool_name: str, tool_args: str, tool_content: str) -> str:
    """Create an informative 1-line summary of a tool call + result.

    Replaces large tool outputs with a short but useful description of what
    the tool did, rather than a generic truncation that carries little info.
    Used during context compaction to keep summaries meaningful.

    Returns strings like::

        [bash] ran `ls -la` -> 47 lines output
        [read] read app/agent.py from line 1 (2,300 chars)
        [grep] search for 'compact' in app/ -> 5 matches
        [write] wrote to app/llm.py (120 lines)
    """
    try:
        args = json.loads(tool_args) if tool_args else {}
    except (json.JSONDecodeError, TypeError):
        args = {}

    content = tool_content or ""
    content_len = len(content)
    line_count = content.count("\n") + 1 if content.strip() else 0

    if tool_name == "bash":
        cmd = args.get("command", "")
        if len(cmd) > 60:
            cmd = cmd[:57] + "..."
        return f"[bash] ran `{cmd}` -> {line_count} lines output"

    if tool_name == "read":
        path = args.get("path", "?")
        offset = args.get("offset", 1)
        limit = args.get("limit", "")
        detail = f"from line {offset}" if offset else ""
        if limit:
            detail += f" ({limit} lines)"
        return f"[read] read {path} {detail} ({content_len:,} chars)"

    if tool_name == "write":
        path = args.get("path", "?")
        written_lines = args.get("content", "").count("\n") + 1 if args.get("content") else "?"
        return f"[write] wrote to {path} ({written_lines} lines)"

    if tool_name == "edit":
        path = args.get("path", "?")
        old_preview = (args.get("old_text") or "")[:30].replace("\n", " ")
        return f"[edit] replaced text in {path} (old='{old_preview}...')"

    # All non-base tools are now skills executed via bash — use generic fallback
    return f"[{tool_name}] called with {len(args)} arg(s) -> {line_count} lines output"


def _make_args_key(tool_name: str, args: dict) -> tuple:
    """Create a hashable key from (tool_name, args) for repetitive-call detection.

    Normalizes args by sorting keys and converting to canonical JSON, then hashing
    to avoid issues with large arg values in the deque.
    """
    if not isinstance(args, dict):
        args = {}
    sorted_args = json.dumps(args, sort_keys=True, default=str)
    return (tool_name, hashlib.md5(sorted_args.encode()).hexdigest())


# ── Repetitive call guard config ──
_REPEAT_THRESHOLD = 3       # N consecutive identical calls triggers the guard
_REPEAT_HISTORY_WINDOW = 10 # Track last N calls in the deque


class LLM:
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
        for attempt in range(1, 4):  # up to 3 attempts
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
             response_format: Optional[Dict] = None) -> str:
        body = {"model": self.model, "messages": messages,
                "temperature": temperature, "max_tokens": max_tokens, "stream": False}
        if response_format:
            body["response_format"] = response_format
        resp = self._post(body)
        msg = resp["choices"][0]["message"]
        return _strip_think(msg.get("content") or msg.get("reasoning_content") or "")

    def run_agent(self, messages: List[Dict], tool_executor: Callable[[str, Dict], tuple],
                  temperature: float = 0.5,
                  on_step: Optional[Callable] = None, tools: List[Dict] = None,
                  terminal_tools: Optional[set] = None,
                  response_format: Optional[Dict] = None) -> Dict:
        """Tool-calling agent loop. tool_executor(name, args) -> (result_str, is_error).

        Runs until the model returns a text response (RESULT/BLOCKED).  When the message
        history grows too large for the context window, older tool exchanges are compacted
        into a summary so work can continue without losing the thread.

        terminal_tools: calling any one of these tools ends the round immediately (treating that tool's result as the round's output) —
for "finish after this single action" scenarios —
        avoiding the model dragging on without returning a final message and burning up to max.

        response_format: optional JSON schema dict passed to the API's response_format parameter.
        When set, the model's final text response is constrained to match the schema.
        """
        tools = tools or ALL_TOOLS
        terminal = set(terminal_tools or [])
        _response_format = response_format
        msgs = list(messages)
        calls, results = [], []
        # Keep the original system + user messages intact; only compact tool exchanges.
        _initial_len = len(msgs)
        # Compact when total messages exceed this threshold (keeps ~last 40 exchanges visible).
        # Kept as fallback if token estimation fails (backward compatibility).
        _COMPACT_AT = 60
        # After compaction, keep at most this many recent tool messages.
        # Kept as fallback if token-based cut-point selection fails.
        _KEEP_RECENT = 30
        # Duplicate detection: track MD5 hashes of tool outputs to avoid re-feeding identical content
        _seen_outputs: Set[str] = set()
        _dup_count = 0
        # Repetitive call guard: detect N consecutive identical (tool, args) calls
        _repeat_history: deque = deque(maxlen=_REPEAT_HISTORY_WINDOW)
        _repeat_consecutive = 0
        _repeat_last_key = None
        _repeat_cached: Dict[str, str] = {}  # args_key -> raw result content

        # No hard stop: loop until the model returns a text response (RESULT/BLOCKED).
        # Context compaction below keeps history manageable for long runs.
        while True:
            # Token-aware max_tokens clamping to prevent silent truncation
            _context_tokens = estimate_context_tokens(msgs)
            _clamped_max = clamp_max_tokens(4096, _context_tokens)

            body = {"model": self.model, "messages": msgs, "tools": tools,
                    "temperature": temperature, "max_tokens": _clamped_max, "stream": False}
            if _response_format:
                body["response_format"] = _response_format
            resp = self._post(body)
            if not resp.get("choices"):
                break
            m = resp["choices"][0]["message"]
            tcs = m.get("tool_calls") or []
            if not tcs:
                return {"content": _strip_think(m.get("content") or ""), "calls": calls, "results": results}
            msgs.append(m)
            for tc in tcs:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    args = {}

                # ── Repetitive call guard: short-circuit N consecutive identical calls ──
                _args_key = _make_args_key(name, args)
                if _args_key == _repeat_last_key:
                    _repeat_consecutive += 1
                else:
                    _repeat_consecutive = 1
                    _repeat_last_key = _args_key

                if _repeat_consecutive >= _REPEAT_THRESHOLD:
                    # Guard triggered — return cached result + warning, skip actual execution
                    _cached_result = _repeat_cached.get(_args_key, "(no cached result)")
                    _warning = (
                        f"[REPETITIVE CALL GUARD] You have run this identical command "
                        f"{_repeat_consecutive} times in a row with the same result. "
                        f"Do NOT repeat it. Use the result you already have, try a DIFFERENT approach, "
                        f"or conclude with RESULT/BLOCKED."
                    )
                    res = _cached_result + "\n\n" + _warning
                    err = False
                    calls.append({"name": name, "args": args, "error": False})
                    results.append(res)
                    if on_step:
                        on_step(name, args, res, False)
                    tool_content = _warning
                    msgs.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                                 "content": tool_content})
                    continue

                res, err = tool_executor(name, args)
                # Cache result for repetitive-call guard (only successful calls)
                if not err:
                    _repeat_cached[_args_key] = str(res)
                _repeat_history.append(_args_key)
                calls.append({"name": name, "args": args, "error": err})
                results.append(res)
                if on_step:
                    on_step(name, args, res, err)
                raw_content = f"ERROR: {res}" if err else str(res)
                # Duplicate detection: hash the output to catch repeated file reads / identical results
                out_hash = hashlib.md5(raw_content.encode(errors="replace")).hexdigest()
                if out_hash in _seen_outputs:
                    _dup_count += 1
                    tool_content = f"[DUPLICATE OUTPUT — same as a previous {name} call, skipping content to save tokens]"
                else:
                    _seen_outputs.add(out_hash)
                    # Prune large outputs to reduce token waste while preserving structure
                    tool_content = _prune_tool_output(name, raw_content[:10000])
                msgs.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                             "content": tool_content})
                if name in terminal and not err:
                    return {"content": str(res)[:300], "calls": calls, "results": results}

            # ── Context compaction: token-aware — summarise older tool exchanges when approaching context limit ──
            _context_tokens = estimate_context_tokens(msgs)

            # Token-aware trigger with backward-compatible fallback to message count
            _should_compact = (
                _context_tokens > (_CONTEXT_WINDOW - _COMPACTION_RESERVE)
                or len(msgs) > _COMPACT_AT
            )
            if _should_compact:
                extra = len(msgs) - _initial_len
                # Token-aware cut point: walk backwards from end, accumulate tokens, stop when >= _KEEP_RECENT_TOKENS
                _cut_idx = _initial_len  # default: compact everything after initial messages
                accumulated = 0
                for i in range(len(msgs) - 1, _initial_len - 1, -1):
                    msg_tokens = estimate_tokens(msgs[i].get("content", "") or "")
                    for tc2 in msgs[i].get("tool_calls") or []:
                        fn2 = tc2.get("function", {})
                        msg_tokens += estimate_tokens(fn2.get("arguments", "") or "")
                    accumulated += msg_tokens
                    if accumulated >= _KEEP_RECENT_TOKENS:
                        _cut_idx = i
                        break

                compactable = msgs[_initial_len:_cut_idx]
                if not compactable:
                    continue  # Nothing to compact

                summary_parts = []
                # Walk compactable messages pairing assistant tool_calls with their tool results
                pending_tool_info = {}  # tool_call_id -> (name, args_json)
                for cm in compactable:
                    role = cm.get("role", "")
                    if role == "assistant" and cm.get("tool_calls"):
                        for tc2 in cm["tool_calls"]:
                            fn2 = tc2.get("function", {})
                            tid = tc2.get("id", "")
                            pending_tool_info[tid] = (fn2.get("name", "?"),
                                                      fn2.get("arguments", "{}"))
                    elif role == "tool":
                        tid = cm.get("tool_call_id", "")
                        content = cm.get("content") or ""
                        if tid in pending_tool_info:
                            t_name, t_args = pending_tool_info.pop(tid)
                            summary_parts.append(_summarize_tool_result(t_name, t_args, content))
                        else:
                            # Orphaned tool result (no matching assistant message in compactable range)
                            summary_parts.append(f"    -> {(content)[:120]}")
                # Emit any unmatched tool calls (assistant called but result not in compactable range)
                for t_name, t_args in pending_tool_info.values():
                    summary_parts.append(f"  {t_name}({json.dumps(t_args)[:80]}) [result kept]")
                summary_text = "\n".join(summary_parts)

                # File operation tracking: extract read/modified paths from compacted messages
                file_paths = _extract_file_paths(compactable)
                file_note = ""
                if file_paths["read"] or file_paths["modified"]:
                    parts = []
                    if file_paths["read"]:
                        parts.append(f"Read files: {', '.join(sorted(file_paths['read']))}")
                    if file_paths["modified"]:
                        parts.append(f"Modified files: {', '.join(sorted(file_paths['modified']))}")
                    file_note = "\n  File operations in compacted history:\n    " + "\n    ".join(parts)

                dup_note = f" ({_dup_count} duplicate output(s) detected and skipped)" if _dup_count else ""
                summary_msg = {"role": "user", "content": (
                    f"[COMPACTED HISTORY — {len(compactable)} earlier tool exchanges, kept only the most recent for context{dup_note}]\n"
                    f"{summary_text}"
                    f"{file_note}\n\nContinue working on the task from where you left off."
                )}
                msgs = msgs[:_initial_len] + [summary_msg] + msgs[_cut_idx:]

        # Unreachable: the loop only exits via explicit returns (text response or terminal tool).
        return {"content": "", "calls": calls, "results": results}
