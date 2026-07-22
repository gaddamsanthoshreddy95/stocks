"""Run the read-only Stock Analyzer MCP server."""

import argparse

from src.assistant.mcp_server import create_mcp_server


def main() -> None:
    parser = argparse.ArgumentParser(description="Stock Analyzer read-only MCP server")
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    parser.add_argument("--database", default="data/ui/stock_analyzer.db")
    args = parser.parse_args()
    server = create_mcp_server(args.database, ".")
    server.run(transport=args.transport)


if __name__ == "__main__":
    main()
