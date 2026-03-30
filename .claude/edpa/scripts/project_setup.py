#!/usr/bin/env python3
"""
EDPA GitHub Project Setup — Automated initialization of GitHub Projects v2.

Creates a fully configured GitHub Project with:
- Custom fields (Job Size, BV, TC, RR, WSJF Score, Team)
- Issues for all backlog items (from .edpa/ per-item YAML files)
- Native Issue Types assigned via GraphQL (Initiative, Epic, Feature, Story)
- Enabler label for technical work items
- Field values set on all project items
- Project linked to repository

Usage:
    python .claude/edpa/scripts/project_setup.py --org technomaton --repo edpa-simulation
    python .claude/edpa/scripts/project_setup.py --org technomaton --repo edpa-simulation --dry-run

Prerequisite:
    gh auth login (with project scope)
    .edpa/backlog/ directory with per-item YAML files (initiatives/, epics/, features/, stories/)
"""

import argparse
import json
import subprocess
import sys
import textwrap
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required. Install with: pip install pyyaml")
    sys.exit(1)


# ANSI colors
class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    RED = "\033[31m"
    GRAY = "\033[38;5;245m"
    PURPLE = "\033[38;5;93m"


def run(cmd, check=True):
    """Run a shell command and return stdout."""
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and result.returncode != 0:
        return None
    return result.stdout.strip()


def gh_graphql(query):
    """Execute GitHub GraphQL query."""
    result = subprocess.run(
        ["gh", "api", "graphql", "-f", f"query={query}"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None
    return json.loads(result.stdout)


def step(num, text):
    print(f"\n  {C.CYAN}{C.BOLD}[{num}]{C.RESET} {text}")


def ok(text):
    print(f"      {C.GREEN}✓{C.RESET} {text}")


def fail(text):
    print(f"      {C.RED}✗{C.RESET} {text}")


def info(text):
    print(f"      {C.GRAY}{text}{C.RESET}")


def main():
    parser = argparse.ArgumentParser(description="EDPA GitHub Project Setup")
    parser.add_argument("--org", required=True, help="GitHub organization")
    parser.add_argument("--repo", required=True, help="Repository name")
    parser.add_argument("--project-title", default="EDPA — Medical Platform",
                        help="Project title")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print plan without executing")
    args = parser.parse_args()

    full_repo = f"{args.org}/{args.repo}"

    print(f"\n{C.BOLD}{C.PURPLE}  EDPA GitHub Project Setup{C.RESET}")
    print(f"  {C.GRAY}Organization: {args.org}")
    print(f"  Repository:  {full_repo}")
    print(f"  Backlog:     .edpa/backlog/ (per-item files){C.RESET}")

    if args.dry_run:
        print(f"  {C.YELLOW}Mode: DRY RUN{C.RESET}")

    # Load items from per-file .edpa/backlog/ directories
    backlog_dir = Path(".edpa/backlog")
    if not backlog_dir.is_dir():
        fail("Cannot find .edpa/backlog/ directory")
        sys.exit(1)

    items = []
    for type_dir in ["initiatives", "epics", "features", "stories"]:
        dir_path = backlog_dir / type_dir
        if not dir_path.exists():
            continue
        for f in sorted(dir_path.glob("*.yaml")):
            raw = yaml.safe_load(open(f))
            if not raw:
                continue
            entry = {
                "id": raw["id"],
                "title": raw.get("title", ""),
                "level": raw.get("type", ""),
                "js": raw.get("js", 0),
                "bv": raw.get("bv", 0),
                "tc": raw.get("tc", 0),
                "rr": raw.get("rr", 0),
                "wsjf": raw.get("wsjf", 0),
                "status": raw.get("status", "Active"),
                "owner": raw.get("owner", ""),
                "assignee": raw.get("assignee", ""),
                "iteration": raw.get("iteration", ""),
                "type": raw.get("epic_type", ""),
            }
            items.append(entry)

    print(f"\n  {C.BOLD}Backlog: {len(items)} items{C.RESET}")
    for level in ["Initiative", "Epic", "Feature", "Story"]:
        count = sum(1 for i in items if i["level"] == level)
        if count:
            print(f"    {level}: {count}")

    if args.dry_run:
        print(f"\n  {C.YELLOW}Dry run complete. {len(items)} items would be created.{C.RESET}")
        return

    # ═══════════════════════════════════════════════════════════
    # STEP 1: Create labels
    # ═══════════════════════════════════════════════════════════
    step(1, "Creating labels")
    labels = {
        "Enabler": ("fbbf24", "Technical work without direct business value"),
    }
    for name, (color, desc) in labels.items():
        result = run(f'gh label create "{name}" --color "{color}" --description "{desc}" --repo {full_repo}')
        if result is not None:
            ok(f"{name} ({color})")
        else:
            info(f"{name} (already exists)")

    # ═══════════════════════════════════════════════════════════
    # STEP 2: Create GitHub Project
    # ═══════════════════════════════════════════════════════════
    step(2, "Creating GitHub Project")
    result = run(f'gh project create --owner {args.org} --title "{args.project_title}" --format json')
    if result:
        project_data = json.loads(result)
        project_id = project_data["id"]
        project_num = project_data["number"]
        ok(f"Project #{project_num} created (id={project_id})")
    else:
        # Project might already exist, find it
        result = run(f'gh project list --owner {args.org} --format json')
        projects = json.loads(result).get("projects", [])
        match = [p for p in projects if args.project_title in p.get("title", "")]
        if match:
            project_num = match[0]["number"]
            project_id = match[0]["id"]
            info(f"Project #{project_num} already exists")
        else:
            fail("Could not create or find project")
            sys.exit(1)

    # ═══════════════════════════════════════════════════════════
    # STEP 3: Create custom fields
    # ═══════════════════════════════════════════════════════════
    step(3, "Creating custom fields")
    number_fields = ["Job Size", "Business Value", "Time Criticality",
                     "Risk Reduction", "WSJF Score"]
    for name in number_fields:
        run(f'gh project field-create {project_num} --owner {args.org} '
            f'--name "{name}" --data-type NUMBER')
        ok(f"{name} (NUMBER)")

    run(f'gh project field-create {project_num} --owner {args.org} '
        f'--name "Team" --data-type SINGLE_SELECT '
        f'--single-select-options "Core,Platform,Management"')
    ok("Team (SINGLE_SELECT)")

    # Get field IDs
    field_json = run(f'gh project field-list {project_num} --owner {args.org} --format json')
    fields = json.loads(field_json).get("fields", [])
    field_ids = {f["name"]: f["id"] for f in fields}
    option_ids = {}
    for f in fields:
        for opt in f.get("options", []):
            option_ids[f"{f['name']}:{opt['name']}"] = opt["id"]

    info(f"Fields: {len(field_ids)}, Options: {len(option_ids)}")

    # ═══════════════════════════════════════════════════════════
    # STEP 4: Link project to repo
    # ═══════════════════════════════════════════════════════════
    step(4, "Linking project to repository")
    run(f'gh project link {project_num} --owner {args.org} --repo {full_repo}')
    ok(f"Linked to {full_repo}")

    # ═══════════════════════════════════════════════════════════
    # STEP 5: Query native Issue Type IDs from organization
    # ═══════════════════════════════════════════════════════════
    step(5, "Querying organization Issue Type IDs")
    issue_type_ids = {}
    type_query = f'{{ organization(login: "{args.org}") {{ issueTypes(first: 20) {{ nodes {{ id name }} }} }} }}'
    type_result = gh_graphql(type_query)
    if type_result and type_result.get("data"):
        for t in type_result["data"]["organization"]["issueTypes"]["nodes"]:
            issue_type_ids[t["name"]] = t["id"]
        ok(f"Found {len(issue_type_ids)} issue types: {', '.join(issue_type_ids.keys())}")
    else:
        fail("Could not query issue types from org. Run 'issue_types.py setup --org ORG' first.")
        fail("Issue Type assignment will be skipped.")
        issue_type_ids = {}

    # ═══════════════════════════════════════════════════════════
    # STEP 6: Create issues
    # ═══════════════════════════════════════════════════════════
    step(6, f"Creating {len(items)} issues")
    issue_map = {}  # item_id → (issue_number, project_item_id)

    for item in items:
        title = f"{item['id']}: {item['title']}"
        body_parts = [f"{item['level']}"]
        if item.get("js"): body_parts.append(f"JS={item['js']}")
        if item.get("bv"): body_parts.append(f"BV={item['bv']}")
        if item.get("tc"): body_parts.append(f"TC={item['tc']}")
        if item.get("rr"): body_parts.append(f"RR={item['rr']}")
        if item.get("wsjf"): body_parts.append(f"WSJF={item['wsjf']}")
        if item.get("assignee"): body_parts.append(f"owner={item['assignee']}")
        if item.get("iteration"): body_parts.append(f"iteration={item['iteration']}")
        body = ", ".join(body_parts)

        # Add Enabler label only for items with type: Enabler in backlog
        label_flag = ""
        if item.get("type") == "Enabler":
            label_flag = ' --label "Enabler"'

        result = run(f'gh issue create --repo {full_repo} --title "{title}" '
                     f'--body "{body}"{label_flag}')
        if result:
            issue_url = result.strip()
            issue_num = issue_url.split("/")[-1]
            ok(f"{title} → #{issue_num}")

            # Assign native Issue Type via GraphQL
            type_id = issue_type_ids.get(item["level"])
            if type_id:
                node_query = (
                    f'{{ repository(owner: "{args.org}", name: "{args.repo}") '
                    f'{{ issue(number: {issue_num}) {{ id }} }} }}'
                )
                node_result = gh_graphql(node_query)
                if node_result and node_result.get("data"):
                    issue_node_id = node_result["data"]["repository"]["issue"]["id"]
                    mutation = (
                        f'mutation {{ updateIssueIssueType(input: '
                        f'{{ issueId: "{issue_node_id}", issueTypeId: "{type_id}" }}) '
                        f'{{ issue {{ id }} }} }}'
                    )
                    gh_graphql(mutation)
                    info(f"  Issue type → {item['level']}")

            # Add to project
            add_result = run(f'gh project item-add {project_num} --owner {args.org} '
                           f'--url {issue_url} --format json')
            if add_result:
                item_data = json.loads(add_result)
                project_item_id = item_data.get("id", "")
                issue_map[item["id"]] = (issue_num, project_item_id)

                # Close done items
                if item["status"] == "Done":
                    run(f'gh issue close {issue_num} --repo {full_repo}')
        else:
            fail(f"Failed: {title}")

    # ═══════════════════════════════════════════════════════════
    # STEP 7: Set custom field values
    # ═══════════════════════════════════════════════════════════
    step(7, "Setting custom field values on project items")

    status_map = {
        "Done": option_ids.get("Status:Done"),
        "In Progress": option_ids.get("Status:In Progress"),
        "Active": option_ids.get("Status:In Progress"),
        "Planned": option_ids.get("Status:Todo"),
        "Todo": option_ids.get("Status:Todo"),
    }

    set_count = 0
    for item in items:
        mapping = issue_map.get(item["id"])
        if not mapping:
            continue
        _, proj_item_id = mapping

        def set_field(field_name, number=None, option_id=None):
            nonlocal set_count
            fid = field_ids.get(field_name)
            if not fid:
                return
            cmd = f'gh project item-edit --project-id {project_id} --id {proj_item_id} --field-id {fid}'
            if number is not None:
                cmd += f' --number {number}'
            elif option_id:
                cmd += f' --single-select-option-id {option_id}'
            else:
                return
            run(cmd)
            set_count += 1

        # Set Status
        status_opt = status_map.get(item["status"])
        if status_opt:
            set_field("Status", option_id=status_opt)

        # Set number fields
        if item.get("js"):
            set_field("Job Size", number=item["js"])
        if item.get("bv"):
            set_field("Business Value", number=item["bv"])
        if item.get("tc"):
            set_field("Time Criticality", number=item["tc"])
        if item.get("rr"):
            set_field("Risk Reduction", number=item["rr"])
        if item.get("wsjf"):
            set_field("WSJF Score", number=item["wsjf"])

    ok(f"{set_count} field values set")

    # ═══════════════════════════════════════════════════════════
    # STEP 8: Update config
    # ═══════════════════════════════════════════════════════════
    step(8, "Updating .edpa/config/edpa.yaml")
    config_path = Path(".edpa/config/edpa.yaml")
    if config_path.exists():
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}
        sync = config.get("sync", {})
        sync["github_org"] = args.org
        sync["github_project_number"] = project_num
        sync["github_project_id"] = project_id
        config["sync"] = sync
        with open(config_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
        ok(f"Project #{project_num} saved to config")

    # ═══════════════════════════════════════════════════════════
    # DONE
    # ═══════════════════════════════════════════════════════════
    print(f"\n{'═' * 70}")
    print(f"  {C.GREEN}{C.BOLD}Setup complete!{C.RESET}")
    print(f"  Project: https://github.com/orgs/{args.org}/projects/{project_num}")
    print(f"  Issues:  {len(issue_map)} created")
    print(f"  Fields:  {set_count} values set")
    print(f"\n  {C.YELLOW}{C.BOLD}Manual step required:{C.RESET}")
    print(f"  GitHub Projects v2 API does not support view column configuration.")
    print(f"  Open the project in browser and click '+' in the table header to add:")
    print(f"    Job Size, Business Value, Time Criticality,")
    print(f"    Risk Reduction, WSJF Score, Team")
    print(f"{'═' * 70}\n")


if __name__ == "__main__":
    main()
