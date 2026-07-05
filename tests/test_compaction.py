"""Compaction module verification — token estimation, trigger logic.

Run: python3 tests/test_compaction.py
Does NOT require an LLM server."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sdk.hooks.compaction import (
    CompactionSettings,
    estimate_tokens,
    estimate_context_tokens,
    clamp_max_tokens,
    should_compact,
)


def _ok(name):
    print(f"  ✓ {name}")


def _fail(name, msg):
    print(f"  ✗ {name}: {msg}")
    sys.exit(1)


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    _ok("empty string → 0")

    assert estimate_tokens("hello") >= 1
    _ok("short text ≥ 1")

    # chars/4 heuristic
    assert estimate_tokens("a" * 8) == 2
    _ok("chars/4 heuristic")


def test_estimate_context_tokens():
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello there"},
    ]
    total = estimate_context_tokens(msgs)
    assert total > 0
    _ok("basic messages")

    msgs_with_tools = [
        {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "read", "arguments": '{"path": "foo.py"}'}}
        ]},
    ]
    total2 = estimate_context_tokens(msgs_with_tools)
    assert total2 > 0
    _ok("messages with tool_calls")


def test_clamp_max_tokens():
    # Plenty of headroom
    assert clamp_max_tokens(4096, 10_000, 128_000) == 4096
    _ok("plenty of headroom")

    # Tight headroom — should clamp down
    clamped = clamp_max_tokens(4096, 120_000, 128_000)
    assert clamped <= 4096
    assert clamped >= 256
    _ok("tight headroom clamped")

    # Almost full — minimum 256
    assert clamp_max_tokens(4096, 127_000, 128_000) == 256
    _ok("near-full returns min 256")


def test_should_compact_disabled():
    settings = CompactionSettings(enabled=False)
    assert not should_compact(999_999, 128_000, settings)
    _ok("disabled → never triggers")


def test_should_compact_token_triggered():
    settings = CompactionSettings(reserve_tokens=16384)
    # tokens > window - reserve
    assert should_compact(120_000, 128_000, settings)
    _ok("token threshold triggers")

    assert not should_compact(100_000, 128_000, settings)
    _ok("below token threshold does not trigger")


def main():
    print("Compaction verification\n")

    test_estimate_tokens()
    test_estimate_context_tokens()
    test_clamp_max_tokens()
    test_should_compact_disabled()
    test_should_compact_token_triggered()

    print("\nAll tests passed.")


if __name__ == "__main__":
    main()
