# Python standards baseline

Existing violations at `e577b5cdd1aa86bd5313ba3d6eb2d80000ef8e56` are baselined by exact authored file and rule. New files receive all enabled checks. Remove an exception when repairing its source. The tool configuration owns enforcement; this document records the rollout evidence and rationale.

## Ruff

Reason: preserve existing implementations during the standards rollout. Each row records the existing count and a representative required repair. No wildcard path or whole-rule ignores were added.

| File | Rule | Existing count | Required repair |
| --- | --- | ---: | --- |
| `.agents/azure/utility/scaffold/scripts/scaffold.py` | `B904` | 1 | Within an `except` clause, raise exceptions with `raise ... from err` or `raise ... from None` to distinguish them from errors in exception handling |
| `.agents/azure/utility/scaffold/scripts/scaffold.py` | `BLE001` | 2 | Do not catch blind exception: `BaseException` |
| `.agents/azure/utility/scaffold/scripts/scaffold.py` | `C416` | 1 | Unnecessary list comprehension (rewrite using `list()`) |
| `.agents/azure/utility/scaffold/scripts/scaffold.py` | `E501` | 152 | Line too long (90 > 88) |
| `.agents/azure/utility/scaffold/scripts/scaffold.py` | `F841` | 1 | Local variable `name` is assigned to but never used |
| `.agents/azure/utility/scaffold/scripts/scaffold.py` | `I001` | 1 | Import block is un-sorted or un-formatted |
| `.agents/azure/utility/scaffold/scripts/scaffold.py` | `PTH100` | 1 | `os.path.abspath()` should be replaced by `Path.resolve()` |
| `.agents/azure/utility/scaffold/scripts/scaffold.py` | `RET504` | 1 | Unnecessary assignment to `text` before `return` statement |
| `.agents/azure/utility/scaffold/scripts/scaffold.py` | `SIM102` | 1 | Use a single `if` statement instead of nested `if` statements |
| `.agents/azure/utility/scaffold/scripts/scaffold.py` | `SIM103` | 1 | Return the negated condition directly |
| `.agents/tests/generate_for_lint.py` | `E501` | 5 | Line too long (104 > 88) |
| `.agents/tests/test_declared_dependencies.py` | `E501` | 4 | Line too long (113 > 88) |
| `.agents/tests/test_declared_dependencies.py` | `I001` | 1 | Import block is un-sorted or un-formatted |
| `.agents/tests/test_declared_dependencies.py` | `PT009` | 2 | Use a regular `assert` instead of unittest-style `assertTrue` |
| `.agents/tests/test_declared_dependencies.py` | `TID251` | 1 | `unittest.TestCase` is banned: Use plain pytest functions and bare assertions. |
| `.agents/tests/test_scaffold.py` | `B007` | 1 | Loop control variable `match` not used within loop body |
| `.agents/tests/test_scaffold.py` | `B023` | 14 | Function definition does not bind loop variable `original_open` |
| `.agents/tests/test_scaffold.py` | `E501` | 249 | Line too long (105 > 88) |
| `.agents/tests/test_scaffold.py` | `I001` | 1 | Import block is un-sorted or un-formatted |
| `.agents/tests/test_scaffold.py` | `PLR6301` | 4 | Method `scaffold` could be a function, class method, or static method |
| `.agents/tests/test_scaffold.py` | `PT009` | 453 | Use a regular `assert` instead of unittest-style `assertEqual` |
| `.agents/tests/test_scaffold.py` | `PTH108` | 1 | `os.unlink()` should be replaced by `Path.unlink()` |
| `.agents/tests/test_scaffold.py` | `RUF012` | 1 | Mutable default value for class attribute |
| `.agents/tests/test_scaffold.py` | `SIM117` | 4 | Use a single `with` statement with multiple contexts instead of nested `with` statements |
| `.agents/tests/test_scaffold.py` | `TID251` | 21 | `importlib.util.spec_from_file_location` is banned: Import the installed public API; do not load another checkout by path. |

## Ruff formatter

Exact existing files requiring formatting are excluded from the formatter during this rollout. Regenerate payloads after formatting authored source and then remove its exception. New files are checked.

- `.agents/azure/utility/scaffold/scripts/scaffold.py`
- `.agents/tests/generate_for_lint.py`
- `.agents/tests/test_declared_dependencies.py`
- `.agents/tests/test_scaffold.py`

## Mypy

Existing typing debt is baselined by exact module and diagnostic code, with its source file and count below. Add the missing annotations or fix the indicated type mismatch, then remove the corresponding module/code override. No wildcard modules or whole-module ignore was added.

| File | Module | Rule | Existing count | Required repair |
| --- | --- | --- | ---: | --- |
| `.agents/azure/utility/scaffold/scripts/scaffold.py` | `scaffold` | `arg-type` | 7 | Argument 1 to "workload_host" has incompatible type "Any \| None"; expected "str" |
| `.agents/azure/utility/scaffold/scripts/scaffold.py` | `scaffold` | `assignment` | 1 | Incompatible types in assignment (expression has type "BaseException", variable has type "Exception \| None") |
| `.agents/azure/utility/scaffold/scripts/scaffold.py` | `scaffold` | `attr-defined` | 1 | "object" has no attribute "__iter__"; maybe "__dir__" or "__str__"? (not iterable) |
| `.agents/azure/utility/scaffold/scripts/scaffold.py` | `scaffold` | `call-overload` | 1 | No overload variant of "fullmatch" matches argument types "Any", "object" |
| `.agents/azure/utility/scaffold/scripts/scaffold.py` | `scaffold` | `misc` | 2 | Assignment to variable "error" outside except: block |
| `.agents/azure/utility/scaffold/scripts/scaffold.py` | `scaffold` | `no-any-return` | 3 | Returning Any from function declared to return "dict[Any, Any]" |
| `.agents/azure/utility/scaffold/scripts/scaffold.py` | `scaffold` | `no-untyped-def` | 7 | Function is missing a return type annotation |
| `.agents/azure/utility/scaffold/scripts/scaffold.py` | `scaffold` | `return-value` | 1 | Incompatible return value type (got "Any \| None", expected "str") |
| `.agents/azure/utility/scaffold/scripts/scaffold.py` | `scaffold` | `type-arg` | 42 | Missing type arguments for generic type "dict" |
| `.agents/azure/utility/scaffold/scripts/scaffold.py` | `scaffold` | `unreachable` | 1 | Statement is unreachable |
| `.agents/tests/generate_for_lint.py` | `generate_for_lint` | `no-any-return` | 1 | Returning Any from function declared to return "dict[Any, Any]" |
| `.agents/tests/generate_for_lint.py` | `generate_for_lint` | `type-arg` | 2 | Missing type arguments for generic type "dict" |
| `.agents/tests/test_declared_dependencies.py` | `test_declared_dependencies` | `no-untyped-def` | 1 | Function is missing a return type annotation |
| `.agents/tests/test_scaffold.py` | `test_scaffold` | `arg-type` | 1 | Argument 1 to "module_from_spec" has incompatible type "ModuleSpec \| None"; expected "ModuleSpec" |
| `.agents/tests/test_scaffold.py` | `test_scaffold` | `attr-defined` | 5 | "type[TerraformGateTests]" has no attribute "cache_dir" |
| `.agents/tests/test_scaffold.py` | `test_scaffold` | `list-item` | 4 | List item 0 has incompatible type "str \| None"; expected "str \| bytes \| PathLike[str] \| PathLike[bytes]" |
| `.agents/tests/test_scaffold.py` | `test_scaffold` | `no-any-return` | 1 | Returning Any from function declared to return "dict[Any, Any]" |
| `.agents/tests/test_scaffold.py` | `test_scaffold` | `no-untyped-def` | 52 | Function is missing a type annotation for one or more parameters |
| `.agents/tests/test_scaffold.py` | `test_scaffold` | `type-arg` | 9 | Missing type arguments for generic type "dict" |
| `.agents/tests/test_scaffold.py` | `test_scaffold` | `union-attr` | 2 | Item "None" of "ModuleSpec \| None" has no attribute "loader" |
| `.agents/tests/test_scaffold.py` | `test_scaffold` | `var-annotated` | 6 | Need type annotation for "checked" (hint: "checked: list[<type>] = ...") |

## Custom checker

Reason: preserve exact pre-existing custom-checker findings during the standards rollout. Each entry maps to one authored path and rule; repair the stated design issue and remove its exception. No generated or vendored path is included.

| File | Rule | Existing count | Required repair |
| --- | --- | ---: | --- |
| .agents/tests/test_scaffold.py | PY016 | 10 | Inject process state or confine the read to an entry-point main. |
