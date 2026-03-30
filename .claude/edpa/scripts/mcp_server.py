#!/usr/bin/env python3
"""
EDPA MCP Server — exposes .edpa/ project data to AI assistants.

Read-only server that provides structured access to EDPA configuration,
iterations, people, and backlog items. Works with any MCP client
(Claude Code, Cursor, Codex CLI, etc.).

Usage:
    python3 .claude/edpa/scripts/mcp_server.py
"""

import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required. Install with: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Resource, TextContent, Tool

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def find_edpa_root() -> Path | None:
    """Walk up from CWD to find .edpa/ directory."""
    p = Path.cwd()
    while p != p.parent:
        if (p / ".edpa").is_dir():
            return p / ".edpa"
        p = p.parent
    return None


def load_yaml(path: Path) -> dict | None:
    """Load a YAML file, return None on failure."""
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return None

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

server = Server("edpa")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="edpa_status",
            description="Get EDPA project status: current PI, active iteration, team size, total capacity.",
            inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
        ),
        Tool(
            name="edpa_iterations",
            description="List all iterations with id, status, dates, and type.",
            inputSchema={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": "Filter by status: closed, active, planned. Omit for all.",
                        "enum": ["closed", "active", "planned"],
                    }
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name="edpa_people",
            description="List team members with id, name, role, FTE, capacity, and team.",
            inputSchema={
                "type": "object",
                "properties": {
                    "team": {
                        "type": "string",
                        "description": "Filter by team ID. Omit for all.",
                    }
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name="edpa_backlog",
            description="List backlog items from .edpa/backlog/. Filterable by iteration, type, or status.",
            inputSchema={
                "type": "object",
                "properties": {
                    "iteration": {
                        "type": "string",
                        "description": "Filter by iteration ID (e.g., PI-2026-1.3).",
                    },
                    "type": {
                        "type": "string",
                        "description": "Filter by item type.",
                        "enum": ["Story", "Feature", "Epic", "Initiative"],
                    },
                    "status": {
                        "type": "string",
                        "description": "Filter by status (e.g., Done, In Progress, Planned).",
                    },
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name="edpa_item",
            description="Get detail for a single backlog item by ID (e.g., S-200, F-100).",
            inputSchema={
                "type": "object",
                "properties": {
                    "item_id": {
                        "type": "string",
                        "description": "Item ID (e.g., S-200, F-100, E-10).",
                    }
                },
                "required": ["item_id"],
                "additionalProperties": False,
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    edpa_root = find_edpa_root()
    if edpa_root is None:
        return [TextContent(type="text", text="ERROR: .edpa/ directory not found. Run `/edpa setup` first.")]

    if name == "edpa_status":
        return _handle_status(edpa_root)
    elif name == "edpa_iterations":
        return _handle_iterations(edpa_root, arguments.get("status"))
    elif name == "edpa_people":
        return _handle_people(edpa_root, arguments.get("team"))
    elif name == "edpa_backlog":
        return _handle_backlog(edpa_root, arguments.get("iteration"), arguments.get("type"), arguments.get("status"))
    elif name == "edpa_item":
        return _handle_item(edpa_root, arguments["item_id"])
    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def _handle_status(edpa_root: Path) -> list[TextContent]:
    config = load_yaml(edpa_root / "config" / "edpa.yaml") or {}
    people_cfg = load_yaml(edpa_root / "config" / "people.yaml") or {}

    pi = config.get("pi", {})
    iterations = pi.get("iterations", [])
    active = next((i for i in iterations if i.get("status") == "active"), None)
    closed_count = sum(1 for i in iterations if i.get("status") == "closed")

    people = people_cfg.get("people", [])
    total_capacity = sum(p.get("capacity_per_iteration") or p.get("capacity", 0) for p in people)

    project = people_cfg.get("project", {})

    result = {
        "project": project.get("name", "unknown"),
        "current_pi": pi.get("current", "unknown"),
        "iterations_total": len(iterations),
        "iterations_closed": closed_count,
        "active_iteration": active["id"] if active else None,
        "active_iteration_dates": active.get("dates") if active else None,
        "team_size": len(people),
        "total_capacity_per_iteration": total_capacity,
        "cadence": f"{pi.get('iteration_weeks', 2)}-week iterations, {pi.get('pi_weeks', 10)}-week PI",
    }
    return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]


def _handle_iterations(edpa_root: Path, status_filter: str | None) -> list[TextContent]:
    config = load_yaml(edpa_root / "config" / "edpa.yaml") or {}
    iterations = config.get("pi", {}).get("iterations", [])

    if status_filter:
        iterations = [i for i in iterations if i.get("status") == status_filter]

    result = []
    for it in iterations:
        entry = {
            "id": it.get("id"),
            "status": it.get("status"),
            "dates": it.get("dates"),
        }
        if it.get("type"):
            entry["type"] = it["type"]
        # Check if results exist
        results_path = edpa_root / "reports" / f"iteration-{it.get('id')}" / "edpa_results.json"
        entry["has_results"] = results_path.exists()
        result.append(entry)

    return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]


def _handle_people(edpa_root: Path, team_filter: str | None) -> list[TextContent]:
    people_cfg = load_yaml(edpa_root / "config" / "people.yaml") or {}
    people = people_cfg.get("people", [])

    if team_filter:
        people = [p for p in people if p.get("team") == team_filter]

    result = []
    for p in people:
        result.append({
            "id": p.get("id"),
            "name": p.get("name", p.get("id")),
            "role": p.get("role"),
            "team": p.get("team"),
            "fte": p.get("fte"),
            "capacity": p.get("capacity_per_iteration") or p.get("capacity", 0),
        })

    return [TextContent(type="text", text=json.dumps(result, indent=2, ensure_ascii=False))]


def _handle_backlog(edpa_root: Path, iteration: str | None, type_filter: str | None, status_filter: str | None) -> list[TextContent]:
    backlog_dir = edpa_root / "backlog"
    if not backlog_dir.exists():
        return [TextContent(type="text", text="[]")]

    type_dirs = {
        "stories": "Story",
        "features": "Feature",
        "epics": "Epic",
        "initiatives": "Initiative",
    }

    items = []
    for dir_name, level in type_dirs.items():
        type_dir = backlog_dir / dir_name
        if not type_dir.exists():
            continue
        if type_filter and level != type_filter:
            continue

        for yaml_file in sorted(type_dir.glob("*.yaml")):
            data = load_yaml(yaml_file)
            if not data or not isinstance(data, dict):
                continue

            if iteration and data.get("iteration") != iteration:
                continue
            if status_filter and (data.get("status", "").lower() != status_filter.lower()):
                continue

            items.append({
                "id": data.get("id", yaml_file.stem),
                "type": data.get("type", level),
                "title": data.get("title", ""),
                "status": data.get("status", ""),
                "js": data.get("js") or data.get("job_size", 0),
                "iteration": data.get("iteration", ""),
                "assignee": data.get("assignee") or data.get("owner", ""),
                "parent": data.get("parent", ""),
            })

    return [TextContent(type="text", text=json.dumps(items, indent=2, ensure_ascii=False))]


def _handle_item(edpa_root: Path, item_id: str) -> list[TextContent]:
    backlog_dir = edpa_root / "backlog"
    if not backlog_dir.exists():
        return [TextContent(type="text", text=f"ERROR: Backlog not found.")]

    # Determine type directory from prefix
    prefix_map = {"S": "stories", "F": "features", "E": "epics", "I": "initiatives",
                  "T": "stories", "D": "defects"}
    prefix = item_id.split("-")[0] if "-" in item_id else ""
    dir_name = prefix_map.get(prefix)

    search_dirs = [backlog_dir / dir_name] if dir_name else list(backlog_dir.iterdir())

    for d in search_dirs:
        if not d.is_dir():
            continue
        candidate = d / f"{item_id}.yaml"
        if candidate.exists():
            data = load_yaml(candidate)
            if data:
                return [TextContent(type="text", text=json.dumps(data, indent=2, ensure_ascii=False, default=str))]

    return [TextContent(type="text", text=f"ERROR: Item {item_id} not found in backlog.")]


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------

@server.list_resources()
async def list_resources() -> list[Resource]:
    edpa_root = find_edpa_root()
    resources = []
    if edpa_root:
        if (edpa_root / "config" / "edpa.yaml").exists():
            resources.append(Resource(uri="edpa://config", name="EDPA Configuration", description="Master config: PI, iterations, cadence, sync settings", mimeType="application/x-yaml"))
        if (edpa_root / "config" / "people.yaml").exists():
            resources.append(Resource(uri="edpa://people", name="EDPA Team Registry", description="Team members, roles, FTE, capacity", mimeType="application/x-yaml"))
        # Add iteration resources for each iteration
        for it_dir in sorted((edpa_root / "reports").glob("iteration-*")) if (edpa_root / "reports").exists() else []:
            results_file = it_dir / "edpa_results.json"
            if results_file.exists():
                it_id = it_dir.name.replace("iteration-", "")
                resources.append(Resource(uri=f"edpa://results/{it_id}", name=f"EDPA Results: {it_id}", description=f"Engine results for iteration {it_id}", mimeType="application/json"))
    return resources


@server.read_resource()
async def read_resource(uri: str) -> str:
    edpa_root = find_edpa_root()
    if not edpa_root:
        return "ERROR: .edpa/ directory not found."

    if uri == "edpa://config":
        path = edpa_root / "config" / "edpa.yaml"
    elif uri == "edpa://people":
        path = edpa_root / "config" / "people.yaml"
    elif uri.startswith("edpa://results/"):
        it_id = uri.replace("edpa://results/", "")
        path = edpa_root / "reports" / f"iteration-{it_id}" / "edpa_results.json"
    else:
        return f"ERROR: Unknown resource URI: {uri}"

    if not path.exists():
        return f"ERROR: File not found: {path}"

    return path.read_text()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
