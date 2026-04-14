/**
 * @name Hardcoded API keys or secrets
 * @description Detects potential hardcoded API keys, tokens, or secrets in code
 * @kind problem
 * @problem.severity error
 * @security-severity 9.0
 * @id algvex/hardcoded-secrets
 * @tags security
 *       external/cwe/cwe-798
 */

import python

from StringLiteral str
where
  exists(string value |
    value = str.getText() and
    (
      // Binance API key format (64-char alphanumeric)
      value.regexpMatch("^[A-Za-z0-9]{64}$")
      or
      // DeepSeek API key format (starts with 'sk-')
      value.regexpMatch("^sk-[A-Za-z0-9]{40,}$")
      or
      // Telegram bot token format (starts with digits, contains colon)
      value.regexpMatch("^[0-9]{8,10}:[A-Za-z0-9_-]{35}$")
      or
      // Long alphanumeric keys (20+ chars, not all lowercase to avoid false positives)
      (value.regexpMatch("^[A-Za-z0-9]{20,}$") and
       not value.regexpMatch("^[a-z]+$"))
    )
  )
  // Exclude test files, example configs, and Python cache
  and not str.getLocation().getFile().getRelativePath().matches("%test%")
  and not str.getLocation().getFile().getRelativePath().matches("%.example%")
  and not str.getLocation().getFile().getRelativePath().matches("%/__pycache__%")
select str, "Potential hardcoded secret detected (check if this is a real key or test data)"
