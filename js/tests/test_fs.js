import { test } from 'node:test';
import assert from 'node:assert/strict';
import { PebbleFS, PebbleFSError } from '../fs.js';

function makeFS() {
  return new PebbleFS();
}

// ── File operations ───────────────────────────────────────────────────────────

test('fileExists returns false for nonexistent file', () => {
  const fs = makeFS();
  assert.equal(fs.fileExists('hello.txt'), false);
});

test('createFile then fileExists returns true', () => {
  const fs = makeFS();
  fs.createFile('hello.txt', 'content');
  assert.equal(fs.fileExists('hello.txt'), true);
});

test('readFile returns stored content', () => {
  const fs = makeFS();
  fs.createFile('note.txt', 'hello world');
  assert.equal(fs.readFile('note.txt'), 'hello world');
});

test('writeFile updates content', () => {
  const fs = makeFS();
  fs.createFile('note.txt', 'old');
  fs.writeFile('note.txt', 'new');
  assert.equal(fs.readFile('note.txt'), 'new');
});

test('modifyFile updates content', () => {
  const fs = makeFS();
  fs.createFile('note.txt', 'old');
  fs.modifyFile('note.txt', 'updated');
  assert.equal(fs.readFile('note.txt'), 'updated');
});

test('deleteFile removes the file', () => {
  const fs = makeFS();
  fs.createFile('note.txt', 'data');
  fs.deleteFile('note.txt');
  assert.equal(fs.fileExists('note.txt'), false);
});

test('createFile on existing file throws PebbleFSError', () => {
  const fs = makeFS();
  fs.createFile('note.txt', 'data');
  assert.throws(() => fs.createFile('note.txt', 'data2'), PebbleFSError);
});

test('readFile on nonexistent file throws PebbleFSError', () => {
  const fs = makeFS();
  assert.throws(() => fs.readFile('missing.txt'), PebbleFSError);
});

test('modifyFile on nonexistent file throws PebbleFSError', () => {
  const fs = makeFS();
  assert.throws(() => fs.modifyFile('missing.txt', 'x'), PebbleFSError);
});

test('deleteFile on nonexistent file throws PebbleFSError', () => {
  const fs = makeFS();
  assert.throws(() => fs.deleteFile('missing.txt'), PebbleFSError);
});

test('fileTime returns a string for existing file', () => {
  const fs = makeFS();
  fs.createFile('note.txt', 'data');
  assert.equal(typeof fs.fileTime('note.txt'), 'string');
});

// ── Directory operations ──────────────────────────────────────────────────────

test('dirExists returns true for root', () => {
  const fs = makeFS();
  assert.equal(fs.dirExists(''), true);
  assert.equal(fs.dirExists('/'), true);
});

test('dirExists returns false for nonexistent dir', () => {
  const fs = makeFS();
  assert.equal(fs.dirExists('mydir'), false);
});

test('createDir then dirExists returns true', () => {
  const fs = makeFS();
  fs.createDir('mydir');
  assert.equal(fs.dirExists('mydir'), true);
});

test('createDir on existing dir throws PebbleFSError', () => {
  const fs = makeFS();
  fs.createDir('mydir');
  assert.throws(() => fs.createDir('mydir'), PebbleFSError);
});

test('dirEmpty returns true for empty dir', () => {
  const fs = makeFS();
  fs.createDir('emptydir');
  assert.equal(fs.dirEmpty('emptydir'), true);
});

test('dirEmpty returns false after file created inside', () => {
  const fs = makeFS();
  fs.createDir('mydir');
  fs.createFile('mydir/file.txt', 'data');
  assert.equal(fs.dirEmpty('mydir'), false);
});

test('removeDir removes directory', () => {
  const fs = makeFS();
  fs.createDir('mydir');
  fs.removeDir('mydir');
  assert.equal(fs.dirExists('mydir'), false);
});

test('storeFile auto-creates parent directories', () => {
  const fs = makeFS();
  fs.createFile('a/b/c.txt', 'data');
  assert.equal(fs.dirExists('a'), true);
  assert.equal(fs.dirExists('a/b'), true);
  assert.equal(fs.fileExists('a/b/c.txt'), true);
});

// ── Listing ───────────────────────────────────────────────────────────────────

test('listFiles returns all logical paths', () => {
  const fs = makeFS();
  fs.createFile('dir/a.txt', '1');
  fs.createFile('dir/b.txt', '2');
  const files = fs.listFiles();
  assert.deepEqual(files.sort(), ['dir/a.txt', 'dir/b.txt']);
});

test('listDir returns names relative to directory', () => {
  const fs = makeFS();
  fs.createFile('dir/a.txt', '1');
  fs.createFile('dir/b.txt', '2');
  const names = fs.listDir('dir');
  assert.deepEqual(names.sort(), ['a.txt', 'b.txt']);
});

test('listFiles returns empty array for empty dir', () => {
  const fs = makeFS();
  fs.createDir('emptydir');
  assert.deepEqual(fs.listFiles('emptydir'), []);
});

test('fileCount counts stored files', () => {
  const fs = makeFS();
  fs.createFile('a.txt', 'hello');
  fs.createFile('b.txt', 'world');
  assert.equal(fs.fileCount(), 2);
});

test('totalBytes sums content lengths', () => {
  const fs = makeFS();
  fs.createFile('a.txt', 'hello');   // 5 bytes
  fs.createFile('b.txt', 'world!!'); // 7 bytes
  assert.equal(fs.totalBytes(), 12);
});

// ── Path resolution ───────────────────────────────────────────────────────────

test('resolve absolute path strips leading slash', () => {
  const fs = makeFS();
  assert.equal(fs.resolve('/', '/anything'), '');
  assert.equal(fs.resolve('/etc', '/'), 'etc');
  assert.equal(fs.resolve('/a/b/c', '/x/y'), 'a/b/c');
});

test('resolve relative path appends to cwd', () => {
  const fs = makeFS();
  assert.equal(fs.resolve('home', '/'), 'home');
  assert.equal(fs.resolve('b', '/a'), 'a/b');
});

test('resolve .. goes up one level', () => {
  const fs = makeFS();
  assert.equal(fs.resolve('..', '/a/b'), 'a');
  assert.equal(fs.resolve('../c', '/a/b'), 'a/c');
});
