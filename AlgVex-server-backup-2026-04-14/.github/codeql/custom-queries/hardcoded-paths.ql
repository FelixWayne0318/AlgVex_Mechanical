/**
 * @name Hardcoded file paths
 * @description Detects hardcoded file paths that may break after file reorganization
 * @kind problem
 * @problem.severity warning
 * @id algvex/hardcoded-paths
 * @tags maintainability
 *       refactoring
 */

import python

from StringLiteral str
where
  // Match common Python file patterns
  str.getText().regexpMatch(".*\\.(py|yaml|yml|json|md)$")
  and
  // Exclude test files and documentation
  not str.getLocation().getFile().getRelativePath().matches("%test%")
  and
  // Must contain a path separator
  (str.getText().matches("%/%") or str.getText().matches("%\\\\%"))
select str, "Hardcoded file path: " + str.getText() + " - consider using Path or config"
