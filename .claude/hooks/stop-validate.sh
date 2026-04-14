#!/bin/bash
# Stop hook: Auto-run regression detection when Claude finishes a task.
# Only runs if Python files were modified in the working tree.

cd "$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0

# Check if any Python files were modified (staged or unstaged)
CHANGED=$(git diff --name-only HEAD 2>/dev/null; git diff --name-only --cached 2>/dev/null)
PY_CHANGED=$(echo "$CHANGED" | grep '\.py$' | sort -u)

# No Python changes — skip
[ -z "$PY_CHANGED" ] && exit 0

echo "🔍 Stop Hook: Python files changed, running validation..." >&2

# 1. Logic sync check (fast, ~2s)
if [ -f "scripts/check_logic_sync.py" ]; then
    OUTPUT=$(python3 scripts/check_logic_sync.py 2>&1)
    RC=$?
    if [ $RC -ne 0 ]; then
        echo "❌ Logic sync check FAILED:" >&2
        echo "$OUTPUT" >&2
        echo "" >&2
        echo "Fix sync issues before committing." >&2
        exit 1
    else
        echo "✅ Logic sync: all clones in sync" >&2
    fi
fi

# 2. Smart commit analyzer (medium, ~5-10s)
if [ -f "scripts/smart_commit_analyzer.py" ]; then
    OUTPUT=$(python3 scripts/smart_commit_analyzer.py --validate 2>&1)
    RC=$?
    if [ $RC -ne 0 ]; then
        echo "⚠️  Regression detection found issues:" >&2
        echo "$OUTPUT" | tail -20 >&2
    else
        echo "✅ Regression check: all rules passed" >&2
    fi
fi

echo "" >&2
exit 0
