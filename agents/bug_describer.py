"""
Bug Describer Agent — generates clear, human-readable bug explanations.

Responsibilities:
  - Generate a concise explanation of the detected bug
  - Reference the MCP bug manual when applicable
  - If no MCP match, use LLM to generate an explanation
"""

from __future__ import annotations
import logging
from typing import Optional

from models.schemas import ParsedCode, BugPattern, DetectionResult, DescriptionResult
from utils.llm_client import call_llm
import config

logger = logging.getLogger(__name__)


class BugDescriberAgent:
    """
    Agent 5 — Generates human-readable explanations for detected bugs.
    
    If the bug matches a known pattern from the MCP manual, the manual's
    description is used/adapted. Otherwise, an LLM generates an explanation.
    """
    
    async def describe(
        self,
        detection: DetectionResult,
        parsed_code: ParsedCode,
        context: Optional[str],
        mcp_patterns: list[BugPattern],
        correct_code: Optional[str] = None,
    ) -> DescriptionResult:
        """
        Generate an explanation for the detected bug.
        
        Args:
            detection: Detection result from Bug Detector Agent.
            parsed_code: Parsed code from Code Parser Agent.
            context: Context/description about the code.
            mcp_patterns: Known bug patterns from MCP Lookup Agent.
            correct_code: Correct version of the code (if available).
        
        Returns:
            DescriptionResult with human-readable explanation.
        """
        # Strategy 1: Use MCP manual description if available and relevant
        manual_explanation = self._try_manual_explanation(detection, mcp_patterns)
        if manual_explanation:
            logger.info("[Describer] Using MCP manual explanation")
            return DescriptionResult(
                explanation=manual_explanation,
                references_manual=True,
            )
        
        # Strategy 2: Use diff-based reasoning if available
        if detection.detection_method == "diff" and detection.raw_reasoning:
            diff_explanation = self._format_diff_explanation(detection)
            if diff_explanation:
                logger.info("[Describer] Using diff-based explanation")
                return DescriptionResult(
                    explanation=diff_explanation,
                    references_manual=False,
                )
        
        # Strategy 3: Use LLM to generate explanation
        logger.info("[Describer] Generating LLM explanation")
        llm_explanation = await self._generate_llm_explanation(
            detection, parsed_code, context, mcp_patterns, correct_code
        )
        
        return DescriptionResult(
            explanation=llm_explanation,
            references_manual=False,
        )
    
    def _try_manual_explanation(
        self,
        detection: DetectionResult,
        mcp_patterns: list[BugPattern],
    ) -> Optional[str]:
        """
        Try to find a matching manual explanation for the detected bug.
        """
        if not mcp_patterns:
            return None
        
        # If detection was via pattern matching, the bug_type contains the context
        if detection.detection_method == "pattern" and ":" in detection.bug_type:
            pattern_context = detection.bug_type.split(":", 1)[1]
            for pattern in mcp_patterns:
                if pattern.context.lower() == pattern_context.lower() and pattern.description:
                    return pattern.description
        
        # Try matching any pattern's description
        for pattern in mcp_patterns:
            if pattern.description and self._is_relevant(detection.raw_reasoning, pattern):
                return pattern.description
        
        return None
    
    @staticmethod
    def _is_relevant(reasoning: str, pattern: BugPattern) -> bool:
        """Check if a bug pattern is relevant to the detection reasoning."""
        if not reasoning or not pattern.description:
            return False
        # Simple keyword overlap check
        reasoning_words = set(reasoning.lower().split())
        pattern_words = set(pattern.description.lower().split())
        overlap = reasoning_words & pattern_words
        # If more than 30% of pattern words appear in reasoning, consider relevant
        if len(pattern_words) > 0 and len(overlap) / len(pattern_words) > 0.3:
            return True
        return False
    
    def _format_diff_explanation(self, detection: DetectionResult) -> Optional[str]:
        """
        Format a clean explanation from diff-based detection reasoning.
        
        The raw reasoning from diff detection contains details like:
        "Line 5: 'RDI_begin()' should be 'RDI_END()'"
        
        We clean this up into a user-friendly explanation.
        """
        reasoning = detection.raw_reasoning
        if not reasoning:
            return None
        
        # Extract the first line of reasoning (before the full diff)
        first_line = reasoning.split("\n")[0].strip()
        
        # Clean up the "Line N: ..." format
        if first_line.startswith("Line "):
            # Extract the explanation part after the line reference
            parts = first_line.split(":", 1)
            if len(parts) > 1:
                return parts[1].strip().strip("'\"")
        
        return first_line if len(first_line) < 200 else first_line[:200]
    
    async def _generate_llm_explanation(
        self,
        detection: DetectionResult,
        parsed_code: ParsedCode,
        context: Optional[str],
        mcp_patterns: list[BugPattern],
        correct_code: Optional[str],
    ) -> str:
        """
        Use an LLM to generate a clear explanation of the bug.
        """
        try:
            # Get the buggy line content
            buggy_line = parsed_code.get_line(detection.bug_line)
            buggy_line_content = buggy_line.content if buggy_line else "N/A"
            
            prompt_parts = [
                "Generate a CONCISE, clear explanation of the bug in this C++ code.",
                "",
                f"**Bug location**: Line {detection.bug_line}: `{buggy_line_content}`",
                f"**Detection method**: {detection.detection_method}",
                f"**Bug type**: {detection.bug_type}",
            ]
            
            if context:
                prompt_parts.append(f"**Code context**: {context}")
            
            if detection.raw_reasoning:
                prompt_parts.append(f"**Analysis notes**: {detection.raw_reasoning[:500]}")
            
            if correct_code:
                prompt_parts.append(f"\n**Correct version of the code**:\n```cpp\n{correct_code}\n```")
            
            if mcp_patterns:
                patterns_text = "; ".join(
                    f"{p.context}: {p.description}" for p in mcp_patterns if p.description
                )
                prompt_parts.append(f"\n**Known bug patterns**: {patterns_text}")
            
            prompt_parts.extend([
                "",
                "Requirements:",
                "- Keep the explanation to 1-2 sentences maximum",
                "- Be specific about what is wrong and what it should be",
                "- Reference the bug manual patterns if they apply",
                "- Do NOT include the line number in your explanation",
                "- Do NOT include any JSON formatting, just the plain text explanation",
            ])
            
            response = await call_llm(
                prompt="\n".join(prompt_parts),
                system_prompt="You are a concise C++ code reviewer. Generate short, clear bug explanations.",
                json_mode=False,
                temperature=0.2,
            )
            
            # Clean up the response
            explanation = response.strip().strip('"').strip("'")
            
            # Ensure it's not too long
            if len(explanation) > 300:
                explanation = explanation[:297] + "..."
            
            return explanation
            
        except Exception as e:
            logger.error(f"[Describer] LLM explanation failed: {e}")
            # Fallback to raw reasoning
            if detection.raw_reasoning:
                return detection.raw_reasoning.split("\n")[0][:200]
            return f"Bug detected at line {detection.bug_line} ({detection.bug_type})"
