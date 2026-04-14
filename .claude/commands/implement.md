# Implement Feature — 标准开发流程

You MUST follow these steps in order. Do NOT skip any step.

## Step 1: Understand (先调研)
- Read the relevant source files and ALL their dependents (check SSoT table in CLAUDE.md)
- If modifying a framework-related behavior, search official docs first (NautilusTrader, python-telegram-bot, etc.)
- Summarize what you found to the user before proceeding

## Step 2: Plan (出方案)
- List ALL files that will be modified
- For each file, describe WHAT will change and WHY
- Identify risks: will this break any SSoT dependents?
- Present the plan to the user and wait for approval before coding

## Step 3: Implement (逐步改)
- Modify ONE file at a time
- After each file, verify it doesn't introduce syntax errors: `python3 -c "import ast; ast.parse(open('FILE').read())"`
- Do NOT refactor, add comments, or "improve" code beyond what was requested

## Step 4: Validate (验证)
- Run: `python3 scripts/check_logic_sync.py`
- Run: `python3 scripts/smart_commit_analyzer.py --validate`
- If either fails, fix the issues before proceeding
- Report results to the user

## Step 5: Test (测试)
- Run relevant tests: `python3 -m pytest tests/ -x --tb=short -q`
- If tests fail, fix and re-run
- Report test results

## RULES:
- Never guess. If unsure, read the code or ask the user.
- Never modify files not listed in Step 2 without telling the user.
- Stop Hook will auto-validate at the end, but run checks manually in Step 4 too.
