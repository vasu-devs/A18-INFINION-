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
    ) -> list[DetectionResult]:
        """
        Run all detection layers and return ALL detected bugs.
        
        Args:
            parsed_code: Structured parsed code from Code Parser Agent.
            correct_code: Correct version of the code (if available).
            context: Context/description of what the code does.
            mcp_patterns: Known bug patterns from MCP Lookup Agent.
        
        Returns:
            List of DetectionResults for all bugs found.
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
        
        # ─── Layer 3: LLM analysis (returns ALL bugs) ────────────────
        if config.ENABLE_LLM_DETECTION:
            llm_results = await self._detect_via_llm(
                parsed_code, correct_code, context, mcp_patterns, documentation_context
            )
            if llm_results:
                for r in llm_results:
                    candidates.append(r)
                    logger.info(f"[LLM] Detected bug at line {r.bug_line} "
                               f"(confidence: {r.confidence:.2f})")
        
        # ─── Deduplicate by line number ────────────────────────────
        if not candidates:
            logger.warning("No detection layer produced a result. Defaulting to line 1.")
            return [DetectionResult(
                bug_line=1,
                bug_type="unknown",
                confidence=0.0,
                detection_method="none",
                raw_reasoning="No detection layer could identify the bug.",
            )]
        
        # Deduplicate: keep highest-confidence result per line
        by_line: dict[int, DetectionResult] = {}
        for c in candidates:
            if c.bug_line not in by_line or c.confidence > by_line[c.bug_line].confidence:
                by_line[c.bug_line] = c
        
        # Filter out low-confidence detections
        MIN_CONFIDENCE = 0.70
        filtered = {line: r for line, r in by_line.items() if r.confidence >= MIN_CONFIDENCE}
        
        if not filtered:
            # If all filtered out, keep the single highest-confidence one
            best = max(by_line.values(), key=lambda r: r.confidence)
            filtered = {best.bug_line: best}
        
        results = sorted(filtered.values(), key=lambda r: r.bug_line)
        logger.info(f"Total unique bugs found: {len(results)} at lines {[r.bug_line for r in results]}")
        return results
    
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
    ) -> list[DetectionResult]:
        """
        Use an LLM to analyze the code and identify ALL buggy lines.
        
        Returns a list of DetectionResult (one per bug found).
        """
        try:
            prompt = self._build_llm_prompt(parsed_code, correct_code, context, mcp_patterns, documentation_context)
            
            system_prompt = (
                "You are an expert RDI/SmartRDI C++ API reviewer. "
                "CRITICAL: You must read and internalize the provided MCP manual / API documentation "
                "BEFORE analyzing the code. You must prioritize the rules in the provided manual "
                "over standard C++ syntax rules. If the manual mentions a specific function or "
                "variable constraint, check for that exact constraint first. "
                "Your task is to find lines that contain REAL bugs — wrong function names, "
                "wrong parameter values, wrong argument order, wrong API usage, inverted lifecycle, "
                "or misspelled identifiers. Do NOT flag correct code, comments, or style issues. "
                "Be VERY selective — only flag genuine errors that violate the documented API rules. "
                "Respond ONLY with valid JSON."
            )
            
            response = await call_llm(
                prompt=prompt,
                system_prompt=system_prompt,
                json_mode=True,
                temperature=0.1,
            )
            
            data = parse_json_response(response)
            
            # Handle both single-bug and multi-bug responses
            bugs_list = data.get("bugs", None)
            if bugs_list is None:
                # Fallback: single-bug format
                bugs_list = [data]
            
            results = []
            for bug in bugs_list:
                bug_line = int(bug.get("bug_line", bug.get("line_number", 1)))
                reasoning = bug.get("reasoning", bug.get("explanation", ""))
                bug_type = bug.get("bug_type", "llm_detected")
                confidence_raw = bug.get("confidence", 0.7)
                
                # ── 0-index safety guard ──────────────────────────
                # LLMs sometimes return 0-indexed line numbers.
                # The output CSV must be strictly 1-indexed.
                if bug_line == 0:
                    logger.warning(
                        "[LLM] Returned 0-indexed bug_line=0. "
                        "Applying +1 offset to correct to 1-indexed."
                    )
                    bug_line += 1
                
                # Validate line number is within range
                if bug_line < 1 or bug_line > parsed_code.total_lines:
                    logger.warning(
                        f"[LLM] Returned line {bug_line} outside range [1, {parsed_code.total_lines}]. "
                        f"Clamping."
                    )
                    bug_line = max(1, min(bug_line, parsed_code.total_lines))
                
                results.append(DetectionResult(
                    bug_line=bug_line,
                    bug_type=bug_type,
                    confidence=min(float(confidence_raw), 0.90),
                    detection_method="llm",
                    raw_reasoning=reasoning,
                ))
            
            return results if results else None
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
        """Build the LLM prompt for bug detection.
        
        Structure is intentionally ordered to enforce 'context-first' reasoning:
        Task → MCP Documentation → Known Patterns → Code → Reference → Output
        """
        sections = []
        
        # Task description — highly specific for RDI API
        sections.append(
            '## Task\n'
            'Analyze the C++ RDI/SmartRDI API code and find lines that contain REAL bugs.\n\n'
            'A REAL bug is ONE of these:\n'
            '- **Wrong/misspelled function name** (e.g. getMeans instead of getMeas, getFFC instead of getFFV)\n'
            '- **Wrong parameter value** (e.g. voltage exceeds allowed range, wrong mode constant)\n'
            '- **Swapped/reversed arguments** (e.g. iClamp(high, low) instead of iClamp(low, high))\n'
            '- **Wrong method** (e.g. write() instead of execute(), read() instead of execute())\n'
            '- **Inverted lifecycle** (e.g. RDI_END before RDI_BEGIN, or functions called outside their block)\n'
            '- **Missing required parameter** (e.g. getAlarmValue() without pin name)\n'
            '- **Wrong variable name** (e.g. using vec_port2 when vec_port1 was declared)\n'
            '- **Wrong method name** (e.g. push_forward instead of push_back, burst() on non-burst object)\n'
            '- **Pin name mismatch** (e.g. D0 vs DO — digit zero vs letter O)\n'
            '- **Wrong casing** (e.g. imeasRange instead of iMeasRange)\n\n'
            'NOT a bug:\n'
            '- Comments, blank lines, or variable declarations\n'
            '- Correct execute() calls, correct RDI_BEGIN/END pairs\n'
            '- Lines that are simply part of a multi-line method chain and are themselves correct\n'
            '- Style preferences or formatting\n'
        )
        
        # ── CONTEXT-FIRST: MCP documentation and patterns BEFORE the code ──
        # This ordering forces the LLM to absorb API rules before seeing the code.
        
        # Context-first directive
        sections.append(
            '## ⚠️ Context-First Directive\n'
            'You must prioritize the rules in the provided manual over standard C++ syntax rules. '
            'If the manual mentions a specific function or variable constraint, check for that '
            'exact constraint first. Read the documentation sections below BEFORE analyzing the code.\n'
        )
        
        # RDI API documentation from MCP server (moved BEFORE code)
        if documentation_context:
            sections.append(f"## RDI API Documentation (Retrieved from MCP Server)\n{documentation_context}\n")
        
        # Known bug patterns from MCP (moved BEFORE code)
        if mcp_patterns:
            pattern_text = "\n".join(
                f"- **{p.context}**: {p.description}"
                for p in mcp_patterns if p.description
            )
            sections.append(f"## Known Bug Patterns (from manual)\n{pattern_text}\n")
        
        # Context
        if context:
            sections.append(f"## Context\nThis code is related to: {context}\n")
        
        # Code with line numbers (placed AFTER documentation so LLM reads rules first)
        sections.append(f"## Code (with line numbers)\n```cpp\n{parsed_code.get_numbered_code()}\n```\n")
        
        # Correct code (if available, for reference)
        if correct_code:
            sections.append(f"## Reference (Correct Version)\n```cpp\n{correct_code}\n```\n")
        
        # Output format instruction
        sections.append(
            '## Output\n'
            'Respond with a JSON object containing a `bugs` array. ONLY include lines you are VERY confident contain a real bug.\n'
            'Each bug has:\n'
            '- `bug_line`: The exact 1-indexed line number (integer)\n'
            '- `bug_type`: A short category (string)\n'
            '- `reasoning`: Brief explanation referencing the specific manual rule or API constraint violated (string)\n'
            '- `confidence`: Confidence 0.0-1.0 — ONLY include bugs with confidence >= 0.80 (number)\n\n'
            'IMPORTANT: Do NOT include lines that look correct. Be precise. '
            'Reference the documentation or manual rule that the buggy line violates.\n'
            'Example: {"bugs": [{"bug_line": 3, "bug_type": "wrong_function", "reasoning": "Per the RDI manual, getFFC should be getFFV for frequency voltage", "confidence": 0.95}]}\n'
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
