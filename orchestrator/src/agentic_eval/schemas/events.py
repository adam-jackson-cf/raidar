"""Event schemas for tracking execution."""

from typing import Literal

from pydantic import BaseModel, Field


class GateEvent(BaseModel):
    """Event from a verification gate execution."""

    timestamp: str = Field(description="ISO timestamp")
    gate_name: str = Field(description="Gate identifier")
    command: str = Field(description="Command executed")
    exit_code: int = Field(description="Process exit code")
    stdout: str = Field(description="Standard output (truncated)")
    stderr: str = Field(description="Standard error (truncated)")
    failure_category: str | None = Field(
        default=None,
        description="Categorized failure type",
    )
    is_repeat: bool = Field(
        default=False,
        description="Whether this is a repeat of a previous failure category",
    )


class SessionEvent(BaseModel):
    """Event from agent session log."""

    timestamp: str = Field(description="ISO timestamp")
    event_type: Literal[
        "user_prompt",
        "assistant_message",
        "file_change",
        "bash_command",
        "tool_call",
        "gate_result",
    ] = Field(description="Type of event")
    data: dict = Field(default_factory=dict, description="Event-specific data")


# Failure category patterns for gate watcher
FAILURE_CATEGORIES: list[tuple[str, str, str]] = [
    ("type_error", r"TS\d+:", "TypeScript Error"),
    ("lint_unused", r"no-unused-vars", "Unused Variable"),
    ("lint_import", r"import/order", "Import Order"),
    ("lint_complexity", r"complexity", "Complexity"),
    ("test_assertion", r"AssertionError", "Test Assertion"),
    ("test_timeout", r"Timeout", "Test Timeout"),
    ("build_module", r"Cannot find module", "Missing Module"),
    ("build_syntax", r"SyntaxError", "Syntax Error"),
]
