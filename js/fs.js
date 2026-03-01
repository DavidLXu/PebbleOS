// PebbleOS in-memory filesystem for the browser
// Paths are stored without leading '/'; the root is ''.

export class PebbleFS {
  constructor() {
    // Map<logicalPath, content>  (path = '' for root files, 'dir/file' for nested)
    this._files = new Map();
    // Set<logicalPath> for directories
    this._dirs = new Set();
    this._dirs.add('');  // root always exists
    // Map<alias, fetchPrefix>  e.g. 'system' -> 'pebble_system/'
    this._mounts = new Map();
    // Timestamps: Map<logicalPath, dateString>
    this._times = new Map();
  }

  // ── Mounting ────────────────────────────────────────────────────────────────

  mount(alias, fetchPrefix) {
    this._mounts.set(alias, fetchPrefix);
  }

  // ── Preloading ──────────────────────────────────────────────────────────────

  // Fetch a list of relative file paths from fetchPrefix and store them.
  // Each relativePath like 'runtime.peb' is stored as '<alias>/<relativePath>'.
  async preload(alias, relativePaths) {
    const prefix = this._mounts.get(alias);
    if (!prefix) throw new Error(`Unknown mount alias '${alias}'`);
    const results = await Promise.allSettled(
      relativePaths.map(async (rel) => {
        const url = prefix + rel;
        const resp = await fetch(url);
        if (!resp.ok) throw new Error(`fetch ${url}: ${resp.status}`);
        const text = await resp.text();
        const logicalPath = alias + '/' + rel;
        this._storeFile(logicalPath, text, '2025-01-01, 00:00:00');
      })
    );
    const errors = results.filter(r => r.status === 'rejected');
    if (errors.length > 0) {
      console.warn('PebbleFS: some system files failed to load:', errors.map(e => e.reason));
    }
  }

  // Preload user disk files (pebble_disk/).
  async preloadDisk(fileMap) {
    // fileMap: { logicalPath: content }
    for (const [path, content] of Object.entries(fileMap)) {
      this._storeFile(path, content, this._now());
    }
  }

  _storeFile(logicalPath, content, timestamp) {
    this._files.set(logicalPath, content);
    this._times.set(logicalPath, timestamp || this._now());
    // Ensure parent directories exist
    const parts = logicalPath.split('/');
    for (let i = 1; i < parts.length; i++) {
      const dir = parts.slice(0, i).join('/');
      if (dir) this._dirs.add(dir);
    }
  }

  // ── Core operations ─────────────────────────────────────────────────────────

  fileExists(logicalPath) {
    return this._files.has(logicalPath);
  }

  dirExists(logicalPath) {
    if (logicalPath === '' || logicalPath === '/') return true;
    const clean = logicalPath.replace(/^\//, '');
    if (this._dirs.has(clean)) return true;
    // Check if any file starts with this prefix
    const prefix = clean + '/';
    for (const k of this._files.keys()) {
      if (k.startsWith(prefix)) return true;
    }
    return false;
  }

  dirEmpty(logicalPath) {
    const clean = logicalPath.replace(/^\//, '');
    const prefix = clean + '/';
    for (const k of this._files.keys()) {
      if (k.startsWith(prefix)) return false;
    }
    for (const d of this._dirs) {
      if (d !== clean && d.startsWith(prefix)) return false;
    }
    return true;
  }

  readFile(logicalPath) {
    if (!this._files.has(logicalPath)) {
      throw new PebbleFSError(`file '${logicalPath}' does not exist`);
    }
    return this._files.get(logicalPath);
  }

  writeFile(logicalPath, content) {
    this._files.set(logicalPath, content);
    this._times.set(logicalPath, this._now());
    this._ensureParentDirs(logicalPath);
    return content;
  }

  createFile(logicalPath, content) {
    if (this._files.has(logicalPath)) {
      throw new PebbleFSError(`file '${logicalPath}' already exists`);
    }
    this._storeFile(logicalPath, content || '', this._now());
  }

  modifyFile(logicalPath, content) {
    if (!this._files.has(logicalPath)) {
      throw new PebbleFSError(`file '${logicalPath}' does not exist`);
    }
    this._files.set(logicalPath, content);
    this._times.set(logicalPath, this._now());
  }

  deleteFile(logicalPath) {
    if (!this._files.has(logicalPath)) {
      throw new PebbleFSError(`file '${logicalPath}' does not exist`);
    }
    this._files.delete(logicalPath);
    this._times.delete(logicalPath);
  }

  fileTime(logicalPath) {
    if (!this._files.has(logicalPath)) {
      throw new PebbleFSError(`file '${logicalPath}' does not exist`);
    }
    return this._times.get(logicalPath) || this._now();
  }

  createDir(logicalPath) {
    const clean = logicalPath.replace(/^\//, '');
    if (this._dirs.has(clean)) {
      throw new PebbleFSError(`directory '${logicalPath}' already exists`);
    }
    this._dirs.add(clean);
    this._ensureParentDirs(clean + '/_placeholder_');
  }

  removeDir(logicalPath) {
    const clean = logicalPath.replace(/^\//, '');
    if (!this._dirs.has(clean)) {
      throw new PebbleFSError(`directory '${logicalPath}' does not exist`);
    }
    if (!this.dirEmpty(clean)) {
      throw new PebbleFSError(`directory '${logicalPath}' is not empty`);
    }
    this._dirs.delete(clean);
  }

  // Return sorted list of all logical file paths.
  listFiles() {
    return Array.from(this._files.keys()).sort();
  }

  // Return files/dirs immediately under a given logical directory.
  // Returns names (not full paths) relative to the directory.
  listDir(logicalDir) {
    const clean = logicalDir === '/' ? '' : logicalDir.replace(/^\//, '');
    const prefix = clean ? clean + '/' : '';
    const seen = new Set();

    for (const path of this._files.keys()) {
      if (prefix === '' || path.startsWith(prefix)) {
        const rest = path.slice(prefix.length);
        const segment = rest.split('/')[0];
        if (segment) seen.add(segment);
      }
    }
    for (const dir of this._dirs) {
      if (dir === clean) continue;
      if (prefix === '' || dir.startsWith(prefix)) {
        const rest = dir.slice(prefix.length);
        const segment = rest.split('/')[0];
        if (segment && !rest.includes('/')) seen.add(segment);
      }
    }
    return Array.from(seen).sort();
  }

  fileCount() {
    return this._files.size;
  }

  totalBytes() {
    let total = 0;
    for (const v of this._files.values()) total += v.length;
    return total;
  }

  // ── Path resolution ──────────────────────────────────────────────────────────

  // Convert a logical display path (e.g. '/docs/foo.txt') to a storage path
  // (e.g. 'docs/foo.txt'). Resolves CWD for relative paths.
  // Returns '' for root.
  resolve(logicalPath, cwd) {
    if (!logicalPath) throw new PebbleFSError('file name cannot be empty');

    // Strip leading slash or treat as relative
    let parts;
    if (logicalPath.startsWith('/')) {
      parts = logicalPath.slice(1).split('/').filter(Boolean);
    } else if (logicalPath.startsWith('system/')) {
      parts = logicalPath.split('/').filter(Boolean);
    } else {
      // Relative: resolve against cwd
      const base = (cwd || '/').replace(/^\//, '').split('/').filter(Boolean);
      parts = [...base, ...logicalPath.split('/').filter(Boolean)];
    }

    // Normalize . and ..
    const out = [];
    for (const p of parts) {
      if (p === '.') continue;
      if (p === '..') {
        if (out.length === 0) throw new PebbleFSError('path escapes root');
        out.pop();
      } else {
        out.push(p);
      }
    }
    return out.join('/');
  }

  // ── Helpers ──────────────────────────────────────────────────────────────────

  _now() {
    const d = new Date();
    const pad = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}, ` +
           `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }

  _ensureParentDirs(logicalPath) {
    const parts = logicalPath.split('/');
    for (let i = 1; i < parts.length; i++) {
      const dir = parts.slice(0, i).join('/');
      if (dir) this._dirs.add(dir);
    }
  }
}

export class PebbleFSError extends Error {
  constructor(msg) { super(msg); this.name = 'PebbleFSError'; }
}
