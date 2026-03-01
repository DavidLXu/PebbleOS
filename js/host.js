// PebbleOS Host Functions for the Browser
// Provides implementations of the ~50 host functions that Python's shell.py
// registered with the interpreter.  Async functions return Promises.

import { PebbleInterpreter, PebbleError } from './lang.js';
import { PebbleFSError } from './fs.js';

// Creates and returns the host functions object bound to the given context.
//
// state: {
//   cwd: string,             current working directory (display path, e.g. '/')
//   env: { [k]: v },        environment variables
//   nextTaskId: number,      auto-incremented task id
//   vmTasks: Map<id, task>, completed vm task records
//   nextJobId: number,
//   jobs: Map<id, job>,     background job records
//   nextPid: number,
// }
//
// readLine(prompt): async function -> string (readline from xterm.js)
// readKey(timeoutMs): async function -> string (single key from xterm.js)
export function createHostFunctions(term, fs, state, readLine, readKey) {

  // ── Helper to run a Pebble program as a child process ─────────────────────
  async function runPebbleProgram(programPath, argv, captureOutput) {
    // Load runtime + program source
    const runtimeSrc = fs.readFile('system/runtime.peb');
    const programSrc = fs.readFile(programPath);
    const source = runtimeSrc + '\n' + programSrc;

    const childState = {
      cwd: state.cwd,
      env: { ...state.env },
      nextTaskId: 1,
      vmTasks: new Map(),
      nextJobId: 1,
      jobs: new Map(),
      nextPid: 1,
    };

    const childOutput = [];
    const childHostFns = createHostFunctions(term, fs, childState, readLine, readKey);

    const child = new PebbleInterpreter({
      hostFunctions: childHostFns,
      outputConsumer: captureOutput
        ? (text) => childOutput.push(text)
        : (text) => { term.write(text + '\r\n'); },
      inputProvider: (prompt) => readLine(prompt),
      fs,
      state: childState,
    });

    const initialGlobals = {
      SYSTEM_RUNTIME_PATH: 'system/runtime.peb',
      SYSTEM_SHELL_PATH: 'system/shell.peb',
      FS_MODE: 'mfs',
      CWD: state.cwd,
      ENV: { ...state.env },
      PATH: state.env['PATH'] || '/system/bin:/system/sbin:/bin',
      ARGC: argv.length,
      ARGV: [programPath, ...argv],
    };

    try {
      await child.execute(source, initialGlobals);
    } catch (e) {
      if (e && e.name === 'PebbleError') {
        if (!captureOutput) term.write('\r\n\x1b[31mError: ' + e.message + '\x1b[0m\r\n');
      } else {
        throw e;
      }
    }

    // Propagate CWD changes back to parent
    if (childState.cwd !== state.cwd) {
      state.cwd = childState.cwd;
    }

    return captureOutput ? childOutput.join('\n') : 0;
  }

  // ── File I/O host functions ────────────────────────────────────────────────

  function hostRawListFiles(args, line) {
    return fs.listFiles();
  }

  function hostRawFileExists(args, line) {
    if (!args[0] || typeof args[0] !== 'string') return 0;
    return fs.fileExists(args[0]) ? 1 : 0;
  }

  function hostRawReadFile(args, line) {
    if (!args[0] || typeof args[0] !== 'string') throw new PebbleError(`line ${line}: raw_read_file() requires a string path`);
    try { return fs.readFile(args[0]); }
    catch (e) { throw new PebbleError(`line ${line}: ${e.message}`); }
  }

  function hostRawWriteFile(args, line) {
    if (args.length < 2) throw new PebbleError(`line ${line}: raw_write_file(path, content)`);
    try { fs.writeFile(args[0], args[1] === null ? '' : String(args[1])); return 0; }
    catch (e) { throw new PebbleError(`line ${line}: ${e.message}`); }
  }

  function hostRawCreateFile(args, line) {
    if (args.length < 2) throw new PebbleError(`line ${line}: raw_create_file(path, content)`);
    try { fs.createFile(args[0], args[1] === null ? '' : String(args[1])); return 0; }
    catch (e) { throw new PebbleError(`line ${line}: ${e.message}`); }
  }

  function hostRawModifyFile(args, line) {
    if (args.length < 2) throw new PebbleError(`line ${line}: raw_modify_file(path, content)`);
    try { fs.modifyFile(args[0], args[1] === null ? '' : String(args[1])); return 0; }
    catch (e) { throw new PebbleError(`line ${line}: ${e.message}`); }
  }

  function hostRawDeleteFile(args, line) {
    if (!args[0]) throw new PebbleError(`line ${line}: raw_delete_file() requires a path`);
    try { fs.deleteFile(args[0]); return 0; }
    catch (e) { throw new PebbleError(`line ${line}: ${e.message}`); }
  }

  function hostRawFileTime(args, line) {
    if (!args[0]) throw new PebbleError(`line ${line}: raw_file_time() requires a path`);
    try { return fs.fileTime(args[0]); }
    catch (e) { return '2025-01-01, 00:00:00'; }
  }

  function hostRawDirExists(args, line) {
    if (!args[0] && args[0] !== '') return 0;
    return fs.dirExists(args[0]) ? 1 : 0;
  }

  function hostRawMakeDir(args, line) {
    if (!args[0]) throw new PebbleError(`line ${line}: raw_make_directory() requires a path`);
    try { fs.createDir(args[0]); return 0; }
    catch (e) { throw new PebbleError(`line ${line}: ${e.message}`); }
  }

  function hostRawRemoveDir(args, line) {
    if (!args[0]) throw new PebbleError(`line ${line}: raw_remove_directory() requires a path`);
    try { fs.removeDir(args[0]); return 0; }
    catch (e) { throw new PebbleError(`line ${line}: ${e.message}`); }
  }

  function hostRawDirEmpty(args, line) {
    if (!args[0] && args[0] !== '') return 1;
    return fs.dirEmpty(args[0]) ? 1 : 0;
  }

  function hostFilesystemFileCount(args, line) {
    return fs.fileCount();
  }

  function hostFilesystemTotalBytes(args, line) {
    return fs.totalBytes();
  }

  function hostFilesystemSync(args, line) {
    return 0;  // no-op in browser
  }

  // ── Higher-level FS aliases (override when runtime.peb not loaded yet) ─────
  function hostFileExists(args, line) { return hostRawFileExists(args, line); }
  function hostFileTime(args, line) { return hostRawFileTime(args, line); }
  function hostDirExists(args, line) { return hostRawDirExists(args, line); }
  function hostCreateFile(args, line) { return hostRawCreateFile(args, line); }
  function hostModifyFile(args, line) { return hostRawModifyFile(args, line); }
  function hostDeleteFile(args, line) { return hostRawDeleteFile(args, line); }
  function hostMakeDir(args, line) { return hostRawMakeDir(args, line); }
  function hostRemoveDir(args, line) { return hostRawRemoveDir(args, line); }
  function hostDirEmpty(args, line) { return hostRawDirEmpty(args, line); }

  // ── CWD ───────────────────────────────────────────────────────────────────

  function hostCwd(args, line) {
    return state.cwd || '/';
  }

  function hostChdir(args, line) {
    if (!args[0] || typeof args[0] !== 'string') throw new PebbleError(`line ${line}: chdir() requires a path`);
    // Validation is done by Pebble's chdir(); just update state
    state.cwd = args[0];
    return 0;
  }

  // ── Terminal ──────────────────────────────────────────────────────────────

  function hostTermWrite(args, line) {
    if (args.length > 0) term.write(String(args[0]));
    return 0;
  }

  function hostTermFlush(args, line) {
    return 0;  // no-op; xterm.js is immediate
  }

  function hostTermClear(args, line) {
    term.write('\x1b[2J\x1b[H');
    return 0;
  }

  function hostTermMove(args, line) {
    // move_cursor(row, col) — 0-indexed in Pebble
    const row = (args[0] || 0) + 1;
    const col = (args[1] || 0) + 1;
    term.write(`\x1b[${row};${col}H`);
    return 0;
  }

  function hostTermHideCursor(args, line) {
    term.write('\x1b[?25l');
    return 0;
  }

  function hostTermShowCursor(args, line) {
    term.write('\x1b[?25h');
    return 0;
  }

  async function hostTermReadKey(args, line) {
    return await readKey(null);
  }

  async function hostTermReadKeyTimeout(args, line) {
    const ms = typeof args[0] === 'number' ? args[0] : 1000;
    return await readKey(ms);
  }

  function hostTermRows(args, line) {
    return term.rows || 24;
  }

  function hostTermCols(args, line) {
    return term.cols || 80;
  }

  function hostTermOwnerPgid(args, line) { return 1; }
  function hostTermMode(args, line) { return 'cooked'; }
  function hostTermState(args, line) {
    return {
      owner_pgid: 1, mode: 'cooked', interactive: 1, foreground_raw: 0,
      rows: term.rows || 24, cols: term.cols || 80,
    };
  }

  // ── Time ──────────────────────────────────────────────────────────────────

  function hostCurrentTime(args, line) {
    const d = new Date();
    const pad = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}, ` +
           `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }

  // ── Runtime error ─────────────────────────────────────────────────────────

  function hostRuntimeError(args, line) {
    const msg = args[0] ? String(args[0]) : 'runtime error';
    throw new PebbleError(`line ${line}: ${msg}`);
  }

  // ── Program execution ─────────────────────────────────────────────────────

  async function hostRunProgram(args, line) {
    if (!args[0] || typeof args[0] !== 'string') throw new PebbleError(`line ${line}: run_program() requires a path`);
    const programPath = args[0];
    const argv = Array.isArray(args[1]) ? args[1].map(String) : [];

    if (!fs.fileExists(programPath)) {
      throw new PebbleError(`line ${line}: file '${programPath}' does not exist`);
    }
    await runPebbleProgram(programPath, argv, false);
    return 0;
  }

  async function hostExecProgram(args, line) {
    // Same as run_program in our simplified implementation
    return await hostRunProgram(args, line);
  }

  // ── VM Task management (simplified: run synchronously, cache output) ──────

  async function hostVmCreateTask(args, line) {
    // args: [source, argv]  OR  [source, argv, name]
    const source = typeof args[0] === 'string' ? args[0] : '';
    const argv = Array.isArray(args[1]) ? args[1] : [];
    const taskId = state.nextTaskId++;
    const taskOutput = [];

    const taskState = {
      cwd: state.cwd,
      env: { ...state.env },
      nextTaskId: 1,
      vmTasks: new Map(),
      nextJobId: 1,
      jobs: new Map(),
      nextPid: 1,
    };
    const taskHostFns = createHostFunctions(term, fs, taskState, readLine, readKey);
    const taskInterp = new PebbleInterpreter({
      hostFunctions: taskHostFns,
      outputConsumer: (text) => taskOutput.push(text),
      inputProvider: (prompt) => readLine(prompt),
      fs,
      state: taskState,
    });

    let status = 'halted';
    try {
      await taskInterp.execute(source, {
        CWD: state.cwd, FS_MODE: 'mfs',
        ENV: { ...state.env }, ARGC: argv.length, ARGV: argv,
      });
    } catch (e) {
      status = 'error';
    }

    state.vmTasks.set(taskId, { id: taskId, status, output: taskOutput, outputConsumed: 0 });
    return taskId;
  }

  function hostVmStepTask(args, line) {
    // Already completed synchronously; return 0 steps run
    return 0;
  }

  function hostVmTaskStatus(args, line) {
    const taskId = args[0];
    const task = state.vmTasks.get(taskId);
    return task ? task.status : 'halted';
  }

  function hostVmTakeTaskOutput(args, line) {
    const taskId = args[0];
    const task = state.vmTasks.get(taskId);
    if (!task) return [];
    const out = task.output.slice(task.outputConsumed);
    task.outputConsumed = task.output.length;
    return out;
  }

  function hostVmSnapshotTask(args, line) { return 0; }
  function hostVmRestoreTask(args, line) { return state.nextTaskId++; }
  function hostVmDropTask(args, line) {
    if (args[0]) state.vmTasks.delete(args[0]);
    return 0;
  }

  // ── Background jobs ───────────────────────────────────────────────────────

  async function hostStartBackgroundJob(args, line) {
    const program = args[0] ? String(args[0]) : '';
    const argv = Array.isArray(args[1]) ? args[1] : [];
    const jobId = state.nextJobId++;

    const jobOutput = [];
    state.jobs.set(jobId, { id: jobId, program, argv, status: 'running', output: jobOutput });

    // Run synchronously since we're single-threaded
    try {
      await runPebbleProgram(program, argv, true).then(out => {
        state.jobs.get(jobId).output = String(out).split('\n');
        state.jobs.get(jobId).status = 'done';
      });
    } catch (e) {
      const job = state.jobs.get(jobId);
      if (job) { job.status = 'error'; job.output = [String(e.message)]; }
    }

    return jobId;
  }

  function hostListBackgroundJobs(args, line) {
    return Array.from(state.jobs.values()).map(j =>
      `[${j.id}] ${j.status} ${j.program}`
    );
  }

  function hostForegroundJob(args, line) {
    const jobId = args[0];
    const job = state.jobs.get(jobId);
    if (!job) return [];
    const out = job.output || [];
    state.jobs.delete(jobId);
    return out;
  }

  function hostBackgroundJob(args, line) { return []; }

  // ── Process management (stubs) ────────────────────────────────────────────

  function hostListProcesses(args, line) {
    return ['  PID  STATE    COMMAND', '    1  running  shell'];
  }

  function hostListProcessRecords(args, line) {
    return [{
      pid: 1, ppid: 0, pgid: 1, sid: 1,
      state: 'foreground', cwd: state.cwd,
      argv: ['shell'], env: state.env,
    }];
  }

  function hostListChildProcesses(args, line) { return []; }
  function hostCurrentForegroundPgid(args, line) { return 1; }
  function hostWaitProcess(args, line) { return 0; }
  function hostWaitChildProcess(args, line) { return 0; }
  function hostReapProcess(args, line) { return 0; }
  function hostKillProcess(args, line) { return 0; }

  // ── Signals ───────────────────────────────────────────────────────────────
  function hostListSignalEvents(args, line) { return []; }
  function hostDrainSignalEvents(args, line) { return []; }

  // ── Threads (stubs) ───────────────────────────────────────────────────────
  let _nextTid = 2;
  function hostListThreadRecords(args, line) {
    return [{ tid: 1, tgid: 1, state: 'running', blocked_on: '', name: 'main' }];
  }
  async function hostThreadSpawnSource(args, line) { return _nextTid++; }
  function hostThreadJoin(args, line) { return 0; }
  function hostThreadStatus(args, line) { return 'halted'; }
  function hostThreadSelf(args, line) { return 1; }
  function hostThreadYield(args, line) { return 0; }

  // ── Mutexes (stubs) ───────────────────────────────────────────────────────
  let _nextMutexId = 1;
  function hostListMutexRecords(args, line) { return []; }
  function hostMutexCreate(args, line) { return _nextMutexId++; }
  function hostMutexLock(args, line) { return 0; }
  function hostMutexTryLock(args, line) { return 1; }  // always succeeds
  function hostMutexUnlock(args, line) { return 0; }

  // ── FD operations (simplified) ────────────────────────────────────────────
  let _nextFd = 3;
  const _openFds = new Map();
  // Reserve 0=stdin, 1=stdout, 2=stderr

  function hostFdOpen(args, line) {
    const path = args[0] ? String(args[0]) : '';
    const mode = args[1] ? String(args[1]) : 'r';
    const fd = _nextFd++;
    _openFds.set(fd, { path, mode });
    return fd;
  }

  function hostFdRead(args, line) {
    const fd = args[0];
    if (fd === 0) return '';  // stdin - would block; return empty
    return '';
  }

  function hostFdWrite(args, line) {
    const fd = args[0];
    const text = args[1] !== undefined ? String(args[1]) : '';
    if (fd === 1 || fd === 2) {
      term.write(text.replace(/\n/g, '\r\n'));
    } else if (_openFds.has(fd)) {
      const info = _openFds.get(fd);
      const path = info.path.replace(/^\/dev\//, '');
      if (path === 'stdout' || path === 'stderr' || path === 'tty') {
        term.write(text.replace(/\n/g, '\r\n'));
      } else {
        // Write to FS (append mode)
        try {
          const existing = fs.fileExists(info.path.replace(/^\//, '')) ? fs.readFile(info.path.replace(/^\//, '')) : '';
          fs.writeFile(info.path.replace(/^\//, ''), existing + text);
        } catch (_) {}
      }
    }
    return text.length;
  }

  function hostFdClose(args, line) {
    if (args[0]) _openFds.delete(args[0]);
    return 0;
  }

  function hostFdStat(args, line) {
    const fd = args[0];
    if (_openFds.has(fd)) {
      const info = _openFds.get(fd);
      return { path: info.path, mode: info.mode, size: 0 };
    }
    return {};
  }

  function hostFdReaddir(args, line) { return []; }

  // ── Misc ──────────────────────────────────────────────────────────────────

  function hostCaptureText(args, line) { return ''; }
  function hostSourceShellScript(args, line) { return 0; }

  // ── Process context (for system.lib.base.process_context) ─────────────────
  // This is called as hostFn "process_context" but actually comes from lib/base.peb
  // We provide it as a fallback for any direct call.
  function hostProcessContext(args, line) {
    return {
      pid: state.nextPid || 1,
      ppid: 0,
      pgid: 1,
      sid: 1,
      cwd: state.cwd,
      argv: state.env['ARGV'] || [],
      env: state.env,
    };
  }

  // ── Return the host functions map ─────────────────────────────────────────
  return {
    // File I/O
    raw_list_files: hostRawListFiles,
    raw_file_exists: hostRawFileExists,
    raw_read_file: hostRawReadFile,
    raw_write_file: hostRawWriteFile,
    raw_create_file: hostRawCreateFile,
    raw_modify_file: hostRawModifyFile,
    raw_delete_file: hostRawDeleteFile,
    raw_file_time: hostRawFileTime,
    raw_directory_exists: hostRawDirExists,
    raw_make_directory: hostRawMakeDir,
    raw_remove_directory: hostRawRemoveDir,
    raw_directory_empty: hostRawDirEmpty,
    list_files: hostRawListFiles,
    // Higher-level FS (override when runtime.peb not prepended)
    file_exists: hostFileExists,
    file_time: hostFileTime,
    directory_exists: hostDirExists,
    create_file: hostCreateFile,
    modify_file: hostModifyFile,
    delete_file: hostDeleteFile,
    make_directory: hostMakeDir,
    remove_directory: hostRemoveDir,
    directory_empty: hostDirEmpty,
    // Filesystem metadata
    filesystem_file_count: hostFilesystemFileCount,
    filesystem_total_bytes: hostFilesystemTotalBytes,
    filesystem_sync: hostFilesystemSync,
    // CWD
    cwd: hostCwd,
    chdir: hostChdir,
    // Terminal
    term_write: hostTermWrite,
    term_flush: hostTermFlush,
    term_clear: hostTermClear,
    term_move: hostTermMove,
    term_hide_cursor: hostTermHideCursor,
    term_show_cursor: hostTermShowCursor,
    term_read_key: hostTermReadKey,
    term_read_key_timeout: hostTermReadKeyTimeout,
    term_rows: hostTermRows,
    term_cols: hostTermCols,
    term_owner_pgid: hostTermOwnerPgid,
    term_mode: hostTermMode,
    term_state: hostTermState,
    // Time / error
    current_time: hostCurrentTime,
    runtime_error: hostRuntimeError,
    // Programs
    run_program: hostRunProgram,
    exec_program: hostExecProgram,
    // VM tasks
    vm_create_task: hostVmCreateTask,
    vm_step_task: hostVmStepTask,
    vm_task_status: hostVmTaskStatus,
    vm_take_task_output: hostVmTakeTaskOutput,
    vm_snapshot_task: hostVmSnapshotTask,
    vm_restore_task: hostVmRestoreTask,
    vm_drop_task: hostVmDropTask,
    // Background jobs
    start_background_job: hostStartBackgroundJob,
    list_background_jobs: hostListBackgroundJobs,
    foreground_job: hostForegroundJob,
    background_job: hostBackgroundJob,
    // Process management
    list_processes: hostListProcesses,
    list_process_records: hostListProcessRecords,
    list_child_processes: hostListChildProcesses,
    current_foreground_pgid: hostCurrentForegroundPgid,
    wait_process: hostWaitProcess,
    wait_child_process: hostWaitChildProcess,
    reap_process: hostReapProcess,
    kill_process: hostKillProcess,
    // Signals
    list_signal_events: hostListSignalEvents,
    drain_signal_events: hostDrainSignalEvents,
    // Threads
    list_thread_records: hostListThreadRecords,
    thread_spawn_source_host: hostThreadSpawnSource,
    thread_join_host: hostThreadJoin,
    thread_status_host: hostThreadStatus,
    thread_self_host: hostThreadSelf,
    thread_yield_host: hostThreadYield,
    // Mutexes
    list_mutex_records: hostListMutexRecords,
    mutex_create_host: hostMutexCreate,
    mutex_lock_host: hostMutexLock,
    mutex_try_lock_host: hostMutexTryLock,
    mutex_unlock_host: hostMutexUnlock,
    // FD operations
    fd_open: hostFdOpen,
    fd_read: hostFdRead,
    fd_write: hostFdWrite,
    fd_close: hostFdClose,
    fd_stat: hostFdStat,
    fd_readdir: hostFdReaddir,
    // Misc
    capture_text: hostCaptureText,
    source_shell_script: hostSourceShellScript,
    process_context: hostProcessContext,
  };
}
