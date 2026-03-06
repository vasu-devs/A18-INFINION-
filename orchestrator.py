"""
Orchestrator Agent — top-level controller for the bug detection pipeline.

Responsibilities:
  - Start the MCP server process
  - Read the input dataset CSV
  - Coordinate all sub-agents for each code snippet
  - Collect results and write output CSV
  - Handle errors gracefully (per-snippet isolation)
"""

from __future__ import annotations
import asyncio
import logging
import time
from typing import Optional

from models.schemas import PipelineInput, PipelineOutput, DetectionResult
from agents.code_parser import CodeParserAgent
from agents.mcp_lookup import MCPLookupAgent
from agents.bug_detector import BugDetectorAgent
from agents.bug_describer import BugDescriberAgent
from utils.csv_io import read_input_csv, write_output_csv
import config

logger = logging.getLogger(__name__)


class Orchestrator:
    """
    Agent 1 — The top-level orchestrator that drives the full pipeline.
    
    For each code snippet in the dataset:
      1. Parse the code (Code Parser Agent)
      2. Query MCP for RDI API documentation (MCP Lookup Agent)
      3. Detect the buggy line (Bug Detector Agent)
      4. Generate explanation (Bug Describer Agent)
      5. Collect the result
    """
    
    def __init__(self):
        self.code_parser = CodeParserAgent()
        self.mcp_lookup = MCPLookupAgent()
        self.bug_detector = BugDetectorAgent()
        self.bug_describer = BugDescriberAgent()
        self._results: list[PipelineOutput] = []
    
    async def run(
        self,
        input_path: Optional[str] = None,
        output_path: Optional[str] = None,
    ) -> list[PipelineOutput]:
        """
        Run the full bug detection pipeline.
        
        Args:
            input_path: Path to input dataset CSV. Defaults to config.
            output_path: Path to write output CSV. Defaults to config.
        
        Returns:
            List of PipelineOutput results.
        """
        input_path = input_path or str(config.DEFAULT_INPUT_CSV)
        output_path = output_path or str(config.OUTPUT_CSV_PATH)
        
        logger.info("=" * 60)
        logger.info("🔍 Agentic C++ Bug Detection Pipeline — Starting")
        logger.info("=" * 60)
        
        start_time = time.time()
        
        # Step 1: Load input dataset
        logger.info(f"Loading dataset from: {input_path}")
        try:
            inputs = read_input_csv(input_path)
        except Exception as e:
            logger.error(f"Failed to load input dataset: {e}")
            raise
        
        logger.info(f"Loaded {len(inputs)} code snippets to analyze")
        
        # Step 2: Start and connect to MCP server
        logger.info("Starting MCP server...")
        server_started = await self.mcp_lookup.start_server()
        if server_started:
            mcp_connected = await self.mcp_lookup.connect()
            if mcp_connected:
                logger.info("✅ MCP server connected — RDI API documentation available")
            else:
                logger.warning("⚠️  MCP server started but connection failed")
        else:
            logger.warning("⚠️  MCP server unavailable — proceeding without documentation lookup")
        
        # Step 3: Process each snippet
        self._results = []
        for i, snippet in enumerate(inputs):
            logger.info(f"\n--- Snippet {i+1}/{len(inputs)} (ID={snippet.id}) ---")
            try:
                result = await self._process_snippet(snippet)
                self._results.append(result)
                logger.info(
                    f"  ✅ Bug at line {result.bug_line}: {result.explanation[:80]}..."
                    if len(result.explanation) > 80
                    else f"  ✅ Bug at line {result.bug_line}: {result.explanation}"
                )
            except Exception as e:
                logger.error(f"  ❌ Failed to process snippet ID={snippet.id}: {e}")
                # Add a fallback result so we don't skip any IDs
                self._results.append(PipelineOutput(
                    id=snippet.id,
                    bug_line=1,
                    explanation=f"Error: could not analyze this snippet ({e})",
                ))
        
        # Step 4: Write output CSV
        logger.info(f"\nWriting results to: {output_path}")
        write_output_csv(self._results, output_path)
        
        # Step 5: Cleanup
        await self.mcp_lookup.disconnect()
        
        elapsed = time.time() - start_time
        logger.info(f"\n{'=' * 60}")
        logger.info(f"✅ Pipeline complete — {len(self._results)} snippets analyzed in {elapsed:.1f}s")
        logger.info(f"{'=' * 60}")
        
        return self._results
    
    async def _process_snippet(self, snippet: PipelineInput) -> PipelineOutput:
        """
        Process a single code snippet through all agents.
        
        Args:
            snippet: A single PipelineInput row from the dataset.
        
        Returns:
            PipelineOutput with detection results.
        """
        # Agent 2: Parse the code
        logger.debug(f"  [Parser] Parsing code...")
        parsed_code = self.code_parser.parse(snippet.code)
        
        # Agent 3: Query MCP server for RDI API documentation
        mcp_patterns = []
        documentation_context = ""
        if snippet.context:
            logger.debug(f"  [MCP] Looking up documentation for context: '{snippet.context}'")
            mcp_patterns = await self.mcp_lookup.lookup_bug_patterns(snippet.context)
            documentation_context = await self.mcp_lookup.get_documentation_context(
                snippet.code, snippet.context
            )
        
        # Agent 4: Detect the bug
        logger.debug(f"  [Detector] Running detection layers...")
        detection = await self.bug_detector.detect(
            parsed_code=parsed_code,
            correct_code=snippet.correct_code,
            context=snippet.context,
            mcp_patterns=mcp_patterns,
            documentation_context=documentation_context,
        )
        
        # Agent 5: Generate explanation
        logger.debug(f"  [Describer] Generating explanation...")
        description = await self.bug_describer.describe(
            detection=detection,
            parsed_code=parsed_code,
            context=snippet.context,
            mcp_patterns=mcp_patterns,
            correct_code=snippet.correct_code,
        )
        
        return PipelineOutput(
            id=snippet.id,
            bug_line=detection.bug_line,
            explanation=description.explanation,
        )
