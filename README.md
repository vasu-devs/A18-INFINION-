# Agentic C++ Bug Detection System

A modular, AI-powered system that analyzes C++ code snippets to detect bugs with **line-level precision** and generate clear explanations.

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                     Orchestrator                         │
│  ┌──────────┐  ┌───────────┐  ┌───────────┐  ┌────────┐ │
│  │  Code     │  │   MCP     │  │   Bug     │  │  Bug   │ │
│  │  Parser   │→ │  Lookup   │→ │  Detector │→ │Describer│ │
│  └──────────┘  └───────────┘  └───────────┘  └────────┘ │
└──────────────────────────────────────────────────────────┘
       ↑               ↑               │              │
  Input CSV       MCP Server       3-Layer        Output CSV
                                  Detection
```

**5 modular agents:**
1. **Orchestrator** — coordinates the pipeline
2. **Code Parser** — tokenizes C++ into structured lines
3. **MCP Lookup** — queries MCP server for known bug patterns
4. **Bug Detector** — 3-layer detection (diff → pattern → LLM)
5. **Bug Describer** — generates human-readable explanations

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure (copy and edit .env)
cp .env.example .env
# Edit .env with your API keys

# 3. Run
python main.py --input dataset.csv --output output.csv
```

## CLI Options

```
python main.py --help
  --input, -i       Input CSV path
  --output, -o      Output CSV path
  --provider, -p    LLM provider (openai/gemini/ollama)
  --model, -m       LLM model name
  --verbose, -v     Debug logging
```

## Output Format

| ID | Bug Line | Explanation |
|----|----------|-------------|
| 0  | 5        | RDI_begin() changed to RDI_END() |
| 1  | 3        | Changes 'v' value from 3.0v to -2.0v |
