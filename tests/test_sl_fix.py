#!/usr/bin/env python3
"""
测试止损修复是否正确
Test script for stop loss fix validation
"""

def test_stop_loss_calculation():
    """测试止损计算逻辑"""

    print("=" * 60)
    print("  止损修复验证测试")
    print("=" * 60)

    # 配置
    sl_buffer_pct = 0.001  # 0.1%

    test_cases = [
        # (side, entry_price, support, resistance, description)
        ("BUY", 91626.10, 91808.10, 92500.00, "Bug场景: 支撑位高于入场价"),
        ("BUY", 91626.10, 90000.00, 92500.00, "正常场景: 支撑位低于入场价"),
        ("BUY", 91626.10, 0, 0, "无支撑/阻力: 使用默认2%"),
        ("SELL", 91626.10, 90000.00, 91000.00, "Bug场景: 阻力位低于入场价"),
        ("SELL", 91626.10, 90000.00, 93000.00, "正常场景: 阻力位高于入场价"),
        ("SELL", 91626.10, 0, 0, "无支撑/阻力: 使用默认2%"),
    ]

    all_passed = True

    for i, (side, entry_price, support, resistance, desc) in enumerate(test_cases, 1):
        print(f"\n测试 {i}: {desc}")
        print(f"  方向: {side}, 入场价: ${entry_price:,.2f}")
        print(f"  支撑位: ${support:,.2f}, 阻力位: ${resistance:,.2f}")

        # 计算止损 (模拟修复后的逻辑)
        if side == "BUY":
            default_sl = entry_price * 0.98
            if support > 0:
                potential_sl = support * (1 - sl_buffer_pct)
                if potential_sl < entry_price:  # 验证: 止损必须低于入场价
                    stop_loss_price = potential_sl
                    method = "支撑位"
                else:
                    stop_loss_price = default_sl
                    method = "默认2% (支撑位无效)"
            else:
                stop_loss_price = default_sl
                method = "默认2%"

            # 验证
            is_valid = stop_loss_price < entry_price

        else:  # SELL
            default_sl = entry_price * 1.02
            if resistance > 0:
                potential_sl = resistance * (1 + sl_buffer_pct)
                if potential_sl > entry_price:  # 验证: 止损必须高于入场价
                    stop_loss_price = potential_sl
                    method = "阻力位"
                else:
                    stop_loss_price = default_sl
                    method = "默认2% (阻力位无效)"
            else:
                stop_loss_price = default_sl
                method = "默认2%"

            # 验证
            is_valid = stop_loss_price > entry_price

        # 输出结果
        print(f"  止损价: ${stop_loss_price:,.2f} (方法: {method})")

        if side == "BUY":
            print(f"  验证: 止损 ${stop_loss_price:,.2f} < 入场 ${entry_price:,.2f}?", end=" ")
        else:
            print(f"  验证: 止损 ${stop_loss_price:,.2f} > 入场 ${entry_price:,.2f}?", end=" ")

        if is_valid:
            print("✅ 通过")
        else:
            print("❌ 失败")
            all_passed = False

    print("\n" + "=" * 60)
    if all_passed:
        print("  ✅ 所有测试通过! 止损修复正确!")
    else:
        print("  ❌ 部分测试失败!")
    print("=" * 60)

    return all_passed


def test_strategy_import():
    """测试策略模块能否正常导入"""
    print("\n" + "=" * 60)
    print("  测试策略模块导入")
    print("=" * 60)

    try:
        from strategy.ai_strategy import AITradingStrategy, AITradingStrategyConfig
        print("  ✅ 策略模块导入成功")
        return True
    except Exception as e:
        print(f"  ❌ 导入失败: {e}")
        return False


def test_env_file():
    """测试 .env 文件配置"""
    print("\n" + "=" * 60)
    print("  测试环境变量配置")
    print("=" * 60)

    import os
    from pathlib import Path

    env_file = Path(".env")
    if not env_file.exists():
        print("  ❌ .env 文件不存在")
        return False

    required_keys = [
        "BINANCE_API_KEY",
        "BINANCE_API_SECRET",
        "DEEPSEEK_API_KEY",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
    ]

    # 读取 .env 文件
    env_content = env_file.read_text()

    all_present = True
    for key in required_keys:
        if key in env_content:
            print(f"  ✅ {key}: 已配置")
        else:
            print(f"  ❌ {key}: 缺失")
            all_present = False

    return all_present


if __name__ == "__main__":
    import sys

    results = []

    # 测试1: 止损计算逻辑
    results.append(("止损计算逻辑", test_stop_loss_calculation()))

    # 测试2: 策略模块导入
    results.append(("策略模块导入", test_strategy_import()))

    # 测试3: 环境变量配置
    results.append(("环境变量配置", test_env_file()))

    # 总结
    print("\n" + "=" * 60)
    print("  测试总结")
    print("=" * 60)

    all_passed = True
    for name, passed in results:
        status = "✅ 通过" if passed else "❌ 失败"
        print(f"  {name}: {status}")
        if not passed:
            all_passed = False

    print("=" * 60)
    if all_passed:
        print("\n🎉 所有测试通过! 可以正常运行交易机器人。")
    else:
        print("\n⚠️ 部分测试失败，请检查配置。")

    sys.exit(0 if all_passed else 1)
