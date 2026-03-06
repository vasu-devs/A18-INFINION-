"""
Bug Detector Agent — the core intelligence that identifies buggy lines.

Uses a 2-layer detection strategy:
  Layer 1: Pattern matching against MCP bug manual
  Layer 2: LLM-powered code analysis (for unknown/novel bugs)
"""

from __future__ import annotations
import logging
import re
from typing import Optional

from models.schemas import ParsedCode, BugPattern, DetectionResult
from utils.llm_client import call_llm, parse_json_response
import config

logger = logging.getLogger(__name__)


class BugDetectorAgent:
    """
    Agent 4 — Multi-layered bug detector.
    
    Combines pattern matching and LLM reasoning
    to identify the exact line containing a bug in C++ code.
    """
    
    async def detect(
        self,
        parsed_code: ParsedCode,
        context: Optional[str],
        mcp_patterns: list[BugPattern],
        documentation_context: str = "",
        numbered_code: str = "",
    ) -> list[DetectionResult]:
        """
        Run all detection layers and return ALL detected bugs.
        
        Args:
            parsed_code: Structured parsed code from Code Parser Agent.
            context: Context/description of what the code does.
            mcp_patterns: Known bug patterns from MCP Lookup Agent.
            documentation_context: Retrieved MCP documentation text.
            numbered_code: Pre-formatted code with explicit line numbers from CodeParserAgent.
        
        Returns:
            List of DetectionResults for all bugs found.
        """
        candidates: list[DetectionResult] = []
        
        # ─── Layer 1: Pattern matching ───────────────────────────────
        if config.ENABLE_PATTERN_DETECTION and mcp_patterns:
            pattern_result = self._detect_via_pattern(parsed_code, mcp_patterns)
            if pattern_result:
                candidates.append(pattern_result)
                logger.info(f"[Pattern] Detected bug at line {pattern_result.bug_line} "
                           f"(confidence: {pattern_result.confidence:.2f})")
        
        # ─── Layer 2: LLM analysis (returns ALL bugs) ────────────────
        if config.ENABLE_LLM_DETECTION:
            llm_results = await self._detect_via_llm(
                parsed_code, context, mcp_patterns, documentation_context, numbered_code
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
    
    # ─── Layer 1: Pattern Matching ───────────────────────────────────────────
    
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
    
    # ─── Layer 2: LLM Analysis ───────────────────────────────────────────────
    
    async def _detect_via_llm(
        self,
        parsed_code: ParsedCode,
        context: Optional[str],
        mcp_patterns: list[BugPattern],
        documentation_context: str = "",
        numbered_code: str = "",
    ) -> list[DetectionResult]:
        """
        Use an LLM to analyze the code and identify ALL buggy lines.
        
        Returns a list of DetectionResult (one per bug found).
        """
        try:
            prompt = self._build_llm_prompt(parsed_code, context, mcp_patterns, documentation_context, numbered_code)
            
            system_prompt = (
                "You are an expert C++ debugging agent operating under strict automated grading constraints.\n"
                "You will receive a C++ snippet with explicitly numbered lines (e.g., \"1: code\").\n"
                "You will also receive constraints from the MCP Manual.\n\n"
                "YOUR PRIME DIRECTIVE:\n"
                "You must find the EXACT, SINGLE line number that represents the ROOT CAUSE of the bug. \n"
                "An automated script will grade your output. If you output multiple lines when only one line needs to be fixed, you will score 0.\n\n"
                "ROOT CAUSE TRACING RULES:\n"
                "1. Setup vs Execution: If a function execution fails because an earlier configuration/state was set incorrectly (e.g., wrong mode, wrong range, missing parameter), the ROOT CAUSE is the line where the incorrect configuration was set, NOT the line where it executes.\n"
                "2. Typos/Names: If a function name is misspelled (e.g., `readHumanSeniority` instead of `readHumSensor`), the root cause is the line with the typo.\n"
                "3. Order of Operations: If a function is called out of order (e.g., END before BEGIN), the root cause is the misplaced line itself.\n"
                "4. DO NOT BLEED: Never flag surrounding lines, variable declarations, or subsequent symptom lines.\n\n"
                "OUTPUT FORMAT:\n"
                "Respond ONLY in valid JSON:\n"
                "{\n"
                "  \"bug_lines\": [int], // Array containing ONLY the absolute root cause line number(s). \n"
                "  \"confidence\": float,\n"
                "  \"explanation\": \"According to the MCP manual...\"\n"
                "}"
            )
            
            response = await call_llm(
                prompt=prompt,
                system_prompt=system_prompt,
                json_mode=True,
                temperature=0.1,
            )
            
            data = parse_json_response(response)
            
            # Provide support for the new "bug_lines" array format
            results = []
            
            # Extract bug lines from the new format or fallback to the old formats
            if "bug_lines" in data and isinstance(data["bug_lines"], list):
                # New format: {"bug_lines": [1, 2], "confidence": 0.9, "explanation": "..."}
                for b_line in data["bug_lines"]:
                    results.append(DetectionResult(
                        bug_line=int(b_line),
                        bug_type="llm_detected",
                        confidence=min(float(data.get("confidence", 0.90)), 0.90),
                        detection_method="llm",
                        raw_reasoning=data.get("explanation", ""),
                    ))
            else:
                # Handle old single-bug and multi-bug responses
                bugs_list = data.get("bugs", None)
                if bugs_list is None:
                    bugs_list = [data]
                
                for bug in bugs_list:
                    bug_line = int(bug.get("bug_line", bug.get("line_number", 1)))
                    reasoning = bug.get("reasoning", bug.get("explanation", ""))
                    bug_type = bug.get("bug_type", "llm_detected")
                    confidence_raw = bug.get("confidence", 0.7)
                    
                    results.append(DetectionResult(
                        bug_line=bug_line,
                        bug_type=bug_type,
                        confidence=min(float(confidence_raw), 0.90),
                        detection_method="llm",
                        raw_reasoning=reasoning,
                    ))
            
            # Post-process the extracted results (0-index correction & clamping)
            for i, res in enumerate(results):
                # ── 0-index safety guard ──────────────────────────
                if res.bug_line == 0:
                    logger.warning("[LLM] Returned 0-indexed bug_line=0. Applying +1 offset.")
                    res.bug_line += 1
                
                # Validate line number is within range
                if res.bug_line < 1 or res.bug_line > parsed_code.total_lines:
                    logger.warning(
                        f"[LLM] Returned line {res.bug_line} outside range [1, {parsed_code.total_lines}]. Clamping."
                    )
                    res.bug_line = max(1, min(res.bug_line, parsed_code.total_lines))
            
            return results if results else None
        except Exception as e:
            logger.error(f"[LLM] Detection failed: {e}")
            return None
    
    def _build_llm_prompt(
        self,
        parsed_code: ParsedCode,
        context: Optional[str],
        mcp_patterns: list[BugPattern],
        documentation_context: str = "",
        numbered_code: str = "",
    ) -> str:
        """Build the LLM prompt for bug detection.
        
        Structure is intentionally ordered to enforce 'context-first' reasoning:
        Task → MCP Documentation → Known Patterns → Code → Output
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
        
        # Critical line-number precision instruction
        sections.append(
            '## ⚠️ CRITICAL LINE NUMBER INSTRUCTIONS\n'
            'You must be surgical. ONLY flag the exact line number where the root cause of the error occurs.\n'
            'DO NOT flag surrounding lines, variable initializations, or subsequent lines that just happen '
            'to fail because of the earlier bug. If the error is a typo or wrong parameter, output ONLY '
            'the single line containing that typo.\n'
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
        # Use the pre-formatted numbered_code from CodeParserAgent if available,
        # which uses the explicit "N: content" format for unambiguous line references.
        code_text = numbered_code if numbered_code else parsed_code.get_numbered_code()
        sections.append(f"## Code (with line numbers)\n```cpp\n{code_text}\n```\n")
        sections.append(
            '## Output\n'
            'Respond ONLY with a JSON object in this format:\n'
            '{\n'
            '  "bug_lines": [int], // Array containing ONLY the absolute root cause line number(s).\n'
            '  "confidence": float,\n'
            '  "explanation": "Brief explanation referencing the specific manual rule or API constraint violated"\n'
            '}\n'
        )
        
        return "\n".join(sections)
    
    # ─── Ensemble ────────────────────────────────────────────────────────────
    
    def _select_best(self, candidates: list[DetectionResult]) -> DetectionResult:
        """
        Select the best detection result from multiple candidates.
        
        Priority: pattern > llm (weighted by confidence).
        If multiple layers agree on the same line, boost confidence.
        """
        # Method priority weights
        method_weight = {
            "pattern": 1.1,  # Pattern match is highest
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
