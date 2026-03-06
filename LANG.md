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
- `obj.attr = expression`
- `print expression`
- `import math`
- `import os`
- `import text`
- `import random`
- `import memory`
- `import heap`
- `import numpy` (file module in default Pebble disk)
- `import torch` (file module in default Pebble disk)
- `import matplotlib` (file module in default Pebble disk)
- `pass`
- `break`
- `continue`
- `if expression:`
- `elif expression:`
- `else:`
- `while expression:`
- `for name in range(...):`
- `def name(arg1, arg2):`
- `class Name:`
- `return expression`
- `raise expression`
- `try: ... except:`
- `try: ... except err:`

## Expressions

Pebble expressions support:

- integer literals
- float literals
- `True`, `False`, `None`
- string literals
- list literals
- dict literals
- variables
- function values
- function calls
- module-qualified calls like `math.sin(...)`
- class/instance attribute calls like `obj.method(...)`
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
- Bracketed expressions inside `()`, `[]`, and `{}` may span multiple lines
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

User-defined functions can also be treated as first-class values in a bootstrap
form: they can be assigned to variables, passed around, and called indirectly.

## Modules

Pebble supports both built-in modules and file-based user modules.

Current built-in modules:

- `math`: `abs`, `pow`, `sqrt`, `sin`, `cos`, `tan`
- `text`: `len`, `repeat`, `lines`, `join`, `first_line`
- `os`: `list`, `exists`, `read`, `write`, `delete`, `time`
- `random`: `seed`, `next`, `range`
- `memory`: `init`, `size`, `read`, `write`, `clear`, `fill`, `copy`, `slice`, `store`, `dump`, `alloc`, `top`
- `heap`: `init`, `capacity`, `used`, `count`, `alloc`, `kind`, `size`, `read`, `write`, `store`, `slice`
- `numpy` (from `system/lib/numpy.peb`, importable as `import numpy`): `array`, `shape`, `ndim`, `size`, `tolist`, `zeros`, `ones`, `full`, `eye`, `reshape`, `transpose`, `add`, `sub`, `mul`, `div`, `dot`, `matmul`, `sum`
- `torch` (from `system/lib/torch.peb`, importable as `import torch`): `tensor`, `shape`, `ndim`, `size`, `tolist`, `zeros`, `ones`, `full`, `rand`, `randn`, `reshape`, `transpose`, `add`, `sub`, `mul`, `div`, `dot`, `matmul`, `sum`, `argmax`, `argmax_rows`, `one_hot`, `linear`, `mse_loss`, `mse_grad`, `mean_rows`, `sgd`
- `matplotlib` (from `system/lib/matplotlib.peb`, importable as `import matplotlib`): `render`, `plot`, `show`, `show_height` for simple text-based plotting

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

def add_one(x):
    return x + 1

fn = add_one
print fn(4)

try:
    print missing_name
except:
    print "recovered"

try:
    raise "boom"
except:
    print "raised and handled"

try:
    raise "disk full"
except err:
    print err
```

## Notes

- The shell command `lang` prints an in-system summary of current syntax
- `try/except` currently supports `except:` and `except err:` but still has no `finally`
- `raise expression` currently raises a plain Pebble runtime error using the stringified value
- first-class functions currently cover user-defined functions and are intended
  to support bootstrap APIs like `thread_spawn(func, args)`
- classes now support class bodies, methods, constructors via `__init__`, and bound method values
- Trigonometric functions use degree input
- `sin`, `cos`, and `tan` currently return fixed-point integers scaled by `10000`
- More detailed memory behavior for `memory` and `heap` is documented in [MEMORY.md](MEMORY.md)
- More detailed filesystem behavior is documented in [FILESYSTEM.md](FILESYSTEM.md)
