/**
 * @name Inline import statements
 * @description Import statements inside functions/methods should be moved to module level
 * @kind problem
 * @problem.severity recommendation
 * @id algvex/find-imports
 * @tags maintainability
 *       dependency
 */

import python

from ImportingStmt imp, string moduleName
where
  moduleName = imp.getAnImportedModuleName()
  // Only flag imports nested inside a function body (inline imports)
  // Module-level imports are intentional and should not be reported
  and exists(Function f | f.getBody().contains(imp))
select imp, "Inline import of module: " + moduleName + " — move to module level"
