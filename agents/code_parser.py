"""
Code Parser Agent — parses C++ code snippets into structured, analyzable form.

Responsibilities:
  - Split code into individual lines with metadata
  - Identify comments, preprocessor directives, blank lines
  - Provide line-numbered code for LLM prompts
"""

from __future__ import annotations
import re
import logging

from models.schemas import CodeLine, ParsedCode

logger = logging.getLogger(__name__)


class CodeParserAgent:
    """
    Agent 2 — Parses raw C++ code into a structured representation.
    
    Uses regex-based tokenization (no external dependencies) to classify
    each line as code, comment, preprocessor directive, or blank.
    """
    
    # Regex patterns for C++ line classification
    _COMMENT_LINE = re.compile(r"^\s*//")              # Single-line comment
    _BLOCK_COMMENT_START = re.compile(r"/\*")           # Block comment start
    _BLOCK_COMMENT_END = re.compile(r"\*/")             # Block comment end
    _PREPROCESSOR = re.compile(r"^\s*#")                # Preprocessor directive
    _BLANK_LINE = re.compile(r"^\s*$")                  # Blank/whitespace-only line
    
    def parse(self, raw_code: str) -> ParsedCode:
        """
        Parse a raw C++ code string into a ParsedCode structure.
        
        Args:
            raw_code: Raw C++ source code as a string.
        
        Returns:
            ParsedCode with classified lines.
        """
        if not raw_code or not raw_code.strip():
            logger.warning("Empty code snippet received")
            return ParsedCode(lines=[], total_lines=0, raw_code=raw_code)
        
        source_lines = raw_code.splitlines()
        parsed_lines: list[CodeLine] = []
        in_block_comment = False
        
        for idx, content in enumerate(source_lines, start=1):
            is_blank = bool(self._BLANK_LINE.match(content))
            is_comment = False
            is_preprocessor = False
            
            if in_block_comment:
                is_comment = True
                if self._BLOCK_COMMENT_END.search(content):
                    in_block_comment = False
            elif self._COMMENT_LINE.match(content):
                is_comment = True
            elif self._BLOCK_COMMENT_START.search(content):
                is_comment = True
                if not self._BLOCK_COMMENT_END.search(content):
                    in_block_comment = True
            elif self._PREPROCESSOR.match(content):
                is_preprocessor = True
            
            parsed_lines.append(CodeLine(
                line_number=idx,
                content=content,
                is_blank=is_blank,
                is_comment=is_comment,
                is_preprocessor=is_preprocessor,
            ))
        
        result = ParsedCode(
            lines=parsed_lines,
            total_lines=len(parsed_lines),
            raw_code=raw_code,
        )
        
        logger.debug(
            f"Parsed {result.total_lines} lines: "
            f"{sum(1 for l in parsed_lines if l.is_comment)} comments, "
            f"{sum(1 for l in parsed_lines if l.is_preprocessor)} preprocessor, "
            f"{sum(1 for l in parsed_lines if l.is_blank)} blank"
        )
        
        return result
    
    def extract_identifiers(self, raw_code: str) -> list[str]:
        """
        Extract function names, variable names, and other identifiers from C++ code.
        Useful for pattern matching against the MCP bug manual.
        
        Args:
            raw_code: Raw C++ source code.
        
        Returns:
            List of identifiers found in the code.
        """
        # Match C/C++ identifiers (excluding common keywords)
        keywords = {
            "if", "else", "for", "while", "do", "switch", "case", "break",
            "continue", "return", "void", "int", "float", "double", "char",
            "bool", "long", "short", "unsigned", "signed", "const", "static",
            "struct", "class", "enum", "typedef", "namespace", "using",
            "include", "define", "ifndef", "ifdef", "endif", "pragma",
            "true", "false", "nullptr", "NULL", "auto", "template", "typename",
        }
        
        identifiers = re.findall(r"\b([a-zA-Z_]\w*)\b", raw_code)
        return [ident for ident in identifiers if ident not in keywords]
