#!/bin/bash
# Run all diagnostic scripts and save results to a single report file
# Usage: bash scripts/run_all_diagnostics.sh
# Output is shown on terminal AND saved to file simultaneously

REPORT="data/diagnostic_report_$(date -u +%Y%m%d_%H%M%S).txt"
BRANCH=$(git branch --show-current)

echo "╔════════════════════════════════════════════════════════════╗"
echo "║   AlgVex Full Diagnostic Suite                           ║"
echo "║   Branch: $BRANCH"
echo "║   Output: $REPORT"
echo "╚════════════════════════════════════════════════════════════╝"

mkdir -p data

run_step() {
    local step="$1"
    local desc="$2"
    shift 2
    echo ""
    echo "================================================================"
    echo "  [$step] $desc"
    echo "================================================================"
    # Run command, if it fails continue to next step
    "$@" 2>&1 || echo "  ⚠️  Step [$step] exited with error (continuing...)"
    echo ""
}

# Use tee to show on terminal AND save to file
{
    echo "================================================================"
    echo "  AlgVex Full Diagnostic Report"
    echo "  Date: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
    echo "  Branch: $BRANCH"
    echo "  Commit: $(git log --oneline -1)"
    echo "================================================================"

    run_step "1/6" "Code Integrity + Regression Detection" \
        python3 scripts/smart_commit_analyzer.py

    python3 scripts/check_logic_sync.py 2>&1 || true

    run_step "2/6" "Feature Pipeline Diagnostic" \
        python3 scripts/diagnose_feature_pipeline.py --with-external

    run_step "3/6" "Technical Indicator Verification" \
        python3 scripts/verify_indicators.py

    run_step "4/6" "External Data Pipeline Validation" \
        python3 scripts/validate_data_pipeline.py

    run_step "5/6" "AI Quality Scoring Diagnostic" \
        python3 scripts/diagnose_quality_scoring.py

    run_step "6/6" "Full Realtime Diagnostic (with AI calls)" \
        python3 scripts/diagnose_realtime.py --summary

    echo "================================================================"
    echo "  DIAGNOSTIC SUITE COMPLETE"
    echo "  Date: $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
    echo "================================================================"
} 2>&1 | tee "$REPORT"

echo ""
echo "✅ Report saved to: $REPORT ($(wc -l < "$REPORT") lines)"
echo ""

# Commit and push (force-add because data/ is in .gitignore)
git add -f "$REPORT"
git commit -m "diagnostic: full suite report $(date -u +%Y%m%d_%H%M%S)"
git push -u origin "$BRANCH"

echo "✅ Pushed to branch: $BRANCH"
echo "   File: $REPORT"
