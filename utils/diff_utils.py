"""
Diff utilities — line-by-line comparison of buggy vs. correct C++ code.

Used by the Bug Detector Agent (Layer 1) for high-confidence detection
when the correct code is available.
"""

from __future__ import annotations
import difflib
from typing import Optional
from dataclasses import dataclass


@dataclass
class DiffChange:
    """Represents a single changed line between buggy and correct code."""
    line_number: int       # Line number in the BUGGY (original) code (1-indexed)
    change_type: str       # "modified", "added", "removed"
    old_content: str       # Content in buggy code
    new_content: str       # Content in correct code
    

def compute_line_diff(
    buggy_code: str,
    correct_code: str,
) -> list[DiffChange]:
    """
    Compare buggy code against correct code line-by-line.
    
    Returns a list of DiffChange objects identifying lines that differ.
    Focus is on finding the FIRST substantive change (the bug).
    
    Args:
        buggy_code: The code containing the bug.
        correct_code: The correct version of the code.
    
    Returns:
        List of DiffChange objects, sorted by line number.
    """
    buggy_lines = buggy_code.splitlines()
    correct_lines = correct_code.splitlines()
    
    changes: list[DiffChange] = []
    
    # Use SequenceMatcher for fine-grained line mapping
    matcher = difflib.SequenceMatcher(None, buggy_lines, correct_lines)
    
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        elif tag == "replace":
            # Lines were modified
            for idx in range(i1, i2):
                new_idx = j1 + (idx - i1)
                new_content = correct_lines[new_idx] if new_idx < j2 else ""
                changes.append(DiffChange(
                    line_number=idx + 1,  # Convert to 1-indexed
                    change_type="modified",
                    old_content=buggy_lines[idx],
                    new_content=new_content,
                ))
        elif tag == "delete":
            # Lines exist in buggy but not correct (extra buggy lines)
            for idx in range(i1, i2):
                changes.append(DiffChange(
                    line_number=idx + 1,
                    change_type="removed",
                    old_content=buggy_lines[idx],
                    new_content="",
                ))
        elif tag == "insert":
            # Lines exist in correct but not buggy (missing lines)
            # Map to the insertion point in buggy code
            insertion_line = i1 + 1 if i1 < len(buggy_lines) else len(buggy_lines)
            for idx in range(j1, j2):
                changes.append(DiffChange(
                    line_number=insertion_line,
                    change_type="added",
                    old_content="",
                    new_content=correct_lines[idx],
                ))
    
    return changes


def find_primary_bug_line(
    buggy_code: str,
    correct_code: str,
) -> Optional[tuple[int, str]]:
    """
    Find the primary bug line using diff analysis.
    
    Returns the line number and a description of the first substantive change.
    
    Returns:
        Tuple of (line_number, description) or None if codes are identical.
    """
    changes = compute_line_diff(buggy_code, correct_code)
    
    if not changes:
        return None
    
    # Filter out purely whitespace changes
    substantive = [
        c for c in changes
        if c.old_content.strip() != c.new_content.strip()
    ]
    
    if not substantive:
        return None
    
    # Return the first substantive change
    first = substantive[0]
    
    if first.change_type == "modified":
        desc = f"Line {first.line_number}: '{first.old_content.strip()}' should be '{first.new_content.strip()}'"
    elif first.change_type == "removed":
        desc = f"Line {first.line_number}: extra/incorrect line '{first.old_content.strip()}'"
    else:  # added
        desc = f"Line {first.line_number}: missing line '{first.new_content.strip()}'"
    
    return (first.line_number, desc)


def generate_diff_summary(buggy_code: str, correct_code: str) -> str:
    """
    Generate a human-readable unified diff summary.
    
    Useful for passing to the LLM or Bug Describer for context.
    """
    buggy_lines = buggy_code.splitlines(keepends=True)
    correct_lines = correct_code.splitlines(keepends=True)
    
    diff = difflib.unified_diff(
        buggy_lines,
        correct_lines,
        fromfile="buggy_code.cpp",
        tofile="correct_code.cpp",
        lineterm="",
    )
    
    return "\n".join(diff)
