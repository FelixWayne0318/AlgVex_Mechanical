"""
Binance Enum Patches for NautilusTrader

This module patches NautilusTrader's Binance enums to handle unknown values
that Binance may add in the future. This prevents msgspec.ValidationError
when Binance introduces new filter types or other enum values.

Usage:
    # Must be called BEFORE importing any NautilusTrader modules
    from patches.binance_enums import apply_binance_enum_patches
    apply_binance_enum_patches()

Why this is needed:
    - Binance frequently adds new filter types (e.g., POSITION_RISK_CONTROL)
    - NautilusTrader 1.202.0 doesn't include these new values
    - msgspec raises ValidationError for unknown enum values
    - The _missing_ hook is the official msgspec-recommended solution

Reference:
    https://github.com/jcrist/msgspec/issues/531
"""

import logging

logger = logging.getLogger(__name__)

# Track already-warned values to avoid log spam
_warned_unknown_values: set = set()


def apply_binance_enum_patches() -> bool:
    """
    Apply patches to Binance enums to handle unknown values gracefully.

    This function adds a _missing_ classmethod to BinanceSymbolFilterType
    that dynamically creates enum members for unknown values instead of
    raising a ValidationError.

    Returns
    -------
    bool
        True if patches were applied successfully, False otherwise.

    Notes
    -----
    This must be called BEFORE importing any NautilusTrader adapter modules,
    as the enum classes are used during module initialization.
    """
    try:
        from nautilus_trader.adapters.binance.common.enums import BinanceSymbolFilterType

        # Check if already patched
        if hasattr(BinanceSymbolFilterType, '_nautilus_patched'):
            logger.debug("BinanceSymbolFilterType already patched, skipping")
            return True

        # Define the _missing_ classmethod
        @classmethod
        def _missing_(cls, value):
            """
            Handle unknown enum values by dynamically creating new members.

            This is called by msgspec when it encounters an enum value that
            doesn't match any defined member. Instead of raising an error,
            we create a pseudo-member that can be used safely.

            Parameters
            ----------
            value : str
                The unknown enum value string

            Returns
            -------
            BinanceSymbolFilterType
                A new enum member for the unknown value
            """
            # Check if already cached (should not reach here if cached, but double-check)
            if value in cls._value2member_map_:
                return cls._value2member_map_[value]

            # Log warning only once per unique unknown value
            if value not in _warned_unknown_values:
                _warned_unknown_values.add(value)
                logger.warning(
                    f"Unknown BinanceSymbolFilterType value encountered: '{value}'. "
                    f"Creating dynamic member. Consider updating NautilusTrader."
                )

            # Create a pseudo-member for the unknown value
            # This approach creates a new enum member dynamically
            pseudo_member = object.__new__(cls)
            pseudo_member._value_ = value
            pseudo_member._name_ = value  # Use value as name

            # Cache it to avoid recreating for the same value
            cls._value2member_map_[value] = pseudo_member

            return pseudo_member

        # Apply the patch
        BinanceSymbolFilterType._missing_ = _missing_

        # Mark as patched to avoid double-patching
        BinanceSymbolFilterType._nautilus_patched = True

        logger.info(
            "✅ Patched BinanceSymbolFilterType with _missing_ hook "
            "(handles unknown filter types like POSITION_RISK_CONTROL)"
        )

        return True

    except ImportError as e:
        logger.error(f"Failed to import BinanceSymbolFilterType: {e}")
        return False
    except Exception as e:
        logger.error(f"Failed to patch BinanceSymbolFilterType: {e}")
        return False


def apply_all_patches() -> bool:
    """
    Apply all Binance-related patches.

    Returns
    -------
    bool
        True if all patches were applied successfully.

    IMPORTANT: Order matters!
    1. First apply aiohttp patch (BEFORE any NautilusTrader imports)
    2. Then apply enum patch (which imports NautilusTrader)
    """
    print("[PATCH] apply_all_patches() starting...", flush=True)
    success = True

    # =========================================================================
    # STEP 1: Apply aiohttp position filter patch FIRST
    # This MUST happen before NautilusTrader imports aiohttp
    # =========================================================================
    try:
        print("[PATCH] Step 1: Importing binance_positions module...", flush=True)
        from patches.binance_positions import apply_http_response_filter, AIOHTTP_AVAILABLE

        if not AIOHTTP_AVAILABLE:
            print("[PATCH] ERROR: aiohttp is not installed!", flush=True)
            logger.error(
                "❌ CRITICAL: aiohttp is not installed!\n"
                "   This will cause crashes if Binance returns non-ASCII symbols.\n"
                "   Install with: pip install aiohttp\n"
                "   Then restart the service."
            )
        elif not apply_http_response_filter():
            print("[PATCH] WARNING: Position filter patch not applied", flush=True)
            logger.warning("Position filter patch not applied")
            success = False
        else:
            print("[PATCH] ✅ Position filter patch applied successfully", flush=True)
            logger.info("✅ Position filter patch applied successfully")

    except ImportError as e:
        print(f"[PATCH] ERROR: Could not import position filter patch: {e}", flush=True)
        import traceback
        traceback.print_exc()
        logger.warning(f"Could not import position filter patch: {e}")
        success = False

    # =========================================================================
    # STEP 2: Apply BinanceSymbolFilterType patch
    # This imports NautilusTrader, so must come AFTER aiohttp patch
    # =========================================================================
    print("[PATCH] Step 2: Applying BinanceSymbolFilterType patch...", flush=True)
    if not apply_binance_enum_patches():
        success = False

    print(f"[PATCH] apply_all_patches() completed, success={success}", flush=True)
    return success


if __name__ == "__main__":
    # Test the patch
    logging.basicConfig(level=logging.DEBUG)

    print("Testing Binance enum patches...")

    # Apply patches
    if apply_all_patches():
        print("✅ Patches applied successfully")

        # Test with a known value
        from nautilus_trader.adapters.binance.common.enums import BinanceSymbolFilterType

        # Test existing value
        price_filter = BinanceSymbolFilterType("PRICE_FILTER")
        print(f"Existing value: {price_filter}")

        # Test unknown value (simulating POSITION_RISK_CONTROL)
        unknown = BinanceSymbolFilterType("POSITION_RISK_CONTROL")
        print(f"Unknown value (dynamic): {unknown}")
        print(f"Unknown value name: {unknown.name}")
        print(f"Unknown value value: {unknown.value}")

        print("✅ All tests passed!")
    else:
        print("❌ Failed to apply patches")
