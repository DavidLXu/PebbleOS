import { test } from 'node:test';
import assert from 'node:assert/strict';
import { PebbleInterpreter, PebbleError } from '../lang.js';

// Helper: run source, return collected output lines.
async function run(src, globals = {}, hostFns = {}) {
  const lines = [];
  const interp = new PebbleInterpreter({
    outputConsumer: (line) => lines.push(line),
    hostFunctions: hostFns,
  });
  await interp.execute(src, globals);
  return { lines, interp };
}

// Helper: assert a single output line equals expected value.
async function assertOutput(src, expected, globals = {}) {
  const { lines } = await run(src, globals);
  assert.equal(lines[0], String(expected));
}

// ── Print ─────────────────────────────────────────────────────────────────────

test('print string literal', async () => {
  await assertOutput('print "hello"', 'hello');
});

test('print integer', async () => {
  await assertOutput('print 42', '42');
});

test('print None', async () => {
  await assertOutput('print None', 'None');
});

test('print True and False', async () => {
  const { lines } = await run('print True\nprint False');
  assert.equal(lines[0], 'True');
  assert.equal(lines[1], 'False');
});

// ── Assignment & variables ────────────────────────────────────────────────────

test('assign and print variable', async () => {
  await assertOutput('x = 5\nprint x', '5');
});

test('assign computed value (the fixed m[3] bug)', async () => {
  await assertOutput('x = 1 + 2\nprint x', '3');
});

test('assign string concatenation result', async () => {
  await assertOutput('s = "hello" + " world"\nprint s', 'hello world');
});

test('reassign variable', async () => {
  await assertOutput('x = 1\nx = 2\nprint x', '2');
});

// ── Arithmetic ────────────────────────────────────────────────────────────────

test('addition', async () => { await assertOutput('print 2 + 3', '5'); });
test('subtraction', async () => { await assertOutput('print 10 - 4', '6'); });
test('multiplication', async () => { await assertOutput('print 3 * 4', '12'); });
test('true division', async () => { await assertOutput('print 10 / 4', '2.5'); });
test('int_div builtin', async () => { await assertOutput('print int_div(10, 3)', '3'); });
test('modulo', async () => { await assertOutput('print 10 % 3', '1'); });
test('unary negation', async () => { await assertOutput('x = -5\nprint x', '-5'); });

// ── Comparisons ───────────────────────────────────────────────────────────────

// Comparisons return 1 (true) or 0 (false) as integers, not booleans
test('greater than true', async () => { await assertOutput('print 3 > 2', '1'); });
test('greater than false', async () => { await assertOutput('print 2 > 3', '0'); });
test('less than', async () => { await assertOutput('print 1 < 2', '1'); });
test('equal', async () => { await assertOutput('print 2 == 2', '1'); });
test('not equal', async () => { await assertOutput('print 2 != 3', '1'); });
test('less than or equal', async () => { await assertOutput('print 3 <= 3', '1'); });
test('greater than or equal', async () => { await assertOutput('print 4 >= 5', '0'); });

// ── Boolean operators ─────────────────────────────────────────────────────────

test('and: both true', async () => { await assertOutput('print True and True', 'True'); });
test('and: one false', async () => { await assertOutput('print True and False', 'False'); });
test('or: one true', async () => { await assertOutput('print False or True', 'True'); });
test('or: both false', async () => { await assertOutput('print False or False', 'False'); });
test('not true', async () => { await assertOutput('print not True', 'False'); });
test('not false', async () => { await assertOutput('print not False', 'True'); });

// ── If / elif / else ──────────────────────────────────────────────────────────

test('if true branch', async () => {
  await assertOutput('if 1:\n    print "yes"\nelse:\n    print "no"', 'yes');
});

test('if false branch', async () => {
  await assertOutput('if 0:\n    print "yes"\nelse:\n    print "no"', 'no');
});

test('elif branch', async () => {
  const src = 'x = 2\nif x == 1:\n    print "one"\nelif x == 2:\n    print "two"\nelse:\n    print "other"';
  await assertOutput(src, 'two');
});

// ── While loop ────────────────────────────────────────────────────────────────

test('while loop counts up', async () => {
  const src = 'i = 0\nwhile i < 3:\n    print i\n    i = i + 1';
  const { lines } = await run(src);
  assert.deepEqual(lines, ['0', '1', '2']);
});

test('while with break', async () => {
  const src = 'i = 0\nwhile 1:\n    print i\n    i = i + 1\n    if i == 2:\n        break';
  const { lines } = await run(src);
  assert.deepEqual(lines, ['0', '1']);
});

test('while with continue', async () => {
  const src = 'i = 0\nwhile i < 4:\n    i = i + 1\n    if i == 2:\n        continue\n    print i';
  const { lines } = await run(src);
  assert.deepEqual(lines, ['1', '3', '4']);
});

// ── For loop ──────────────────────────────────────────────────────────────────

test('for over range(n)', async () => {
  const { lines } = await run('for i in range(3):\n    print i');
  assert.deepEqual(lines, ['0', '1', '2']);
});

test('for over range(start, stop)', async () => {
  const { lines } = await run('for i in range(1, 4):\n    print i');
  assert.deepEqual(lines, ['1', '2', '3']);
});

test('for over range(start, stop, step)', async () => {
  const { lines } = await run('for i in range(0, 10, 3):\n    print i');
  assert.deepEqual(lines, ['0', '3', '6', '9']);
});

test('for over list literal', async () => {
  const { lines } = await run('for x in [10, 20, 30]:\n    print x');
  assert.deepEqual(lines, ['10', '20', '30']);
});

test('for over string iterates chars', async () => {
  const { lines } = await run('for c in "abc":\n    print c');
  assert.deepEqual(lines, ['a', 'b', 'c']);
});

test('for over dict iterates keys', async () => {
  const src = 'keys = []\nfor k in {"a": 1, "b": 2}:\n    append(keys, k)\nprint len(keys)';
  await assertOutput(src, '2');
});

// ── Functions ─────────────────────────────────────────────────────────────────

test('def and call returning value', async () => {
  await assertOutput('def add(a, b):\n    return a + b\nprint add(3, 4)', '7');
});

test('function with no return returns 0', async () => {
  await assertOutput('def noop():\n    pass\nprint noop()', '0');
});

test('recursive function (factorial)', async () => {
  const src = 'def fact(n):\n    if n <= 1:\n        return 1\n    return n * fact(n - 1)\nprint fact(5)';
  await assertOutput(src, '120');
});

test('callGlobalFunction from JS', async () => {
  const interp = new PebbleInterpreter({});
  await interp.execute('def greet(name):\n    return "hello " + name');
  const result = await interp.callGlobalFunction('greet', ['world']);
  assert.equal(result, 'hello world');
});

// ── Lists ─────────────────────────────────────────────────────────────────────

test('list literal and index', async () => {
  await assertOutput('x = [10, 20, 30]\nprint x[1]', '20');
});

test('list append and len', async () => {
  await assertOutput('x = [1, 2]\nappend(x, 3)\nprint len(x)', '3');
});

test('nested list index', async () => {
  await assertOutput('x = [[1, 2], [3, 4]]\nprint x[1][0]', '3');
});

test('list index assignment', async () => {
  await assertOutput('x = [1, 2, 3]\nx[1] = 99\nprint x[1]', '99');
});

test('double-indexed assignment (dict-of-list)', async () => {
  await assertOutput('d = {"cells": [10, 20, 30]}\nd["cells"][1] = 99\nprint d["cells"][1]', '99');
});

test('triple-indexed assignment', async () => {
  const src = 'd = {"tasks": {1: {"status": "old"}}}\nd["tasks"][1]["status"] = "new"\nprint d["tasks"][1]["status"]';
  await assertOutput(src, 'new');
});

// ── Dicts ─────────────────────────────────────────────────────────────────────

test('dict literal and key lookup', async () => {
  await assertOutput('d = {"key": "val"}\nprint d["key"]', 'val');
});

test('dict key assignment', async () => {
  await assertOutput('d = {}\nd["x"] = 42\nprint d["x"]', '42');
});

test('keys() builtin', async () => {
  const src = 'd = {"a": 1, "b": 2}\nprint len(keys(d))';
  await assertOutput(src, '2');
});

// ── Strings ───────────────────────────────────────────────────────────────────

test('string index', async () => {
  await assertOutput('s = "hello"\nprint s[0]', 'h');
});

test('string len', async () => {
  await assertOutput('print len("hello")', '5');
});

test('string concatenation', async () => {
  await assertOutput('print "ab" + "cd"', 'abcd');
});

// ── Builtins ──────────────────────────────────────────────────────────────────

test('str() converts int to string', async () => {
  await assertOutput('print str(42)', '42');
});

test('int() converts string to int', async () => {
  await assertOutput('print int("5") + 1', '6');
});

test('float() converts string to float', async () => {
  await assertOutput('print float("3.14")', '3.14');
});

test('abs() positive', async () => { await assertOutput('print abs(5)', '5'); });
test('abs() negative', async () => { await assertOutput('print abs(-7)', '7'); });
test('min()', async () => { await assertOutput('print min(3, 1, 2)', '1'); });
test('max()', async () => { await assertOutput('print max(3, 1, 2)', '3'); });
test('pow()', async () => { await assertOutput('print pow(2, 10)', '1024'); });

test('sorted() returns sorted list', async () => {
  const src = 'x = sorted([3, 1, 2])\nprint x[0]';
  await assertOutput(src, '1');
});

test('type() returns type name string', async () => {
  await assertOutput('print type(42)', 'int');
  await assertOutput('print type("hi")', 'str');
  await assertOutput('print type([])', 'list');
  await assertOutput('print type({})', 'dict');
  await assertOutput('print type(None)', 'NoneType');
});

test('chr() and ord()', async () => {
  await assertOutput('print chr(65)', 'A');
  await assertOutput('print ord("A")', '65');
});

// ── try/except ────────────────────────────────────────────────────────────────

test('try/except catches raised error', async () => {
  const src = 'try:\n    raise "oops"\nexcept e:\n    print e';
  await assertOutput(src, 'oops');
});

test('try body executes normally when no error', async () => {
  const src = 'try:\n    print "ok"\nexcept e:\n    print "err"';
  await assertOutput(src, 'ok');
});

// ── Host functions ────────────────────────────────────────────────────────────

test('host function is callable from Pebble', async () => {
  const called = [];
  const hostFns = {
    my_host: (args) => { called.push(args[0]); return 0; },
  };
  await run('my_host("ping")', {}, hostFns);
  assert.deepEqual(called, ['ping']);
});

// ── Error cases ───────────────────────────────────────────────────────────────

test('undefined variable throws PebbleError', async () => {
  await assert.rejects(() => run('print undefined_var'), PebbleError);
});

test('raise throws PebbleError propagated to JS', async () => {
  await assert.rejects(() => run('raise "bad"'), PebbleError);
});

test('unknown function throws PebbleError', async () => {
  await assert.rejects(() => run('no_such_function()'), PebbleError);
});
