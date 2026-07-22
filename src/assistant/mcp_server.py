"""MCP exposure for the same read-only tools used by the Streamlit assistant."""

from __future__ import annotations

import json
from pathlib import Path

from src.assistant.context_tools import StockAnalyzerTools
from src.ui.database import ReportDatabase


def create_mcp_server(database_path: str | Path = "data/ui/stock_analyzer.db",
                      repository_root: str | Path = "."):
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError("The optional 'mcp' package is required to run the MCP server.") from exc

    tools = StockAnalyzerTools(ReportDatabase(database_path), repository_root)
    server = FastMCP("Alphatrace", instructions=(
        "Read-only access to saved stock-analysis reports and secret-safe project source. "
        "Use report values as the source of truth and never infer live prices or place trades."
    ))

    @server.tool()
    def list_selected_stocks(report_id: int | None = None) -> str:
        """List trade, watchlist, and rejected candidates in a saved or latest report."""
        return json.dumps(tools.selected_stocks(report_id), default=str)

    @server.tool()
    def get_stock_decision(symbol: str, report_id: int | None = None) -> str:
        """Get the grounded decision, risk, evidence, and trade plan for one stock."""
        return json.dumps(tools.candidate_summary(symbol, report_id), default=str)

    @server.tool()
    def search_project_code(query: str, max_results: int = 12) -> str:
        """Search safe project source files; credentials, data, caches, and .git are excluded."""
        return json.dumps(tools.search_project(query, max_results), default=str)

    @server.tool()
    def read_project_source(relative_path: str, start_line: int = 1,
                            max_lines: int = 160) -> str:
        """Read a bounded range from an allowed project source file."""
        return json.dumps(tools.read_project_file(relative_path, start_line, max_lines), default=str)

    return server
