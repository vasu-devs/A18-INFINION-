"""
CSV I/O utilities — reading input datasets and writing output results.

Handles the dataset format specified by the Infineon challenge:
  Input:  ID, Code, Correct Code, Context, Explanation
  Output: ID, Bug Line, Explanation
"""

from __future__ import annotations
import csv
import logging
from pathlib import Path
from typing import Optional

import pandas as pd

from models.schemas import PipelineInput, PipelineOutput

logger = logging.getLogger(__name__)


def read_input_csv(filepath: str | Path) -> list[PipelineInput]:
    """
    Read the input dataset CSV and parse it into PipelineInput objects.
    
    The CSV is expected to have columns (case-insensitive, flexible naming):
        - ID (or id, code_id)
        - Code (or code, buggy_code, incorrect_code)
        - Correct Code (or correct_code, fixed_code)
        - Context (or context, description)
        - Explanation (or explanation, bug_description)
    
    Args:
        filepath: Path to the input CSV file.
    
    Returns:
        List of PipelineInput objects.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"Input CSV not found: {filepath}")
    
    df = pd.read_csv(filepath)
    
    # Normalize column names (lowercase, strip whitespace, replace spaces with underscores)
    df.columns = [col.strip().lower().replace(" ", "_") for col in df.columns]
    
    # Map flexible column names to our standard names
    column_map = {
        "id": ["id", "code_id", "snippet_id"],
        "code": ["code", "buggy_code", "incorrect_code"],
        "correct_code": ["correct_code", "fixed_code", "correct"],
        "context": ["context", "description", "code_context"],
        "explanation": ["explanation", "bug_description", "bug_explanation"],
    }
    
    resolved_columns: dict[str, str] = {}
    for standard_name, aliases in column_map.items():
        for alias in aliases:
            if alias in df.columns:
                resolved_columns[standard_name] = alias
                break
    
    if "id" not in resolved_columns:
        raise ValueError(f"Input CSV must have an 'ID' column. Found columns: {list(df.columns)}")
    if "code" not in resolved_columns:
        raise ValueError(f"Input CSV must have a 'Code' column. Found columns: {list(df.columns)}")
    
    inputs: list[PipelineInput] = []
    for _, row in df.iterrows():
        inp = PipelineInput(
            id=int(row[resolved_columns["id"]]),
            code=str(row[resolved_columns["code"]]),
            correct_code=_get_optional_str(row, resolved_columns.get("correct_code")),
            context=_get_optional_str(row, resolved_columns.get("context")),
            explanation=_get_optional_str(row, resolved_columns.get("explanation")),
        )
        inputs.append(inp)
    
    logger.info(f"Loaded {len(inputs)} code snippets from {filepath}")
    return inputs


def write_output_csv(results: list[PipelineOutput], filepath: str | Path) -> None:
    """
    Write the pipeline results to the output CSV.
    
    Output format (per Infineon spec):
        ID, Bug Line, Explanation
    
    Args:
        results: List of PipelineOutput objects.
        filepath: Path to write the output CSV.
    """
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_ALL)
        writer.writerow(["ID", "Bug Line", "Explanation"])
        for result in results:
            # Ensure bug_line is always a string (even single values)
            bug_line_str = str(result.bug_line)
            writer.writerow([result.id, bug_line_str, result.explanation])
    
    logger.info(f"Wrote {len(results)} results to {filepath}")


def _get_optional_str(row: pd.Series, column: Optional[str]) -> Optional[str]:
    """Safely get an optional string value from a DataFrame row."""
    if column is None or column not in row.index:
        return None
    value = row[column]
    if pd.isna(value):
        return None
    return str(value).strip()
