# Pebble OS Memory

## Overview

Pebble OS now has a Pebble-managed memory layer. This is separate from Python's
real process memory.

Python still hosts the interpreter and VM process, but Pebble now exposes its
own logical runtime memory model through modules implemented in
[`system/runtime.peb`](pebble_system/runtime.peb).

The current memory stack has three parts:

- `memory`: flat virtual RAM cells
- `heap`: Pebble-managed object allocation on top of that RAM
- `exec` VM state: an explicit bytecode value stack and frame stack

This means Pebble now has a clearer separation between:

- language-visible runtime memory APIs
- VM execution state
- Python's hidden host process memory

## `memory` Module

`import memory` gives Pebble a flat array of runtime-managed cells.

Current API:

- `memory.init(size)`
- `memory.size()`
- `memory.read(index)`
- `memory.write(index, value)`
- `memory.clear()`
- `memory.fill(value)`
- `memory.copy(src, dst, count)`
- `memory.slice(base, count)`
- `memory.store(base, items)`
- `memory.dump()`
- `memory.alloc(count)`
- `memory.top()`

Example:

```text
import memory
memory.init(8)
memory.store(0, [1, 2, 3])
print memory.slice(0, 3)
```

This is Pebble's first explicit RAM abstraction. It is not hardware RAM and not
an OS page system, but it gives Pebble programs a stable, address-like region
to manipulate directly.

## `heap` Module

`import heap` builds a simple Pebble-native allocator on top of `memory`.

Current API:

- `heap.init(size)`
- `heap.capacity()`
- `heap.used()`
- `heap.count()`
- `heap.alloc(kind, payload_size)`
- `heap.kind(ptr)`
- `heap.size(ptr)`
- `heap.read(ptr, offset)`
- `heap.write(ptr, offset, value)`
- `heap.store(ptr, items)`
- `heap.slice(ptr)`

The current heap is a simple arena allocator:

- each object starts with a small header
- header cell `0`: object kind
- header cell `1`: payload size
- remaining cells: payload

Example:

```text
import heap
heap.init(12)
obj = heap.alloc("pair", 2)
heap.store(obj, [7, 9])
print heap.kind(obj)
print heap.slice(obj)
```

This is intentionally simple:

- no free list
- no garbage collector
- no compaction

The point is to give Pebble a real allocation layer that it controls.

## Bytecode VM Memory Direction

`exec` mode now uses a more explicit VM state in
[`lang.py`](pebble_bootloader/lang.py):

- a `value_stack`
- a `frame_stack`
- frame records with function name, locals, and call-site line number

That is still implemented in Python, but it is a clearer VM-style execution
model than relying only on ad hoc local Python temporaries. It is the first
step toward a more Pebble-defined runtime.

## What Is Still Python-Hosted

Pebble has not removed Python from the bottom of the stack. Python still owns:

- the real host process memory
- interpreter implementation
- bytecode VM implementation
- terminal bridge
- filesystem bridge

So the current goal is semantic independence, not physical independence.

Pebble is moving toward:

- Pebble-defined logical RAM
- Pebble-defined heap/object allocation
- Pebble-defined VM execution structures

rather than immediate full removal of Python hosting.

## Why This Matters

This memory layer makes future work possible:

- Pebble-managed object layouts
- slot-based locals in the VM
- runtime data structures that are not just plain Python objects
- task/process-style memory regions
- future GC or allocator experiments

It is an architectural step toward Pebble owning more of its own runtime model.
