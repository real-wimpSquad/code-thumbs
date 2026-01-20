# Code Thumbs

Multi-language code formatting, linting, and auto-fixing tools exposed via HTTP API and MCP server.

## Features

- **Multi-language support**: Python, JavaScript, TypeScript, C#, Rust
- **HTTP API**: OpenAI-compatible function calling schemas
- **MCP Server**: STDIO transport for Claude Desktop integration
- **Docker-based**: Isolated execution environment
- **Format**: ruff, black, prettier, csharpier, rustfmt
- **Lint**: ruff, pylint, mypy, eslint, cargo-clippy
- **Auto-fix**: ruff --fix, eslint --fix

## Quick Start

### Development

```bash
# Build and start services
docker-compose up -d

# Test API
curl http://localhost:8072/health
curl http://localhost:8072/languages

# Format Python code
curl -X POST http://localhost:8072/format \
  -H "Content-Type: application/json" \
  -d '{"language":"python","content":"def foo(x,y): return x+y"}'
```

### Production (Pre-built Images)

See `atomic-pumpkin-deploy` for production deployment using GHCR images.

## API Endpoints

- `GET /` - API overview
- `GET /health` - Health check
- `GET /languages` - List supported languages and tools
- `POST /format` - Format code
- `POST /lint` - Lint code
- `POST /fix` - Auto-fix issues
- `POST /check` - Comprehensive format + lint check
- `GET /tools/openai` - OpenAI function schemas

## MCP Server

Located in `mcp-server/mcp_server_code_thumbs.py`. Configure in Claude Desktop:

```json
{
  "mcpServers": {
    "code-quality": {
      "command": "python3",
      "args": ["/path/to/mcp_server_code_thumbs.py"],
      "env": {
        "CODE_THUMBS_API_URL": "http://localhost:8072"
      }
    }
  }
}
```

## Architecture

```
Client → API (port 8072) → Docker exec → code-thumbs container (tools)
                ↓
         MCP Server (STDIO) → API
```

## License

MIT - See LICENSE file
