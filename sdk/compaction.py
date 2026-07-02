"""Context compaction — token estimation, cut-point detection, LLM-based summarization.

Pure functions. No dependency on core/config or sdk/tools.
"""
import json
import os
from typing import Dict, List, Optional, Set

# ── Config (all env-overridable) ────────────────────────────────────
_CONTEXT_WINDOW = int(os.environ.get("_CONTEXT_WINDOW", "128000"))
_COMPACTION_RESERVE = int(os.environ.get("_COMPACTION_RESERVE", "16384"))
_KEEP_RECENT_TOKENS = int(os.environ.get("_KEEP_RECENT_TOKENS", "20000"))
_COMPACT_SUMMARIZE_TOKENS = int(os.environ.get("_COMPACT_SUMMARIZE_TOKENS", "1024"))

# ── Token estimation ────────────────────────────────────────────────


def estimate_tokens(text: str) -> int:
    """Estimate token count from text using chars/4 heuristic."""
    return max(1, len(text) // 4) if text else 0


def estimate_context_tokens(messages: List[Dict]) -> int:
    """Sum estimated tokens across all messages in the conversation."""
    total = 0
    for msg in messages:
        content = msg.get("content", "") or ""
        total += estimate_tokens(content)
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


def should_compact(context_tokens: int, msg_count: int) -> bool:
    """Check if compaction should trigger based on token count or message count."""
    _COMPACT_AT = 60
    return (
        context_tokens > (_CONTEXT_WINDOW - _COMPACTION_RESERVE)
        or msg_count > _COMPACT_AT
    )


def find_cut_point(messages: List[Dict], initial_len: int) -> int:
    """Find the cut point that keeps approximately _KEEP_RECENT_TOKENS of recent messages.

    Walks backwards from end, accumulating tokens, stops when >= _KEEP_RECENT_TOKENS.
    Returns the index to start keeping from (everything before this gets compacted).
    """
    cut_idx = initial_len
    accumulated = 0
    for i in range(len(messages) - 1, initial_len - 1, -1):
        msg_tokens = estimate_tokens(messages[i].get("content", "") or "")
        for tc in messages[i].get("tool_calls") or []:
            fn = tc.get("function", {})
            msg_tokens += estimate_tokens(fn.get("arguments", "") or "")
        accumulated += msg_tokens
        if accumulated >= _KEEP_RECENT_TOKENS:
            cut_idx = i
            break
    return cut_idx


# ── File path extraction ────────────────────────────────────────────


def extract_file_paths(messages: List[Dict]) -> Dict[str, Set[str]]:
    """Extract read/modified file paths from tool calls in the given messages.

    Returns {'read': {paths}, 'modified': {paths}}.
    """
    read_files: Set[str] = set()
    modified_files: Set[str] = set()
    for msg in messages:
        if msg.get("role") != "assistant":
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


def format_file_note(file_paths: Dict[str, Set[str]]) -> str:
    """Format file operations as a note string for the compaction summary message."""
    if not file_paths["read"] and not file_paths["modified"]:
        return ""
    parts = []
    if file_paths["read"]:
        parts.append(f"Read files: {', '.join(sorted(file_paths['read']))}")
    if file_paths["modified"]:
        parts.append(f"Modified files: {', '.join(sorted(file_paths['modified']))}")
    return "\n  File operations in compacted history:\n    " + "\n    ".join(parts)


# ── Conversation serialization ──────────────────────────────────────

_TOOL_RESULT_MAX_CHARS = 2000


def serialize_conversation(messages: List[Dict]) -> str:
    """Convert LLM messages to plain text for summarization prompts.

    Prevents the model from treating the summary request as a conversation to continue.
    Format: [User]: ... / [Assistant]: ... / [Assistant tool call]: ... / [Tool result]: ...
    """
    parts = []
    for msg in messages:
        role = msg.get("role", "")
        if role == "assistant":
            text = (msg.get("content") or "").strip()
            tool_calls = msg.get("tool_calls") or []
            if text:
                parts.append(f"[Assistant]: {text}")
            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "?")
                args_str = fn.get("arguments", "{}")
                parts.append(f"[Assistant tool call]: {name}({args_str})")
        elif role == "tool":
            content = (msg.get("content") or "")[:_TOOL_RESULT_MAX_CHARS]
            if len(msg.get("content") or "") > _TOOL_RESULT_MAX_CHARS:
                content += f"\n... [{len(msg['content']) - _TOOL_RESULT_MAX_CHARS} chars truncated]"
            parts.append(f"[Tool result]: {content}")
        elif role == "user":
            parts.append(f"[User]: {msg.get('content', '')}")
    return "\n\n".join(parts)


# ── Summarization prompts ───────────────────────────────────────────

COMPACT_SYSTEM = (
    "You are a context summarization assistant. Read the conversation and produce "
    "a structured summary following the exact format specified. "
    "Do NOT continue the conversation. Do NOT respond to any questions in it. "
    "ONLY output the structured summary."
)

COMPACT_PROMPT = """\
The messages above are a conversation to summarize. Create a structured context checkpoint that another LLM will use to continue the work.

Use this EXACT format:

## Goal
[What is the user trying to accomplish?]

## Progress
### Done
- [x] [Completed tasks/changes]

### In Progress
- [ ] [Current work]

### Blocked
- [Issues preventing progress, if any]

## Key Decisions
- **[Decision]**: [Brief rationale]

## Next Steps
1. [Ordered list of what should happen next]

## Critical Context
- [Any file paths, error messages, or data needed to continue]
"""

COMPACT_UPDATE_PROMPT = """\
The messages above are NEW conversation messages to incorporate into the existing summary.

<previous-summary>
{previous_summary}
</previous-summary>

Update the summary: PRESERVE existing info, ADD new progress, UPDATE Next Steps.
Use the same format as above.
"""


# ── LLM-based summarization ────────────────────────────────────────

class ChatClient:
    """Minimal interface for compaction to call the LLM.

    The LLM class implements this via its chat() method.
    """
    def chat(self, messages: List[Dict], *, temperature: float, max_tokens: int) -> str: ...


def summarize(client: ChatClient, system_msg: str, compactable: List[Dict],
              previous_summary: str = "") -> str:
    """Use the LLM to produce a structured summary of compacted tool exchanges.

    Serializes messages to plain text (not raw message objects) so the model
    treats this as a summarization task, not a conversation to continue.

    If previous_summary is provided, uses the UPDATE prompt to merge new
    content into the existing summary incrementally.
    """
    conversation_text = serialize_conversation(compactable)

    # Build the user prompt: system context + conversation + instructions
    user_parts = []
    if system_msg:
        user_parts.append(f"System prompt:\n{system_msg}")
    user_parts.append(f"<conversation>\n{conversation_text}\n</conversation>")

    if previous_summary:
        user_parts.append(COMPACT_UPDATE_PROMPT.format(previous_summary=previous_summary))
    else:
        user_parts.append(COMPACT_PROMPT)

    summary = client.chat(
        [
            {"role": "system", "content": COMPACT_SYSTEM},
            {"role": "user", "content": "\n\n".join(user_parts)},
        ],
        temperature=0.3,
        max_tokens=_COMPACT_SUMMARIZE_TOKENS,
    )
    return (summary or "").strip()


# ── Full compaction step ────────────────────────────────────────────

def compact_step(client: ChatClient, messages: List[Dict], initial_len: int,
                 previous_summary: str = "", dup_count: int = 0) -> Optional[List[Dict]]:
    """Execute one compaction cycle. Returns new messages list, or None if no compaction needed.

    Checks trigger → finds cut point → summarizes → splices summary into messages.
    The caller should update its previous_summary with the returned summary text.
    Use get_last_summary() to extract it from the result.
    """
    context_tokens = estimate_context_tokens(messages)
    if not should_compact(context_tokens, len(messages)):
        return None

    cut_idx = find_cut_point(messages, initial_len)
    compactable = messages[initial_len:cut_idx]
    if not compactable:
        return None

    # Extract system message for context
    system_msg = ""
    if messages and messages[0].get("role") == "system":
        system_msg = messages[0].get("content", "") or ""

    # Generate summary
    summary_text = summarize(client, system_msg, compactable, previous_summary)

    # File operation tracking
    file_paths = extract_file_paths(compactable)
    file_note = format_file_note(file_paths)

    dup_note = f" ({dup_count} duplicate output(s) detected and skipped)" if dup_count else ""
    summary_msg = {"role": "user", "content": (
        f"[COMPACTED HISTORY — {len(compactable)} earlier tool exchanges, kept only the most recent for context{dup_note}]\n"
        f"{summary_text}"
        f"{file_note}\n\nContinue working on the task from where you left off."
    )}

    return messages[:initial_len] + [summary_msg] + messages[cut_idx:]


def get_last_summary(messages: List[Dict], initial_len: int) -> str:
    """Extract the summary text from the last compaction message (if any)."""
    if len(messages) <= initial_len + 1:
        return ""
    compact_msg = messages[initial_len]
    content = compact_msg.get("content", "") or ""
    if not content.startswith("[COMPACTED"):
        return ""
    # Summary starts after the first header line and ends before file note or "Continue"
    lines = content.split("\n")
    summary_lines = []
    for line in lines[1:]:
        if line.startswith("  File operations") or line.startswith("Continue working"):
            break
        summary_lines.append(line)
    return "\n".join(summary_lines).strip()
