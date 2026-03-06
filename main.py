"""
Agentic C++ Bug Detection System — Main Entry Point

Usage:
    python main.py --input dataset.csv --output output.csv
    python main.py  (uses default paths from config)
"""

from __future__ import annotations
import asyncio
import logging
import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent))

import config
from orchestrator import Orchestrator


def setup_logging() -> None:
    """Configure logging with rich formatting."""
    log_level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)
    
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s │ %(levelname)-7s │ %(name)-20s │ %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )
    
    # Quiet noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def main() -> None:
    """Parse CLI arguments and run the pipeline."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="🔍 Agentic C++ Bug Detection System — Infineon A18 Challenge",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python main.py --input dataset.csv\n"
            "  python main.py --input dataset.csv --output results.csv\n"
            "  python main.py --provider gemini --model gemini-2.0-flash\n"
        ),
    )
    
    parser.add_argument(
        "--input", "-i",
        type=str,
        default=str(config.DEFAULT_INPUT_CSV),
        help="Path to input dataset CSV (default: input_dataset.csv)",
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=str(config.OUTPUT_CSV_PATH),
        help="Path to output CSV (default: output.csv)",
    )
    parser.add_argument(
        "--provider", "-p",
        type=str,
        choices=["openai", "gemini", "ollama", "deepseek", "groq"],
        default=None,
        help="LLM provider to use (overrides .env config)",
    )
    parser.add_argument(
        "--model", "-m",
        type=str,
        default=None,
        help="LLM model name (overrides .env config)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose (DEBUG) logging",
    )
    
    args = parser.parse_args()
    
    # Override config from CLI args
    if args.provider:
        config.LLM_PROVIDER = args.provider
    if args.model:
        provider = config.LLM_PROVIDER.lower()
        if provider == "openai":
            config.OPENAI_MODEL = args.model
        elif provider == "gemini":
            config.GEMINI_MODEL = args.model
        elif provider == "ollama":
            config.OLLAMA_MODEL = args.model
        elif provider == "deepseek":
            config.DEEPSEEK_MODEL = args.model
        elif provider == "groq":
            config.GROQ_MODEL = args.model
    if args.verbose:
        config.LOG_LEVEL = "DEBUG"
    
    # Setup logging
    setup_logging()
    
    logger = logging.getLogger(__name__)
    logger.info("Starting Agentic C++ Bug Detection Pipeline")
    logger.info(f"LLM Provider: {config.LLM_PROVIDER} ({_get_model_name()})")
    logger.info(f"Input: {args.input}")
    logger.info(f"Output: {args.output}")
    
    # Validate input file exists
    input_path = Path(args.input)
    if not input_path.exists():
        logger.error(f"Input file not found: {input_path}")
        sys.exit(1)
    
    # Run the pipeline
    orchestrator = Orchestrator()
    
    try:
        results = asyncio.run(orchestrator.run(
            input_path=str(input_path),
            output_path=args.output,
        ))
        
        # Print summary
        print(f"\n{'─' * 50}")
        print(f"📊 Results Summary ({len(results)} snippets)")
        print(f"{'─' * 50}")
        for r in results:
            print(f"  ID={r.id:>3} │ Line={r.bug_line:>5} │ {r.explanation[:60]}")
        print(f"{'─' * 50}")
        print(f"💾 Output saved to: {args.output}")
        
    except KeyboardInterrupt:
        logger.info("Pipeline interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Pipeline failed: {e}", exc_info=True)
        sys.exit(1)


def _get_model_name() -> str:
    """Get the active model name for display."""
    provider = config.LLM_PROVIDER.lower()
    if provider == "openai":
        return config.OPENAI_MODEL
    elif provider == "gemini":
        return config.GEMINI_MODEL
    elif provider == "ollama":
        return config.OLLAMA_MODEL
    elif provider == "deepseek":
        return config.DEEPSEEK_MODEL
    elif provider == "groq":
        return config.GROQ_MODEL
    return "unknown"


if __name__ == "__main__":
    main()
