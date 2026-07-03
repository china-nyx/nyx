"""Phase 1 verification — compaction serialization and prompt building.

Run: python3 tests/test_compaction.py
Does NOT require an LLM server."""

import json
import sys
from pathlib import Path

# Add repo root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sdk.compaction import (
    serialize_conversation as _serialize_conversation,
    COMPACT_SYSTEM as _COMPACT_SYSTEM,
    COMPACT_PROMPT as _COMPACT_PROMPT,
    COMPACT_UPDATE_PROMPT as _COMPACT_UPDATE_PROMPT,
    _TOOL_RESULT_MAX_CHARS,
)


def _ok(name):
    print(f"  ✓ {name}")


def _fail(name, msg):
    print(f"  ✗ {name}: {msg}")
    sys.exit(1)


def test_serialize_basic():
    """Basic message serialization."""
    msgs = [
        {"role": "assistant", "content": "Let me check the file.", "tool_calls": [
            {"id": "tc1", "function": {"name": "read", "arguments": json.dumps({"path": "app/foo.py"})}}
        ]},
        {"role": "tool", "tool_call_id": "tc1", "content": "def hello(): pass"},
    ]
    text = _serialize_conversation(msgs)

    assert "[Assistant]: Let me check the file." in text, f"Missing assistant text: {text!r}"
    _ok("assistant text serialized")

    assert 'read(' in text, f"Missing tool call: {text!r}"
    _ok("tool call serialized")

    assert "[Tool result]: def hello(): pass" in text, f"Missing tool result: {text!r}"
    _ok("tool result serialized")


def test_serialize_no_tool_calls():
    """Assistant message with only text (no tool calls)."""
    msgs = [
        {"role": "assistant", "content": "Task complete.", "tool_calls": []},
    ]
    text = _serialize_conversation(msgs)

    assert "[Assistant]: Task complete." in text
    assert "tool call" not in text.lower()
    _ok("text-only assistant message")


def test_serialize_user_message():
    """User message serialization."""
    msgs = [
        {"role": "user", "content": "Fix the bug in foo.py"},
    ]
    text = _serialize_conversation(msgs)

    assert "[User]: Fix the bug in foo.py" in text
    _ok("user message serialized")


def test_serialize_truncation():
    """Tool result truncation at _TOOL_RESULT_MAX_CHARS."""
    long_content = "x" * (_TOOL_RESULT_MAX_CHARS + 500)
    msgs = [
        {"role": "tool", "tool_call_id": "tc1", "content": long_content},
    ]
    text = _serialize_conversation(msgs)

    assert "truncated" in text, f"Expected truncation marker in: {text[-200:]!r}"
    # The serialized content should be capped
    result_part = text.split("[Tool result]: ", 1)[1]
    assert len(result_part) < _TOOL_RESULT_MAX_CHARS + 100, "Content not truncated"
    _ok("tool result truncation")


def test_serialize_empty_content():
    """Messages with empty/None content."""
    msgs = [
        {"role": "assistant", "content": "", "tool_calls": []},
        {"role": "tool", "tool_call_id": "tc1", "content": ""},
        {"role": "user", "content": None},
    ]
    text = _serialize_conversation(msgs)

    # Should not crash and should produce something (even if empty parts)
    assert isinstance(text, str)
    _ok("empty content handling")


def test_serialize_multiple_tool_calls():
    """Multiple tool calls in one assistant message."""
    msgs = [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "tc1", "function": {"name": "read", "arguments": json.dumps({"path": "a.py"})}},
            {"id": "tc2", "function": {"name": "bash", "arguments": json.dumps({"command": "ls"})}},
        ]},
        {"role": "tool", "tool_call_id": "tc1", "content": "file a"},
        {"role": "tool", "tool_call_id": "tc2", "content": "a.py b.py"},
    ]
    text = _serialize_conversation(msgs)

    assert text.count("[Assistant tool call]:") == 2, f"Expected 2 tool calls, got: {text!r}"
    assert "read(" in text
    assert "bash(" in text
    _ok("multiple tool calls")


def test_compact_prompts_exist():
    """Verify compaction prompts are defined and non-empty."""
    assert _COMPACT_SYSTEM.strip(), "_COMPACT_SYSTEM is empty"
    _ok("_COMPACT_SYSTEM defined")

    assert "## Goal" in _COMPACT_PROMPT, "Missing ## Goal in prompt"
    assert "## Progress" in _COMPACT_PROMPT
    assert "### Done" in _COMPACT_PROMPT
    assert "### In Progress" in _COMPACT_PROMPT
    assert "## Key Decisions" in _COMPACT_PROMPT
    assert "## Next Steps" in _COMPACT_PROMPT
    assert "## Critical Context" in _COMPACT_PROMPT
    _ok("_COMPACT_PROMPT has all required sections")

    assert "previous-summary" in _COMPACT_UPDATE_PROMPT, "Missing previous-summary in update prompt"
    assert "PRESERVE" in _COMPACT_UPDATE_PROMPT
    _ok("_COMPACT_UPDATE_PROMPT references previous summary")


def test_update_prompt_formatting():
    """Verify UPDATE prompt formats previous_summary correctly."""
    prev = "## Goal\nTest goal\n\n## Progress\n### Done\n- [x] done"
    formatted = _COMPACT_UPDATE_PROMPT.format(previous_summary=prev)

    assert "<previous-summary>" in formatted
    assert "</previous-summary>" in formatted
    assert "Test goal" in formatted
    _ok("UPDATE prompt formats previous summary")


def test_serialize_preserves_order():
    """Messages should appear in chronological order."""
    msgs = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "second", "tool_calls": []},
        {"role": "tool", "tool_call_id": "tc1", "content": "third"},
        {"role": "assistant", "content": "fourth", "tool_calls": []},
    ]
    text = _serialize_conversation(msgs)

    pos_first = text.index("[User]: first")
    pos_second = text.index("[Assistant]: second")
    pos_third = text.index("[Tool result]: third")
    pos_fourth = text.index("[Assistant]: fourth")

    assert pos_first < pos_second < pos_third < pos_fourth, "Messages not in order"
    _ok("message order preserved")


def main():
    print("Phase 1 verification: compaction serialization + prompts\n")

    test_serialize_basic()
    test_serialize_no_tool_calls()
    test_serialize_user_message()
    test_serialize_truncation()
    test_serialize_empty_content()
    test_serialize_multiple_tool_calls()
    test_compact_prompts_exist()
    test_update_prompt_formatting()
    test_serialize_preserves_order()

    print("\nAll tests passed.")


if __name__ == "__main__":
    main()
