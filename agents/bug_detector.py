"""
Bug Detector Agent — the core intelligence that identifies buggy lines.

Uses a 3-layer detection strategy:
  Layer 1: Diff-based detection (highest confidence, when correct code available)
  Layer 2: Pattern matching against MCP bug manual
  Layer 3: LLM-powered code analysis (for unknown/novel bugs)
"""

from __future__ import annotations
import logging
import re
from typing import Optional

from models.schemas import ParsedCode, BugPattern, DetectionResult
from utils.diff_utils import find_primary_bug_line, compute_line_diff, generate_diff_summary
from utils.llm_client import call_llm, parse_json_response
import config

logger = logging.getLogger(__name__)


class BugDetectorAgent:
    """
    Agent 4 — Multi-layered bug detector.
    
    Combines diff analysis, pattern matching, and LLM reasoning
    to identify the exact line containing a bug in C++ code.
    """
    
    async def detect(
        self,
        parsed_code: ParsedCode,
        correct_code: Optional[str],
        context: Optional[str],
        mcp_patterns: list[BugPattern],
        documentation_context: str = "",
    ) -> DetectionResult:
        """
        Run all detection layers and return the best result.
        
        Args:
            parsed_code: Structured parsed code from Code Parser Agent.
            correct_code: Correct version of the code (if available).
            context: Context/description of what the code does.
            mcp_patterns: Known bug patterns from MCP Lookup Agent.
        
        Returns:
            DetectionResult with the identified bug line and metadata.
        """
        candidates: list[DetectionResult] = []
        
        # ─── Layer 1: Diff-based detection ───────────────────────────
        if config.ENABLE_DIFF_DETECTION and correct_code:
            diff_result = self._detect_via_diff(parsed_code.raw_code, correct_code)
            if diff_result:
                candidates.append(diff_result)
                logger.info(f"[Diff] Detected bug at line {diff_result.bug_line} "
                           f"(confidence: {diff_result.confidence:.2f})")
        
        # ─── Layer 2: Pattern matching ───────────────────────────────
        if config.ENABLE_PATTERN_DETECTION and mcp_patterns:
            pattern_result = self._detect_via_pattern(parsed_code, mcp_patterns)
            if pattern_result:
                candidates.append(pattern_result)
                logger.info(f"[Pattern] Detected bug at line {pattern_result.bug_line} "
                           f"(confidence: {pattern_result.confidence:.2f})")
        
        # ─── Layer 3: LLM analysis ──────────────────────────────────
        if config.ENABLE_LLM_DETECTION:
            llm_result = await self._detect_via_llm(
                parsed_code, correct_code, context, mcp_patterns, documentation_context
            )
            if llm_result:
                candidates.append(llm_result)
                logger.info(f"[LLM] Detected bug at line {llm_result.bug_line} "
                           f"(confidence: {llm_result.confidence:.2f})")
        
        # ─── Ensemble: select best result ────────────────────────────
        if not candidates:
            logger.warning("No detection layer produced a result. Defaulting to line 1.")
            return DetectionResult(
                bug_line=1,
                bug_type="unknown",
                confidence=0.0,
                detection_method="none",
                raw_reasoning="No detection layer could identify the bug.",
            )
        
        return self._select_best(candidates)
    
    # ─── Layer 1: Diff Detection ─────────────────────────────────────────────
    
    def _detect_via_diff(
        self,
        buggy_code: str,
        correct_code: str,
    ) -> Optional[DetectionResult]:
        """
        Use line-by-line diff to find the bug.
        
        This is the highest-confidence method but requires correct code.
        """
        try:
            result = find_primary_bug_line(buggy_code, correct_code)
            if result is None:
                logger.debug("[Diff] No difference found between buggy and correct code")
                return None
            
            line_num, description = result
            
            # Get all changes for context
            changes = compute_line_diff(buggy_code, correct_code)
            diff_summary = generate_diff_summary(buggy_code, correct_code)
            
            return DetectionResult(
                bug_line=line_num,
                bug_type="diff_detected",
                confidence=0.95,
                detection_method="diff",
                raw_reasoning=f"{description}\n\nFull diff:\n{diff_summary}",
            )
        except Exception as e:
            logger.error(f"[Diff] Detection failed: {e}")
            return None
    
    # ─── Layer 2: Pattern Matching ───────────────────────────────────────────
    
    def _detect_via_pattern(
        self,
        parsed_code: ParsedCode,
        patterns: list[BugPattern],
    ) -> Optional[DetectionResult]:
        """
        Match code against known bug patterns from the MCP manual.
        
        Looks for pattern signatures in the code lines and returns a
        match if found.
        """
        try:
            for pattern in patterns:
                if not pattern.code_pattern:
                    continue
                
                # Search each code line for the pattern
                for line in parsed_code.lines:
                    if line.is_blank or line.is_comment:
                        continue
                    
                    # Check if the buggy pattern appears in this line
                    if self._fuzzy_match(line.content, pattern.code_pattern):
                        return DetectionResult(
                            bug_line=line.line_number,
                            bug_type=f"pattern_match:{pattern.context}",
                            confidence=0.80,
                            detection_method="pattern",
                            raw_reasoning=(
                                f"Matched known bug pattern '{pattern.context}': "
                                f"{pattern.description}\n"
                                f"Pattern: '{pattern.code_pattern}' found at line {line.line_number}"
                            ),
                        )
            
            return None
        except Exception as e:
            logger.error(f"[Pattern] Detection failed: {e}")
            return None
    
    @staticmethod
    def _fuzzy_match(line_content: str, pattern: str) -> bool:
        """
        Check if a line fuzzy-matches a bug pattern.
        
        Uses case-insensitive substring matching with some normalization.
        """
        norm_line = re.sub(r"\s+", " ", line_content.strip().lower())
        norm_pattern = re.sub(r"\s+", " ", pattern.strip().lower())
        return norm_pattern in norm_line
    
    # ─── Layer 3: LLM Analysis ───────────────────────────────────────────────
    
    async def _detect_via_llm(
        self,
        parsed_code: ParsedCode,
        correct_code: Optional[str],
        context: Optional[str],
        mcp_patterns: list[BugPattern],
        documentation_context: str = "",
    ) -> Optional[DetectionResult]:
        """
        Use an LLM to analyze the code and identify the buggy line.
        
        Constructs a detailed prompt with code, context, and known patterns,
        and asks the LLM to identify the exact line number.
        """
        try:
            prompt = self._build_llm_prompt(parsed_code, correct_code, context, mcp_patterns, documentation_context)
            
            system_prompt = (
                "You are an expert C++ code reviewer specializing in bug detection. "
                "Your task is to analyze C++ code snippets and identify the EXACT line "
                "number that contains a bug. You must respond ONLY with valid JSON."
            )
            
            response = await call_llm(
                prompt=prompt,
                system_prompt=system_prompt,
                json_mode=True,
                temperature=0.1,
            )
            
            data = parse_json_response(response)
            
            bug_line = int(data.get("bug_line", data.get("line_number", 1)))
            reasoning = data.get("reasoning", data.get("explanation", ""))
            bug_type = data.get("bug_type", "llm_detected")
            confidence_raw = data.get("confidence", 0.7)
            
            # Validate line number is within range
            if bug_line < 1 or bug_line > parsed_code.total_lines:
                logger.warning(
                    f"[LLM] Returned line {bug_line} outside range [1, {parsed_code.total_lines}]. "
                    f"Clamping."
                )
                bug_line = max(1, min(bug_line, parsed_code.total_lines))
            
            return DetectionResult(
                bug_line=bug_line,
                bug_type=bug_type,
                confidence=min(float(confidence_raw), 0.90),  # Cap LLM confidence
                detection_method="llm",
                raw_reasoning=reasoning,
            )
        except Exception as e:
            logger.error(f"[LLM] Detection failed: {e}")
            return None
    
    def _build_llm_prompt(
        self,
        parsed_code: ParsedCode,
        correct_code: Optional[str],
        context: Optional[str],
        mcp_patterns: list[BugPattern],
        documentation_context: str = "",
    ) -> str:
        """Build the LLM prompt for bug detection."""
        sections = []
        
        sections.append("## Task\nAnalyze the following C++ code snippet and identify the EXACT line number that contains a bug.\n")
        
        # Code with line numbers
        sections.append(f"## Code (with line numbers)\n```cpp\n{parsed_code.get_numbered_code()}\n```\n")
        
        # Context
        if context:
            sections.append(f"## Context\nThis code is related to: {context}\n")
        
        # Correct code (if available, for reference)
        if correct_code:
            sections.append(f"## Reference (Correct Version)\n```cpp\n{correct_code}\n```\n")
        
        # Known bug patterns from MCP
        if mcp_patterns:
            pattern_text = "\n".join(
                f"- **{p.context}**: {p.description}"
                for p in mcp_patterns if p.description
            )
            sections.append(f"## Known Bug Patterns (from manual)\n{pattern_text}\n")
        
        # RDI API documentation from MCP server
        if documentation_context:
            sections.append(f"## RDI API Documentation (Retrieved from MCP Server)\n{documentation_context}\n")
        
        # Output format instruction
        sections.append(
            '## Output\n'
            'Respond with a JSON object containing:\n'
            '- `bug_line`: The exact 1-indexed line number containing the bug (integer)\n'
            '- `bug_type`: A short category for the bug type (string)\n'
            '- `reasoning`: A clear explanation of what the bug is and why this line is problematic (string)\n'
            '- `confidence`: Your confidence level from 0.0 to 1.0 (number)\n\n'
            'Example: {"bug_line": 5, "bug_type": "naming_error", "reasoning": "Function RDI_begin() should be RDI_END()", "confidence": 0.9}\n'
        )
        
        return "\n".join(sections)
    
    # ─── Ensemble ────────────────────────────────────────────────────────────
    
    def _select_best(self, candidates: list[DetectionResult]) -> DetectionResult:
        """
        Select the best detection result from multiple candidates.
        
        Priority: diff > pattern > llm (weighted by confidence).
        If multiple layers agree on the same line, boost confidence.
        """
        # Method priority weights
        method_weight = {
            "diff": 1.3,     # Diff is most reliable
            "pattern": 1.1,  # Pattern match is second
            "llm": 1.0,      # LLM is baseline
        }
        
        # Score each candidate
        scored = []
        for c in candidates:
            weight = method_weight.get(c.detection_method, 1.0)
            score = c.confidence * weight
            scored.append((score, c))
        
        # Check for consensus (multiple methods agree on same line)
        line_counts: dict[int, int] = {}
        for _, c in scored:
            line_counts[c.bug_line] = line_counts.get(c.bug_line, 0) + 1
        
        # Boost candidates that have consensus
        for i, (score, c) in enumerate(scored):
            if line_counts.get(c.bug_line, 0) > 1:
                scored[i] = (score * 1.2, c)  # 20% boost for consensus
        
        # Select highest scored
        scored.sort(key=lambda x: x[0], reverse=True)
        best = scored[0][1]
        
        # If we have consensus, note it in reasoning
        if line_counts.get(best.bug_line, 0) > 1:
            agreeing_methods = [
                c.detection_method for _, c in scored
                if c.bug_line == best.bug_line
            ]
            best.raw_reasoning += (
                f"\n\n[Consensus] {len(agreeing_methods)} detection methods agree "
                f"on line {best.bug_line}: {', '.join(agreeing_methods)}"
            )
            best.confidence = min(best.confidence + 0.05, 1.0)
        
        logger.info(
            f"Selected: line {best.bug_line} via {best.detection_method} "
            f"(confidence: {best.confidence:.2f})"
        )
        
        return best
