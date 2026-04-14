#!/bin/bash
# PostToolUse hook: Warn when editing a Single Source of Truth (SSoT) file.
# Reads the tool input from stdin (JSON) and checks if the edited file
# is in the SSoT list.  Outputs a warning to stderr for Claude to see.

INPUT=$(cat)
FILE=$(echo "$INPUT" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    fp = data.get('tool_input', {}).get('file_path', '')
    print(fp)
except:
    pass
" 2>/dev/null)

# Bail if we couldn't extract the file path
[ -z "$FILE" ] && exit 0

# SSoT files — editing these requires checking dependents
case "$FILE" in
  */utils/shared_logic.py)
    echo "⚠️  SSoT EDIT: shared_logic.py — ALL importers rely on this." >&2
    echo "   Run: python3 scripts/check_logic_sync.py" >&2
    ;;
  */utils/order_flow_processor.py)
    echo "⚠️  SSoT EDIT: order_flow_processor.py" >&2
    echo "   Dependents: verify_indicators.py, validate_data_pipeline.py" >&2
    echo "   Run: python3 scripts/check_logic_sync.py" >&2
    ;;
  */indicators/technical_manager.py)
    echo "⚠️  SSoT EDIT: technical_manager.py" >&2
    echo "   Dependents: verify_extension_ratio.py, verify_indicators.py" >&2
    echo "   Run: python3 scripts/check_logic_sync.py" >&2
    ;;
  */strategy/trading_logic.py)
    echo "⚠️  SSoT EDIT: trading_logic.py" >&2
    echo "   Dependents: backtest_high_signals.py, backtest_sr_zones.py" >&2
    echo "   Dependents: web/backend/services/trade_evaluation_service.py" >&2
    echo "   Run: python3 scripts/check_logic_sync.py" >&2
    ;;
  */strategy/ai_strategy.py)
    echo "⚠️  SSoT EDIT: ai_strategy.py — check _layer_orders / _next_layer_idx invariants" >&2
    echo "   Run: python3 scripts/check_logic_sync.py" >&2
    ;;
  */strategy/event_handlers.py)
    echo "⚠️  SSoT EDIT: event_handlers.py — mixin for on_order_*/on_position_* callbacks" >&2
    echo "   Contains _layer_orders.clear() sites — check paired _next_layer_idx = 0" >&2
    echo "   Run: python3 scripts/check_logic_sync.py" >&2
    ;;
  */strategy/order_execution.py)
    echo "⚠️  SSoT EDIT: order_execution.py — mixin for _execute_trade / _submit_*" >&2
    echo "   Run: python3 scripts/check_logic_sync.py" >&2
    ;;
  */strategy/position_manager.py)
    echo "⚠️  SSoT EDIT: position_manager.py — mixin for layer orders / scaling" >&2
    echo "   Run: python3 scripts/check_logic_sync.py" >&2
    ;;
  */strategy/safety_manager.py)
    echo "⚠️  SSoT EDIT: safety_manager.py — mixin for emergency SL / safety" >&2
    echo "   Contains _layer_orders.clear() sites — check paired _next_layer_idx = 0" >&2
    echo "   Run: python3 scripts/check_logic_sync.py" >&2
    ;;
  */strategy/telegram_commands.py)
    echo "⚠️  SSoT EDIT: telegram_commands.py — mixin for Telegram commands" >&2
    echo "   Contains _layer_orders.clear() sites — check paired _next_layer_idx = 0" >&2
    echo "   Run: python3 scripts/check_logic_sync.py" >&2
    ;;
  */agents/multi_agent_analyzer.py)
    echo "⚠️  SSoT EDIT: multi_agent_analyzer.py" >&2
    echo "   Check: prompt templates, divergence detection" >&2
    echo "   Run: python3 scripts/check_logic_sync.py" >&2
    ;;
  */agents/prompt_constants.py)
    echo "⚠️  SSoT EDIT: prompt_constants.py" >&2
    echo "   Check: INDICATOR_DEFINITIONS, SIGNAL_CONFIDENCE_MATRIX" >&2
    echo "   Run: python3 scripts/check_logic_sync.py" >&2
    ;;
  */agents/report_formatter.py)
    echo "⚠️  SSoT EDIT: report_formatter.py — report formatting mixin" >&2
    echo "   Run: python3 scripts/check_logic_sync.py" >&2
    ;;
  */agents/memory_manager.py)
    echo "⚠️  SSoT EDIT: memory_manager.py — memory/reflection mixin" >&2
    echo "   Run: python3 scripts/check_logic_sync.py" >&2
    ;;
  */utils/telegram_bot.py)
    echo "⚠️  SSoT EDIT: telegram_bot.py — side_to_cn() is the SSoT for direction display" >&2
    echo "   Dependents: telegram_command_handler.py, strategy mixin files (inline uses)" >&2
    ;;
esac

exit 0
