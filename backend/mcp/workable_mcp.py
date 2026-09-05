"""Model Context Protocol (MCP) tool adapter for Workable ATS.

Allows LLM agents to query candidates, jobs, past interview notes, and post
feedback comments directly into Workable.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from backend.config import Settings
from backend.integrations.workable import (
    WorkableClient,
    WorkableError,
    format_candidate_briefing,
    format_candidate_cv,
    format_job_description,
    parse_workable_candidate_input,
)


def get_default_client(subdomain: str | None = None, api_key: str | None = None) -> WorkableClient:
    """Create a WorkableClient using settings or explicit arguments."""
    sub = subdomain or os.getenv("WORKABLE_SUBDOMAIN")
    key = api_key or os.getenv("WORKABLE_API_KEY")
    if not (sub and key):
        try:
            settings = Settings()
            sub = sub or settings.workable_subdomain
            key = key or settings.workable_api_key
        except Exception:
            pass
    return WorkableClient(subdomain=sub, api_key=key)


WORKABLE_TOOLS = [
    {
        "name": "workable_get_candidate",
        "description": "Get a candidate profile from Workable, including experience, education, skills, and resume metadata.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "candidate_id": {
                    "type": "string",
                    "description": "Workable candidate ID or URL",
                },
                "subdomain": {
                    "type": "string",
                    "description": "Optional Workable company subdomain override",
                },
            },
            "required": ["candidate_id"],
        },
    },
    {
        "name": "workable_get_job",
        "description": "Get job details from Workable by job shortcode (title, description, requirements, benefits).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "shortcode": {
                    "type": "string",
                    "description": "Workable job shortcode",
                },
                "subdomain": {
                    "type": "string",
                    "description": "Optional Workable company subdomain override",
                },
            },
            "required": ["shortcode"],
        },
    },
    {
        "name": "workable_get_candidate_notes",
        "description": "Get previous interviewer notes, ratings, evaluations, and comments from a candidate's Workable timeline.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "candidate_id": {
                    "type": "string",
                    "description": "Workable candidate ID or URL",
                },
                "subdomain": {
                    "type": "string",
                    "description": "Optional Workable company subdomain override",
                },
            },
            "required": ["candidate_id"],
        },
    },
    {
        "name": "workable_post_feedback",
        "description": "Post an interview feedback report or note to the candidate's Workable timeline.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "candidate_id": {
                    "type": "string",
                    "description": "Workable candidate ID",
                },
                "feedback_text": {
                    "type": "string",
                    "description": "Report or comment text to post",
                },
                "policy": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional visibility policy roles, e.g. ['admin', 'recruiter']",
                },
                "subdomain": {
                    "type": "string",
                    "description": "Optional Workable company subdomain override",
                },
            },
            "required": ["candidate_id", "feedback_text"],
        },
    },
    {
        "name": "workable_import_dossier",
        "description": "Import a complete interview dossier (CV, Job Description, past recruiter notes) from Workable in one step.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url_or_id": {
                    "type": "string",
                    "description": "Workable candidate URL or ID",
                },
                "subdomain": {
                    "type": "string",
                    "description": "Optional Workable company subdomain override",
                },
            },
            "required": ["url_or_id"],
        },
    },
]


async def execute_tool(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Execute a Workable tool call."""
    subdomain = arguments.get("subdomain")
    client = get_default_client(subdomain=subdomain)

    if tool_name == "workable_get_candidate":
        candidate_id, _ = parse_workable_candidate_input(arguments["candidate_id"])
        cand = await client.get_candidate(candidate_id)
        formatted_cv = format_candidate_cv(cand)
        return {
            "candidate_id": candidate_id,
            "name": cand.get("name"),
            "formatted_cv": formatted_cv,
            "raw": cand,
        }

    elif tool_name == "workable_get_job":
        shortcode = arguments["shortcode"].strip()
        job = await client.get_job(shortcode)
        formatted_jd = format_job_description(job)
        return {
            "shortcode": shortcode,
            "title": job.get("title"),
            "formatted_jd": formatted_jd,
            "raw": job,
        }

    elif tool_name == "workable_get_candidate_notes":
        candidate_id, _ = parse_workable_candidate_input(arguments["candidate_id"])
        cand = await client.get_candidate(candidate_id)
        activities = await client.get_candidate_activities(candidate_id)
        briefing = format_candidate_briefing(cand, activities)
        return {
            "candidate_id": candidate_id,
            "formatted_briefing": briefing,
            "activities_count": len(activities),
            "activities": activities,
        }

    elif tool_name == "workable_post_feedback":
        candidate_id, _ = parse_workable_candidate_input(arguments["candidate_id"])
        feedback = arguments["feedback_text"]
        policy = arguments.get("policy")
        res = await client.post_candidate_comment(candidate_id, feedback, policy=policy)
        return {"ok": True, "candidate_id": candidate_id, "result": res}

    elif tool_name == "workable_import_dossier":
        url_or_id = arguments["url_or_id"]
        dossier = await client.import_candidate_dossier(url_or_id)
        return dossier.model_dump()

    else:
        raise ValueError(f"Unknown tool: {tool_name}")


async def handle_stdio_rpc() -> None:
    """Run MCP server over standard input/output (JSON-RPC 2.0)."""
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: protocol, sys.stdin)

    while True:
        line = await reader.readline()
        if not line:
            break
        try:
            req = json.loads(line.decode().strip())
        except Exception:
            continue

        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})

        if method == "initialize":
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "transcriptor-workable", "version": "1.0.0"},
                },
            }
        elif method == "tools/list":
            resp = {"jsonrpc": "2.0", "id": req_id, "result": {"tools": WORKABLE_TOOLS}}
        elif method == "tools/call":
            tool_name = params.get("name")
            tool_args = params.get("arguments", {})
            try:
                result = await execute_tool(tool_name, tool_args)
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}]
                    },
                }
            except Exception as exc:
                resp = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32603, "message": str(exc)},
                }
        else:
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method {method} not found"},
            }

        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    try:
        asyncio.run(handle_stdio_rpc())
    except (KeyboardInterrupt, BrokenPipeError):
        pass
