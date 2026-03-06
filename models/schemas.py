"""
Pydantic data models used throughout the bug detection pipeline.

These schemas define the contracts between agents, ensuring
type-safe, validated data flow.
"""

from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field


# ─── Code Parsing Models ────────────────────────────────────────────────────

class CodeLine(BaseModel):
    """A single line of C++ source code with metadata."""
    line_number: int = Field(..., description="1-indexed line number")
    content: str = Field(..., description="Raw text content of the line")
    is_blank: bool = Field(default=False, description="Whether the line is blank/whitespace")
    is_comment: bool = Field(default=False, description="Whether the line is a comment")
    is_preprocessor: bool = Field(default=False, description="Whether the line is a preprocessor directive")


class ParsedCode(BaseModel):
    """Structured representation of a C++ code snippet after parsing."""
    lines: list[CodeLine] = Field(default_factory=list, description="List of parsed code lines")
    total_lines: int = Field(default=0, description="Total number of lines in the snippet")
    raw_code: str = Field(default="", description="Original raw code string")

    def get_line(self, line_number: int) -> Optional[CodeLine]:
        """Get a specific line by its 1-indexed line number."""
        for line in self.lines:
            if line.line_number == line_number:
                return line
        return None

    def get_numbered_code(self) -> str:
        """Return the code with line numbers prefixed (for LLM prompts)."""
        return "\n".join(
            f"{line.line_number:>4} | {line.content}" for line in self.lines
        )


# ─── MCP / Bug Pattern Models ───────────────────────────────────────────────

class BugPattern(BaseModel):
    """A known bug pattern from the MCP bug manual."""
    pattern_id: Optional[str] = Field(default=None, description="ID in the bug manual")
    context: str = Field(default="", description="Context category (e.g. 'RDI method naming')")
    description: str = Field(default="", description="Human-readable description of the bug pattern")
    code_pattern: Optional[str] = Field(default=None, description="Code pattern or signature to match")
    correct_pattern: Optional[str] = Field(default=None, description="Correct version of the code pattern")
    characteristics: Optional[str] = Field(default=None, description="Bug characteristics")


# ─── Detection Models ────────────────────────────────────────────────────────

class DetectionResult(BaseModel):
    """Result from the Bug Detector Agent — identifies the buggy line."""
    bug_line: int = Field(..., description="1-indexed line number where bug is located")
    bug_type: str = Field(default="unknown", description="Category/type of the bug")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Detection confidence [0,1]")
    detection_method: str = Field(default="", description="Which layer detected it (diff/pattern/llm)")
    raw_reasoning: str = Field(default="", description="Raw reasoning or diff output")


class DescriptionResult(BaseModel):
    """Result from the Bug Describer Agent — explains the bug."""
    explanation: str = Field(..., description="Clear, human-readable explanation of the bug")
    references_manual: bool = Field(default=False, description="Whether explanation references the MCP bug manual")


# ─── Pipeline I/O Models ────────────────────────────────────────────────────

class PipelineInput(BaseModel):
    """A single row from the input dataset — one code snippet to analyze."""
    id: int = Field(..., description="Unique ID of the code snippet")
    code: str = Field(..., description="Buggy C++ code snippet")
    context: Optional[str] = Field(default=None, description="Context / description of what the code does")


class PipelineOutput(BaseModel):
    """A single row of the output CSV — the detection result."""
    id: int = Field(..., description="Code ID (matches input)")
    bug_line: str = Field(..., description="Line number(s) containing bugs, comma-separated if multiple")
    explanation: str = Field(..., description="Generated explanation(s) of the bugs")
