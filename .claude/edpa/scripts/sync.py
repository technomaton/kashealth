#!/usr/bin/env python3
"""
EDPA Sync CLI -- Bidirectional sync between GitHub Projects and .edpa/ item files.

Usage:
    python .claude/edpa/scripts/sync.py pull          # GitHub Projects -> .edpa/backlog/ item files
    python .claude/edpa/scripts/sync.py push          # .edpa/backlog/ item files -> GitHub Projects
    python .claude/edpa/scripts/sync.py diff           # Show what would change (dry-run)
    python .claude/edpa/scripts/sync.py log            # Show changelog
    python .claude/edpa/scripts/sync.py conflicts      # Show unresolved conflicts
    python .claude/edpa/scripts/sync.py status         # Show sync status

Flags:
    --mock       Simulate GitHub Project data from existing backlog (for testing)
    --commit     Auto-commit changes after pull (used by CI)
    --verbose    Show detailed output
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except ImportError:
    print("Error: PyYAML is required. Install with: pip install pyyaml")
    sys.exit(1)


# -- ANSI Colors (EDPA palette) -----------------------------------------------

class C:
    """ANSI color codes matching EDPA design palette."""
    RESET    = "\033[0m"
    BOLD     = "\033[1m"
    DIM      = "\033[2m"
    INIT     = "\033[35m"
    EPIC     = "\033[38;5;93m"
    FEAT     = "\033[36m"
    STORY    = "\033[32m"
    DONE     = "\033[32m"
    ACTIVE   = "\033[33m"
    PROGRESS = "\033[34m"
    PLANNED  = "\033[37m"
    WARN     = "\033[33m"
    ERR      = "\033[31m"
    OK       = "\033[32m"
    HEADER   = "\033[38;5;147m"
    MUTED    = "\033[38;5;245m"
    SYNC     = "\033[38;5;81m"   # Cyan-blue for sync operations
    DIFF_ADD = "\033[32m"
    DIFF_DEL = "\033[31m"
    DIFF_MOD = "\033[33m"


def color(text, code):
    return f"{code}{text}{C.RESET}"


def bold(text):
    return f"{C.BOLD}{text}{C.RESET}"


# -- Box-drawing characters ---------------------------------------------------

PIPE  = "\u2502"
TEE   = "\u251c"
ELBOW = "\u2514"
DASH  = "\u2500"
DOT   = "\u2022"
ARROW = "\u2192"
CHECK = "\u2713"
CROSS = "\u2717"
SYNC_ICON = "\u21c4"  # bidirectional arrow


# -- Path Resolution ----------------------------------------------------------

def find_repo_root():
    """Walk up from CWD to find the repo root (contains .edpa/)."""
    p = Path.cwd()
    while p != p.parent:
        if (p / ".edpa").is_dir():
            return p
        p = p.parent
    fallback = Path("/Users/jurby/projects/edpa")
    if (fallback / ".edpa").is_dir():
        return fallback
    return None


# -- Data Loading / Writing ----------------------------------------------------

def load_yaml(path):
    """Load a YAML file."""
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_yaml(path, data):
    """Write a YAML file preserving readability."""
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True,
                  sort_keys=False, width=120)


def load_json(path):
    """Load a JSON file."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    """Write a JSON file."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def append_jsonl(path, entry):
    """Append a single JSONL entry."""
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load_jsonl(path):
    """Read all JSONL entries."""
    entries = []
    if not path.exists():
        return entries
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return entries


# -- Config Loading ------------------------------------------------------------

DEFAULT_SYNC_CONFIG = {
    "github_org": "YOUR_ORG",
    "github_project_number": 1,
    "sync_interval": "15m",
    "auto_commit": True,
    "fields_mapping": {
        "js": "Job Size",
        "bv": "Business Value",
        "tc": "Time Criticality",
        "rr": "Risk Reduction",
        "wsjf": "WSJF Score",
        "iteration": "Iteration",
        "team": "Team",
    },
}


def load_sync_config(root):
    """Load sync configuration from .edpa/config/edpa.yaml."""
    config_path = root / ".edpa" / "config" / "edpa.yaml"
    if not config_path.exists():
        return DEFAULT_SYNC_CONFIG
    config = load_yaml(config_path)
    return config.get("sync", DEFAULT_SYNC_CONFIG)


# -- Backlog Helpers -----------------------------------------------------------

TYPE_DIRS = ["initiatives", "epics", "features", "stories"]


def collect_items_flat(root):
    """Collect all items from per-file .edpa/backlog/ directories into a flat dict keyed by ID.

    Reads individual YAML files from .edpa/backlog/initiatives/, .edpa/backlog/epics/,
    .edpa/backlog/features/, and .edpa/backlog/stories/.
    """
    items = {}
    backlog = root / ".edpa" / "backlog"
    for type_dir in TYPE_DIRS:
        dir_path = backlog / type_dir
        if not dir_path.exists():
            continue
        for f in sorted(dir_path.glob("*.yaml")):
            item = load_yaml(f)
            if not item:
                continue
            item_id = item.get("id")
            if not item_id:
                continue
            entry = {
                "level": item.get("type", ""),
                "title": item.get("title", ""),
                "status": item.get("status", ""),
                "parent": item.get("parent") or "",
                "owner": item.get("owner", ""),
                "assignee": item.get("assignee", ""),
                "iteration": item.get("iteration", ""),
                "js": item.get("js", 0),
                "bv": item.get("bv", 0),
                "tc": item.get("tc", 0),
                "rr": item.get("rr", 0),
                "wsjf": item.get("wsjf", 0),
                "type": item.get("epic_type", ""),
            }
            items[item_id] = entry
    return items


def compute_backlog_checksum(root):
    """Compute a deterministic checksum for the backlog content."""
    items = collect_items_flat(root)
    serialized = json.dumps(items, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode()).hexdigest()[:12]


# -- GitHub CLI Interface ------------------------------------------------------

SYNC_FIELDS = ["js", "bv", "tc", "rr", "wsjf", "iteration", "status"]


def gh_fetch_project_items(sync_config):
    """Fetch project items via `gh project item-list`."""
    org = sync_config.get("github_org", "YOUR_ORG")
    project_num = sync_config.get("github_project_number", 1)

    cmd = [
        "gh", "project", "item-list", str(project_num),
        "--owner", org,
        "--format", "json",
        "--limit", "500",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            print(color(f"  Error: gh CLI failed: {result.stderr.strip()}", C.ERR))
            return None
        return json.loads(result.stdout)
    except FileNotFoundError:
        print(color("  Error: `gh` CLI not found. Install from https://cli.github.com/", C.ERR))
        return None
    except subprocess.TimeoutExpired:
        print(color("  Error: gh CLI timed out after 30s", C.ERR))
        return None
    except json.JSONDecodeError:
        print(color("  Error: Could not parse gh output as JSON", C.ERR))
        return None


def gh_update_project_item(sync_config, item_id, project_id, field_id, value):
    """Update a single field on a project item."""
    cmd = [
        "gh", "project", "item-edit", item_id,
        "--project-id", project_id,
        "--field-id", field_id,
        "--text", str(value),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def parse_gh_item_type(item):
    """Determine EDPA item type from GitHub Issue Type, labels, or title prefix."""
    # 1. Check native Issue Type (preferred)
    issue_type = item.get("issueType", {})
    if isinstance(issue_type, dict) and issue_type.get("name"):
        return issue_type["name"]

    # 2. Fallback to labels (backward compat)
    labels = []
    if isinstance(item.get("labels"), list):
        labels = [l.lower() if isinstance(l, str) else l.get("name", "").lower()
                  for l in item["labels"]]
    elif isinstance(item.get("labels"), str):
        labels = [item["labels"].lower()]

    for label in labels:
        if "initiative" in label:
            return "Initiative"
        if "epic" in label:
            return "Epic"
        if "feature" in label:
            return "Feature"
        if "story" in label:
            return "Story"

    # 3. Fallback to title prefix (I-, E-, F-, S-)
    title = item.get("title", "")
    if title.startswith("I-") or "initiative" in title.lower():
        return "Initiative"
    if title.startswith("E-") or "epic" in title.lower():
        return "Epic"
    if title.startswith("F-") or "feature" in title.lower():
        return "Feature"
    return "Story"


def map_gh_items_to_edpa(gh_data, fields_mapping):
    """Map GitHub Project items to EDPA flat item dict."""
    items = {}
    if not gh_data or "items" not in gh_data:
        return items

    # Build reverse lookup: "job size" -> "js", "business value" -> "bv", ...
    reverse_fields = {v.lower(): k for k, v in fields_mapping.items()}
    numeric_fields = {"js", "bv", "tc", "rr", "wsjf"}

    for gh_item in gh_data["items"]:
        title = gh_item.get("title", "")

        # Extract EDPA ID from title: "S-200: OMOP parser impl." or "S-200 title"
        edpa_id = None
        content = gh_item.get("content", {}) or {}

        for prefix in ("I-", "E-", "F-", "S-"):
            if title.startswith(prefix):
                parts = title.split(" ", 1)
                candidate = parts[0].rstrip(":")
                if len(candidate) > 2 and candidate[2:].isdigit():
                    edpa_id = candidate
                    title = parts[1].lstrip(": ") if len(parts) > 1 else title
                    break

        if not edpa_id:
            continue

        item_type = parse_gh_item_type(gh_item)

        entry = {
            "level": item_type,
            "title": title,
            "status": gh_item.get("status", ""),
            "_gh_item_id": gh_item.get("id", ""),
        }

        # Map fields by checking both mapped names and direct EDPA key names
        for gh_field_name, value in gh_item.items():
            if gh_field_name in ("id", "title", "status", "labels", "content", "fieldValues"):
                continue

            key_lower = gh_field_name.lower()
            edpa_key = None

            # Check reverse mapping first (e.g., "job size" -> "js")
            if key_lower in reverse_fields:
                edpa_key = reverse_fields[key_lower]
            # Check if it's already an EDPA key name (e.g., "js", "bv", ...)
            elif key_lower in ("js", "bv", "tc", "rr", "wsjf", "iteration",
                               "assignee", "owner", "team"):
                edpa_key = key_lower

            if edpa_key and value is not None and value != "":
                if edpa_key in numeric_fields:
                    try:
                        entry[edpa_key] = float(value)
                    except (ValueError, TypeError):
                        entry[edpa_key] = value
                else:
                    entry[edpa_key] = value

        # Also check nested fieldValues (GraphQL format)
        field_values = gh_item.get("fieldValues", {})
        if isinstance(field_values, dict):
            for field_obj in field_values.get("nodes", []):
                field_name = (field_obj.get("field", {}).get("name", "") or "").lower()
                edpa_key = reverse_fields.get(field_name)
                if not edpa_key:
                    continue
                val = field_obj.get("text") or field_obj.get("name") or field_obj.get("number")
                if val is not None:
                    if edpa_key in numeric_fields:
                        try:
                            entry[edpa_key] = float(val)
                        except (ValueError, TypeError):
                            entry[edpa_key] = val
                    else:
                        entry[edpa_key] = val

        items[edpa_id] = entry

    return items


# -- Mock Data Generator -------------------------------------------------------

def generate_mock_gh_data(root, fields_mapping=None):
    """Generate fake GitHub Project data from existing .edpa/ item files for testing.

    Produces data in the same shape that `gh project item-list --format json`
    returns, using mapped field names so `map_gh_items_to_edpa` can round-trip.
    """
    if fields_mapping is None:
        fields_mapping = DEFAULT_SYNC_CONFIG["fields_mapping"]

    items = collect_items_flat(root)
    gh_items = []

    for item_id, item in items.items():
        gh_item = {
            "id": f"PVTI_mock_{item_id}",
            "title": f"{item_id}: {item['title']}",
            "status": item.get("status", ""),
            "issueType": {"name": item["level"].capitalize()},
            "labels": [item["level"].lower()],
        }

        # Add custom fields using the mapped GitHub field names
        for edpa_key, gh_name in fields_mapping.items():
            val = item.get(edpa_key)
            if val is not None and val != "" and val != 0:
                gh_item[gh_name] = val

        # Also include assignee and iteration as direct fields
        if item.get("iteration"):
            gh_item["Iteration"] = item["iteration"]
        if item.get("assignee"):
            gh_item["assignee"] = item["assignee"]
        if item.get("owner"):
            gh_item["owner"] = item["owner"]

        gh_items.append(gh_item)

    # Simulate some "remote" changes for diff demonstration
    for gh_item in gh_items:
        if "S-221" in gh_item.get("title", ""):
            gh_item["status"] = "Done"
            break

    return {"items": gh_items}


# -- Diff Engine ---------------------------------------------------------------

def compute_diff(local_items, remote_items):
    """
    Compare local (.edpa/backlog/) and remote (GitHub Project) items.
    Returns a list of change dicts.
    """
    changes = []
    all_ids = set(local_items.keys()) | set(remote_items.keys())

    for item_id in sorted(all_ids):
        local = local_items.get(item_id)
        remote = remote_items.get(item_id)

        if local and not remote:
            changes.append({
                "id": item_id,
                "action": "local_only",
                "detail": f"Exists in .edpa/backlog/ but not in GitHub Project",
                "local": local,
            })
            continue

        if remote and not local:
            changes.append({
                "id": item_id,
                "action": "remote_only",
                "detail": f"Exists in GitHub Project but not in .edpa/backlog/",
                "remote": remote,
            })
            continue

        # Both exist -- compare fields
        compare_fields = ["status", "title", "js", "bv", "tc", "rr", "wsjf",
                          "iteration", "assignee", "owner"]
        for field in compare_fields:
            local_val = local.get(field, "")
            remote_val = remote.get(field, "")
            # Normalize: treat None and "" as equivalent
            if not local_val:
                local_val = ""
            if not remote_val:
                remote_val = ""
            # Normalize numeric comparisons
            if isinstance(local_val, (int, float)) and isinstance(remote_val, (int, float)):
                if abs(float(local_val) - float(remote_val)) < 0.01:
                    continue
            elif str(local_val) == str(remote_val):
                continue

            changes.append({
                "id": item_id,
                "action": "field_changed",
                "field": field,
                "local_val": local_val,
                "remote_val": remote_val,
                "level": local.get("level", remote.get("level", "?")),
            })

    return changes


LEVEL_TO_DIR = {
    "Initiative": "initiatives",
    "Epic": "epics",
    "Feature": "features",
    "Story": "stories",
}

ID_PREFIX_TO_DIR = {
    "I": "initiatives",
    "E": "epics",
    "F": "features",
    "S": "stories",
}


def _item_file_path(root, item_id):
    """Resolve the .edpa/backlog/ file path for a given item ID (e.g., S-200 -> .edpa/backlog/stories/S-200.yaml)."""
    prefix = item_id.split("-")[0] if "-" in item_id else ""
    type_dir = ID_PREFIX_TO_DIR.get(prefix)
    if type_dir:
        return root / ".edpa" / "backlog" / type_dir / f"{item_id}.yaml"
    return None


def apply_remote_changes(root, changes):
    """
    Apply remote (GitHub) changes into individual .edpa/ item files.
    Returns applied_count.

    Finds the per-item YAML file by ID, loads it, updates the field, and writes back.
    """
    applied = 0
    updatable_fields = {"status", "js", "bv", "tc", "rr", "wsjf", "owner",
                        "assignee", "iteration", "title"}

    for change in changes:
        if change["action"] != "field_changed":
            continue

        item_id = change["id"]
        field = change["field"]
        new_value = change["remote_val"]

        item_path = _item_file_path(root, item_id)
        if not item_path or not item_path.exists():
            continue

        item = load_yaml(item_path)
        if not item:
            continue

        if field in item or field in updatable_fields:
            item[field] = new_value
            save_yaml(item_path, item)
            applied += 1

    return applied


# -- Changelog Helpers ---------------------------------------------------------

def log_change(root, source, action, item_id, field="", old="", new="", actor="sync-bot"):
    """Append a change entry to the changelog."""
    changelog_path = root / ".edpa" / "changelog.jsonl"
    entry = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": source,
        "action": action,
        "item": item_id,
    }
    if field:
        entry["field"] = field
    if old:
        entry["old"] = str(old)
    if new:
        entry["new"] = str(new)
    entry["actor"] = actor

    append_jsonl(changelog_path, entry)


def update_sync_state(root, direction, items_count, checksum):
    """Update the sync state file."""
    state_path = root / ".edpa" / "sync_state.json"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    state = {}
    if state_path.exists():
        try:
            state = load_json(state_path)
        except (json.JSONDecodeError, FileNotFoundError):
            state = {}

    if direction == "pull":
        state["last_pull"] = now
    elif direction == "push":
        state["last_push"] = now

    state["items_synced"] = items_count
    state["checksum"] = checksum

    save_json(state_path, state)


# -- Commands ------------------------------------------------------------------

def cmd_pull(root, sync_config, args):
    """Pull changes from GitHub Projects into .edpa/ item files."""
    print()
    print(bold(color(f"  {SYNC_ICON} EDPA Sync: Pull (GitHub Projects {ARROW} .edpa/backlog/ items)", C.HEADER)))
    print()

    # Fetch remote data
    fields_mapping = sync_config.get("fields_mapping", DEFAULT_SYNC_CONFIG["fields_mapping"])
    if args.mock:
        print(color("  [mock] Generating simulated GitHub Project data...", C.MUTED))
        gh_data = generate_mock_gh_data(root, fields_mapping)
    else:
        org = sync_config.get("github_org", "YOUR_ORG")
        project_num = sync_config.get("github_project_number", 1)
        print(color(f"  Fetching project items from {org}/project#{project_num}...", C.SYNC))
        gh_data = gh_fetch_project_items(sync_config)
        if gh_data is None:
            print(color("  Pull aborted: could not fetch GitHub Project data.", C.ERR))
            sys.exit(1)

    # Map to EDPA format
    remote_items = map_gh_items_to_edpa(gh_data, fields_mapping)
    local_items = collect_items_flat(root)

    print(color(f"  Remote items: {len(remote_items)}", C.MUTED))
    print(color(f"  Local items:  {len(local_items)}", C.MUTED))
    print()

    # Compute diff
    changes = compute_diff(local_items, remote_items)
    field_changes = [c for c in changes if c["action"] == "field_changed"]

    if not field_changes:
        print(color(f"  {CHECK} No changes to apply. Backlog is up to date.", C.OK))
        update_sync_state(root, "pull", len(local_items), compute_backlog_checksum(root))
        print()
        return

    # Display changes
    print(color(f"  Changes detected: {len(field_changes)}", C.DIFF_MOD))
    print()

    for ch in field_changes:
        item_id = ch["id"]
        field = ch["field"]
        local_val = ch["local_val"]
        remote_val = ch["remote_val"]
        print(f"    {color(item_id, C.SYNC):18s}  "
              f"{field:12s}  "
              f"{color(str(local_val), C.DIFF_DEL)} {ARROW} {color(str(remote_val), C.DIFF_ADD)}")

    print()

    # Apply changes to individual item files
    applied = apply_remote_changes(root, field_changes)

    # Log changes
    for ch in field_changes:
        log_change(root, "github", "field_change", ch["id"],
                   field=ch["field"], old=str(ch["local_val"]), new=str(ch["remote_val"]))

    # Update sync state
    checksum = compute_backlog_checksum(root)
    update_sync_state(root, "pull", len(local_items), checksum)

    print(color(f"  {CHECK} Applied {applied} changes to .edpa/backlog/ item files", C.OK))

    # Auto-commit if requested
    if args.commit:
        _git_commit(root, f"sync: pull {applied} changes from GitHub Projects")

    print()


def cmd_push(root, sync_config, args):
    """Push changes from .edpa/ item files to GitHub Projects."""
    print()
    print(bold(color(f"  {SYNC_ICON} EDPA Sync: Push (.edpa/backlog/ items {ARROW} GitHub Projects)", C.HEADER)))
    print()

    # Fetch current remote state
    fields_mapping = sync_config.get("fields_mapping", DEFAULT_SYNC_CONFIG["fields_mapping"])
    if args.mock:
        print(color("  [mock] Generating simulated GitHub Project data...", C.MUTED))
        gh_data = generate_mock_gh_data(root, fields_mapping)
    else:
        org = sync_config.get("github_org", "YOUR_ORG")
        project_num = sync_config.get("github_project_number", 1)
        print(color(f"  Fetching current state from {org}/project#{project_num}...", C.SYNC))
        gh_data = gh_fetch_project_items(sync_config)
        if gh_data is None:
            print(color("  Push aborted: could not fetch GitHub Project data.", C.ERR))
            sys.exit(1)

    remote_items = map_gh_items_to_edpa(gh_data, fields_mapping)
    local_items = collect_items_flat(root)

    print(color(f"  Local items:  {len(local_items)}", C.MUTED))
    print(color(f"  Remote items: {len(remote_items)}", C.MUTED))
    print()

    # Compute diff (local is source of truth for push)
    changes = compute_diff(remote_items, local_items)
    field_changes = [c for c in changes if c["action"] == "field_changed"]

    if not field_changes:
        print(color(f"  {CHECK} No changes to push. GitHub Project is up to date.", C.OK))
        update_sync_state(root, "push", len(local_items), compute_backlog_checksum(root))
        print()
        return

    print(color(f"  Changes to push: {len(field_changes)}", C.DIFF_MOD))
    print()

    pushed = 0
    for ch in field_changes:
        item_id = ch["id"]
        field = ch["field"]
        old_val = ch["local_val"]   # remote's current value
        new_val = ch["remote_val"]  # local's value (what we want to push)

        print(f"    {color(item_id, C.SYNC):18s}  "
              f"{field:12s}  "
              f"{color(str(old_val), C.DIFF_DEL)} {ARROW} {color(str(new_val), C.DIFF_ADD)}",
              end="")

        if args.mock:
            print(f"  {color('[mock: ok]', C.MUTED)}")
            pushed += 1
        else:
            # Find the GH item ID for this EDPA item
            gh_item_id = remote_items.get(item_id, {}).get("_gh_item_id")
            if gh_item_id:
                field_gh_name = fields_mapping.get(field, field)
                success = gh_update_project_item(sync_config, gh_item_id, "", "", new_val)
                if success:
                    print(f"  {color('[ok]', C.OK)}")
                    pushed += 1
                else:
                    print(f"  {color('[failed]', C.ERR)}")
            else:
                print(f"  {color('[skipped: no GH item ID]', C.WARN)}")

    print()

    # Log changes
    for ch in field_changes:
        log_change(root, "git", "field_change", ch["id"],
                   field=ch["field"], old=str(ch["local_val"]), new=str(ch["remote_val"]))

    update_sync_state(root, "push", len(local_items), compute_backlog_checksum(root))
    print(color(f"  {CHECK} Pushed {pushed}/{len(field_changes)} changes to GitHub Project", C.OK))
    print()


def cmd_diff(root, sync_config, args):
    """Show what would change without applying (dry-run)."""
    print()
    print(bold(color(f"  {SYNC_ICON} EDPA Sync: Diff (dry-run)", C.HEADER)))
    print()

    # Fetch remote data
    fields_mapping = sync_config.get("fields_mapping", DEFAULT_SYNC_CONFIG["fields_mapping"])
    if args.mock:
        print(color("  [mock] Generating simulated GitHub Project data...", C.MUTED))
        gh_data = generate_mock_gh_data(root, fields_mapping)
    else:
        org = sync_config.get("github_org", "YOUR_ORG")
        project_num = sync_config.get("github_project_number", 1)
        print(color(f"  Fetching project items from {org}/project#{project_num}...", C.SYNC))
        gh_data = gh_fetch_project_items(sync_config)
        if gh_data is None:
            print(color("  Diff aborted: could not fetch GitHub Project data.", C.ERR))
            sys.exit(1)

    remote_items = map_gh_items_to_edpa(gh_data, fields_mapping)
    local_items = collect_items_flat(root)

    print(color(f"  Remote items: {len(remote_items)}", C.MUTED))
    print(color(f"  Local items:  {len(local_items)}", C.MUTED))
    print()

    changes = compute_diff(local_items, remote_items)

    if not changes:
        print(color(f"  {CHECK} No differences. Everything is in sync.", C.OK))
        print()
        return

    # Group by action type
    field_changes = [c for c in changes if c["action"] == "field_changed"]
    local_only = [c for c in changes if c["action"] == "local_only"]
    remote_only = [c for c in changes if c["action"] == "remote_only"]

    if field_changes:
        print(bold(color("  Field differences:", C.DIFF_MOD)))
        print()
        # Table header
        print(color(f"    {'Item':10s}  {'Field':12s}  {'Local':20s}     {'Remote':20s}", C.MUTED))
        print(color(f"    {DASH * 75}", C.MUTED))
        for ch in field_changes:
            level = ch.get("level", "")
            lc = C.STORY
            if level == "Epic":
                lc = C.EPIC
            elif level == "Feature":
                lc = C.FEAT
            elif level == "Initiative":
                lc = C.INIT

            print(f"    {color(ch['id'], lc):20s}  "
                  f"{ch['field']:12s}  "
                  f"{color(str(ch['local_val']), C.DIFF_DEL):30s} {ARROW}  "
                  f"{color(str(ch['remote_val']), C.DIFF_ADD)}")
        print()

    if local_only:
        print(bold(color("  Local only (not in GitHub Project):", C.DIFF_ADD)))
        for ch in local_only:
            print(f"    {color('+', C.DIFF_ADD)} {ch['id']}: {ch['local'].get('title', '')}")
        print()

    if remote_only:
        print(bold(color("  Remote only (not in .edpa/backlog/):", C.DIFF_DEL)))
        for ch in remote_only:
            print(f"    {color('-', C.DIFF_DEL)} {ch['id']}: {ch['remote'].get('title', '')}")
        print()

    # Summary
    print(color(f"  Summary: {len(field_changes)} field changes, "
                f"{len(local_only)} local-only, {len(remote_only)} remote-only", C.MUTED))
    print()


def cmd_log(root, _sync_config, args):
    """Show the sync changelog."""
    print()
    print(bold(color("  EDPA Sync Changelog", C.HEADER)))
    print()

    changelog_path = root / ".edpa" / "changelog.jsonl"
    entries = load_jsonl(changelog_path)

    if not entries:
        print(color("  No changelog entries yet.", C.MUTED))
        print()
        return

    # Show last N entries (default 20)
    limit = getattr(args, "limit", 20) or 20
    entries = entries[-limit:]

    # Table header
    print(color(f"    {'Timestamp':22s}  {'Source':8s}  {'Action':15s}  {'Item':8s}  "
                f"{'Field':12s}  {'Change':30s}  {'Actor':10s}", C.MUTED))
    print(color(f"    {DASH * 115}", C.MUTED))

    for entry in reversed(entries):
        ts = entry.get("ts", "")[:19]
        source = entry.get("source", "?")
        action = entry.get("action", "?")
        item = entry.get("item", "?")
        field = entry.get("field", "")
        old = entry.get("old", "")
        new = entry.get("new", "")
        actor = entry.get("actor", "?")

        source_color = C.SYNC if source == "github" else C.OK
        change_str = ""
        if old and new:
            change_str = f"{old} {ARROW} {new}"
        elif new:
            change_str = f"{ARROW} {new}"

        print(f"    {color(ts, C.MUTED):32s}  "
              f"{color(source, source_color):18s}  "
              f"{action:15s}  "
              f"{color(item, C.STORY):18s}  "
              f"{field:12s}  "
              f"{change_str:30s}  "
              f"{color(actor, C.DIM)}")

    print()
    print(color(f"  Showing last {len(entries)} of {len(load_jsonl(changelog_path))} entries", C.MUTED))
    print()


def cmd_conflicts(root, _sync_config, args):
    """Show items changed in both sources since last sync."""
    print()
    print(bold(color(f"  {SYNC_ICON} EDPA Sync: Conflicts", C.HEADER)))
    print()

    state_path = root / ".edpa" / "sync_state.json"
    if not state_path.exists():
        print(color("  No sync state found. Run `pull` or `push` first.", C.WARN))
        print()
        return

    state = load_json(state_path)
    last_pull = state.get("last_pull", "")
    last_push = state.get("last_push", "")

    # Check changelog for changes from both sources since last sync
    changelog_path = root / ".edpa" / "changelog.jsonl"
    entries = load_jsonl(changelog_path)

    last_sync = max(last_pull, last_push) if last_pull and last_push else (last_pull or last_push)
    if not last_sync:
        print(color("  No sync history. Cannot detect conflicts.", C.WARN))
        print()
        return

    # Find items changed from both sources
    github_changes = {}
    git_changes = {}

    for entry in entries:
        ts = entry.get("ts", "")
        if ts < last_sync:
            continue

        item_id = entry.get("item", "")
        source = entry.get("source", "")
        field = entry.get("field", "")

        if source == "github":
            github_changes.setdefault(item_id, []).append(entry)
        elif source == "git":
            git_changes.setdefault(item_id, []).append(entry)

    # Items changed in both
    conflict_ids = set(github_changes.keys()) & set(git_changes.keys())

    if not conflict_ids:
        print(color(f"  {CHECK} No conflicts detected.", C.OK))
        print(color(f"  Last sync: {last_sync}", C.MUTED))
        print()
        return

    print(color(f"  {CROSS} {len(conflict_ids)} items have changes from both sources:", C.ERR))
    print()

    for item_id in sorted(conflict_ids):
        print(f"    {bold(color(item_id, C.WARN))}")

        gh_entries = github_changes[item_id]
        git_entries = git_changes[item_id]

        print(color(f"      GitHub changes:", C.SYNC))
        for e in gh_entries:
            field = e.get("field", "?")
            old = e.get("old", "")
            new = e.get("new", "")
            print(f"        {field}: {old} {ARROW} {new}  [{e.get('ts', '')[:19]}]")

        print(color(f"      Git changes:", C.OK))
        for e in git_entries:
            field = e.get("field", "?")
            old = e.get("old", "")
            new = e.get("new", "")
            print(f"        {field}: {old} {ARROW} {new}  [{e.get('ts', '')[:19]}]")

        print()

    print(color("  Resolution: manually edit .edpa/backlog/ item files and run `push`.", C.MUTED))
    print()


def cmd_status(root, sync_config, args):
    """Show sync status overview."""
    print()
    print(bold(color(f"  {SYNC_ICON} EDPA Sync Status", C.HEADER)))
    print()

    org = sync_config.get("github_org", "YOUR_ORG")
    project_num = sync_config.get("github_project_number", 1)

    print(f"  {bold('Organization:')}     {org}")
    print(f"  {bold('Project:')}          #{project_num}")
    print()

    # Sync state
    state_path = root / ".edpa" / "sync_state.json"
    if state_path.exists():
        state = load_json(state_path)
        last_pull = state.get("last_pull", "never")
        last_push = state.get("last_push", "never")
        items_synced = state.get("items_synced", 0)
        checksum = state.get("checksum", "n/a")

        print(f"  {bold('Last pull:')}        {color(last_pull, C.SYNC)}")
        print(f"  {bold('Last push:')}        {color(last_push, C.OK)}")
        print(f"  {bold('Items synced:')}     {items_synced}")
        print(f"  {bold('Checksum:')}         {color(checksum, C.MUTED)}")
    else:
        print(color("  No sync state found. Run `pull` or `push` to initialize.", C.WARN))

    print()

    # Current backlog stats
    items = collect_items_flat(root)
    levels = {}
    statuses = {}
    for item in items.values():
        level = item.get("level", "?")
        status = item.get("status", "?")
        levels[level] = levels.get(level, 0) + 1
        statuses[status] = statuses.get(status, 0) + 1

    print(f"  {bold('Backlog items:')}")
    for level in ("Initiative", "Epic", "Feature", "Story"):
        count = levels.get(level, 0)
        lc = {"Initiative": C.INIT, "Epic": C.EPIC, "Feature": C.FEAT, "Story": C.STORY}.get(level, C.RESET)
        print(f"    {color(f'{level}:', lc):22s} {count}")

    print()
    print(f"  {bold('By status:')}")
    for status in ("Done", "In Progress", "Active", "Planned"):
        count = statuses.get(status, 0)
        sc = {"Done": C.DONE, "In Progress": C.PROGRESS, "Active": C.ACTIVE, "Planned": C.PLANNED}.get(status, C.RESET)
        print(f"    {color(f'{status}:', sc):22s} {count}")

    print()

    # Changelog stats
    changelog_path = root / ".edpa" / "changelog.jsonl"
    entries = load_jsonl(changelog_path)
    print(f"  {bold('Changelog:')}        {len(entries)} entries")

    # Current checksum vs stored
    current_checksum = compute_backlog_checksum(root)
    if state_path.exists():
        state = load_json(state_path)
        stored = state.get("checksum", "")
        if stored and stored != current_checksum:
            print(color(f"  {CROSS} Backlog has changed since last sync (checksum mismatch)", C.WARN))
        elif stored:
            print(color(f"  {CHECK} Backlog matches last sync state", C.OK))

    print()


# -- Git Helpers ---------------------------------------------------------------

def _git_commit(root, message):
    """Stage .edpa/ changes and commit."""
    try:
        subprocess.run(["git", "add", ".edpa/"], cwd=root, capture_output=True, check=True)
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=root, capture_output=True
        )
        if result.returncode == 0:
            print(color("  No staged changes to commit.", C.MUTED))
            return
        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=root, capture_output=True, check=True
        )
        print(color(f"  {CHECK} Committed: {message}", C.OK))
    except subprocess.CalledProcessError as e:
        print(color(f"  Warning: git commit failed: {e}", C.WARN))


# -- Main CLI ------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog="sync",
        description="EDPA bidirectional sync: GitHub Projects <-> .edpa/backlog/ item files",
    )

    parser.add_argument("--mock", action="store_true",
                        help="Simulate GitHub Project data from existing backlog (for testing)")
    parser.add_argument("--verbose", action="store_true",
                        help="Show detailed output")

    sub = parser.add_subparsers(dest="command", help="Available commands")

    # pull
    p_pull = sub.add_parser("pull", help="GitHub Projects -> .edpa/backlog/ item files")
    p_pull.add_argument("--commit", action="store_true",
                        help="Auto-commit changes after pull")
    p_pull.add_argument("--mock", action="store_true",
                        help="Use mock data instead of real GitHub API")

    # push
    p_push = sub.add_parser("push", help=".edpa/backlog/ item files -> GitHub Projects")
    p_push.add_argument("--mock", action="store_true",
                        help="Use mock data instead of real GitHub API")

    # diff
    p_diff = sub.add_parser("diff", help="Show what would change (dry-run)")
    p_diff.add_argument("--mock", action="store_true",
                        help="Use mock data instead of real GitHub API")

    # log
    p_log = sub.add_parser("log", help="Show sync changelog")
    p_log.add_argument("--limit", type=int, default=20,
                       help="Number of entries to show (default: 20)")

    # conflicts
    sub.add_parser("conflicts", help="Show unresolved conflicts")

    # status
    sub.add_parser("status", help="Show sync status")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    # Resolve mock flag from either global or subcommand level
    if not hasattr(args, "mock"):
        args.mock = False
    if not hasattr(args, "commit"):
        args.commit = False

    root = find_repo_root()
    if root is None:
        print(color("Error: Cannot find .edpa/ directory. Run from the EDPA project directory.", C.ERR))
        sys.exit(1)

    sync_config = load_sync_config(root)

    # Ensure changelog and sync_state files exist
    changelog_path = root / ".edpa" / "changelog.jsonl"
    if not changelog_path.exists():
        changelog_path.touch()

    sync_state_path = root / ".edpa" / "sync_state.json"
    if not sync_state_path.exists():
        save_json(sync_state_path, {
            "last_pull": None,
            "last_push": None,
            "items_synced": 0,
            "checksum": "",
        })

    commands = {
        "pull": cmd_pull,
        "push": cmd_push,
        "diff": cmd_diff,
        "log": cmd_log,
        "conflicts": cmd_conflicts,
        "status": cmd_status,
    }

    cmd_func = commands.get(args.command)
    if cmd_func:
        cmd_func(root, sync_config, args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
