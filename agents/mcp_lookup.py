"""
MCP Lookup Agent — queries the FastMCP Server for RDI API documentation.

The MCP server exposes a `search_documents(query)` tool that performs
vector similarity search over indexed RDI API documentation using
BAAI/bge-base-en-v1.5 embeddings.

Responsibilities:
  - Start the MCP server process (if not already running)
  - Connect via SSE transport on port 8003
  - Query `search_documents` for context relevant to each code snippet
  - Cache results to avoid redundant queries
"""

from __future__ import annotations
import asyncio
import json
import logging
import subprocess
import sys
import time
from typing import Optional

from models.schemas import BugPattern
import config

logger = logging.getLogger(__name__)


class MCPLookupAgent:
    """
    Agent 3 — Queries the MCP Server for RDI API documentation.
    
    Uses FastMCP SSE client to connect to the server and call
    the `search_documents(query)` tool.
    """
    
    def __init__(self):
        self._cache: dict[str, list[BugPattern]] = {}
        self._doc_cache: dict[str, list[dict]] = {}  # Raw doc cache
        self._connected = False
        self._client = None
        self._session = None
        self._server_process: Optional[subprocess.Popen] = None
    
    async def start_server(self) -> bool:
        """
        Start the MCP server process if not already running.
        
        Returns:
            True if server started or already running.
        """
        try:
            # Check if server is already running by trying to connect
            import aiohttp
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"http://localhost:{config.MCP_SERVER_PORT}/sse",
                        timeout=aiohttp.ClientTimeout(total=2),
                    ) as resp:
                        if resp.status == 200:
                            logger.info("MCP server already running")
                            return True
            except (aiohttp.ClientError, asyncio.TimeoutError):
                pass
            
            # Start the server — must run from the server directory
            # so the embedding_model and storage paths resolve correctly
            server_dir = str(config.MCP_SERVER_DIR)
            server_script = "mcp_server.py"
            logger.info(f"Starting MCP server from: {server_dir}/{server_script}")
            
            python_exe = sys.executable
            self._server_process = subprocess.Popen(
                [python_exe, server_script],
                cwd=server_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            
            # Wait for server to be ready
            logger.info("Waiting for MCP server to start...")
            for i in range(30):  # Wait up to 30 seconds
                await asyncio.sleep(1)
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(
                            f"http://localhost:{config.MCP_SERVER_PORT}/sse",
                            timeout=aiohttp.ClientTimeout(total=2),
                        ) as resp:
                            if resp.status == 200:
                                logger.info(f"MCP server started successfully (took {i+1}s)")
                                return True
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    continue
            
            logger.error("MCP server failed to start within 30 seconds")
            return False
            
        except Exception as e:
            logger.error(f"Failed to start MCP server: {e}")
            return False

    async def connect(self) -> bool:
        """
        Establish connection to the MCP server via SSE.
        
        Returns:
            True if connection successful, False otherwise.
        """
        try:
            from mcp import ClientSession
            from mcp.client.sse import sse_client
            
            url = config.MCP_SERVER_URL
            logger.info(f"Connecting to MCP server at: {url}")
            
            self._transport_ctx = sse_client(url=url)
            streams = await self._transport_ctx.__aenter__()
            self._session = ClientSession(*streams)
            await self._session.__aenter__()
            await self._session.initialize()
            
            self._connected = True
            
            # Discover tools
            tools_response = await self._session.list_tools()
            tool_names = [t.name for t in tools_response.tools]
            logger.info(f"MCP connected. Available tools: {tool_names}")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to connect to MCP server: {e}")
            self._connected = False
            return False
    
    async def search_documents(self, query: str) -> list[dict]:
        """
        Search the RDI API documentation using the MCP server's
        `search_documents` tool.
        
        Args:
            query: The search query (e.g., "vForceRange parameters", 
                   "RDI method naming", "iClamp arguments").
        
        Returns:
            List of dicts with 'text' and 'score' keys.
        """
        # Check cache
        if query in self._doc_cache:
            return self._doc_cache[query]
        
        if not self._connected or not self._session:
            logger.warning("MCP not connected. Returning empty results.")
            return []
        
        try:
            result = await self._session.call_tool(
                "search_documents",
                arguments={"query": query},
            )
            
            documents = []
            for content in result.content:
                if hasattr(content, "text"):
                    try:
                        data = json.loads(content.text)
                        if isinstance(data, list):
                            documents = data
                        elif isinstance(data, dict):
                            documents = [data]
                    except json.JSONDecodeError:
                        documents = [{"text": content.text, "score": 1.0}]
            
            # Cache results
            self._doc_cache[query] = documents
            logger.info(f"MCP search '{query}': {len(documents)} documents found")
            
            return documents
            
        except Exception as e:
            logger.error(f"MCP search_documents failed for '{query}': {e}")
            return []
    
    async def lookup_bug_patterns(self, context: str) -> list[BugPattern]:
        """
        Query the MCP server for bug patterns matching the given context.
        
        Searches the RDI API documentation and converts results into
        BugPattern objects for the detection pipeline.
        
        Args:
            context: Context string (e.g., "RDI method naming", 
                     "mode for editing vectors").
        
        Returns:
            List of BugPattern objects.
        """
        if context in self._cache:
            return self._cache[context]
        
        documents = await self.search_documents(context)
        
        patterns = []
        for doc in documents:
            text = doc.get("text", "")
            score = doc.get("score", 0.0)
            
            if text and score > 0.3:  # Filter low-relevance results
                patterns.append(BugPattern(
                    pattern_id=None,
                    context=context,
                    description=text[:500],  # Trim to reasonable length
                    code_pattern=None,
                    correct_pattern=None,
                    characteristics=f"similarity_score={score:.3f}",
                ))
        
        self._cache[context] = patterns
        return patterns
    
    async def get_documentation_context(self, code: str, context: str) -> str:
        """
        Get a consolidated documentation context string for the LLM.
        
        Combines multiple search queries to build comprehensive context
        for bug detection and description.
        
        Args:
            code: The C++ code snippet to analyze.
            context: The context/description from the dataset.
        
        Returns:
            A consolidated documentation string.
        """
        queries = [context] if context else []
        
        # Extract API calls from code to search for their documentation
        import re
        api_calls = re.findall(r'rdi\.(\w+(?:\(\))?(?:\.\w+(?:\(\))?)*)', code)
        for call in api_calls[:3]:  # Limit to first 3 unique API chains
            queries.append(f"rdi.{call}")
        
        all_docs = []
        seen_texts = set()
        
        for query in queries:
            docs = await self.search_documents(query)
            for doc in docs[:5]:  # Top 5 per query
                text = doc.get("text", "")
                score = doc.get("score", 0.0)
                if text and score > 0.3 and text not in seen_texts:
                    seen_texts.add(text)
                    all_docs.append(f"[Score: {score:.2f}] {text}")
        
        if not all_docs:
            return ""
        
        return "--- RDI API Documentation (from MCP) ---\n" + "\n\n".join(all_docs[:10])
    
    async def disconnect(self) -> None:
        """Close the MCP server connection and stop the server process."""
        try:
            if self._session:
                await self._session.__aexit__(None, None, None)
            if hasattr(self, "_transport_ctx") and self._transport_ctx:
                await self._transport_ctx.__aexit__(None, None, None)
        except Exception as e:
            logger.debug(f"Error closing MCP connection: {e}")
        finally:
            self._connected = False
            self._session = None
        
        # Stop server process if we started it
        if self._server_process:
            try:
                self._server_process.terminate()
                self._server_process.wait(timeout=5)
            except Exception:
                self._server_process.kill()
            self._server_process = None
        
        logger.info("MCP server disconnected")
