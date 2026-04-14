#!/bin/bash
# scripts/check_circular_imports.sh
# 循环导入验证脚本
#
# 用途: 在 Phase 3 实施前验证所有 import 语句不会导致循环导入
#
# Usage:
#   bash scripts/check_circular_imports.sh

set -e

echo "=========================================="
echo "  循环导入验证测试"
echo "=========================================="
echo ""

# 项目根目录
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

# 测试 1: 验证 config_manager 可以单独导入
echo "测试 1: config_manager 单独导入"
python3 -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT')
from utils.config_manager import ConfigManager
print('✅ config_manager 导入成功')
"
echo ""

# 测试 2: 验证 config_manager + strategy 不会循环导入
echo "测试 2: config_manager + ai_strategy 导入"
python3 -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT')
from utils.config_manager import ConfigManager
from strategy.ai_strategy import AITradingStrategy
print('✅ config_manager + ai_strategy 导入成功')
"
echo ""

# 测试 3: 验证 trading_logic + multi_agent_analyzer 导入
echo "测试 3: trading_logic + multi_agent_analyzer 导入"
python3 -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT')
from strategy.trading_logic import check_confidence_threshold, validate_multiagent_sltp
from agents.multi_agent_analyzer import MultiAgentAnalyzer
print('✅ trading_logic + multi_agent_analyzer 导入成功')
"
echo ""

# 测试 4: 验证完整导入链 (config_manager → strategy → agents → utils)
echo "测试 4: 完整导入链验证"
python3 -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT')

# 按依赖顺序导入
from utils.config_manager import ConfigManager
from agents.multi_agent_analyzer import MultiAgentAnalyzer
from strategy import trading_logic
from strategy.ai_strategy import AITradingStrategy

print('✅ 完整导入链验证成功')
print('   config_manager → multi_agent_analyzer → trading_logic → ai_strategy')
"
echo ""

# 测试 5: 验证 main_live.py 可以导入所有模块
echo "测试 5: main_live.py 完整导入验证"
python3 -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT')

# 模拟 main_live.py 的导入顺序
from pathlib import Path
from nautilus_trader.live.node import TradingNode
from utils.config_manager import ConfigManager

print('✅ main_live.py 导入验证成功')
"
echo ""

# 总结
echo "=========================================="
echo "  循环导入验证: 全部通过"
echo "=========================================="
echo ""
echo "✅ 所有导入测试通过，无循环依赖"
echo ""
echo "下一步: 可以安全实施 Phase 3 (trading_logic.py 迁移)"
