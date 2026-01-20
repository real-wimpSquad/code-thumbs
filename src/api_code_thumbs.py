#!/usr/bin/env python3
"""
============================================================================
Copyright 2026 Jon Sherlin (real-wimpSquad)

SPDX-License-Identifier: MIT
See LICENSE file for full license text.
============================================================================

Code Quality API - OpenAI/MCP-compatible endpoints for code_thumbs container
Exposes formatting, linting, and auto-fixing as HTTP API
"""

from fastapi import FastAPI, HTTPException
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
            "POST /format": "Format code",
            "POST /lint": "Lint code",
            "POST /fix": "Auto-fix issues",
            "POST /check": "Format + lint combined",
            "GET /tools/openai": "OpenAI function schemas",
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
    """Health check - verify container is accessible"""
    try:
        returncode, stdout, stderr = exec_in_container(["echo", "ready"])
        if returncode == 0:
            container_name = os.environ.get("CODE_THUMBS_CONTAINER", "code_thumbs")
            return {
                "status": "healthy",
                "container": container_name,
                "accessible": True,
            }
        else:
            container_name = os.environ.get("CODE_THUMBS_CONTAINER", "code_thumbs")
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


@app.post("/format", response_model=FormatResponse)
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
        else:
            raise HTTPException(status_code=400, detail=f"Unknown tool: {tool}")

        # Filter empty strings from cmd
        cmd = [c for c in cmd if c]

        returncode, stdout, stderr = exec_in_container(cmd)

        # Check if changed
        changed = returncode != 0 if req.check_only else True

        # Read formatted content (if not check_only)
        formatted_content = None
        if not req.check_only:
            formatted_content = read_temp_file(filepath)
            changed = formatted_content != req.content

        return FormatResponse(
            formatted_content=formatted_content,
            changed=changed,
            tool_used=tool,
            diff=stderr if changed else None,
        )

    finally:
        cleanup_temp_file(filepath)


@app.post("/lint", response_model=LintResponse)
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
        else:
            raise HTTPException(status_code=400, detail=f"Unknown tool: {tool}")

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

        return LintResponse(
            issues=issues,
            error_count=error_count,
            warning_count=warning_count,
            info_count=info_count,
            tool_used=tool,
            clean=len(issues) == 0,
        )

    finally:
        cleanup_temp_file(filepath)


@app.post("/fix", response_model=FixResponse)
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
        else:
            raise HTTPException(status_code=400, detail=f"Unknown tool: {tool}")

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

        return FixResponse(
            fixed_content=fixed_content,
            issues_fixed=issues_fixed,
            remaining_issues=remaining_issues,
            tool_used=tool,
            changes_made=changes_made,
        )

    finally:
        cleanup_temp_file(filepath)


@app.post("/check", response_model=CheckResponse)
async def check_code(req: CheckRequest):
    """Comprehensive check: format + lint + compile (if applicable)"""
    # Check formatting
    format_req = FormatRequest(
        language=req.language, content=req.content, check_only=True
    )
    format_resp = await format_code(format_req)

    # Run linting
    lint_req = LintRequest(language=req.language, content=req.content)
    lint_resp = await lint_code(lint_req)

    # TODO: Add compilation checks for compiled languages (tsc, cargo check, etc)

    overall_clean = not format_resp.changed and lint_resp.clean

    return CheckResponse(
        format_issues=format_resp.changed,
        lint_issues=lint_resp.issues,
        overall_clean=overall_clean,
    )


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
        ]
    }


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8072))
    uvicorn.run(app, host="0.0.0.0", port=port)
