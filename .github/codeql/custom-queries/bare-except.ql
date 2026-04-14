/**
 * @name Bare except clause
 * @description Bare except catches all exceptions including KeyboardInterrupt and SystemExit
 * @kind problem
 * @problem.severity warning
 * @id algvex/bare-except
 * @tags correctness
 *       maintainability
 */

import python

from ExceptStmt except
where
  // Bare except has no type specified
  not exists(except.getType())
  // Exclude test files
  and not except.getLocation().getFile().getRelativePath().matches("%test%")
select except, "Bare 'except:' clause - use 'except Exception:' to avoid catching KeyboardInterrupt/SystemExit"
