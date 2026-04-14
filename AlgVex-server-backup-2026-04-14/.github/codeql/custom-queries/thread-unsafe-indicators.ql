/**
 * @name Thread-unsafe Rust indicator import
 * @description Detects imports from nautilus_trader.core.nautilus_pyo3 which are not thread-safe
 * @kind problem
 * @problem.severity error
 * @id algvex/thread-unsafe-indicators
 * @tags correctness
 *       concurrency
 */

import python

from ImportMember imp
where
  // Check the module expression as a string (e.g., "nautilus_trader.core.nautilus_pyo3")
  imp.getModule().toString() = "nautilus_trader.core.nautilus_pyo3"
  and
  // Common indicator names that are not thread-safe
  imp.getName().regexpMatch("(?i).*(RSI|MACD|SMA|EMA|BollingerBands|ATR|ADX).*")
select imp, "Thread-unsafe Rust indicator import: " + imp.getName() +
  " - Use nautilus_trader.indicators (Cython) instead for thread safety"
