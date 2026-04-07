
#!/usr/bin/env python3
"""
============================================================================
Copyright 2026 real-wimpSquad

SPDX-License-Identifier: MIT
See LICENSE file for full license text.
============================================================================

Code Quality API - OpenAI/MCP-compatible endpoints for code_thumbs container
Exposes formatting, linting, and auto-fixing as HTTP API
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Optional, List, Literal
import subprocess
import os
import hashlib
from pathlib import Path

app = FastAPI(
    title="Code Quality API",
    version="1.0.0",
    description="Format, lint, and fix code via HTTP, MCP, or OpenAI function calling",
    docs_url="/docs",
    redoc_url="/redoc",
)


# ============================================================================
# CUSTOM EXCEPTION HANDLER (ML-EXCLUSIVE ERROR FORMAT)
# ============================================================================


@app.exception_handler(HTTPException)
async def ml_exclusive_exception_handler(request: Request, exc: HTTPException):
    """Return compressed error format for agent consumption"""
    # Map HTTP status to error type
    error_type = {
        400: "bad_req",
        404: "not_found",
        500: "server_err",
        503: "unavailable",
        504: "timeout",
    }.get(exc.status_code, "error")

    # Compress error message
    detail = exc.detail.replace(" ", "_").replace("'", "").lower()[:100]
    compressed = f"err:{error_type}|code:{exc.status_code}|msg:{detail}"

    return JSONResponse(status_code=exc.status_code, content={"result": compressed})


# ============================================================================
# TOOL REGISTRY
# ============================================================================

LANGUAGE_TOOLS = {
    "python": {
        "format": ["ruff", "black"],
        "lint": ["ruff", "pylint", "mypy"],
        "fix": ["ruff"],
        "default_format": "ruff",
        "default_lint": "ruff",
        "extensions": [".py"],
    },
    "javascript": {
        "format": ["prettier"],
        "lint": ["eslint"],
        "fix": ["eslint"],
        "default_format": "prettier",
        "default_lint": "eslint",
        "extensions": [".js", ".jsx", ".mjs"],
    },
    "typescript": {
        "format": ["prettier"],
        "lint": ["eslint", "tsc"],
        "fix": ["eslint"],
        "default_format": "prettier",
        "default_lint": "eslint",
        "extensions": [".ts", ".tsx"],
    },
    "csharp": {
        "format": ["csharpier", "dotnet-format"],
        "lint": ["dotnet-format"],
        "fix": [],
        "default_format": "csharpier",
        "default_lint": "dotnet-format",
        "extensions": [".cs"],
    },
    "rust": {
        "format": ["rustfmt", "cargo-fmt"],
        "lint": ["cargo-clippy"],
        "fix": [],
        "default_format": "rustfmt",
        "default_lint": "cargo-clippy",
        "extensions": [".rs"],
    },
    "go": {
        "format": ["gofmt", "goimports"],
        "lint": ["golangci-lint"],
        "fix": ["golangci-lint"],
        "default_format": "gofmt",
        "default_lint": "golangci-lint",
        "extensions": [".go"],
    },
    "java": {
        "format": ["google-java-format"],
        "lint": ["checkstyle"],
        "fix": [],
        "default_format": "google-java-format",
        "default_lint": "checkstyle",
        "extensions": [".java"],
    },
    "c": {
        "format": ["clang-format"],
        "lint": ["clang-tidy"],
        "fix": [],
        "default_format": "clang-format",
        "default_lint": "clang-tidy",
        "extensions": [".c", ".h"],
    },
    "cpp": {
        "format": ["clang-format"],
        "lint": ["clang-tidy"],
        "fix": [],
        "default_format": "clang-format",
        "default_lint": "clang-tidy",
        "extensions": [".cpp", ".hpp", ".cc", ".cxx", ".hxx"],
    },
    "shell": {
        "format": ["shfmt"],
        "lint": ["shellcheck"],
        "fix": [],
        "default_format": "shfmt",
        "default_lint": "shellcheck",
        "extensions": [".sh", ".bash"],
    },
    "sql": {
        "format": ["sqlfluff"],
        "lint": ["sqlfluff"],
        "fix": ["sqlfluff"],
        "default_format": "sqlfluff",
        "default_lint": "sqlfluff",
        "extensions": [".sql"],
    },
    "php": {
        "format": ["php-cs-fixer"],
        "lint": ["phpstan"],
        "fix": ["php-cs-fixer"],
        "default_format": "php-cs-fixer",
        "default_lint": "phpstan",
        "extensions": [".php"],
    },
    "kotlin": {
        "format": ["ktlint"],
        "lint": ["ktlint"],
        "fix": ["ktlint"],
        "default_format": "ktlint",
        "default_lint": "ktlint",
        "extensions": [".kt", ".kts"],
    },
    "swift": {
        "format": ["swiftformat"],
        "lint": [],
        "fix": [],
        "default_format": "swiftformat",
        "default_lint": "",
        "extensions": [".swift"],
    },
    "ruby": {
        "format": ["rubocop"],
        "lint": ["rubocop"],
        "fix": ["rubocop"],
        "default_format": "rubocop",
        "default_lint": "rubocop",
        "extensions": [".rb"],
    },
    "markdown": {
        "format": ["prettier"],
        "lint": ["markdownlint"],
        "fix": ["markdownlint"],
        "default_format": "prettier",
        "default_lint": "markdownlint",
        "extensions": [".md", ".markdown"],
    },
    "yaml": {
        "format": ["prettier"],
        "lint": ["yamllint"],
        "fix": [],
        "default_format": "prettier",
        "default_lint": "yamllint",
        "extensions": [".yaml", ".yml"],
    },
}

# ============================================================================
# REQUEST/RESPONSE MODELS
# ============================================================================


class FormatRequest(BaseModel):
    language: str = Field(
        ..., description="Programming language (python, javascript, etc)"
    )
    content: str = Field(..., description="Code content to format")
    tool: Optional[str] = Field(
        None, description="Specific tool to use (optional, uses default)"
    )
    check_only: bool = Field(False, description="Only check formatting, don't modify")


class FormatResponse(BaseModel):
    formatted_content: Optional[str] = Field(
        None, description="Formatted code (if not check_only)"
    )
    changed: bool = Field(..., description="Whether formatting would change the code")
    tool_used: str = Field(..., description="Tool that was used")
    diff: Optional[str] = Field(None, description="Diff of changes (if changed)")


class LintIssue(BaseModel):
    line: Optional[int] = None
    column: Optional[int] = None
    severity: Literal["error", "warning", "info"] = "warning"
    code: Optional[str] = None
    message: str
    fixable: bool = False


class LintRequest(BaseModel):
    language: str = Field(..., description="Programming language")
    content: str = Field(..., description="Code content to lint")
    tool: Optional[str] = Field(None, description="Specific linting tool")


class LintResponse(BaseModel):
    issues: List[LintIssue] = []
    error_count: int = 0
    warning_count: int = 0
    info_count: int = 0
    tool_used: str
    clean: bool = Field(..., description="True if no issues found")


class FixRequest(BaseModel):
    language: str = Field(..., description="Programming language")
    content: str = Field(..., description="Code content to fix")
    tool: Optional[str] = Field(None, description="Specific fixing tool")


class FixResponse(BaseModel):
    fixed_content: str
    issues_fixed: List[str] = []
    remaining_issues: List[LintIssue] = []
    tool_used: str
    changes_made: bool


class CheckRequest(BaseModel):
    language: str = Field(..., description="Programming language")
    content: str = Field(..., description="Code content to check")


class CheckResponse(BaseModel):
    format_issues: bool
    lint_issues: List[LintIssue] = []
    compilation_errors: Optional[List[str]] = None
    overall_clean: bool


# ============================================================================
# BATCH REQUEST/RESPONSE MODELS
# ============================================================================


class FileContent(BaseModel):
    path: str = Field(..., description="File path or identifier")
    content: str = Field(..., description="File content")


class BatchFormatRequest(BaseModel):
    language: str = Field(..., description="Programming language")
    files: List[FileContent] = Field(..., description="Files to format")
    tool: Optional[str] = Field(None, description="Specific tool to use")
    check_only: bool = Field(False, description="Only check formatting")


class BatchLintRequest(BaseModel):
    language: str = Field(..., description="Programming language")
    files: List[FileContent] = Field(..., description="Files to lint")
    tool: Optional[str] = Field(None, description="Specific linting tool")


class BatchFixRequest(BaseModel):
    language: str = Field(..., description="Programming language")
    files: List[FileContent] = Field(..., description="Files to fix")
    tool: Optional[str] = Field(None, description="Specific fixing tool")


# ============================================================================
# FILE PATH REQUEST MODELS
# ============================================================================


class FilePathRequest(BaseModel):
    path: str = Field(..., description="File path relative to /workspace")
    language: Optional[str] = Field(
        None, description="Programming language (auto-detected if not provided)"
    )
    tool: Optional[str] = Field(None, description="Specific tool to use (optional)")


class BatchFilePathRequest(BaseModel):
    paths: List[str] = Field(..., description="File paths relative to /workspace")
    language: Optional[str] = Field(
        None, description="Programming language (auto-detected if not provided)"
    )
    tool: Optional[str] = Field(None, description="Specific tool to use (optional)")


# ============================================================================
# DOCKER EXECUTION HELPERS
# ============================================================================


def detect_language(filename: str) -> Optional[str]:
    """Detect language from file extension"""
    ext = Path(filename).suffix.lower()
    for lang, config in LANGUAGE_TOOLS.items():
        if ext in config["extensions"]:
            return lang
    return None


def exec_in_container(
    command: List[str], stdin_data: Optional[str] = None
) -> tuple[int, str, str]:
    """Execute command in code_thumbs container"""
    try:
        container_name = os.environ.get("CODE_THUMBS_CONTAINER", "code_thumbs")
        docker_cmd = ["docker", "exec", "-i", container_name] + command
        result = subprocess.run(
            docker_cmd,
            input=stdin_data.encode() if stdin_data else None,
            capture_output=True,
            timeout=30,
        )
        return result.returncode, result.stdout.decode(), result.stderr.decode()
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Tool execution timed out")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Container execution failed: {e}")


def verify_tool_available(tool: str) -> bool:
    """Check if a tool is available in the container"""
    try:
        returncode, stdout, stderr = exec_in_container(["which", tool])
        if returncode == 0:
            return True

        # Special cases for tools invoked differently
        if tool == "google-java-format":
            returncode, _, _ = exec_in_container(
                ["test", "-f", "/usr/local/java-tools/google-java-format.jar"]
            )
            return returncode == 0
        elif tool == "checkstyle":
            returncode, _, _ = exec_in_container(
                ["test", "-f", "/usr/local/java-tools/checkstyle.jar"]
            )
            return returncode == 0
        elif tool in ["cargo-clippy", "cargo-fmt"]:
            returncode, _, _ = exec_in_container(["cargo", "--version"])
            return returncode == 0

        return False
    except:
        return False


def write_temp_file(content: str, language: str) -> str:
    """Write content to temp file in container workspace"""
    ext = LANGUAGE_TOOLS[language]["extensions"][0]
    filename = f"temp_{hashlib.md5(content.encode()).hexdigest()[:8]}{ext}"
    filepath = f"/workspace/.tmp/{filename}"

    # Ensure .tmp directory exists
    exec_in_container(["mkdir", "-p", "/workspace/.tmp"])

    # Write file using tee (since we can't directly write)
    exec_in_container(["tee", filepath], stdin_data=content)

    return filepath


def read_temp_file(filepath: str) -> str:
    """Read content from temp file in container"""
    returncode, stdout, stderr = exec_in_container(["cat", filepath])
    if returncode != 0:
        raise HTTPException(
            status_code=500, detail=f"Failed to read temp file: {stderr}"
        )
    return stdout


def cleanup_temp_file(filepath: str):
    """Remove temp file from container"""
    exec_in_container(["rm", "-f", filepath])


def read_file_from_container(path: str) -> str:
    """Read file content from container workspace"""
    # Ensure path is relative to /workspace
    if not path.startswith("/workspace/"):
        path = f"/workspace/{path.lstrip('/')}"

    returncode, stdout, stderr = exec_in_container(["cat", path])
    if returncode != 0:
        raise HTTPException(
            status_code=404, detail=f"File not found or not readable: {path}"
        )
    return stdout


def write_file_to_container(path: str, content: str):
    """Write content to file in container workspace"""
    # Ensure path is relative to /workspace
    if not path.startswith("/workspace/"):
        path = f"/workspace/{path.lstrip('/')}"

    # Ensure parent directory exists
    parent_dir = str(Path(path).parent)
    exec_in_container(["mkdir", "-p", parent_dir])

    # Write file using tee
    returncode, stdout, stderr = exec_in_container(["tee", path], stdin_data=content)
    if returncode != 0:
        raise HTTPException(status_code=500, detail=f"Failed to write file: {stderr}")


# ============================================================================
# PARSER HELPERS
# ============================================================================


def parse_ruff_output(output: str) -> List[LintIssue]:
    """Parse ruff check output into structured issues"""
    issues = []
    for line in output.split("\n"):
        if not line.strip():
            continue

        # Format: file.py:line:col: CODE message
        parts = line.split(":", 4)
        if len(parts) >= 5:
            try:
                line_num = int(parts[1])
                col_num = int(parts[2])
                rest = parts[4].strip()
                code = rest.split()[0] if rest else None
                message = " ".join(rest.split()[1:]) if rest else ""

                severity = "error" if "error" in message.lower() else "warning"
                fixable = "[*]" in message or "fixable" in message.lower()

                issues.append(
                    LintIssue(
                        line=line_num,
                        column=col_num,
                        severity=severity,
                        code=code,
                        message=message,
                        fixable=fixable,
                    )
                )
            except (ValueError, IndexError):
                continue

    return issues


def parse_eslint_output(output: str) -> List[LintIssue]:
    """Parse eslint output into structured issues"""
    issues = []
    for line in output.split("\n"):
        if (
            not line.strip()
            or "warning" not in line.lower()
            and "error" not in line.lower()
        ):
            continue

        # Format: line:col severity message code
        parts = line.strip().split()
        if len(parts) >= 3:
            try:
                pos = parts[0].split(":")
                if len(pos) == 2:
                    line_num = int(pos[0])
                    col_num = int(pos[1])
                    severity = "error" if "error" in parts[1].lower() else "warning"
                    message = " ".join(parts[2:])

                    issues.append(
                        LintIssue(
                            line=line_num,
                            column=col_num,
                            severity=severity,
                            message=message,
                            fixable=True,
                        )
                    )
            except (ValueError, IndexError):
                continue

    return issues


# ============================================================================
# ML-EXCLUSIVE COMPRESSION HELPERS
# ============================================================================


def compress_format_response(result: dict, check_only: bool) -> str:
    """Compress format response to ml-exclusive format"""
    if check_only:
        status = "needs_fmt" if result["changed"] else "clean"
        return f"fmt_check:{status}|tool:{result['tool_used']}"
    else:
        changed = "yes" if result["changed"] else "no"
        content = result.get("formatted_content", "")
        return f"tool:{result['tool_used']}|changed:{changed}\n\n{content}"


def compress_lint_response(result: dict) -> str:
    """Compress lint response to ml-exclusive format"""
    if result["clean"]:
        return f"clean|tool:{result['tool_used']}"

    # Compressed issue list: L<line>:<sev>[f]:<code>:<msg>
    issues_compressed = []
    for issue in result["issues"]:
        sev = issue["severity"][0]  # e, w, i
        fixable = "f" if issue.get("fixable") else ""
        line = issue.get("line", "?")
        code = issue.get("code", "")
        msg = issue["message"]
        issues_compressed.append(f"L{line}:{sev}{fixable}:{code}:{msg}")

    header = f"tool:{result['tool_used']}|err:{result['error_count']}|warn:{result['warning_count']}|info:{result['info_count']}"
    issues_text = "\n".join(issues_compressed)
    return f"{header}\n{issues_text}"


def compress_fix_response(result: dict) -> str:
    """Compress fix response to ml-exclusive format"""
    if not result["changes_made"]:
        return f"tool:{result['tool_used']}|fixed:no|reason:no_fixable_issues"

    remaining = len(result["remaining_issues"])
    header = f"tool:{result['tool_used']}|fixed:yes|remaining:{remaining}"

    if result["remaining_issues"]:
        issues = "|".join(
            f"L{i.get('line', '?')}:{i['message'][:40]}"
            for i in result["remaining_issues"][:5]
        )
        return f"{header}\nissues:{issues}\n\n{result['fixed_content']}"
    else:
        return f"{header}\n\n{result['fixed_content']}"


def compress_check_response(result: dict) -> str:
    """Compress check response to ml-exclusive format"""
    if result["overall_clean"]:
        return "clean:fmt+lint"

    fmt_status = "needs_fmt" if result["format_issues"] else "clean"
    lint_issues = result["lint_issues"]
    err_cnt = sum(1 for i in lint_issues if i["severity"] == "error")
    warn_cnt = sum(1 for i in lint_issues if i["severity"] == "warning")
    lint_status = f"err:{err_cnt}+warn:{warn_cnt}" if lint_issues else "clean"

    report = f"fmt:{fmt_status}|lint:{lint_status}"

    if lint_issues:
        top_issues = []
        for issue in lint_issues[:5]:
            sev = issue["severity"][0]
            line = issue.get("line", "?")
            msg = issue["message"][:50]
            top_issues.append(f"L{line}:{sev}:{msg}")
        report += "\n" + "\n".join(top_issues)

    return report


# ============================================================================
# API ENDPOINTS
# ============================================================================


@app.get("/")
async def root():
    """Root endpoint - API overview and quick start"""
    return {
        "service": "Code Quality API",
        "version": "1.0.0",
        "endpoints": {
            "GET /languages": "List supported languages and tools",
            "GET /tools": "Compressed tool specs (ml-exclusive format)",
            "POST /format": "Format code (content)",
            "POST /lint": "Lint code (content)",
            "POST /fix": "Auto-fix issues (content)",
            "POST /check": "Format + lint combined (content)",
            "POST /format/file": "Format file by path",
            "POST /lint/file": "Lint file by path",
            "POST /fix/file": "Fix file by path",
            "POST /check/file": "Check file by path",
            "POST /batch/format/files": "Format multiple files by path",
            "POST /batch/lint/files": "Lint multiple files by path",
            "POST /batch/fix/files": "Fix multiple files by path",
            "GET /tools/openai": "OpenAI function schemas (verbose)",
            "GET /docs": "Interactive API docs (Swagger)",
            "GET /redoc": "API documentation (ReDoc)",
        },
        "example": {
            "curl": 'curl -X POST http://localhost:8072/format -H "Content-Type: application/json" -d \'{"language":"python","content":"def foo(x,y): return x+y"}\'',
            "response": {
                "formatted_content": "def foo(x, y):\n    return x + y\n",
                "changed": True,
                "tool_used": "ruff",
            },
        },
        "mcp_server": "mcp-server/mcp_server_code_thumbs.py",
    }


@app.get("/health")
async def health():
    """Health check - verify container is accessible and show tool availability"""
    try:
        returncode, stdout, stderr = exec_in_container(["echo", "ready"])
        container_name = os.environ.get("CODE_THUMBS_CONTAINER", "code_thumbs")

        if returncode == 0:
            # Check critical tools
            critical_tools = ["ruff", "prettier", "gofmt", "clang-format", "shellcheck"]
            optional_tools = ["swiftformat"]

            tool_status = {}
            for tool in critical_tools + optional_tools:
                tool_status[tool] = (
                    "available" if verify_tool_available(tool) else "missing"
                )

            all_critical_available = all(
                tool_status.get(t) == "available" for t in critical_tools
            )

            return {
                "status": "healthy" if all_critical_available else "degraded",
                "container": container_name,
                "accessible": True,
                "tools": tool_status,
            }
        else:
            return {
                "status": "degraded",
                "container": container_name,
                "accessible": False,
                "error": stderr,
            }
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


@app.get("/languages")
async def list_languages():
    """List supported languages and available tools"""
    return {
        "languages": [
            {
                "name": lang,
                "extensions": config["extensions"],
                "tools": {
                    "format": config["format"],
                    "lint": config["lint"],
                    "fix": config["fix"],
                    "default_format": config["default_format"],
                    "default_lint": config["default_lint"],
                },
            }
            for lang, config in LANGUAGE_TOOLS.items()
        ]
    }


@app.get("/tools")
async def agent_tools():
    """Agent-discoverable tools in compressed ml-exclusive format"""
    return {
        "system": "code_thumbs",
        "version": "1.0.0",
        "compressed": "ml_exclusive_format→max_semantic_density",
        "endpoints": {
            "file_ops": "POST/format/file{path,lang?,tool?}→read+fmt+write|POST/lint/file{path,lang?,tool?}→read+lint+report|POST/fix/file{path,lang?,tool?}→read+fix+write|POST/check/file{path,lang?,tool?}→read+check+report",
            "content_ops": "POST/format{lang,content,tool?,check_only?}→fmt|POST/lint{lang,content,tool?}→issues|POST/fix{lang,content,tool?}→fixed|POST/check{lang,content}→fmt+lint",
            "batch_file": "POST/batch/format/files{paths[],lang?,tool?}→multi_fmt|POST/batch/lint/files{paths[],lang?,tool?}→multi_lint|POST/batch/fix/files{paths[],lang?,tool?}→multi_fix",
            "batch_content": "POST/batch/format{lang,files:[{path,content}],tool?}→multi_fmt|POST/batch/lint{lang,files:[{path,content}],tool?}→multi_lint|POST/batch/fix{lang,files:[{path,content}],tool?}→multi_fix",
            "meta": "GET/health→status|GET/languages→17lang_list|GET/tools→this|GET/tools/openai→verbose_schemas",
        },
        "languages": "py→ruff,black,pylint,mypy|js/ts→prettier,eslint,tsc|go→gofmt,goimports,golangci-lint|rust→rustfmt,clippy|c/cpp→clang-format,clang-tidy|cs→csharpier,dotnet-format|java→google-java-format,checkstyle|kt→ktlint|swift→swiftformat|php→php-cs-fixer,phpstan|rb→rubocop|sh→shfmt,shellcheck|sql→sqlfluff|md→prettier,markdownlint|yaml→prettier,yamllint",
        "response_format": "compressed→tool:ruff|changed:yes\\n\\n{code}|errors→err:type|code:NNN|msg:compressed",
        "workspace": "/workspace→mounted_project_root",
        "lang_detection": "auto_via_extension→.py=python,.ts=typescript,.go=go",
        "philosophy": "agent_first→prefer_file_ops_over_content→atomic_operations→auto_detect_lang→compressed_responses",
        "usage": {
            "recommended": "POST/format/file{path}→1_call_atomic",
            "legacy": "Read→POST/format{content}→parse→Write→5_steps_avoid",
            "batch": "POST/batch/format/files{paths[]}→efficient_multi",
        },
        "health": "GET/health→{status,container,tools:{ruff:available,...}}",
        "tools_available": "17lang|35+tools|format+lint+fix→see_GET/languages_for_detail",
    }


@app.post("/format")
async def format_code(req: FormatRequest):
    """Format code using specified or default formatter"""
    if req.language not in LANGUAGE_TOOLS:
        raise HTTPException(
            status_code=400, detail=f"Unsupported language: {req.language}"
        )

    lang_config = LANGUAGE_TOOLS[req.language]
    tool = req.tool or lang_config["default_format"]

    if tool not in lang_config["format"]:
        raise HTTPException(
            status_code=400, detail=f"Tool {tool} not available for {req.language}"
        )

    # Write content to temp file
    filepath = write_temp_file(req.content, req.language)

    try:
        # Build command based on tool
        if tool == "ruff":
            cmd = [
                "ruff",
                "format" if not req.check_only else "format --check",
                filepath,
            ]
        elif tool == "black":
            cmd = ["black", "--check" if req.check_only else "", filepath]
        elif tool == "prettier":
            cmd = ["prettier", "--check" if req.check_only else "--write", filepath]
        elif tool == "csharpier":
            cmd = ["csharpier", "--check" if req.check_only else "", filepath]
        elif tool == "rustfmt":
            cmd = ["rustfmt", "--check" if req.check_only else "", filepath]
        elif tool == "gofmt":
            if req.check_only:
                cmd = ["gofmt", "-l", filepath]
            else:
                cmd = ["gofmt", "-w", filepath]
        elif tool == "goimports":
            if req.check_only:
                cmd = ["goimports", "-l", filepath]
            else:
                cmd = ["goimports", "-w", filepath]
        elif tool == "google-java-format":
            if req.check_only:
                cmd = [
                    "java",
                    "-jar",
                    "/usr/local/java-tools/google-java-format.jar",
                    "--dry-run",
                    filepath,
                ]
            else:
                cmd = [
                    "java",
                    "-jar",
                    "/usr/local/java-tools/google-java-format.jar",
                    "-i",
                    filepath,
                ]
        elif tool == "clang-format":
            if req.check_only:
                cmd = ["clang-format", "--dry-run", "-Werror", filepath]
            else:
                cmd = ["clang-format", "-i", filepath]
        elif tool == "shfmt":
            if req.check_only:
                cmd = ["shfmt", "-d", filepath]
            else:
                cmd = ["shfmt", "-w", filepath]
        elif tool == "sqlfluff":
            if req.check_only:
                cmd = ["sqlfluff", "format", "--check", filepath]
            else:
                cmd = ["sqlfluff", "format", filepath]
        elif tool == "php-cs-fixer":
            if req.check_only:
                cmd = ["php-cs-fixer", "fix", "--dry-run", filepath]
            else:
                cmd = ["php-cs-fixer", "fix", filepath]
        elif tool == "ktlint":
            if req.check_only:
                cmd = ["ktlint", filepath]
            else:
                cmd = ["ktlint", "-F", filepath]
        elif tool == "swiftformat":
            if req.check_only:
                cmd = ["swiftformat", "--lint", filepath]
            else:
                cmd = ["swiftformat", filepath]
        elif tool == "rubocop":
            if req.check_only:
                cmd = ["rubocop", filepath]
            else:
                cmd = ["rubocop", "-a", filepath]
        elif tool == "markdownlint":
            if req.check_only:
                cmd = ["markdownlint", filepath]
            else:
                cmd = ["markdownlint", "--fix", filepath]
        else:
            raise HTTPException(status_code=400, detail=f"Unknown tool: {tool}")

        # Filter empty strings from cmd
        cmd = [c for c in cmd if c]

        # Verify tool is available before executing
        if not verify_tool_available(tool):
            raise HTTPException(
                status_code=503,
                detail=f"Tool '{tool}' not available in container. It may not be installed or failed during build.",
            )

        returncode, stdout, stderr = exec_in_container(cmd)

        # Check if changed
        changed = returncode != 0 if req.check_only else True

        # Read formatted content (if not check_only)
        formatted_content = None
        if not req.check_only:
            formatted_content = read_temp_file(filepath)
            changed = formatted_content != req.content

        # Build response dict for compression
        result = {
            "formatted_content": formatted_content,
            "changed": changed,
            "tool_used": tool,
            "diff": stderr if changed else None,
        }

        # Return ml-exclusive compressed format
        return {"result": compress_format_response(result, req.check_only)}

    finally:
        cleanup_temp_file(filepath)


@app.post("/lint")
async def lint_code(req: LintRequest):
    """Lint code and return structured issues"""
    if req.language not in LANGUAGE_TOOLS:
        raise HTTPException(
            status_code=400, detail=f"Unsupported language: {req.language}"
        )

    lang_config = LANGUAGE_TOOLS[req.language]
    tool = req.tool or lang_config["default_lint"]

    if tool not in lang_config["lint"]:
        raise HTTPException(
            status_code=400, detail=f"Tool {tool} not available for {req.language}"
        )

    filepath = write_temp_file(req.content, req.language)

    try:
        # Build lint command
        if tool == "ruff":
            cmd = ["ruff", "check", filepath]
        elif tool == "pylint":
            cmd = ["pylint", filepath]
        elif tool == "mypy":
            cmd = ["mypy", filepath]
        elif tool == "eslint":
            cmd = ["eslint", filepath]
        elif tool == "golangci-lint":
            cmd = ["golangci-lint", "run", filepath]
        elif tool == "checkstyle":
            cmd = [
                "java",
                "-jar",
                "/usr/local/java-tools/checkstyle.jar",
                "-c",
                "/usr/local/java-tools/google_checks.xml",
                filepath,
            ]
        elif tool == "clang-tidy":
            cmd = ["clang-tidy", filepath]
        elif tool == "shellcheck":
            cmd = ["shellcheck", filepath]
        elif tool == "sqlfluff":
            cmd = ["sqlfluff", "lint", filepath]
        elif tool == "phpstan":
            cmd = ["phpstan", "analyze", filepath]
        elif tool == "ktlint":
            cmd = ["ktlint", filepath]
        elif tool == "rubocop":
            cmd = ["rubocop", filepath]
        elif tool == "markdownlint":
            cmd = ["markdownlint", filepath]
        elif tool == "yamllint":
            cmd = ["yamllint", filepath]
        elif tool == "dotnet-format":
            cmd = ["dotnet-format", "--verify-no-changes", filepath]
        elif tool == "cargo-clippy":
            cmd = ["cargo", "clippy", "--", "-D", "warnings"]
        elif tool == "tsc":
            cmd = ["tsc", "--noEmit", filepath]
        else:
            raise HTTPException(status_code=400, detail=f"Unknown tool: {tool}")

        # Verify tool is available before executing
        if not verify_tool_available(tool):
            raise HTTPException(
                status_code=503,
                detail=f"Tool '{tool}' not available in container. It may not be installed or failed during build.",
            )

        returncode, stdout, stderr = exec_in_container(cmd)
        output = stdout + stderr

        # Parse output based on tool
        if tool == "ruff" or tool == "pylint" or tool == "mypy":
            issues = parse_ruff_output(output)
        elif tool == "eslint":
            issues = parse_eslint_output(output)
        else:
            issues = []

        error_count = sum(1 for i in issues if i.severity == "error")
        warning_count = sum(1 for i in issues if i.severity == "warning")
        info_count = sum(1 for i in issues if i.severity == "info")

        # Build response dict for compression
        result = {
            "issues": [i.dict() for i in issues],
            "error_count": error_count,
            "warning_count": warning_count,
            "info_count": info_count,
            "tool_used": tool,
            "clean": len(issues) == 0,
        }

        # Return ml-exclusive compressed format
        return {"result": compress_lint_response(result)}

    finally:
        cleanup_temp_file(filepath)


@app.post("/fix")
async def fix_code(req: FixRequest):
    """Auto-fix code issues where possible"""
    if req.language not in LANGUAGE_TOOLS:
        raise HTTPException(
            status_code=400, detail=f"Unsupported language: {req.language}"
        )

    lang_config = LANGUAGE_TOOLS[req.language]

    if not lang_config["fix"]:
        raise HTTPException(
            status_code=400, detail=f"Auto-fix not supported for {req.language}"
        )

    tool = req.tool or lang_config["fix"][0]

    if tool not in lang_config["fix"]:
        raise HTTPException(
            status_code=400, detail=f"Tool {tool} cannot auto-fix for {req.language}"
        )

    filepath = write_temp_file(req.content, req.language)

    try:
        # Build fix command
        if tool == "ruff":
            cmd = ["ruff", "check", "--fix", filepath]
        elif tool == "eslint":
            cmd = ["eslint", "--fix", filepath]
        elif tool == "golangci-lint":
            cmd = ["golangci-lint", "run", "--fix", filepath]
        elif tool == "sqlfluff":
            cmd = ["sqlfluff", "fix", filepath]
        elif tool == "php-cs-fixer":
            cmd = ["php-cs-fixer", "fix", filepath]
        elif tool == "ktlint":
            cmd = ["ktlint", "-F", filepath]
        elif tool == "rubocop":
            cmd = ["rubocop", "-a", filepath]
        elif tool == "markdownlint":
            cmd = ["markdownlint", "--fix", filepath]
        else:
            raise HTTPException(status_code=400, detail=f"Unknown tool: {tool}")

        # Verify tool is available before executing
        if not verify_tool_available(tool):
            raise HTTPException(
                status_code=503,
                detail=f"Tool '{tool}' not available in container. It may not be installed or failed during build.",
            )

        returncode, stdout, stderr = exec_in_container(cmd)

        # Read fixed content
        fixed_content = read_temp_file(filepath)
        changes_made = fixed_content != req.content

        # Parse remaining issues
        remaining_output = stdout + stderr
        remaining_issues = (
            parse_ruff_output(remaining_output)
            if tool == "ruff"
            else parse_eslint_output(remaining_output)
        )

        # Extract what was fixed (rough heuristic)
        issues_fixed = ["Auto-fixed issues"] if changes_made else []

        # Build response dict for compression
        result = {
            "fixed_content": fixed_content,
            "issues_fixed": issues_fixed,
            "remaining_issues": [i.dict() for i in remaining_issues],
            "tool_used": tool,
            "changes_made": changes_made,
        }

        # Return ml-exclusive compressed format
        return {"result": compress_fix_response(result)}

    finally:
        cleanup_temp_file(filepath)


@app.post("/check")
async def check_code(req: CheckRequest):
    """Comprehensive check: format + lint + compile (if applicable)"""
    if req.language not in LANGUAGE_TOOLS:
        raise HTTPException(
            status_code=400, detail=f"Unsupported language: {req.language}"
        )

    # Check formatting
    lang_config = LANGUAGE_TOOLS[req.language]
    tool = lang_config["default_format"]

    filepath = write_temp_file(req.content, req.language)

    try:
        # Format check
        format_changed = False
        if tool == "ruff":
            cmd = ["ruff", "format", "--check", filepath]
        elif tool == "prettier":
            cmd = ["prettier", "--check", filepath]
        elif tool == "gofmt":
            cmd = ["gofmt", "-l", filepath]
        else:
            # Use generic check for other formatters
            cmd = [tool, "--check", filepath] if tool else []

        if cmd and verify_tool_available(tool):
            returncode, _, _ = exec_in_container(cmd)
            format_changed = returncode != 0

        # Lint check
        lint_tool = lang_config["default_lint"]
        lint_issues = []

        if lint_tool and verify_tool_available(lint_tool):
            if lint_tool == "ruff":
                cmd = ["ruff", "check", filepath]
            elif lint_tool == "eslint":
                cmd = ["eslint", filepath]
            elif lint_tool == "golangci-lint":
                cmd = ["golangci-lint", "run", filepath]
            else:
                cmd = [lint_tool, filepath]

            returncode, stdout, stderr = exec_in_container(cmd)
            output = stdout + stderr

            if lint_tool in ["ruff", "pylint", "mypy"]:
                lint_issues = parse_ruff_output(output)
            elif lint_tool == "eslint":
                lint_issues = parse_eslint_output(output)

        overall_clean = not format_changed and len(lint_issues) == 0

        # Build response dict for compression
        result = {
            "format_issues": format_changed,
            "lint_issues": [i.dict() for i in lint_issues],
            "overall_clean": overall_clean,
        }

        # Return ml-exclusive compressed format
        return {"result": compress_check_response(result)}

    finally:
        cleanup_temp_file(filepath)


# ============================================================================
# BATCH ENDPOINTS
# ============================================================================


@app.post("/batch/format")
async def batch_format(req: BatchFormatRequest):
    """Format multiple files in batch"""
    if req.language not in LANGUAGE_TOOLS:
        raise HTTPException(
            status_code=400, detail=f"Unsupported language: {req.language}"
        )

    lang_config = LANGUAGE_TOOLS[req.language]
    tool = req.tool or lang_config["default_format"]

    if tool not in lang_config["format"]:
        raise HTTPException(
            status_code=400, detail=f"Tool {tool} not available for {req.language}"
        )

    if not verify_tool_available(tool):
        raise HTTPException(
            status_code=503, detail=f"Tool '{tool}' not available in container."
        )

    results = []
    for file in req.files:
        try:
            # Reuse single-file format logic
            format_req = FormatRequest(
                language=req.language,
                content=file.content,
                tool=tool,
                check_only=req.check_only,
            )
            result = await format_code(format_req)
            results.append(f"{file.path}|{result['result']}")
        except Exception as e:
            results.append(f"{file.path}|err:{str(e)}")

    return {"result": "\n---\n".join(results)}


@app.post("/batch/lint")
async def batch_lint(req: BatchLintRequest):
    """Lint multiple files in batch"""
    if req.language not in LANGUAGE_TOOLS:
        raise HTTPException(
            status_code=400, detail=f"Unsupported language: {req.language}"
        )

    lang_config = LANGUAGE_TOOLS[req.language]
    tool = req.tool or lang_config["default_lint"]

    if tool not in lang_config["lint"]:
        raise HTTPException(
            status_code=400, detail=f"Tool {tool} not available for {req.language}"
        )

    if not verify_tool_available(tool):
        raise HTTPException(
            status_code=503, detail=f"Tool '{tool}' not available in container."
        )

    results = []
    for file in req.files:
        try:
            lint_req = LintRequest(
                language=req.language, content=file.content, tool=tool
            )
            result = await lint_code(lint_req)
            results.append(f"{file.path}|{result['result']}")
        except Exception as e:
            results.append(f"{file.path}|err:{str(e)}")

    return {"result": "\n---\n".join(results)}


@app.post("/batch/fix")
async def batch_fix(req: BatchFixRequest):
    """Fix multiple files in batch"""
    if req.language not in LANGUAGE_TOOLS:
        raise HTTPException(
            status_code=400, detail=f"Unsupported language: {req.language}"
        )

    lang_config = LANGUAGE_TOOLS[req.language]

    if not lang_config["fix"]:
        raise HTTPException(
            status_code=400, detail=f"Auto-fix not supported for {req.language}"
        )

    tool = req.tool or lang_config["fix"][0]

    if tool not in lang_config["fix"]:
        raise HTTPException(
            status_code=400, detail=f"Tool {tool} cannot auto-fix for {req.language}"
        )

    if not verify_tool_available(tool):
        raise HTTPException(
            status_code=503, detail=f"Tool '{tool}' not available in container."
        )

    results = []
    for file in req.files:
        try:
            fix_req = FixRequest(language=req.language, content=file.content, tool=tool)
            result = await fix_code(fix_req)
            results.append(f"{file.path}|{result['result']}")
        except Exception as e:
            results.append(f"{file.path}|err:{str(e)}")

    return {"result": "\n---\n".join(results)}


# ============================================================================
# FILE PATH ENDPOINTS
# ============================================================================


@app.post("/format/file")
async def format_file(req: FilePathRequest):
    """Format a file by path - reads, formats, and writes back"""
    # Read file content
    content = read_file_from_container(req.path)

    # Auto-detect language if not provided
    language = req.language or detect_language(req.path)
    if not language:
        raise HTTPException(
            status_code=400,
            detail=f"Could not detect language from path: {req.path}. Please specify language explicitly.",
        )

    if language not in LANGUAGE_TOOLS:
        raise HTTPException(status_code=400, detail=f"Unsupported language: {language}")

    lang_config = LANGUAGE_TOOLS[language]
    tool = req.tool or lang_config["default_format"]

    if tool not in lang_config["format"]:
        raise HTTPException(
            status_code=400, detail=f"Tool {tool} not available for {language}"
        )

    if not verify_tool_available(tool):
        raise HTTPException(
            status_code=503, detail=f"Tool '{tool}' not available in container."
        )

    # Write to temp file for processing
    filepath = write_temp_file(content, language)

    try:
        # Build format command
        if tool == "ruff":
            cmd = ["ruff", "format", filepath]
        elif tool == "black":
            cmd = ["black", filepath]
        elif tool == "prettier":
            cmd = ["prettier", "--write", filepath]
        elif tool == "csharpier":
            cmd = ["csharpier", filepath]
        elif tool == "rustfmt":
            cmd = ["rustfmt", filepath]
        elif tool == "gofmt":
            cmd = ["gofmt", "-w", filepath]
        elif tool == "goimports":
            cmd = ["goimports", "-w", filepath]
        elif tool == "google-java-format":
            cmd = [
                "java",
                "-jar",
                "/usr/local/java-tools/google-java-format.jar",
                "-i",
                filepath,
            ]
        elif tool == "clang-format":
            cmd = ["clang-format", "-i", filepath]
        elif tool == "shfmt":
            cmd = ["shfmt", "-w", filepath]
        elif tool == "sqlfluff":
            cmd = ["sqlfluff", "format", filepath]
        elif tool == "php-cs-fixer":
            cmd = ["php-cs-fixer", "fix", filepath]
        elif tool == "ktlint":
            cmd = ["ktlint", "-F", filepath]
        elif tool == "swiftformat":
            cmd = ["swiftformat", filepath]
        elif tool == "rubocop":
            cmd = ["rubocop", "-a", filepath]
        elif tool == "markdownlint":
            cmd = ["markdownlint", "--fix", filepath]
        else:
            raise HTTPException(status_code=400, detail=f"Unknown tool: {tool}")

        cmd = [c for c in cmd if c]
        returncode, stdout, stderr = exec_in_container(cmd)

        # Read formatted content
        formatted_content = read_temp_file(filepath)
        changed = formatted_content != content

        # Write back to original file if changed
        if changed:
            write_file_to_container(req.path, formatted_content)

        result = {
            "formatted_content": formatted_content,
            "changed": changed,
            "tool_used": tool,
            "path": req.path,
        }

        return {"result": compress_format_response(result, check_only=False)}

    finally:
        cleanup_temp_file(filepath)


@app.post("/lint/file")
async def lint_file(req: FilePathRequest):
    """Lint a file by path"""
    # Read file content
    content = read_file_from_container(req.path)

    # Auto-detect language if not provided
    language = req.language or detect_language(req.path)
    if not language:
        raise HTTPException(
            status_code=400,
            detail=f"Could not detect language from path: {req.path}. Please specify language explicitly.",
        )

    if language not in LANGUAGE_TOOLS:
        raise HTTPException(status_code=400, detail=f"Unsupported language: {language}")

    lang_config = LANGUAGE_TOOLS[language]
    tool = req.tool or lang_config["default_lint"]

    if tool not in lang_config["lint"]:
        raise HTTPException(
            status_code=400, detail=f"Tool {tool} not available for {language}"
        )

    if not verify_tool_available(tool):
        raise HTTPException(
            status_code=503, detail=f"Tool '{tool}' not available in container."
        )

    # Use existing lint logic
    lint_req = LintRequest(language=language, content=content, tool=tool)
    result = await lint_code(lint_req)

    # Add path to result
    result_str = result["result"]
    return {"result": f"path:{req.path}|{result_str}"}


@app.post("/fix/file")
async def fix_file(req: FilePathRequest):
    """Fix a file by path - reads, fixes, and writes back"""
    # Read file content
    content = read_file_from_container(req.path)

    # Auto-detect language if not provided
    language = req.language or detect_language(req.path)
    if not language:
        raise HTTPException(
            status_code=400,
            detail=f"Could not detect language from path: {req.path}. Please specify language explicitly.",
        )

    if language not in LANGUAGE_TOOLS:
        raise HTTPException(status_code=400, detail=f"Unsupported language: {language}")

    lang_config = LANGUAGE_TOOLS[language]

    if not lang_config["fix"]:
        raise HTTPException(
            status_code=400, detail=f"Auto-fix not supported for {language}"
        )

    tool = req.tool or lang_config["fix"][0]

    if tool not in lang_config["fix"]:
        raise HTTPException(
            status_code=400, detail=f"Tool {tool} cannot auto-fix for {language}"
        )

    if not verify_tool_available(tool):
        raise HTTPException(
            status_code=503, detail=f"Tool '{tool}' not available in container."
        )

    # Write to temp file for processing
    filepath = write_temp_file(content, language)

    try:
        # Build fix command
        if tool == "ruff":
            cmd = ["ruff", "check", "--fix", filepath]
        elif tool == "eslint":
            cmd = ["eslint", "--fix", filepath]
        elif tool == "golangci-lint":
            cmd = ["golangci-lint", "run", "--fix", filepath]
        elif tool == "sqlfluff":
            cmd = ["sqlfluff", "fix", filepath]
        elif tool == "php-cs-fixer":
            cmd = ["php-cs-fixer", "fix", filepath]
        elif tool == "ktlint":
            cmd = ["ktlint", "-F", filepath]
        elif tool == "rubocop":
            cmd = ["rubocop", "-a", filepath]
        elif tool == "markdownlint":
            cmd = ["markdownlint", "--fix", filepath]
        else:
            raise HTTPException(status_code=400, detail=f"Unknown tool: {tool}")

        returncode, stdout, stderr = exec_in_container(cmd)

        # Read fixed content
        fixed_content = read_temp_file(filepath)
        changes_made = fixed_content != content

        # Write back to original file if changed
        if changes_made:
            write_file_to_container(req.path, fixed_content)

        # Parse remaining issues
        remaining_output = stdout + stderr
        remaining_issues = (
            parse_ruff_output(remaining_output)
            if tool == "ruff"
            else parse_eslint_output(remaining_output)
        )

        issues_fixed = ["Auto-fixed issues"] if changes_made else []

        result = {
            "fixed_content": fixed_content,
            "issues_fixed": issues_fixed,
            "remaining_issues": [i.dict() for i in remaining_issues],
            "tool_used": tool,
            "changes_made": changes_made,
            "path": req.path,
        }

        return {"result": compress_fix_response(result)}

    finally:
        cleanup_temp_file(filepath)


@app.post("/check/file")
async def check_file(req: FilePathRequest):
    """Check a file by path - comprehensive format + lint check"""
    # Read file content
    content = read_file_from_container(req.path)

    # Auto-detect language if not provided
    language = req.language or detect_language(req.path)
    if not language:
        raise HTTPException(
            status_code=400,
            detail=f"Could not detect language from path: {req.path}. Please specify language explicitly.",
        )

    # Use existing check logic
    check_req = CheckRequest(language=language, content=content)
    result = await check_code(check_req)

    # Add path to result
    result_str = result["result"]
    return {"result": f"path:{req.path}|{result_str}"}


# ============================================================================
# BATCH FILE PATH ENDPOINTS
# ============================================================================


@app.post("/batch/format/files")
async def batch_format_files(req: BatchFilePathRequest):
    """Format multiple files by path"""
    results = []
    for path in req.paths:
        try:
            file_req = FilePathRequest(path=path, language=req.language, tool=req.tool)
            result = await format_file(file_req)
            results.append(f"{path}|{result['result']}")
        except Exception as e:
            results.append(f"{path}|err:{str(e)}")

    return {"result": "\n---\n".join(results)}


@app.post("/batch/lint/files")
async def batch_lint_files(req: BatchFilePathRequest):
    """Lint multiple files by path"""
    results = []
    for path in req.paths:
        try:
            file_req = FilePathRequest(path=path, language=req.language, tool=req.tool)
            result = await lint_file(file_req)
            results.append(result["result"])
        except Exception as e:
            results.append(f"path:{path}|err:{str(e)}")

    return {"result": "\n---\n".join(results)}


@app.post("/batch/fix/files")
async def batch_fix_files(req: BatchFilePathRequest):
    """Fix multiple files by path"""
    results = []
    for path in req.paths:
        try:
            file_req = FilePathRequest(path=path, language=req.language, tool=req.tool)
            result = await fix_file(file_req)
            results.append(f"{path}|{result['result']}")
        except Exception as e:
            results.append(f"{path}|err:{str(e)}")

    return {"result": "\n---\n".join(results)}


# ============================================================================
# OPENAI TOOL SCHEMAS
# ============================================================================


@app.get("/tools/openai")
async def openai_tool_schemas():
    """Return OpenAI-compatible tool/function schemas"""
    return {
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "format_code",
                    "description": "Format source code using language-specific formatter (ruff, prettier, etc). Returns formatted code.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "language": {
                                "type": "string",
                                "enum": list(LANGUAGE_TOOLS.keys()),
                                "description": "Programming language",
                            },
                            "content": {
                                "type": "string",
                                "description": "Source code to format",
                            },
                            "tool": {
                                "type": "string",
                                "description": "Specific formatter tool (optional, uses default)",
                            },
                            "check_only": {
                                "type": "boolean",
                                "description": "Only check if formatting needed, don't modify",
                                "default": False,
                            },
                        },
                        "required": ["language", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "lint_code",
                    "description": "Lint source code and return structured list of errors, warnings, and info messages.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "language": {
                                "type": "string",
                                "enum": list(LANGUAGE_TOOLS.keys()),
                                "description": "Programming language",
                            },
                            "content": {
                                "type": "string",
                                "description": "Source code to lint",
                            },
                            "tool": {
                                "type": "string",
                                "description": "Specific linter tool (optional)",
                            },
                        },
                        "required": ["language", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "fix_code",
                    "description": "Automatically fix code issues where possible (auto-fixable lint errors). Returns fixed code.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "language": {
                                "type": "string",
                                "enum": list(LANGUAGE_TOOLS.keys()),
                                "description": "Programming language",
                            },
                            "content": {
                                "type": "string",
                                "description": "Source code to fix",
                            },
                            "tool": {
                                "type": "string",
                                "description": "Specific fixing tool (optional)",
                            },
                        },
                        "required": ["language", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "check_code",
                    "description": "Comprehensive code quality check: formatting + linting + compilation. Returns all issues found.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "language": {
                                "type": "string",
                                "enum": list(LANGUAGE_TOOLS.keys()),
                                "description": "Programming language",
                            },
                            "content": {
                                "type": "string",
                                "description": "Source code to check",
                            },
                        },
                        "required": ["language", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "format_file",
                    "description": "Format a file by path. Reads file from /workspace, formats it, and writes back. Language auto-detected from extension.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "File path relative to /workspace (e.g., 'src/main.py')",
                            },
                            "language": {
                                "type": "string",
                                "enum": list(LANGUAGE_TOOLS.keys()),
                                "description": "Programming language (optional, auto-detected from extension)",
                            },
                            "tool": {
                                "type": "string",
                                "description": "Specific formatter tool (optional)",
                            },
                        },
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "lint_file",
                    "description": "Lint a file by path. Reads file from /workspace and returns lint issues. Language auto-detected.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "File path relative to /workspace",
                            },
                            "language": {
                                "type": "string",
                                "enum": list(LANGUAGE_TOOLS.keys()),
                                "description": "Programming language (optional, auto-detected)",
                            },
                            "tool": {
                                "type": "string",
                                "description": "Specific linter tool (optional)",
                            },
                        },
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "fix_file",
                    "description": "Fix a file by path. Reads file, auto-fixes issues, and writes back. Language auto-detected.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": "File path relative to /workspace",
                            },
                            "language": {
                                "type": "string",
                                "enum": list(LANGUAGE_TOOLS.keys()),
                                "description": "Programming language (optional, auto-detected)",
                            },
                            "tool": {
                                "type": "string",
                                "description": "Specific fixing tool (optional)",
                            },
                        },
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "batch_format_files",
                    "description": "Format multiple files by path in one request. Each file is formatted and written back.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "paths": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Array of file paths relative to /workspace",
                            },
                            "language": {
                                "type": "string",
                                "enum": list(LANGUAGE_TOOLS.keys()),
                                "description": "Programming language (optional, auto-detected per file)",
                            },
                            "tool": {
                                "type": "string",
                                "description": "Specific formatter tool (optional)",
                            },
                        },
                        "required": ["paths"],
                    },
                },
            },
        ]
    }


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8072))
    uvicorn.run(app, host="0.0.0.0", port=port)
