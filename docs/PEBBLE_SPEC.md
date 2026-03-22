# Pebble Language Specification

## Status

This document is the phase-1 baseline language contract for PebbleOS.

It is intentionally conservative. It describes the Pebble language as it is
implemented today in [`pebble_bootloader/lang.py`](../pebble_bootloader/lang.py),
not an aspirational future version. When the implementation and this document
disagree, treat that as a bug or a spec gap to resolve explicitly.

The main rule for the runtime is simple:

- `run FILE` and `exec FILE` are expected to implement the same language.
- Differences between interpreter mode and bytecode mode are bugs.

## Source Form

Pebble source is line-oriented and indentation-sensitive.

- Tabs are not allowed.
- Indentation must use multiples of four spaces.
- A block header (`if`, `while`, `for`, `def`, `class`, `try`, `except`, `else`, `elif`) must be followed by a block indented by exactly one additional level.
- Blank lines are ignored.
- Comments start with `#` and run to the end of the line, unless the `#` appears inside a quoted string.
- Bracketed expressions inside `()`, `[]`, and `{}` may span multiple lines. These lines are merged into one logical expression before parsing.

## Execution Model

Pebble currently has two execution modes:

- interpreter mode (`run`)
- bytecode VM mode (`exec`)

Both modes parse the same source language and are expected to produce the same:

- printed output
- return values visible to Pebble code
- runtime errors

## Statements

Pebble currently supports these top-level and block statements:

- assignment: `name = expression`
- indexed assignment: `items[index] = expression`
- attribute assignment: `obj.attr = expression`
- print statement: `print expression`
- import statement: `import module` or `import package.module`
- `pass`
- `break`
- `continue`
- `if expression:`
- `elif expression:`
- `else:`
- `while expression:`
- `for name in expression:`
- function definition: `def name(arg1, arg2):`
- class definition: `class Name:`
- `return expression`
- `raise expression`
- `try: ... except:`
- `try: ... except err:`

Current notable limits:

- `print` is a statement, not a function.
- `except` must match a preceding `try` at the same indentation level.
- `finally` is not supported.
- `with`, `lambda`, `yield`, `match`, decorators, and comprehensions are not supported.

## Expressions

Pebble expressions are parsed from a Pebble-owned AST shape but currently use a
Python-expression frontend to recognize syntax. Supported forms are:

- integer literals
- float literals
- string literals
- `True`, `False`, `None`
- names
- unary `-`
- unary `not`
- binary `+`, `-`, `*`, `/`
- comparisons: `<`, `>`, `==`, `!=`, `<=`, `>=`
- boolean `and`, `or`
- function calls: `name(...)`
- attribute calls: `value.attr(...)`
- attribute access: `value.attr`
- list literals
- dict literals
- indexing: `value[index]`
- parenthesized expressions

Current notable limits:

- chained comparisons are not supported
- slicing is not supported
- tuple literals are not supported
- keyword arguments are not supported
- only simple calls and attribute calls are supported

## Values

Pebble currently works with these value categories:

- integers
- floats
- booleans
- `None`
- strings
- lists
- dicts
- module objects
- class objects
- class instances
- function values
- bound method values

### Truthiness

The following values are false:

- numeric zero (`0`, `0.0`, and equivalent values)
- `False`
- `None`
- `""`
- `[]`
- `{}`

Everything else is true.

### Comparison and Boolean Results

- comparison operators return integer `1` or `0`
- `not` returns a boolean value
- `and` and `or` short-circuit and return operand values, not normalized booleans

### Mutation and Binding

Current binding semantics are intentionally simple:

- assigning a list or dict stores a cloned copy
- passing a list or dict into a user-defined function binds a cloned copy
- assigning scalars stores the scalar value
- module objects, class objects, instances, function values, and bound methods are reference-like and are not cloned

This means Pebble currently behaves more like value semantics for list/dict data
than Python does.

## Operators

Current operator rules:

- `+` accepts number+number, string+string, or list+list
- `-`, `*`, `/` require numeric operands
- division by zero raises a runtime error
- `/` always uses Pebble's numeric division behavior and may produce floats

## Indexing and Attribute Access

Current indexing rules:

- strings, lists, and dicts can be indexed
- dict indexing uses the provided key
- list and string indices must be integers
- instance indexing is allowed only with string keys and maps to instance fields
- out-of-range list or string access raises a runtime error
- missing dict keys raise a runtime error

Current attribute rules:

- module, class, and instance objects support attribute access
- instance method lookup may return a bound method value
- assigning to `obj.attr` requires a module, class, or instance target

## Functions and Classes

### Functions

- user-defined functions are introduced with `def`
- arity must match exactly
- if a function reaches the end without `return`, it returns `0`
- user-defined functions can be stored in variables and called indirectly

### Classes

- class bodies may contain value assignments and method definitions
- calling a class instantiates it
- if `__init__` exists, it is invoked on construction
- bound methods capture the target instance

## Imports

Pebble supports:

- built-in modules such as `math`, `text`, `os`, `random`, `memory`, and `heap`
- file-based modules loaded from the active Pebble filesystem
- top-level imports that resolve to `system/lib/...` for selected built-in userland libraries such as `numpy`, `torch`, and `matplotlib`

Nested imports such as `import pkg.ops` bind a module tree rooted at `pkg`.

## Builtins

Current core builtins:

- `len`
- `append`
- `range`
- `read_file`
- `write_file`
- `str`
- `int`
- `float`
- `input`
- `argv`
- `keys`

Additional builtins may be provided by the host runtime through the Pebble ABI.

## Errors

Pebble reports both parse and runtime failures as `PebbleError` values in the
host implementation. User-visible error strings are line-oriented and normally
include the source line number.

Current error-contract goals:

- parse failures should identify the failing source line
- runtime failures should identify the failing source line
- interpreter mode and bytecode mode should raise the same error text for the same program

## Conformance Direction

This document is meant to be enforced by tests. New language features should be
added only alongside:

- spec updates here
- interpreter-mode coverage
- bytecode-mode coverage
- parity tests that assert both modes behave the same
