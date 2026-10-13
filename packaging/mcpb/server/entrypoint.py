"""MCPB entry point. Only the MCP protocol is written to stdout."""
from cad2gis.agent_mcp import main

if __name__ == '__main__':
    main(['--transport', 'stdio'])
