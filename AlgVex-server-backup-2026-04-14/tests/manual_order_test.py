"""
Manual Order Testing Script - FOR DEBUGGING ONLY

⚠️  WARNING: This script is for manual debugging and testing ONLY ⚠️

This script allows you to:
1. Test each component step-by-step
2. Force specific signals (BUY/SELL) for testing
3. Verify order logic before live trading

SAFETY NOTES:
- Use Binance TESTNET for testing (see .env.testnet configuration)
- Start with VERY SMALL amounts on live exchange
- ALWAYS verify calculations before submitting orders
- Use DRY_RUN=true to test without real orders

Usage:
    # Dry run (no real orders)
    python tests/manual_order_test.py --dry-run

    # Force a BUY signal with testnet
    python tests/manual_order_test.py --signal BUY --testnet

    # Test with live exchange (USE WITH EXTREME CAUTION)
    python tests/manual_order_test.py --signal BUY --live-danger

"""
import sys
import os
from pathlib import Path
from decimal import Decimal
import argparse
import json
from datetime import datetime
from typing import Dict, Any

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def print_section(title: str):
    """Print a formatted section header."""
    print("\n" + "="*70)
    print(f"  {title}")
    print("="*70 + "\n")


def print_step(step_num: int, title: str):
    """Print a formatted step header."""
    print(f"\n{'─'*70}")
    print(f"Step {step_num}: {title}")
    print(f"{'─'*70}")


def test_binance_connection(testnet: bool = True):
    """Test connection to Binance API."""
    print_step(1, "Testing Binance Connection")

    try:
        import ccxt

        if testnet:
            print("🔧 Connecting to Binance TESTNET...")
            exchange = ccxt.binance({
                'apiKey': os.getenv('BINANCE_TESTNET_API_KEY'),
                'secret': os.getenv('BINANCE_TESTNET_API_SECRET'),
                'options': {
                    'defaultType': 'future',
                    'test': True  # Testnet mode
                }
            })
        else:
            print("⚠️  Connecting to Binance LIVE (production)...")
            from binance.spot import Spot
            client = Spot(
                api_key=os.getenv('BINANCE_API_KEY'),
                api_secret=os.getenv('BINANCE_API_SECRET')
            )

        # Test connection
        print("   Testing API connection...")
        if testnet:
            ticker = exchange.fetch_ticker('BTC/USDT')
            print(f"   ✓ Connected to testnet")
            print(f"   ✓ BTC/USDT Price: ${ticker['last']:,.2f}")
        else:
            from binance.um_futures import UMFutures
            client = UMFutures(
                key=os.getenv('BINANCE_API_KEY'),
                secret=os.getenv('BINANCE_API_SECRET')
            )
            account = client.account()
            balance = float([b for b in account['assets'] if b['asset'] == 'USDT'][0]['walletBalance'])
            print(f"   ✓ Connected to LIVE exchange")
            print(f"   ✓ Account Balance: ${balance:,.2f} USDT")

        return True

    except Exception as e:
        print(f"   ✗ Connection failed: {str(e)}")
        return False


def fetch_live_market_data(symbol: str = 'BTCUSDT'):
    """Fetch real market data from Binance."""
    print_step(2, "Fetching Live Market Data")

    try:
        from binance.um_futures import UMFutures

        client = UMFutures(
            key=os.getenv('BINANCE_API_KEY'),
            secret=os.getenv('BINANCE_API_SECRET')
        )

        # Get recent klines
        print(f"   Fetching {symbol} 5-minute bars...")
        klines = client.klines(symbol, '5m', limit=50)

        # Get current price
        ticker = client.ticker_price(symbol)
        current_price = float(ticker['price'])

        print(f"   ✓ Fetched {len(klines)} bars")
        print(f"   ✓ Current Price: ${current_price:,.2f}")
        print(f"   ✓ Latest bar: O=${float(klines[-1][1]):,.2f} H=${float(klines[-1][2]):,.2f} L=${float(klines[-1][3]):,.2f} C=${float(klines[-1][4]):,.2f}")

        return {
            'klines': klines,
            'current_price': current_price,
            'symbol': symbol
        }

    except Exception as e:
        print(f"   ✗ Failed to fetch market data: {str(e)}")
        return None


def calculate_technical_indicators(market_data: Dict):
    """Calculate technical indicators from market data."""
    print_step(3, "Calculating Technical Indicators")

    klines = market_data['klines']
    closes = [float(k[4]) for k in klines]

    # Simple RSI calculation
    def calculate_rsi(prices, period=14):
        deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
        gains = [d if d > 0 else 0 for d in deltas]
        losses = [-d if d < 0 else 0 for d in deltas]

        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi

    # Calculate indicators
    rsi = calculate_rsi(closes)
    sma5 = sum(closes[-5:]) / 5
    sma20 = sum(closes[-20:]) / 20
    sma50 = sum(closes[-50:]) / 50

    indicators = {
        'rsi': rsi,
        'sma5': sma5,
        'sma20': sma20,
        'sma50': sma50,
        'support': min(closes[-20:]),
        'resistance': max(closes[-20:]),
        'current_price': market_data['current_price']
    }

    print(f"   ✓ RSI: {indicators['rsi']:.2f}")
    print(f"   ✓ SMA5: ${indicators['sma5']:,.2f}")
    print(f"   ✓ SMA20: ${indicators['sma20']:,.2f}")
    print(f"   ✓ SMA50: ${indicators['sma50']:,.2f}")
    print(f"   ✓ Support: ${indicators['support']:,.2f}")
    print(f"   ✓ Resistance: ${indicators['resistance']:,.2f}")

    return indicators


def force_ai_signal(signal_type: str, confidence: str = 'MEDIUM'):
    """Create a forced AI signal for testing."""
    print_step(4, f"Creating Forced {signal_type} Signal")

    print(f"   ⚠️  FORCING {signal_type} signal for testing")
    print(f"   ⚠️  This bypasses actual AI analysis")

    ai_response = {
        'signal': signal_type,
        'confidence': confidence,
        'reason': f'MANUAL TEST: Forced {signal_type} signal for debugging',
        'stop_loss': 0.01,
        'take_profit': 0.02
    }

    print(f"   Signal: {ai_response['signal']}")
    print(f"   Confidence: {ai_response['confidence']}")
    print(f"   Stop Loss: {ai_response['stop_loss']*100:.1f}%")
    print(f"   Take Profit: {ai_response['take_profit']*100:.1f}%")

    return ai_response


def calculate_order_parameters(ai_signal: Dict, indicators: Dict, equity: float = 400.0):
    """Calculate order parameters with minimum notional enforcement."""
    print_step(5, "Calculating Order Parameters")

    current_price = indicators['current_price']
    confidence = ai_signal['confidence']

    # Position sizing
    base_usdt = 100.0  # After hotfix
    confidence_mult = {'HIGH': 1.5, 'MEDIUM': 1.0, 'LOW': 0.5}[confidence]

    # Enforce minimum notional
    MIN_NOTIONAL = 100.0
    suggested_usdt = base_usdt * confidence_mult
    final_usdt = max(suggested_usdt, MIN_NOTIONAL)

    quantity = final_usdt / current_price

    # Calculate SL/TP
    sl_price = current_price * (1 - ai_signal['stop_loss'])
    tp_price = current_price * (1 + ai_signal['take_profit'])

    order_params = {
        'side': ai_signal['signal'],
        'quantity': quantity,
        'entry_price': current_price,
        'stop_loss': sl_price,
        'take_profit': tp_price,
        'notional': quantity * current_price,
        'confidence': confidence
    }

    print(f"   Equity: ${equity:.2f}")
    print(f"   Base USDT: ${base_usdt:.2f}")
    print(f"   Confidence Multiplier: {confidence_mult}x")
    print(f"   Final USDT: ${final_usdt:.2f}")
    print(f"   BTC Quantity: {order_params['quantity']:.6f}")
    print(f"   Notional: ${order_params['notional']:.2f}")
    print(f"\n   Entry: ${order_params['entry_price']:,.2f}")
    print(f"   Stop Loss: ${order_params['stop_loss']:,.2f} ({ai_signal['stop_loss']*100:.1f}%)")
    print(f"   Take Profit: ${order_params['take_profit']:,.2f} ({ai_signal['take_profit']*100:.1f}%)")

    # Validation
    print(f"\n   Validation:")
    if order_params['notional'] >= 100.0:
        print(f"   ✓ Notional ${order_params['notional']:.2f} >= $100 minimum")
    else:
        print(f"   ✗ ERROR: Notional ${order_params['notional']:.2f} < $100 minimum")
        raise ValueError("Order would be rejected by Binance")

    return order_params


def submit_test_order(order_params: Dict, dry_run: bool = True, testnet: bool = True):
    """Submit order to exchange (or simulate in dry-run mode)."""
    print_step(6, "Submitting Order")

    if dry_run:
        print("   🔒 DRY RUN MODE - No real order will be placed")
        print(f"\n   Would submit {order_params['side']} order:")
        print(f"   Quantity: {order_params['quantity']:.6f} BTC")
        print(f"   Entry: ${order_params['entry_price']:,.2f}")
        print(f"   Stop Loss: ${order_params['stop_loss']:,.2f}")
        print(f"   Take Profit: ${order_params['take_profit']:,.2f}")
        print(f"\n   ✓ Order validation passed (dry run)")
        return {'order_id': 'DRY_RUN', 'status': 'simulated'}

    if testnet:
        print("   📡 Submitting to TESTNET...")
        # Add testnet order submission logic here
        print("   ⚠️  Testnet order submission not implemented yet")
        print("   Use Binance Futures Testnet API for safe testing")
        return {'order_id': 'TESTNET', 'status': 'pending'}

    # Live order submission
    print("   ⚠️⚠️⚠️  SUBMITTING TO LIVE EXCHANGE ⚠️⚠️⚠️")
    input("   Press ENTER to confirm or Ctrl+C to abort...")

    try:
        from binance.um_futures import UMFutures

        client = UMFutures(
            key=os.getenv('BINANCE_API_KEY'),
            secret=os.getenv('BINANCE_API_SECRET')
        )

        # Place market order
        response = client.new_order(
            symbol='BTCUSDT',
            side=order_params['side'],
            type='MARKET',
            quantity=f"{order_params['quantity']:.3f}"
        )

        print(f"   ✓ Order placed: {response['orderId']}")
        print(f"   Status: {response['status']}")

        return response

    except Exception as e:
        print(f"   ✗ Order failed: {str(e)}")
        raise


def main():
    parser = argparse.ArgumentParser(description='Manual order testing script')
    parser.add_argument('--dry-run', action='store_true', help='Simulate order without placing it')
    parser.add_argument('--testnet', action='store_true', help='Use Binance Testnet')
    parser.add_argument('--live-danger', action='store_true', help='Use LIVE exchange (dangerous!)')
    parser.add_argument('--signal', choices=['BUY', 'SELL', 'HOLD'], default='BUY', help='Force a specific signal')
    parser.add_argument('--confidence', choices=['LOW', 'MEDIUM', 'HIGH'], default='MEDIUM', help='Signal confidence')

    args = parser.parse_args()

    print_section("Manual Order Testing Script")

    print("⚠️  WARNING: This is a debugging tool for testing order logic")
    print("⚠️  For production testing, use:")
    print("   1. Unit tests (tests/test_strategy_components.py)")
    print("   2. Integration tests (tests/test_integration_mock.py)")
    print("   3. Binance Testnet for live API testing")
    print("   4. Small amounts on live exchange as final verification")

    if args.live_danger and not args.dry_run:
        print("\n" + "⚠️ "*20)
        print("YOU ARE ABOUT TO PLACE A REAL ORDER ON LIVE BINANCE EXCHANGE")
        print("⚠️ "*20)
        confirm = input("\nType 'I UNDERSTAND THE RISKS' to continue: ")
        if confirm != "I UNDERSTAND THE RISKS":
            print("Aborted.")
            return

    # Run test steps
    try:
        # Step 1: Test connection
        if not test_binance_connection(testnet=args.testnet or not args.live_danger):
            print("\n❌ Connection test failed. Aborting.")
            return

        # Step 2: Fetch market data
        market_data = fetch_live_market_data()
        if not market_data:
            print("\n❌ Failed to fetch market data. Aborting.")
            return

        # Step 3: Calculate indicators
        indicators = calculate_technical_indicators(market_data)

        # Step 4: Force AI signal
        ai_signal = force_ai_signal(args.signal, args.confidence)

        # Step 5: Calculate order parameters
        order_params = calculate_order_parameters(ai_signal, indicators)

        # Step 6: Submit order
        result = submit_test_order(
            order_params,
            dry_run=args.dry_run,
            testnet=args.testnet and not args.live_danger
        )

        print_section("Test Complete")
        print(f"✅ All steps completed successfully")
        print(f"\nResult: {json.dumps(result, indent=2)}")

    except Exception as e:
        print_section("Test Failed")
        print(f"❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
