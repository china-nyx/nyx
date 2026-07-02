"""Pydantic schemas for LLM requests, responses and tool definitions."""
from typing import Any

from pydantic import BaseModel, Field


# ── LLM chat ──────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    """A single message in the chat conversation (request or response)."""
    role: str
    content: str | None = None
    tool_calls: list[dict] | None = None
    tool_call_id: str | None = None  # for tool-role messages


class ChatRequest(BaseModel):
    """OpenAI-compatible /chat/completions request body (subset)."""
    model: str
    messages: list[ChatMessage]
    temperature: float = 0.6
    max_tokens: int = 2048
    stream: bool = False
    tools: list[dict] | None = None
    response_format: dict | None = None
    tool_choice: str | None = None


# ── LLM response ──────────────────────────────────────────────────

class ToolCallFunction(BaseModel):
    name: str
    arguments: str  # JSON string — callers use json.loads()


class ToolCall(BaseModel):
    id: str
    type: str
    function: ToolCallFunction


class ChatResponseMessage(BaseModel):
    role: str
    content: str | None = None
    tool_calls: list[ToolCall] | None = None


class ChatChoice(BaseModel):
    index: int
    message: ChatResponseMessage
    finish_reason: str


class Usage(BaseModel):
    completion_tokens: int
    prompt_tokens: int
    total_tokens: int


class ChatCompletionResponse(BaseModel):
    """OpenAI-compatible /chat/completions response."""
    id: str
    object: str
    model: str
    created: int
    choices: list[ChatChoice]
    usage: Usage


# ── Tool definitions (OpenAI-compatible) ──────────────────────────

class ToolParameterProperty(BaseModel):
    type: str
    description: str = ""


class ToolParameter(BaseModel):
    type: str = "object"
    properties: dict[str, Any]
    required: list[str] = []


class ToolFunction(BaseModel):
    name: str
    description: str
    parameters: ToolParameter


class ToolDefinition(BaseModel):
    type: str = "function"
    function: ToolFunction
