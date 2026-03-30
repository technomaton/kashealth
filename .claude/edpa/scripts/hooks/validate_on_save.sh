#!/bin/sh
# EDPA validate_on_save hook — validates YAML, JSON, and Python files when Claude Code writes them.
# Reads Claude Code tool_input JSON from stdin.
# Exit 0 always (non-blocking), but prints validation errors to stderr.
set -e

# Read stdin (Claude Code passes JSON with tool_input)
INPUT=$(cat)

# Extract file_path from JSON
FILE_PATH=$(echo "$INPUT" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    path = data.get('tool_input', {}).get('file_path', '')
    print(path)
except Exception:
    print('')
" 2>/dev/null)

# Skip if no file path or not a supported file type
case "$FILE_PATH" in
    *.yaml|*.yml|*.json|*.py) ;;
    *) exit 0 ;;
esac

# Skip if file doesn't exist
[ -f "$FILE_PATH" ] || exit 0

# Validate syntax (pass path via env to avoid shell injection)
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT_DIR="$SCRIPT_DIR" EDPA_VALIDATE_PATH="$FILE_PATH" python3 -c "
import os, sys
path = os.environ['EDPA_VALIDATE_PATH']
script_dir = os.environ.get('SCRIPT_DIR', '')
sys.path.insert(0, script_dir)
try:
    from validate_syntax import validate_file
    errors = validate_file(path)
    for e in errors:
        print(f'EDPA: validation error: {e}', file=sys.stderr)
except Exception:
    pass
" 2>&1

exit 0
