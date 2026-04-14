/**
 * @name Hardcoded configuration values
 * @description Detects hardcoded values that should be managed by ConfigManager
 * @kind problem
 * @problem.severity warning
 * @id algvex/config-bypass
 * @tags maintainability
 *       configuration
 */

import python

from AssignStmt assign, Name target, Num value
where
  target = assign.getTarget(0) and
  value = assign.getValue() and
  // Common config parameter names that should not be hardcoded
  (
    target.getId().regexpMatch("(?i).*(timeout|interval|delay|retry|threshold|ratio|percent|pct|leverage|equity|amount|size|limit).*")
  )
  and
  // Numeric values that look like config (not 0, 1, -1 which are often constants)
  value.getN().toFloat() > 1.0
  // Exclude test files
  and not assign.getLocation().getFile().getRelativePath().matches("%test%")
  // Exclude __init__ methods (often default values)
  and not assign.getScope().(Function).getName() = "__init__"
select assign, "Hardcoded config value '" + target.getId() + " = " + value.getN() + "' - consider using ConfigManager"
