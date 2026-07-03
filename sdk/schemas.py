"""Pydantic schemas for LLM requests, responses and tool definitions.

This module provides type-safe interfaces for working with OpenAI-compatible
LLM API requests and responses, using Pydantic for validation and documentation.
"""
from typing import Any

from pydantic import BaseModel, Field


# ── LLM chat request ──────────────────────────────────────────────

class ChatMessage(BaseModel):
    """Base class for chat messages."""
    role: str


class SystemMessage(ChatMessage):
    """System message to set the assistant's behavior."""
    role: str = "system"
    content: str


class UserMessage(ChatMessage):
    """User message."""
    role: str = "user"
    content: str


class AssistantMessage(ChatMessage):
    """Assistant message."""
    role: str = "assistant"
    content: str | None = None
    tool_calls: list["ToolCall"] | None = None


class ToolMessage(ChatMessage):
    """Tool result message."""
    role: str = "tool"
    tool_call_id: str
    content: str


class ChatRequest(BaseModel):
    """OpenAI-compatible /chat/completions request body (subset)."""
    model: str
    messages: list[ChatMessage]
    temperature: float = Field(default=0.6, ge=0, le=2)
    max_tokens: int = Field(default=2048, gt=0)
    stream: bool = False
    tools: list["ToolDefinition"] | None = None
    response_format: "ResponseFormat" | None = None
    tool_choice: str | dict | None = None
    parallel_tool_calls: bool = False  # Default: sequential tool calls


# ── LLM response ──────────────────────────────────────────────────

class ToolCallFunction(BaseModel):
    """Function definition for a tool call."""
    name: str
    arguments: str  # JSON string — callers use json.loads()


class ToolCall(BaseModel):
    """A tool call from the model."""
    id: str
    type: str = "function"
    function: ToolCallFunction


class ChatResponseMessage(BaseModel):
    """Response message from the LLM."""
    role: str
    content: str | None = None
    tool_calls: list[ToolCall] | None = None


class ChatChoice(BaseModel):
    """A choice from the LLM response."""
    index: int
    message: ChatResponseMessage
    finish_reason: str


class Usage(BaseModel):
    """Token usage statistics."""
    completion_tokens: int
    prompt_tokens: int
    total_tokens: int


class ChatCompletionResponse(BaseModel):
    """OpenAI-compatible /chat/completions response."""
    id: str
    object: str = "chat.completion"
    model: str
    created: int
    choices: list[ChatChoice]
    usage: Usage


# ── Response format ───────────────────────────────────────────────

class JsonSchema(BaseModel):
    """JSON Schema definition for structured output."""
    name: str
    strict: bool = False
    schema: dict[str, Any]


class ResponseFormat(BaseModel):
    """Response format specification."""
    type: str = "json_schema"  # "json_object" | "json_schema"
    json_schema: JsonSchema | None = None


# ── Tool definitions (OpenAI-compatible) ──────────────────────────

class ToolParameter(BaseModel):
    """Parameter schema for a tool function."""
    type: str = "object"
    properties: dict[str, Any] = Field(default_factory=dict)
    required: list[str] = Field(default_factory=list)
    description: str | None = None


class ToolFunction(BaseModel):
    """Tool function definition."""
    name: str
    description: str
    parameters: ToolParameter


class ToolDefinition(BaseModel):
    """Tool definition for function calling."""
    type: str = "function"
    function: ToolFunction
