# Pebble Language

## Overview

`Pebble` is a small scripting language used by Pebble OS. It is line-based and
uses Python-style blocks with exactly four spaces per indentation level.

Pebble programs can run in two execution modes:

- `run FILE [ARGS...]`: interpreter mode
- `exec FILE [ARGS...]`: bytecode VM mode

Both modes support the same language features.

## Statements

Pebble currently supports:

- `name = expression`
- `items[index] = expression`
- `print expression`
- `import math`
- `import os`
- `import text`
- `import random`
- `import memory`
- `import heap`
- `pass`
- `break`
- `continue`
- `if expression:`
- `elif expression:`
- `else:`
- `while expression:`
- `for name in range(...):`
- `def name(arg1, arg2):`
- `return expression`

## Expressions

Pebble expressions support:

- integer literals
- float literals
- `True`, `False`, `None`
- string literals
- list literals
- dict literals
- variables
- function calls
- module-qualified calls like `math.sin(...)`
- indexing
- parentheses
- `+`
- `-`
- `*`
- `/`
- `<`
- `>`
- `==`
- `!=`
- `<=`
- `>=`
- `and`
- `or`
- `not`

## Syntax Rules

- Blocks must be indented by exactly four spaces
- Comparisons return `1` for true and `0` for false
- `True`, `False`, and `None` follow Python-style truthiness
- `dict` values support indexing and assignment with `data[key]`
- `if`, `elif`, and `else` use integer truthiness: `0` is false, nonzero is true
- `for` supports iterating over `range(...)`, list values, strings, and dict keys
- File I/O stays inside the Pebble OS filesystem root
- Relative paths resolve from the current working directory
- Absolute paths begin with `/`
- `system/...` is a mounted runtime subtree

## Builtins

Current builtins:

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

Pebble programs also receive:

- `ARGC` as the argument count
- `ARGV` as a list of argument strings
- `argv(i)` as a convenience builtin for fetching one argument

## Modules

Pebble supports both built-in modules and file-based user modules.

Current built-in modules:

- `math`: `abs`, `pow`, `sqrt`, `sin`, `cos`, `tan`
- `text`: `len`, `repeat`, `lines`, `join`, `first_line`
- `os`: `list`, `exists`, `read`, `write`, `delete`, `time`
- `random`: `seed`, `next`, `range`
- `memory`: `init`, `size`, `read`, `write`, `clear`, `fill`, `copy`, `slice`, `store`, `dump`, `alloc`, `top`
- `heap`: `init`, `capacity`, `used`, `count`, `alloc`, `kind`, `size`, `read`, `write`, `store`, `slice`

User modules can also be imported from Pebble files in the active filesystem.

Example:

```text
import mymodule
print mymodule.VALUE
print mymodule.twice(7)
```

This loads `mymodule.peb` and exposes its globals and functions through the
module object.

## Example

```text
import math

data = []
append(data, "peb")
append(data, "ble")
name = data[0] + data[1]

i = 0
while i < len(data):
    print data[i]
    i = i + 1

print math.abs(-7)
write_file("hello.txt", name)
print read_file("hello.txt")
```

## Notes

- The shell command `lang` prints an in-system summary of current syntax
- Trigonometric functions use degree input
- `sin`, `cos`, and `tan` currently return fixed-point integers scaled by `10000`
- More detailed memory behavior for `memory` and `heap` is documented in [MEMORY.md](/Users/xulixin/LX_OS/MEMORY.md)
- More detailed filesystem behavior is documented in [FILESYSTEM.md](/Users/xulixin/LX_OS/FILESYSTEM.md)
