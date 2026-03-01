// PebbleOS Browser Entry Point
// Initialises xterm.js, loads the filesystem, boots the Pebble runtime,
// and runs an interactive shell loop.

import { PebbleFS } from './fs.js';
import { PebbleInterpreter, PebbleError } from './lang.js';
import { createHostFunctions } from './host.js';

// ── All pebble_system/ files to preload ───────────────────────────────────────
// These are served as static assets from GitHub Pages.
const SYSTEM_FILES = [
  'runtime.peb',
  'shell.peb',
  'nano.peb',
  'bin/bash.peb',
  'bin/cat.peb',
  'bin/cp.peb',
  'bin/echo.peb',
  'bin/edit.peb',
  'bin/env.peb',
  'bin/find.peb',
  'bin/grep.peb',
  'bin/head.peb',
  'bin/htop.peb',
  'bin/kill.peb',
  'bin/lang.peb',
  'bin/ls.peb',
  'bin/mkdir.peb',
  'bin/mv.peb',
  'bin/pebble.peb',
  'bin/pwd.peb',
  'bin/rm.peb',
  'bin/rmdir.peb',
  'bin/sh.peb',
  'bin/sync.peb',
  'bin/tail.peb',
  'bin/time.peb',
  'bin/top.peb',
  'bin/touch.peb',
  'bin/tree.peb',
  'bin/tty.peb',
  'bin/wc.peb',
  'bin/which.peb',
  'kernel/mutex.peb',
  'kernel/proc.peb',
  'kernel/syscall.peb',
  'kernel/term.peb',
  'kernel/thread.peb',
  'lib/base.peb',
  'lib/cli.peb',
  'lib/path.peb',
];

// ── Pebble disk files to preload as initial user filesystem ──────────────────
// Only plain text files; skip the VFS database.
const DISK_FILES = [
  'etc/profile',
  'etc/passwd',
  'etc/group',
  'etc/fstab',
  'docs/readme.peb',
  'append_demo.txt',
  'merge_demo.txt',
];

// ── Terminal readline helpers ─────────────────────────────────────────────────

let _term = null;

// Queue for raw key data from xterm.js
const _keyQueue = [];
const _keyWaiters = [];

function _enqueueKey(data) {
  if (_keyWaiters.length > 0) {
    const resolve = _keyWaiters.shift();
    resolve(data);
  } else {
    _keyQueue.push(data);
  }
}

// Read a single raw key (or escape sequence) from the terminal.
// timeoutMs: if set, resolves with '' after that many ms.
function readKey(timeoutMs = null) {
  if (_keyQueue.length > 0) return Promise.resolve(_keyQueue.shift());
  return new Promise((resolve) => {
    if (timeoutMs !== null && timeoutMs > 0) {
      const timer = setTimeout(() => {
        const idx = _keyWaiters.indexOf(resolve);
        if (idx !== -1) _keyWaiters.splice(idx, 1);
        resolve('');
      }, timeoutMs);
      const wrappedResolve = (data) => { clearTimeout(timer); resolve(data); };
      _keyWaiters.push(wrappedResolve);
    } else {
      _keyWaiters.push(resolve);
    }
  });
}

// Read a full line from the terminal (echoing input, handling backspace).
async function readLine(prompt) {
  if (prompt) _term.write(prompt);
  let line = '';
  let cursor = 0;

  while (true) {
    const data = await readKey();

    if (data === '\r' || data === '\n') {
      _term.write('\r\n');
      return line;
    }

    if (data === '\x03') {  // Ctrl-C
      _term.write('^C\r\n');
      return '';
    }

    if (data === '\x04') {  // Ctrl-D (EOF)
      _term.write('\r\n');
      return null;
    }

    if (data === '\x7f' || data === '\b') {  // Backspace
      if (cursor > 0) {
        line = line.slice(0, cursor - 1) + line.slice(cursor);
        cursor--;
        // Redraw: move back, erase to end, rewrite tail, move back
        _term.write('\b \b');
        if (cursor < line.length) {
          const tail = line.slice(cursor);
          _term.write(tail + ' ');
          _term.write('\x1b[' + (tail.length + 1) + 'D');
        }
      }
      continue;
    }

    if (data === '\x1b[D') {  // Left arrow
      if (cursor > 0) { cursor--; _term.write('\x1b[D'); }
      continue;
    }
    if (data === '\x1b[C') {  // Right arrow
      if (cursor < line.length) { cursor++; _term.write('\x1b[C'); }
      continue;
    }
    if (data === '\x1b[A' || data === '\x1b[B') {
      // Up/Down arrow — history not implemented; ignore
      continue;
    }
    if (data.startsWith('\x1b')) {
      // Other escape sequences — ignore
      continue;
    }

    // Regular printable characters
    if (data.length === 1 && data.charCodeAt(0) >= 32) {
      line = line.slice(0, cursor) + data + line.slice(cursor);
      cursor++;
      if (cursor === line.length) {
        _term.write(data);
      } else {
        // Insert mode: redraw from cursor
        const tail = line.slice(cursor - 1);
        _term.write(tail);
        _term.write('\x1b[' + (tail.length - 1) + 'D');
      }
    }
  }
}

// ── Shell command loop helpers ────────────────────────────────────────────────

// Minimal shell argument splitter (handles quoted strings).
function splitShellLine(line) {
  const parts = [];
  let current = '';
  let inSingle = false;
  let inDouble = false;

  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === "'" && !inDouble) { inSingle = !inSingle; continue; }
    if (ch === '"' && !inSingle) { inDouble = !inDouble; continue; }
    if (ch === ' ' && !inSingle && !inDouble) {
      if (current.length > 0) { parts.push(current); current = ''; }
      continue;
    }
    current += ch;
  }
  if (current.length > 0) parts.push(current);
  return parts;
}

// ── Boot ──────────────────────────────────────────────────────────────────────

async function main() {
  // 1. Set up xterm.js terminal
  const term = new Terminal({
    cursorBlink: true,
    fontFamily: '"Fira Code", "Cascadia Code", "JetBrains Mono", monospace',
    fontSize: 14,
    theme: {
      background: '#000000',
      foreground: '#00ff41',
      cursor: '#00ff41',
      black: '#000000',
      green: '#00ff41',
      brightGreen: '#69ff47',
    },
    scrollback: 2000,
    convertEol: false,
  });
  _term = term;

  const fitAddon = new FitAddon.FitAddon();
  term.loadAddon(fitAddon);
  term.open(document.getElementById('terminal'));
  fitAddon.fit();

  // Resize handler
  window.addEventListener('resize', () => fitAddon.fit());

  // Route all keyboard input through the key queue
  term.onData(_enqueueKey);

  // 2. Initialise filesystem
  const fs = new PebbleFS();
  fs.mount('system', 'pebble_system/');

  term.writeln('\x1b[32mLoading PebbleOS...\x1b[0m');

  // Preload system files
  await fs.preload('system', SYSTEM_FILES);

  // Preload a selection of disk files (best effort)
  const diskResults = await Promise.allSettled(
    DISK_FILES.map(async (rel) => {
      const url = 'pebble_disk/' + rel;
      const resp = await fetch(url);
      if (!resp.ok) return;
      const text = await resp.text();
      fs._storeFile(rel, text, '2025-01-01, 00:00:00');
    })
  );

  term.writeln('\x1b[32mFilesystem ready. Booting...\x1b[0m');
  document.getElementById('loading').style.display = 'none';

  // 3. Create shared state
  const state = {
    cwd: '/',
    env: {
      PATH: '/system/bin:/system/sbin:/bin',
      HOME: '/',
      USER: 'user',
      SHELL: '/system/bin/sh.peb',
      TERM: 'xterm-256color',
    },
    nextTaskId: 1,
    vmTasks: new Map(),
    nextJobId: 1,
    jobs: new Map(),
    nextPid: 1,
  };

  // 4. Create host functions
  const hostFunctions = createHostFunctions(term, fs, state, readLine, readKey);

  // 5. Create the main interpreter
  const interp = new PebbleInterpreter({
    hostFunctions,
    outputConsumer: (text) => term.writeln(text),
    inputProvider: (prompt) => readLine(prompt),
    fs,
    state,
  });

  // 6. Load runtime + shell source
  const runtimeSrc = fs.readFile('system/runtime.peb');
  const shellSrc   = fs.readFile('system/shell.peb');
  const combinedSrc = runtimeSrc + '\n' + shellSrc;

  // 7. Execute combined source to define all functions + run boot()
  const initialGlobals = {
    SYSTEM_RUNTIME_PATH: 'system/runtime.peb',
    SYSTEM_SHELL_PATH:   'system/shell.peb',
    SYSTEM_SHELL_SOURCE: shellSrc,
    FS_MODE:  'mfs',
    CWD:      '/',
    ENV:      { ...state.env },
    PATH:     state.env.PATH,
    ARGC:     0,
    ARGV:     [],
  };

  try {
    await interp.execute(combinedSrc + '\nboot()\n', initialGlobals);
  } catch (e) {
    term.writeln('\x1b[31mBoot error: ' + (e.message || e) + '\x1b[0m');
    console.error('Boot error:', e);
    // Continue anyway — show a fallback shell
  }

  // 8. Interactive shell loop using shell_dispatch from shell.peb
  try {
    const intro = await interp.callGlobalFunction('shell_intro', []);
    for (const line of String(intro).split('\n')) {
      term.writeln(line);
    }
  } catch (_) {}

  while (true) {
    // Get prompt from Pebble (includes current CWD)
    let prompt = 'pebble-os:' + state.cwd + '> ';
    try {
      const p = await interp.callGlobalFunction('shell_prompt', []);
      if (typeof p === 'string') prompt = p;
    } catch (_) {}

    // Update interpreter's CWD global in case state changed
    interp.globals['CWD'] = state.cwd;
    interp.globals['ENV'] = state.env;

    // Read a command line
    let rawLine;
    try {
      rawLine = await readLine('\r' + prompt);
    } catch (_) {
      break;
    }

    if (rawLine === null) break;  // EOF (Ctrl-D)
    const trimmed = rawLine.trim();
    if (!trimmed) continue;

    // Check for background '&' operator
    let background = false;
    let cmdLine = trimmed;
    if (cmdLine.endsWith(' &') || cmdLine === '&') {
      background = true;
      cmdLine = cmdLine.slice(0, -1).trim();
    }

    // Dispatch to Pebble's shell_dispatch(command, args)
    const parts = splitShellLine(cmdLine);
    if (parts.length === 0) continue;
    const [cmd, ...args] = parts;

    let result = 0;
    try {
      // Update CWD in the interpreter before each command
      interp.globals['CWD'] = state.cwd;
      interp.globals['ENV'] = state.env;

      result = await interp.callGlobalFunction('shell_dispatch', [cmd, args]);
    } catch (e) {
      if (e && e.name === 'PebbleError') {
        term.writeln('\x1b[31m*** ' + e.message + '\x1b[0m');
      } else {
        term.writeln('\x1b[31mError: ' + String(e) + '\x1b[0m');
        console.error(e);
      }
      continue;
    }

    // Handle special return values
    if (result === '__exit__') {
      term.writeln('Goodbye.');
      break;
    }
    if (typeof result === 'string' && result.startsWith('__cwd__:')) {
      state.cwd = result.slice(8);
      interp.globals['CWD'] = state.cwd;
    }
  }

  term.writeln('\r\nPebbleOS session ended. Reload the page to restart.');
}

// Start
main().catch(err => {
  console.error('Fatal error:', err);
  if (_term) {
    _term.writeln('\r\n\x1b[31mFatal: ' + (err.message || err) + '\x1b[0m');
  }
});
