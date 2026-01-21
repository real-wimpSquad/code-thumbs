# Code Thumbs

Multi-language code formatting, linting, and auto-fixing tools exposed via HTTP API and MCP server.
Designed with AI agents in mind.

## Features

- **Multi-language support**: Python, JavaScript, TypeScript, C#, Rust, Go, Java, C/C++, Shell, SQL, PHP, Kotlin, Swift, Ruby, Markdown, YAML
- **HTTP API**: OpenAI-compatible function calling schemas
- **MCP Server**: STDIO transport for Claude Desktop integration
- **Docker-based**: Isolated execution environment
- **17 languages, 35+ tools**: Format, lint, and auto-fix across modern and legacy ecosystems

### Supported Tools by Language

| Language   | Format                      | Lint                  | Fix                    |
|------------|-----------------------------|-----------------------|------------------------|
| Python     | ruff, black                 | ruff, pylint, mypy    | ruff                   |
| JavaScript | prettier                    | eslint                | eslint                 |
| TypeScript | prettier                    | eslint, tsc           | eslint                 |
| C#         | csharpier, dotnet-format    | dotnet-format         | —                      |
| Rust       | rustfmt, cargo-fmt          | cargo-clippy          | —                      |
| Go         | gofmt, goimports            | golangci-lint         | golangci-lint          |
| Java       | google-java-format          | checkstyle            | —                      |
| C/C++      | clang-format                | clang-tidy            | —                      |
| Shell      | shfmt                       | shellcheck            | —                      |
| SQL        | sqlfluff                    | sqlfluff              | sqlfluff               |
| PHP        | php-cs-fixer                | phpstan               | php-cs-fixer           |
| Kotlin     | ktlint                      | ktlint                | ktlint                 |
| Swift      | swiftformat                 | —                     | —                      |
| Ruby       | rubocop                     | rubocop               | rubocop                |
| Markdown   | prettier                    | markdownlint          | markdownlint           |
| YAML       | prettier                    | yamllint              | —                      |

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

# Batch lint multiple files
curl -X POST http://localhost:8072/batch/lint \
  -H "Content-Type: application/json" \
  -d '{
    "language":"python",
    "files":[
      {"path":"main.py","content":"def foo(x,y): return x+y"},
      {"path":"utils.py","content":"import os\nimport sys"}
    ]
  }'
```

### Production (Pre-built Images)

```bash
# Pull pre-built images from GitHub Container Registry
docker pull ghcr.io/real-wimpsquad/code-thumbs:latest
docker pull ghcr.io/real-wimpsquad/code-thumbs-api:latest

# Run with docker-compose
docker-compose up -d

# Or run directly
docker run -d --name code-thumbs \
  -v $(pwd):/workspace \
  ghcr.io/real-wimpsquad/code-thumbs:latest \
  tail -f /dev/null

docker run -d --name code-thumbs-api \
  -p 8072:8072 \
  -v $(pwd):/workspace \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e CODE_THUMBS_CONTAINER=code-thumbs \
  ghcr.io/real-wimpsquad/code-thumbs-api:latest
```

## API Endpoints

### Core Endpoints (ML-Exclusive Format)

All responses use compressed, semantic-dense format optimized for AI agents:

- `POST /format` - Format code
  - Response: `tool:ruff|changed:yes\n\n{formatted_code}` or `fmt_check:clean|tool:ruff`
- `POST /lint` - Lint code
  - Response: `tool:ruff|err:3|warn:5\nL10:e:E501:line too long\nL23:w:F401:unused import`
- `POST /fix` - Auto-fix issues
  - Response: `tool:ruff|fixed:yes|remaining:0\n\n{fixed_code}`
- `POST /check` - Format + lint combined
  - Response: `clean:fmt+lint` or `fmt:needs_fmt|lint:err:3+warn:5\nL10:e:msg`

### Batch Endpoints

Process multiple files in one request:

- `POST /batch/format` - Format multiple files
- `POST /batch/lint` - Lint multiple files
- `POST /batch/fix` - Fix multiple files

Batch response format: `path1|result1\n---\npath2|result2`

### Utility Endpoints

- `GET /` - API overview
- `GET /health` - Health check (includes tool availability)
- `GET /languages` - List supported languages and tools
- `GET /tools/openai` - OpenAI function schemas (structured format)

## ML-Exclusive Response Format

All API endpoints return compressed, semantic-dense responses optimized for AI agents:

**Format endpoint:**
```
# check_only=true
fmt_check:clean|tool:ruff

# check_only=false, changed
tool:ruff|changed:yes

{formatted code here}
```

**Lint endpoint:**
```
# Clean code
clean|tool:ruff

# With issues
tool:ruff|err:2|warn:3|info:0
L10:e:E501:line too long (>88 chars)
L23:wf:F401:os imported but unused
L45:e:E999:syntax error
```

Issue format: `L{line}:{severity}{fixable}:{code}:{message}`
- Severity: `e`=error, `w`=warning, `i`=info
- Fixable: `f` suffix if auto-fixable, omitted otherwise

**Fix endpoint:**
```
# No changes
tool:ruff|fixed:no|reason:no_fixable_issues

# Fixed with remaining issues
tool:ruff|fixed:yes|remaining:2
issues:L10:line too long|L45:syntax error

{fixed code here}
```

**Check endpoint:**
```
# All clean
clean:fmt+lint

# Issues found
fmt:needs_fmt|lint:err:2+warn:3
L10:e:line too long
L23:w:unused import
L45:e:syntax error
```

**Batch endpoints:**
```
main.py|tool:ruff|changed:yes

{code}
---
utils.py|clean|tool:ruff
```

**Error responses:**
```
err:unavailable|code:503|msg:tool_swiftformat_not_available_in_container
err:bad_req|code:400|msg:unsupported_language:brainfuck
err:timeout|code:504|msg:tool_execution_timed_out
```

All responses (success + error) use compressed format with `{"result": "..."}` wrapper.

## MCP Server

Located in `mcp-server/mcp_server_code_thumbs.py`. Example configuration (Claude Desktop):

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

## Error Handling & Reliability

**Build-time resilience:**
- Critical tools (Python, JS/TS, Go, C/C++, Shell, Rust, C#) fail build if installation fails
- Optional tools (Swift) fail gracefully with warnings
- Build verification step ensures all critical tools are installed

**Runtime resilience:**
- API checks tool availability before execution (HTTP 503 if missing)
- Distinguishes between "tool not installed" vs "code has errors"
- `/health` endpoint shows tool availability status
- 30s timeout per operation to prevent hangs

## Integration

### Atomic Pumpkin (Optional)

Code Thumbs can be used as an addon with [Atomic Pumpkin](https://github.com/real-wimpsquad/atomic-pumpkin):

```bash
make dev ADDONS="code-thumbs"
```

This mounts the atomic-pumpkin workspace at `/workspace` for formatting/linting project files.

### Standalone

Code Thumbs works independently - just mount your project directory to `/workspace`:

```bash
docker run -d --name code-thumbs-api \
  -p 8072:8072 \
  -v /path/to/your/project:/workspace \
  -v /var/run/docker.sock:/var/run/docker.sock \
  ghcr.io/real-wimpsquad/code-thumbs-api:latest
```

## License

MIT - See LICENSE file
