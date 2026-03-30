#!/usr/bin/env python3
"""
EDPA Syntax Validator — validates YAML, JSON, and Python files.

Used by:
  - Git pre-commit hook (file list from hook script)
  - Claude Code PostToolUse hook (single file via wrapper)
  - CLI validation (directory or file list)

Checks:
  - YAML: syntax + .tmpl files
  - JSON: syntax
  - Python: syntax (ast.parse)
  - Binary detection (UnicodeDecodeError)
"""

import ast
import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML required. Install with: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

YAML_EXTENSIONS = {".yaml", ".yml", ".tmpl"}
JSON_EXTENSIONS = {".json"}
PYTHON_EXTENSIONS = {".py"}


def validate_yaml(path):
    """Validate a single YAML file. Returns list of error strings."""
    errors = []
    path = Path(path)

    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        errors.append(f"{path}: file not found")
        return errors
    except UnicodeDecodeError:
        errors.append(f"{path}: binary file, not valid YAML")
        return errors

    try:
        yaml.safe_load(content)
    except yaml.YAMLError as e:
        errors.append(f"{path}: {e}")

    return errors


def validate_json(path):
    """Validate a single JSON file. Returns list of error strings."""
    path = Path(path)
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [f"{path}: file not found"]
    except UnicodeDecodeError:
        return [f"{path}: binary file, not valid JSON"]

    try:
        json.loads(content)
    except json.JSONDecodeError as e:
        return [f"{path}: {e}"]
    return []


def validate_python(path):
    """Validate Python syntax. Returns list of error strings."""
    path = Path(path)
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [f"{path}: file not found"]
    except UnicodeDecodeError:
        return [f"{path}: binary file, not valid Python"]

    try:
        ast.parse(content, filename=str(path))
    except SyntaxError as e:
        return [f"{path}: {e.msg} (line {e.lineno})"]
    return []


def validate_file(path):
    """Validate a single file based on its extension."""
    path = Path(path)
    ext = path.suffix.lower()

    if ext in YAML_EXTENSIONS:
        return validate_yaml(path)
    elif ext in JSON_EXTENSIONS:
        return validate_json(path)
    elif ext in PYTHON_EXTENSIONS:
        return validate_python(path)
    else:
        return []  # Unsupported extension, skip


def validate_directory(directory):
    """Validate all supported files in a directory tree."""
    directory = Path(directory)
    all_errors = []
    seen = set()

    for ext_set in [YAML_EXTENSIONS, JSON_EXTENSIONS, PYTHON_EXTENSIONS]:
        for ext in ext_set:
            for path in directory.glob(f"**/*{ext}"):
                if path in seen:
                    continue
                seen.add(path)
                all_errors.extend(validate_file(path))

    return all_errors


def main():
    if len(sys.argv) < 2:
        print("Usage: validate_syntax.py <path> [<path> ...]", file=sys.stderr)
        sys.exit(1)

    all_errors = []
    for arg in sys.argv[1:]:
        p = Path(arg)
        if p.is_dir():
            all_errors.extend(validate_directory(p))
        elif p.is_file():
            all_errors.extend(validate_file(p))
        else:
            all_errors.append(f"{p}: not found")

    if all_errors:
        for err in all_errors:
            print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)
    else:
        print("All files valid.")


if __name__ == "__main__":
    main()
