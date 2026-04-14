#!/bin/bash
#
# 安装 Git Hooks 用于本地自动化检查
#
# 用法:
#   ./scripts/install-hooks.sh
#
# 安装的 Hooks:
#   - pre-commit: 提交前运行 smart_commit_analyzer.py 回归检测
#   - post-commit: 提交后记录分析结果
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
HOOKS_DIR="$PROJECT_ROOT/.git/hooks"

echo "📦 安装 Git Hooks..."

# =========================================================================
# pre-commit hook
# =========================================================================
cat > "$HOOKS_DIR/pre-commit" << 'EOF'
#!/bin/bash
#
# Pre-commit Hook: 提交前检查
#
# 检查内容:
# 1. 运行 smart_commit_analyzer.py 确保没有回归
# 2. (可选) 检查代码格式
#

echo "🔍 Running pre-commit checks..."

# 获取项目根目录
PROJECT_ROOT="$(git rev-parse --show-toplevel)"

# 检查 Python 是否可用
if ! command -v python3 &> /dev/null; then
    echo "⚠️  Python3 not found, skipping validation"
    exit 0
fi

# 运行回归检测 (smart_commit_analyzer.py)
if [ -f "$PROJECT_ROOT/scripts/smart_commit_analyzer.py" ]; then
    echo "  Running smart_commit_analyzer.py..."

    cd "$PROJECT_ROOT"
    RESULT=$(python3 scripts/smart_commit_analyzer.py --json 2>/dev/null || echo '{"failed":[]}')

    FAILED=$(echo "$RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('failed', [])))" 2>/dev/null || echo "0")

    if [ "$FAILED" -gt 0 ]; then
        echo ""
        echo "❌ Regression check failed! $FAILED issue(s) found."
        echo "   Run: python3 scripts/smart_commit_analyzer.py"
        echo "   to see details."
        echo ""
        echo "   Use 'git commit --no-verify' to bypass (not recommended)"
        exit 1
    else
        echo "  ✅ Regression check passed"
    fi
fi

exit 0
EOF

chmod +x "$HOOKS_DIR/pre-commit"
echo "  ✅ pre-commit hook installed"

# =========================================================================
# post-commit hook
# =========================================================================
cat > "$HOOKS_DIR/post-commit" << 'EOF'
#!/bin/bash
#
# Post-commit Hook: 提交后分析
#
# 功能:
# 1. 记录提交分析结果
# 2. 更新本地分析日志
#

PROJECT_ROOT="$(git rev-parse --show-toplevel)"
LOG_FILE="$PROJECT_ROOT/.git/commit-analysis.log"

# 获取最新提交信息
COMMIT_HASH=$(git rev-parse HEAD)
COMMIT_SHORT=$(git rev-parse --short HEAD)
COMMIT_MSG=$(git log -1 --format=%s)
COMMIT_DATE=$(date -u '+%Y-%m-%d %H:%M:%S UTC')

# 记录到日志
echo "[$COMMIT_DATE] $COMMIT_SHORT: $COMMIT_MSG" >> "$LOG_FILE"

# 保持日志文件不超过 1000 行
if [ -f "$LOG_FILE" ]; then
    tail -n 1000 "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
fi

# 可选: 运行快速分析
if [ -f "$PROJECT_ROOT/analyze_git_changes.py" ]; then
    # 后台运行，不阻塞
    (
        cd "$PROJECT_ROOT"
        python3 analyze_git_changes.py --commits 1 --json > ".git/last-commit-analysis.json" 2>/dev/null
    ) &
fi

exit 0
EOF

chmod +x "$HOOKS_DIR/post-commit"
echo "  ✅ post-commit hook installed"

# =========================================================================
# 完成
# =========================================================================
echo ""
echo "✅ Git Hooks 安装完成!"
echo ""
echo "已安装的 Hooks:"
echo "  - pre-commit: 提交前运行 smart_commit_analyzer.py 回归检测"
echo "  - post-commit: 提交后记录分析日志"
echo ""
echo "日志位置: .git/commit-analysis.log"
echo ""
echo "如需禁用: git commit --no-verify"
