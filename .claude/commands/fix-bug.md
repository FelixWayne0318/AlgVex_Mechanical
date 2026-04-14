# Fix Bug — BUG 修复流程

You MUST follow these steps in order. Do NOT skip any step.

## Step 1: Reproduce (复现)
- Read the error message / bug description carefully
- Find the exact file and line where the bug occurs
- Understand the call chain: who calls this code and with what arguments?

## Step 2: Root Cause (根因分析)
- Identify the ROOT CAUSE, not just the symptom
- Check git blame: was this recently changed? Was it a regression?
- Check if similar patterns exist elsewhere that might have the same bug

## Step 3: Fix (最小修复)
- Apply the MINIMAL fix that addresses the root cause
- Do NOT refactor surrounding code
- Do NOT add "defensive" checks for unrelated scenarios
- Do NOT change formatting, imports, or comments in untouched code

## Step 4: Validate (验证)
- Run: `python3 scripts/check_logic_sync.py`
- Run: `python3 scripts/smart_commit_analyzer.py --validate`
- Run: `python3 -m pytest tests/ -x --tb=short -q`
- Report all results to the user

## RULES:
- A bug fix is ONE thing. Don't bundle "improvements" with the fix.
- If the fix touches an SSoT file, check ALL dependents listed in CLAUDE.md.
- If unsure about the root cause, ask the user before guessing.
