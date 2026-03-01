// PebbleOS JavaScript Interpreter
// Ports pebble_bootloader/lang.py (tree-walk interpreter) to the browser.

// ── Signals (control flow) ────────────────────────────────────────────────────

export class PebbleError extends Error {
  constructor(msg) { super(msg); this.name = 'PebbleError'; }
}

class ReturnSignal {
  constructor(value) { this.value = value; }
}

class BreakSignal {}
class ContinueSignal {}

// ── Module object ─────────────────────────────────────────────────────────────

class ModuleObject {
  // builtins: {attr -> builtinFunctionName}  (for builtin modules like math)
  // values:   {name -> value}                (module's globals after execution)
  // functions:{name -> FunctionDef}          (module's defined functions)
  constructor(name, builtins, values, functions) {
    this.name = name;
    this.builtins = builtins || {};
    this.values = values || {};
    this.functions = functions || {};
  }
}

// ── Expression Lexer ──────────────────────────────────────────────────────────

const TT = {
  NUM: 'NUM', STR: 'STR', IDENT: 'IDENT',
  LPAREN: '(', RPAREN: ')', LBRACKET: '[', RBRACKET: ']',
  LBRACE: '{', RBRACE: '}', COMMA: ',', COLON: ':', DOT: '.',
  OP: 'OP', EOF: 'EOF',
};

function tokenizeExpr(src, lineNum) {
  const tokens = [];
  let i = 0;
  while (i < src.length) {
    // Skip whitespace
    if (src[i] === ' ' || src[i] === '\t') { i++; continue; }

    // Numbers
    if (/\d/.test(src[i]) || (src[i] === '-' && /\d/.test(src[i+1] || ''))) {
      let s = '';
      if (src[i] === '-') { s = '-'; i++; }
      while (i < src.length && /[\d.]/.test(src[i])) s += src[i++];
      tokens.push({ t: TT.NUM, v: s.includes('.') ? parseFloat(s) : parseInt(s, 10) });
      continue;
    }

    // Strings
    if (src[i] === '"' || src[i] === "'") {
      const q = src[i++];
      let s = '';
      while (i < src.length && src[i] !== q) {
        if (src[i] === '\\') {
          i++;
          const esc = src[i++];
          if (esc === 'n') s += '\n';
          else if (esc === 't') s += '\t';
          else if (esc === 'r') s += '\r';
          else if (esc === '\\') s += '\\';
          else if (esc === '"') s += '"';
          else if (esc === "'") s += "'";
          else s += '\\' + esc;
        } else {
          s += src[i++];
        }
      }
      if (src[i] === q) i++;
      tokens.push({ t: TT.STR, v: s });
      continue;
    }

    // Two-char operators
    const two = src.slice(i, i+2);
    if (['==', '!=', '<=', '>='].includes(two)) {
      tokens.push({ t: TT.OP, v: two }); i += 2; continue;
    }

    // Single-char tokens
    const ch = src[i];
    if ('+-*/<>%'.includes(ch)) { tokens.push({ t: TT.OP, v: ch }); i++; continue; }
    if (ch === '(') { tokens.push({ t: TT.LPAREN, v: ch }); i++; continue; }
    if (ch === ')') { tokens.push({ t: TT.RPAREN, v: ch }); i++; continue; }
    if (ch === '[') { tokens.push({ t: TT.LBRACKET, v: ch }); i++; continue; }
    if (ch === ']') { tokens.push({ t: TT.RBRACKET, v: ch }); i++; continue; }
    if (ch === '{') { tokens.push({ t: TT.LBRACE, v: ch }); i++; continue; }
    if (ch === '}') { tokens.push({ t: TT.RBRACE, v: ch }); i++; continue; }
    if (ch === ',') { tokens.push({ t: TT.COMMA, v: ch }); i++; continue; }
    if (ch === ':') { tokens.push({ t: TT.COLON, v: ch }); i++; continue; }
    if (ch === '.') { tokens.push({ t: TT.DOT, v: ch }); i++; continue; }

    // Identifiers / keywords
    if (/[a-zA-Z_]/.test(ch)) {
      let s = '';
      while (i < src.length && /\w/.test(src[i])) s += src[i++];
      tokens.push({ t: TT.IDENT, v: s });
      continue;
    }

    throw new PebbleError(`line ${lineNum}: unexpected character in expression: '${ch}'`);
  }
  tokens.push({ t: TT.EOF, v: null });
  return tokens;
}

// ── Expression Parser ─────────────────────────────────────────────────────────

class ExprParser {
  constructor(tokens, lineNum) {
    this.tokens = tokens;
    this.pos = 0;
    this.lineNum = lineNum;
  }

  peek() { return this.tokens[this.pos]; }
  consume() { return this.tokens[this.pos++]; }
  expect(type) {
    const tok = this.consume();
    if (tok.t !== type) throw new PebbleError(`line ${this.lineNum}: expected '${type}', got '${tok.v}'`);
    return tok;
  }
  at(type, value) {
    const tok = this.peek();
    return tok.t === type && (value === undefined || tok.v === value);
  }

  parse() { const e = this.parseBoolOr(); this.expect(TT.EOF); return e; }

  parseBoolOr() {
    let left = this.parseBoolAnd();
    while (this.at(TT.IDENT, 'or')) {
      this.consume();
      const right = this.parseBoolAnd();
      left = { type: 'BoolOpExpr', op: 'or', left, right, line: this.lineNum };
    }
    return left;
  }

  parseBoolAnd() {
    let left = this.parseBoolNot();
    while (this.at(TT.IDENT, 'and')) {
      this.consume();
      const right = this.parseBoolNot();
      left = { type: 'BoolOpExpr', op: 'and', left, right, line: this.lineNum };
    }
    return left;
  }

  parseBoolNot() {
    if (this.at(TT.IDENT, 'not')) {
      this.consume();
      const operand = this.parseBoolNot();
      return { type: 'UnaryExpr', op: 'not', operand, line: this.lineNum };
    }
    return this.parseComparison();
  }

  parseComparison() {
    let left = this.parseAddSub();
    const cmpOps = ['==', '!=', '<', '>', '<=', '>='];
    while (this.at(TT.OP) && cmpOps.includes(this.peek().v)) {
      const op = this.consume().v;
      const right = this.parseAddSub();
      left = { type: 'BinaryExpr', op, left, right, line: this.lineNum };
    }
    return left;
  }

  parseAddSub() {
    let left = this.parseMulDiv();
    while (this.at(TT.OP) && (this.peek().v === '+' || this.peek().v === '-')) {
      const op = this.consume().v;
      const right = this.parseMulDiv();
      left = { type: 'BinaryExpr', op, left, right, line: this.lineNum };
    }
    return left;
  }

  parseMulDiv() {
    let left = this.parseUnary();
    while (this.at(TT.OP) && (this.peek().v === '*' || this.peek().v === '/' || this.peek().v === '%')) {
      const op = this.consume().v;
      const right = this.parseUnary();
      left = { type: 'BinaryExpr', op, left, right, line: this.lineNum };
    }
    return left;
  }

  parseUnary() {
    if (this.at(TT.OP, '-')) {
      this.consume();
      const operand = this.parseUnary();
      return { type: 'UnaryExpr', op: '-', operand, line: this.lineNum };
    }
    return this.parsePrimary();
  }

  parsePrimary() {
    const tok = this.peek();
    let expr;

    if (tok.t === TT.NUM) {
      this.consume();
      expr = { type: 'NumberExpr', value: tok.v, line: this.lineNum };
    } else if (tok.t === TT.STR) {
      this.consume();
      expr = { type: 'StringExpr', value: tok.v, line: this.lineNum };
    } else if (tok.t === TT.IDENT && tok.v === 'True') {
      this.consume(); expr = { type: 'BoolExpr', value: true, line: this.lineNum };
    } else if (tok.t === TT.IDENT && tok.v === 'False') {
      this.consume(); expr = { type: 'BoolExpr', value: false, line: this.lineNum };
    } else if (tok.t === TT.IDENT && tok.v === 'None') {
      this.consume(); expr = { type: 'NoneExpr', line: this.lineNum };
    } else if (tok.t === TT.LPAREN) {
      this.consume();
      expr = this.parseBoolOr();
      this.expect(TT.RPAREN);
    } else if (tok.t === TT.LBRACKET) {
      this.consume();
      const items = [];
      while (!this.at(TT.RBRACKET)) {
        items.push(this.parseBoolOr());
        if (this.at(TT.COMMA)) this.consume();
      }
      this.expect(TT.RBRACKET);
      expr = { type: 'ListExpr', items, line: this.lineNum };
    } else if (tok.t === TT.LBRACE) {
      this.consume();
      const pairs = [];
      while (!this.at(TT.RBRACE)) {
        const key = this.parseBoolOr();
        this.expect(TT.COLON);
        const val = this.parseBoolOr();
        pairs.push([key, val]);
        if (this.at(TT.COMMA)) this.consume();
      }
      this.expect(TT.RBRACE);
      expr = { type: 'DictExpr', pairs, line: this.lineNum };
    } else if (tok.t === TT.IDENT) {
      this.consume();
      const name = tok.v;
      if (this.at(TT.LPAREN)) {
        this.consume();
        const args = this.parseArgList(TT.RPAREN);
        this.expect(TT.RPAREN);
        expr = { type: 'CallExpr', name, args, line: this.lineNum };
      } else {
        expr = { type: 'NameExpr', name, line: this.lineNum };
      }
    } else {
      throw new PebbleError(`line ${this.lineNum}: unexpected token '${tok.v}' in expression`);
    }

    // Postfix: . [ operations (allow chaining)
    while (true) {
      if (this.at(TT.DOT)) {
        this.consume();
        const attr = this.expect(TT.IDENT).v;
        if (this.at(TT.LPAREN)) {
          this.consume();
          const args = this.parseArgList(TT.RPAREN);
          this.expect(TT.RPAREN);
          expr = { type: 'AttrCallExpr', value: expr, attr, args, line: this.lineNum };
        } else {
          expr = { type: 'AttrExpr', value: expr, attr, line: this.lineNum };
        }
      } else if (this.at(TT.LBRACKET)) {
        this.consume();
        const index = this.parseBoolOr();
        this.expect(TT.RBRACKET);
        expr = { type: 'IndexExpr', value: expr, index, line: this.lineNum };
      } else {
        break;
      }
    }

    return expr;
  }

  parseArgList(closingType) {
    const args = [];
    while (!this.at(closingType)) {
      args.push(this.parseBoolOr());
      if (this.at(TT.COMMA)) this.consume();
    }
    return args;
  }
}

function parseExpr(src, lineNum) {
  if (src === null || src === undefined || src.trim() === '') {
    throw new PebbleError(`line ${lineNum}: empty expression`);
  }
  const tokens = tokenizeExpr(src.trim(), lineNum);
  return new ExprParser(tokens, lineNum).parse();
}

// ── Statement Parser ──────────────────────────────────────────────────────────

// Prepared source line: { number, indent, text }
function prepareLines(source) {
  const lines = [];
  const raw = source.split('\n');
  for (let i = 0; i < raw.length; i++) {
    const rawLine = raw[i];
    // Strip trailing carriage return
    const line = rawLine.endsWith('\r') ? rawLine.slice(0, -1) : rawLine;
    const trimmed = line.trimEnd();

    // Skip blank and comment lines
    if (trimmed.length === 0 || trimmed.trimStart().startsWith('#')) continue;

    // Count leading spaces for indent
    let spaces = 0;
    while (spaces < trimmed.length && trimmed[spaces] === ' ') spaces++;

    if (spaces % 4 !== 0) {
      throw new PebbleError(`line ${i+1}: indentation must be a multiple of 4 spaces`);
    }

    const indent = spaces / 4;
    const text = trimmed.slice(spaces);
    lines.push({ number: i + 1, indent, text });
  }
  return lines;
}

// ASSIGN_RE: matches 'target = value' but not '==' etc.
// Target can be: name  OR  name[expr]  OR  name[expr][expr] etc.
const ASSIGN_RE = /^(.+?)\s*(?<!=)=(?!=)\s*(.+)$/s;

class PebbleParser {
  constructor(source) {
    this.lines = prepareLines(source);
    this.pos = 0;
  }

  parse() {
    return this._parseBlock(0);
  }

  _parseBlock(expectedIndent) {
    const stmts = [];
    while (this.pos < this.lines.length) {
      const line = this.lines[this.pos];
      if (line.indent < expectedIndent) break;
      if (line.indent > expectedIndent) {
        throw new PebbleError(`line ${line.number}: unexpected indentation`);
      }
      const stmt = this._parseStatement();
      if (stmt) stmts.push(stmt);
    }
    return stmts;
  }

  _parseStatement() {
    const line = this.lines[this.pos];
    const { number: num, indent, text } = line;

    // --- print ---
    if (text.startsWith('print ') || text === 'print') {
      this.pos++;
      const exprText = text.startsWith('print ') ? text.slice(6).trim() : 'None';
      return { type: 'PrintStmt', expr: parseExpr(exprText, num), line: num };
    }

    // --- return ---
    if (text === 'return' || text.startsWith('return ')) {
      this.pos++;
      const exprText = text.length > 7 ? text.slice(7).trim() : null;
      return { type: 'ReturnStmt', expr: exprText ? parseExpr(exprText, num) : null, line: num };
    }

    // --- pass ---
    if (text === 'pass') { this.pos++; return { type: 'PassStmt', line: num }; }

    // --- break ---
    if (text === 'break') { this.pos++; return { type: 'BreakStmt', line: num }; }

    // --- continue ---
    if (text === 'continue') { this.pos++; return { type: 'ContinueStmt', line: num }; }

    // --- raise ---
    if (text.startsWith('raise ')) {
      this.pos++;
      return { type: 'RaiseStmt', expr: parseExpr(text.slice(6).trim(), num), line: num };
    }

    // --- import ---
    if (text.startsWith('import ')) {
      this.pos++;
      return { type: 'ImportStmt', name: text.slice(7).trim(), line: num };
    }

    // --- if ---
    if (text.startsWith('if ') && text.endsWith(':')) {
      return this._parseIf(indent, num);
    }

    // --- while ---
    if (text.startsWith('while ') && text.endsWith(':')) {
      this.pos++;
      const condText = text.slice(6, -1).trim();
      const body = this._parseBlock(indent + 1);
      return { type: 'WhileStmt', cond: parseExpr(condText, num), body, line: num };
    }

    // --- for ---
    const forMatch = text.match(/^for\s+(\w+)\s+in\s+(.+):$/);
    if (forMatch) {
      this.pos++;
      const varName = forMatch[1];
      const iterText = forMatch[2].trim();
      const body = this._parseBlock(indent + 1);
      return { type: 'ForStmt', var: varName, iter: parseExpr(iterText, num), body, line: num };
    }

    // --- def ---
    const defMatch = text.match(/^def\s+(\w+)\s*\(([^)]*)\)\s*:$/);
    if (defMatch) {
      this.pos++;
      const name = defMatch[1];
      const params = defMatch[2].split(',').map(p => p.trim()).filter(Boolean);
      const body = this._parseBlock(indent + 1);
      return { type: 'FunctionDefStmt', name, params, body, line: num };
    }

    // --- try / except ---
    if (text === 'try:') {
      return this._parseTry(indent, num);
    }

    // --- Assignment or expression statement ---
    return this._parseAssignOrExpr(text, num, indent);
  }

  _parseIf(indent, num) {
    const branches = [];
    // Parse the first 'if'
    const ifLine = this.lines[this.pos];
    this.pos++;
    const ifCondText = ifLine.text.slice(3, -1).trim();
    const ifBody = this._parseBlock(indent + 1);
    branches.push({ cond: parseExpr(ifCondText, ifLine.number), body: ifBody });

    // Parse 'elif' and 'else' at the same indent
    while (this.pos < this.lines.length && this.lines[this.pos].indent === indent) {
      const nextLine = this.lines[this.pos];
      if (nextLine.text.startsWith('elif ') && nextLine.text.endsWith(':')) {
        this.pos++;
        const condText = nextLine.text.slice(5, -1).trim();
        const body = this._parseBlock(indent + 1);
        branches.push({ cond: parseExpr(condText, nextLine.number), body });
      } else if (nextLine.text === 'else:') {
        this.pos++;
        const body = this._parseBlock(indent + 1);
        branches.push({ cond: null, body });
        break;
      } else {
        break;
      }
    }
    return { type: 'IfStmt', branches, line: num };
  }

  _parseTry(indent, num) {
    this.pos++; // consume 'try:'
    const tryBody = this._parseBlock(indent + 1);
    let exceptBody = [];
    let errVar = null;

    if (this.pos < this.lines.length && this.lines[this.pos].indent === indent) {
      const exceptLine = this.lines[this.pos];
      if (exceptLine.text === 'except:' || exceptLine.text.startsWith('except ')) {
        this.pos++;
        if (exceptLine.text.startsWith('except ')) {
          // 'except err:' - bind error variable
          const m = exceptLine.text.match(/^except\s+(\w+)\s*:$/);
          if (m) errVar = m[1];
        }
        exceptBody = this._parseBlock(indent + 1);
      }
    }
    return { type: 'TryStmt', tryBody, exceptBody, errVar, line: num };
  }

  _parseAssignOrExpr(text, num, indent) {
    this.pos++;
    const m = text.match(ASSIGN_RE);
    if (m) {
      const targetText = m[1].trim();
      const valueText = m[2].trim();
      // Parse target: name OR name[expr]
      const target = this._parseAssignTarget(targetText, num);
      if (target) {
        return { type: 'AssignStmt', target, value: parseExpr(valueText, num), line: num };
      }
    }
    // Expression statement
    return { type: 'ExprStmt', expr: parseExpr(text, num), line: num };
  }

  _parseAssignTarget(targetText, num) {
    // Simple name: just an identifier
    if (/^\w+$/.test(targetText)) {
      return { type: 'NameTarget', name: targetText };
    }
    // Indexed: name[expr] (possibly nested)
    const bracketIdx = targetText.indexOf('[');
    if (bracketIdx > 0 && targetText.endsWith(']')) {
      const containerText = targetText.slice(0, bracketIdx).trim();
      const indexText = targetText.slice(bracketIdx + 1, -1).trim();
      // Container can itself be indexed, so parse it as an expression
      const containerExpr = parseExpr(containerText, num);
      const indexExpr = parseExpr(indexText, num);
      return { type: 'IndexTarget', container: containerExpr, index: indexExpr };
    }
    return null; // not an assignment target
  }
}

// ── Interpreter ───────────────────────────────────────────────────────────────

export class PebbleInterpreter {
  constructor({ hostFunctions, outputConsumer, inputProvider, fs, state } = {}) {
    this.hostFunctions = hostFunctions || {};
    this.outputConsumer = outputConsumer || null;
    this.inputProvider = inputProvider || null;
    this.fs = fs || null;
    this.state = state || {};
    this.globals = {};
    this.functions = {};
    this.moduleCache = {};
    this.moduleLoading = new Set();
    this.output = [];
  }

  // Execute source code with optional initial globals.
  // Returns list of output strings.
  async execute(source, initialGlobals) {
    this.globals = {
      CWD: this.state.cwd || '/',
      FS_MODE: 'mfs',
      ARGC: 0,
      ARGV: [],
      ENV: {},
    };
    this.functions = {};
    this.output = [];
    // Reset module caches but keep shared ones from parent if set externally
    if (!this._sharedModuleCache) {
      this.moduleCache = {};
      this.moduleLoading = new Set();
    }

    if (initialGlobals) {
      for (const [k, v] of Object.entries(initialGlobals)) {
        this.globals[k] = this._clone(v);
      }
    }

    const stmts = new PebbleParser(source).parse();
    await this._executeBlock(stmts, null);
    return this.output;
  }

  // Call a function defined in the current interpreter's global scope.
  async callGlobalFunction(name, args) {
    return await this._invokeByName(name, args, 0, null);
  }

  // ── Statement execution ───────────────────────────────────────────────────

  async _executeBlock(stmts, env) {
    for (const stmt of stmts) {
      await this._executeStatement(stmt, env);
    }
  }

  async _executeStatement(stmt, env) {
    switch (stmt.type) {
      case 'AssignStmt': {
        const val = await this._evalExpr(stmt.value, env);
        await this._assignTarget(stmt.target, val, env);
        break;
      }
      case 'PrintStmt': {
        const val = await this._evalExpr(stmt.expr, env);
        this._emit(this._stringify(val));
        break;
      }
      case 'ExprStmt': {
        await this._evalExpr(stmt.expr, env);
        break;
      }
      case 'ReturnStmt': {
        const val = stmt.expr ? await this._evalExpr(stmt.expr, env) : 0;
        throw new ReturnSignal(val);
      }
      case 'PassStmt': break;
      case 'BreakStmt': throw new BreakSignal();
      case 'ContinueStmt': throw new ContinueSignal();
      case 'RaiseStmt': {
        const msg = await this._evalExpr(stmt.expr, env);
        throw new PebbleError(String(msg));
      }
      case 'ImportStmt': {
        const mod = await this._importModule(stmt.name, stmt.line);
        this._bindModule(stmt.name, mod, env);
        break;
      }
      case 'FunctionDefStmt': {
        // Store the function definition; accessible by name lookup
        this.functions[stmt.name] = stmt;
        break;
      }
      case 'IfStmt': {
        for (const branch of stmt.branches) {
          if (branch.cond === null || this._truthy(await this._evalExpr(branch.cond, env))) {
            await this._executeBlock(branch.body, env);
            break;
          }
        }
        break;
      }
      case 'WhileStmt': {
        while (this._truthy(await this._evalExpr(stmt.cond, env))) {
          try {
            await this._executeBlock(stmt.body, env);
          } catch (e) {
            if (e instanceof BreakSignal) break;
            if (e instanceof ContinueSignal) continue;
            throw e;
          }
        }
        break;
      }
      case 'ForStmt': {
        const iterVal = await this._evalExpr(stmt.iter, env);
        const items = this._iterate(iterVal, stmt.line);
        for (const item of items) {
          this._writeVar(stmt.var, this._clone(item), env);
          try {
            await this._executeBlock(stmt.body, env);
          } catch (e) {
            if (e instanceof BreakSignal) break;
            if (e instanceof ContinueSignal) continue;
            throw e;
          }
        }
        break;
      }
      case 'TryStmt': {
        try {
          await this._executeBlock(stmt.tryBody, env);
        } catch (e) {
          if (e instanceof PebbleError || e instanceof Error) {
            const handlerEnv = env ? { ...env } : {};
            if (stmt.errVar) {
              handlerEnv[stmt.errVar] = e.message || String(e);
            }
            // Also store in globals so it's accessible without local env (module context)
            const savedErr = this.globals[stmt.errVar || '__err__'];
            if (stmt.errVar) this.globals[stmt.errVar] = e.message || String(e);
            await this._executeBlock(stmt.exceptBody, handlerEnv);
            if (stmt.errVar) this.globals[stmt.errVar] = savedErr;
          } else {
            throw e; // Re-throw control flow signals
          }
        }
        break;
      }
      default:
        throw new PebbleError(`line ${stmt.line}: unknown statement type '${stmt.type}'`);
    }
  }

  async _assignTarget(target, value, env) {
    if (target.type === 'NameTarget') {
      this._writeVar(target.name, value, env);
    } else if (target.type === 'IndexTarget') {
      const container = await this._evalExpr(target.container, env);
      const index = await this._evalExpr(target.index, env);
      if (Array.isArray(container)) {
        if (typeof index !== 'number' || !Number.isInteger(index)) {
          throw new PebbleError(`list index must be an integer`);
        }
        if (index < 0 || index >= container.length) {
          throw new PebbleError(`list index out of range`);
        }
        container[index] = this._clone(value);
      } else if (container && typeof container === 'object' && !(container instanceof ModuleObject)) {
        container[index] = this._clone(value);
      } else {
        throw new PebbleError(`cannot index-assign into this type`);
      }
    }
  }

  // ── Expression evaluation ─────────────────────────────────────────────────

  async _evalExpr(expr, env) {
    switch (expr.type) {
      case 'NumberExpr': return expr.value;
      case 'StringExpr': return expr.value;
      case 'BoolExpr': return expr.value;
      case 'NoneExpr': return null;
      case 'NameExpr': return this._readVar(expr.name, expr.line, env);
      case 'UnaryExpr': {
        const val = await this._evalExpr(expr.operand, env);
        if (expr.op === '-') {
          if (typeof val !== 'number') throw new PebbleError(`line ${expr.line}: unary '-' requires a number`);
          return -val;
        }
        if (expr.op === 'not') return !this._truthy(val);
        throw new PebbleError(`line ${expr.line}: unknown unary op '${expr.op}'`);
      }
      case 'BinaryExpr': {
        const l = await this._evalExpr(expr.left, env);
        const r = await this._evalExpr(expr.right, env);
        return this._evalBinary(expr.op, l, r, expr.line);
      }
      case 'BoolOpExpr': {
        const l = await this._evalExpr(expr.left, env);
        if (expr.op === 'and') {
          return this._truthy(l) ? await this._evalExpr(expr.right, env) : l;
        }
        if (expr.op === 'or') {
          return this._truthy(l) ? l : await this._evalExpr(expr.right, env);
        }
        throw new PebbleError(`line ${expr.line}: unknown bool op '${expr.op}'`);
      }
      case 'CallExpr': {
        const args = [];
        for (const a of expr.args) args.push(await this._evalExpr(a, env));
        return await this._invokeByName(expr.name, args, expr.line, env);
      }
      case 'AttrCallExpr': {
        const target = await this._evalExpr(expr.value, env);
        const args = [];
        for (const a of expr.args) args.push(await this._evalExpr(a, env));
        return await this._callModuleMember(target, expr.attr, args, expr.line);
      }
      case 'AttrExpr': {
        const target = await this._evalExpr(expr.value, env);
        return this._getModuleMember(target, expr.attr, expr.line);
      }
      case 'ListExpr': {
        const items = [];
        for (const item of expr.items) items.push(this._clone(await this._evalExpr(item, env)));
        return items;
      }
      case 'DictExpr': {
        const out = {};
        for (const [k, v] of expr.pairs) {
          out[await this._evalExpr(k, env)] = this._clone(await this._evalExpr(v, env));
        }
        return out;
      }
      case 'IndexExpr': {
        const container = await this._evalExpr(expr.value, env);
        const idx = await this._evalExpr(expr.index, env);
        if (Array.isArray(container)) {
          if (!Number.isInteger(idx)) throw new PebbleError(`line ${expr.line}: list index must be integer`);
          if (idx < 0 || idx >= container.length) throw new PebbleError(`line ${expr.line}: index out of range`);
          return container[idx];
        }
        if (typeof container === 'string') {
          if (!Number.isInteger(idx)) throw new PebbleError(`line ${expr.line}: string index must be integer`);
          if (idx < 0 || idx >= container.length) throw new PebbleError(`line ${expr.line}: index out of range`);
          return container[idx];
        }
        if (container && typeof container === 'object' && !(container instanceof ModuleObject)) {
          if (!Object.prototype.hasOwnProperty.call(container, idx)) {
            throw new PebbleError(`line ${expr.line}: key '${idx}' not in dict`);
          }
          return container[idx];
        }
        throw new PebbleError(`line ${expr.line}: cannot index this type`);
      }
      default:
        throw new PebbleError(`line ${expr.line || 0}: unknown expr type '${expr.type}'`);
    }
  }

  _evalBinary(op, l, r, line) {
    switch (op) {
      case '+':
        if (typeof l === 'number' && typeof r === 'number') return l + r;
        if (typeof l === 'string' && typeof r === 'string') return l + r;
        if (Array.isArray(l) && Array.isArray(r)) return [...this._cloneList(l), ...this._cloneList(r)];
        if (typeof l === 'string') return l + this._stringify(r);
        throw new PebbleError(`line ${line}: '+' requires numeric, string, or list operands`);
      case '-':
        if (typeof l === 'number' && typeof r === 'number') return l - r;
        throw new PebbleError(`line ${line}: '-' requires numeric operands`);
      case '*':
        if (typeof l === 'number' && typeof r === 'number') return l * r;
        throw new PebbleError(`line ${line}: '*' requires numeric operands`);
      case '/':
        if (typeof l === 'number' && typeof r === 'number') {
          if (r === 0) throw new PebbleError(`line ${line}: division by zero`);
          return l / r;
        }
        throw new PebbleError(`line ${line}: '/' requires numeric operands`);
      case '%':
        if (typeof l === 'number' && typeof r === 'number') {
          if (r === 0) throw new PebbleError(`line ${line}: modulo by zero`);
          return l % r;
        }
        throw new PebbleError(`line ${line}: '%' requires numeric operands`);
      case '<': return l < r ? 1 : 0;
      case '>': return l > r ? 1 : 0;
      case '==': return l == r ? 1 : 0;  // intentional == for cross-type
      case '!=': return l != r ? 1 : 0;
      case '<=': return l <= r ? 1 : 0;
      case '>=': return l >= r ? 1 : 0;
      default:
        throw new PebbleError(`line ${line}: unknown operator '${op}'`);
    }
  }

  // ── Function invocation ───────────────────────────────────────────────────

  async _invokeByName(name, args, line, env) {
    // 1. Check if name is a variable holding a function reference
    let target = null;
    try { target = this._readVar(name, line, env); } catch (_) {}
    if (target instanceof ModuleObject) {
      // treat as module call - shouldn't happen for plain names
    }
    // If it's a function name variable, invoke as user function
    if (this.functions[name]) {
      return await this._callUserFunction(name, args, line, env);
    }
    // 2. Built-ins
    return await this._callBuiltin(name, args, line, env);
  }

  async _callUserFunction(name, args, line, env) {
    const fn = this.functions[name];
    if (!fn) throw new PebbleError(`line ${line}: unknown function '${name}'`);
    if (args.length !== fn.params.length) {
      throw new PebbleError(`line ${line}: function '${name}' expected ${fn.params.length} args but got ${args.length}`);
    }
    const frame = {};
    for (let i = 0; i < fn.params.length; i++) {
      frame[fn.params[i]] = this._clone(args[i]);
    }
    try {
      await this._executeBlock(fn.body, frame);
    } catch (e) {
      if (e instanceof ReturnSignal) return this._clone(e.value);
      throw e;
    }
    return 0;
  }

  async _callBuiltin(name, args, line, env) {
    // ── Core builtins ──────────────────────────────────────────────────────
    if (name === 'len') {
      if (args.length !== 1) throw new PebbleError(`line ${line}: len() takes 1 argument`);
      const v = args[0];
      if (typeof v === 'string') return v.length;
      if (Array.isArray(v)) return v.length;
      if (v && typeof v === 'object' && !(v instanceof ModuleObject)) return Object.keys(v).length;
      throw new PebbleError(`line ${line}: len() expects string, list, or dict`);
    }
    if (name === 'append') {
      if (args.length !== 2 || !Array.isArray(args[0])) throw new PebbleError(`line ${line}: append(list, item) requires a list`);
      args[0].push(this._clone(args[1]));
      return args[0];
    }
    if (name === 'range') {
      if (args.length < 1 || args.length > 3) throw new PebbleError(`line ${line}: range() takes 1-3 arguments`);
      let start = 0, stop = 0, step = 1;
      if (args.length === 1) stop = args[0];
      else if (args.length === 2) { start = args[0]; stop = args[1]; }
      else { start = args[0]; stop = args[1]; step = args[2]; }
      if (step === 0) throw new PebbleError(`line ${line}: range() step cannot be zero`);
      const out = [];
      for (let i = start; step > 0 ? i < stop : i > stop; i += step) out.push(i);
      return out;
    }
    if (name === 'str') {
      if (args.length !== 1) throw new PebbleError(`line ${line}: str() takes 1 argument`);
      return this._stringify(args[0]);
    }
    if (name === 'int') {
      if (args.length !== 1) throw new PebbleError(`line ${line}: int() takes 1 argument`);
      const v = args[0];
      if (typeof v === 'number') return Math.trunc(v);
      if (typeof v === 'boolean') return v ? 1 : 0;
      if (typeof v === 'string') {
        const n = parseInt(v.trim(), 10);
        if (isNaN(n)) throw new PebbleError(`line ${line}: int() could not parse '${v}'`);
        return n;
      }
      throw new PebbleError(`line ${line}: int() expects string, int, or float`);
    }
    if (name === 'float') {
      if (args.length !== 1) throw new PebbleError(`line ${line}: float() takes 1 argument`);
      const v = args[0];
      if (typeof v === 'number') return v;
      if (typeof v === 'string') {
        const n = parseFloat(v.trim());
        if (isNaN(n)) throw new PebbleError(`line ${line}: float() could not parse '${v}'`);
        return n;
      }
      throw new PebbleError(`line ${line}: float() expects string, int, or float`);
    }
    if (name === 'bool') {
      if (args.length !== 1) throw new PebbleError(`line ${line}: bool() takes 1 argument`);
      return this._truthy(args[0]) ? 1 : 0;
    }
    if (name === 'keys') {
      if (args.length !== 1) throw new PebbleError(`line ${line}: keys() takes 1 argument`);
      const v = args[0];
      if (!v || typeof v !== 'object' || Array.isArray(v) || v instanceof ModuleObject) {
        throw new PebbleError(`line ${line}: keys() expects a dict`);
      }
      return Object.keys(v);
    }
    if (name === 'argv') {
      if (args.length !== 1 || typeof args[0] !== 'number') throw new PebbleError(`line ${line}: argv() expects an integer index`);
      const argv = this.globals['ARGV'] || [];
      const idx = Math.trunc(args[0]);
      if (idx < 0 || idx >= argv.length) throw new PebbleError(`line ${line}: argv index out of range`);
      return argv[idx];
    }
    if (name === 'input') {
      if (args.length !== 1) throw new PebbleError(`line ${line}: input() takes 1 argument`);
      if (!this.inputProvider) throw new PebbleError(`line ${line}: input() not available`);
      return await this.inputProvider(typeof args[0] === 'string' ? args[0] : this._stringify(args[0]));
    }
    if (name === 'print') {
      // Pebble uses 'print expr' as a statement, but sometimes called as function
      const text = args.map(a => this._stringify(a)).join(' ');
      this._emit(text);
      return 0;
    }
    if (name === 'read_file') {
      if (args.length !== 1 || typeof args[0] !== 'string') throw new PebbleError(`line ${line}: read_file() takes a string path`);
      if (!this.fs) throw new PebbleError(`line ${line}: filesystem not available`);
      return this.fs.readFile(args[0]);
    }
    if (name === 'write_file') {
      if (args.length !== 2 || typeof args[0] !== 'string') throw new PebbleError(`line ${line}: write_file(path, text)`);
      if (!this.fs) throw new PebbleError(`line ${line}: filesystem not available`);
      return this.fs.writeFile(args[0], args[1]);
    }
    if (name === 'int_div') {
      if (args.length !== 2) throw new PebbleError(`line ${line}: int_div() takes 2 args`);
      const [a, b] = args;
      if (b === 0) throw new PebbleError(`line ${line}: division by zero`);
      return Math.trunc(a / b);
    }
    if (name === 'chr') {
      if (args.length !== 1) throw new PebbleError(`line ${line}: chr() takes 1 argument`);
      return String.fromCharCode(args[0]);
    }
    if (name === 'ord') {
      if (args.length !== 1 || typeof args[0] !== 'string' || args[0].length === 0) {
        throw new PebbleError(`line ${line}: ord() takes a non-empty string`);
      }
      return args[0].charCodeAt(0);
    }
    if (name === 'abs') {
      if (args.length !== 1 || typeof args[0] !== 'number') throw new PebbleError(`line ${line}: abs() takes a number`);
      return Math.abs(args[0]);
    }
    if (name === 'min') {
      if (args.length < 2) throw new PebbleError(`line ${line}: min() takes ≥ 2 args`);
      return args.reduce((a, b) => a < b ? a : b);
    }
    if (name === 'max') {
      if (args.length < 2) throw new PebbleError(`line ${line}: max() takes ≥ 2 args`);
      return args.reduce((a, b) => a > b ? a : b);
    }
    if (name === 'round') {
      if (args.length !== 1) throw new PebbleError(`line ${line}: round() takes 1 arg`);
      return Math.round(args[0]);
    }
    if (name === 'type') {
      if (args.length !== 1) throw new PebbleError(`line ${line}: type() takes 1 arg`);
      const v = args[0];
      if (v === null) return 'NoneType';
      if (typeof v === 'boolean') return 'bool';
      if (typeof v === 'number') return Number.isInteger(v) ? 'int' : 'float';
      if (typeof v === 'string') return 'str';
      if (Array.isArray(v)) return 'list';
      if (v instanceof ModuleObject) return 'module';
      return 'dict';
    }
    if (name === 'sorted') {
      if (args.length !== 1 || !Array.isArray(args[0])) throw new PebbleError(`line ${line}: sorted() takes a list`);
      return [...args[0]].sort((a, b) => a < b ? -1 : a > b ? 1 : 0);
    }

    // ── Builtin math aliases (used in runtime.peb before math module is loaded) ──
    if (name === 'pow') {
      if (args.length !== 2) throw new PebbleError(`line ${line}: pow() takes 2 args`);
      return Math.pow(args[0], args[1]);
    }
    if (name === 'sqrt') {
      if (args.length !== 1) throw new PebbleError(`line ${line}: sqrt() takes 1 arg`);
      return Math.floor(Math.sqrt(args[0]));
    }

    // ── Host functions ────────────────────────────────────────────────────────
    const hostFn = this.hostFunctions[name];
    if (hostFn) {
      return await hostFn(args, line);
    }

    throw new PebbleError(`line ${line}: unknown function '${name}'`);
  }

  // ── Module system ─────────────────────────────────────────────────────────

  async _importModule(name, line) {
    if (this.moduleCache[name]) return this.moduleCache[name];
    if (this.moduleLoading.has(name)) throw new PebbleError(`line ${line}: circular import '${name}'`);

    // Built-in modules
    if (name === 'math') {
      const mod = new ModuleObject('math', {
        abs: 'abs', pow: 'pow', sqrt: 'sqrt',
        sin: '__math_sin', cos: '__math_cos', tan: '__math_tan',
        floor: '__math_floor', ceil: '__math_ceil',
      }, {}, {});
      this.moduleCache[name] = mod;
      return mod;
    }
    if (name === 'text') {
      const mod = new ModuleObject('text', {
        len: 'len', repeat: '__text_repeat', lines: '__text_lines',
        join: '__text_join', first_line: '__text_first_line',
      }, {}, {});
      this.moduleCache[name] = mod;
      return mod;
    }
    if (name === 'os') {
      const mod = new ModuleObject('os', {
        list: 'raw_list_files', exists: 'raw_file_exists',
        read: 'raw_read_file', write: 'raw_write_file',
        delete: 'raw_delete_file', time: 'current_time',
      }, {}, {});
      this.moduleCache[name] = mod;
      return mod;
    }
    if (name === 'random') {
      const mod = new ModuleObject('random', {
        seed: '__random_seed', next: '__random_next', range: '__random_range',
      }, {}, {});
      this.moduleCache[name] = mod;
      return mod;
    }

    // File-based module: convert 'system.kernel.proc' to 'system/kernel/proc.peb'
    const filePath = name.replace(/\./g, '/') + '.peb';
    if (!this.fs) throw new PebbleError(`line ${line}: filesystem not available for import '${name}'`);
    if (!this.fs.fileExists(filePath)) {
      throw new PebbleError(`line ${line}: module '${name}' not found (tried '${filePath}')`);
    }
    const source = this.fs.readFile(filePath);
    this.moduleLoading.add(name);
    try {
      const mod = await this._loadUserModule(name, source, line);
      this.moduleCache[name] = mod;
      return mod;
    } finally {
      this.moduleLoading.delete(name);
    }
  }

  async _loadUserModule(name, source, line) {
    const child = new PebbleInterpreter({
      hostFunctions: this.hostFunctions,
      outputConsumer: null,  // modules suppress output
      inputProvider: this.inputProvider,
      fs: this.fs,
      state: this.state,
    });
    child.moduleCache = this.moduleCache;
    child.moduleLoading = this.moduleLoading;
    child._sharedModuleCache = true;
    await child.execute(source, this.globals);
    return new ModuleObject(name, {}, child.globals, child.functions);
  }

  _bindModule(name, module, env) {
    const parts = name.split('.');
    if (parts.length === 1) {
      this._writeVar(name, module, env);
      return;
    }
    // Build nested module objects: system -> {kernel: {proc: module}}
    const rootName = parts[0];
    let root;
    try { root = this._readVar(rootName, 0, env); } catch (_) { root = null; }
    if (!(root instanceof ModuleObject)) {
      root = new ModuleObject(rootName, {}, {}, {});
    }
    // Navigate to the parent of the last part
    let cursor = root;
    for (let i = 1; i < parts.length - 1; i++) {
      const part = parts[i];
      let child = cursor.values[part];
      if (!(child instanceof ModuleObject)) {
        child = new ModuleObject(part, {}, {}, {});
        cursor.values[part] = child;
      }
      cursor = child;
    }
    cursor.values[parts[parts.length - 1]] = module;
    this._writeVar(rootName, root, env);
  }

  async _callModuleMember(target, attr, args, line) {
    if (!(target instanceof ModuleObject)) {
      throw new PebbleError(`line ${line}: attribute calls require a module, got ${typeof target}`);
    }
    // Check built-in aliases
    const builtinName = target.builtins[attr];
    if (builtinName) {
      // Handle special built-in module functions
      if (builtinName === '__math_sin') {
        if (args.length !== 1) throw new PebbleError(`line ${line}: math.sin() takes 1 arg`);
        return Math.round(Math.sin(args[0] * Math.PI / 180) * 10000);
      }
      if (builtinName === '__math_cos') {
        if (args.length !== 1) throw new PebbleError(`line ${line}: math.cos() takes 1 arg`);
        return Math.round(Math.cos(args[0] * Math.PI / 180) * 10000);
      }
      if (builtinName === '__math_tan') {
        if (args.length !== 1) throw new PebbleError(`line ${line}: math.tan() takes 1 arg`);
        return Math.round(Math.tan(args[0] * Math.PI / 180) * 10000);
      }
      if (builtinName === '__math_floor') {
        if (args.length !== 1) throw new PebbleError(`line ${line}: math.floor() takes 1 arg`);
        return Math.floor(args[0]);
      }
      if (builtinName === '__math_ceil') {
        if (args.length !== 1) throw new PebbleError(`line ${line}: math.ceil() takes 1 arg`);
        return Math.ceil(args[0]);
      }
      if (builtinName === '__text_repeat') {
        if (args.length !== 2) throw new PebbleError(`line ${line}: text.repeat() takes 2 args`);
        return String(args[0]).repeat(args[1]);
      }
      if (builtinName === '__text_lines') {
        if (args.length !== 1) throw new PebbleError(`line ${line}: text.lines() takes 1 arg`);
        return String(args[0]).split('\n');
      }
      if (builtinName === '__text_join') {
        if (args.length !== 2) throw new PebbleError(`line ${line}: text.join() takes 2 args`);
        return args[0].join(String(args[1]));
      }
      if (builtinName === '__text_first_line') {
        if (args.length !== 1) throw new PebbleError(`line ${line}: text.first_line() takes 1 arg`);
        return String(args[0]).split('\n')[0];
      }
      if (builtinName === '__random_seed') { this._rngState = args[0] || 42; return 0; }
      if (builtinName === '__random_next') {
        if (!this._rngState) this._rngState = Date.now();
        this._rngState = (this._rngState * 1664525 + 1013904223) & 0xffffffff;
        return Math.abs(this._rngState);
      }
      if (builtinName === '__random_range') {
        if (args.length !== 1) throw new PebbleError(`line ${line}: random.range() takes 1 arg`);
        if (!this._rngState) this._rngState = Date.now();
        this._rngState = (this._rngState * 1664525 + 1013904223) & 0xffffffff;
        return Math.abs(this._rngState) % args[0];
      }
      // Delegate to named builtin/host
      return await this._invokeByName(builtinName, args, line, null);
    }
    // Check user-defined functions in the module
    const fn = target.functions[attr];
    if (fn) {
      return await this._callModuleFunction(target, attr, args, line);
    }
    throw new PebbleError(`line ${line}: module '${target.name}' has no callable member '${attr}'`);
  }

  async _callModuleFunction(module, funcName, args, line) {
    const savedGlobals = this.globals;
    const savedFunctions = this.functions;
    try {
      this.globals = module.values;
      this.functions = module.functions;
      return await this._callUserFunction(funcName, args, line, null);
    } finally {
      this.globals = savedGlobals;
      this.functions = savedFunctions;
    }
  }

  _getModuleMember(target, attr, line) {
    if (!(target instanceof ModuleObject)) {
      throw new PebbleError(`line ${line}: attribute access requires a module`);
    }
    if (Object.prototype.hasOwnProperty.call(target.values, attr)) return target.values[attr];
    if (Object.prototype.hasOwnProperty.call(target.functions, attr)) {
      // Return a callable reference (not needed for our use case)
      return `<function ${target.name}.${attr}>`;
    }
    if (Object.prototype.hasOwnProperty.call(target.builtins, attr)) {
      return `<builtin ${target.name}.${attr}>`;
    }
    throw new PebbleError(`line ${line}: module '${target.name}' has no member '${attr}'`);
  }

  // ── Variable access ───────────────────────────────────────────────────────

  _readVar(name, line, env) {
    if (env && Object.prototype.hasOwnProperty.call(env, name)) return env[name];
    if (Object.prototype.hasOwnProperty.call(this.globals, name)) return this.globals[name];
    if (this.functions[name]) return `<function ${name}>`;
    throw new PebbleError(`line ${line}: unknown variable '${name}'`);
  }

  _writeVar(name, value, env) {
    if (env !== null && env !== undefined) {
      env[name] = value;
    } else {
      this.globals[name] = value;
    }
  }

  // ── Utilities ─────────────────────────────────────────────────────────────

  _truthy(value) {
    if (value === 0 || value === '' || value === null || value === undefined || value === false) return false;
    if (typeof value === 'number' && isNaN(value)) return false;
    if (Array.isArray(value)) return value.length > 0;
    if (value && typeof value === 'object' && !(value instanceof ModuleObject)) {
      return Object.keys(value).length > 0;
    }
    return true;
  }

  _stringify(value) {
    if (value === null || value === undefined) return 'None';
    if (typeof value === 'boolean') return value ? 'True' : 'False';
    if (typeof value === 'number') {
      if (Number.isInteger(value)) return String(value);
      // Float: format like Python
      let s = String(value);
      if (s.includes('e') || s.includes('E')) return s;
      return s;
    }
    if (typeof value === 'string') return value;
    if (value instanceof ModuleObject) return `<module ${value.name}>`;
    if (Array.isArray(value)) return '[' + value.map(v => this._repr(v)).join(', ') + ']';
    if (typeof value === 'object') {
      const parts = Object.entries(value).map(([k, v]) => `${this._repr(k)}: ${this._repr(v)}`);
      return '{' + parts.join(', ') + '}';
    }
    return String(value);
  }

  _repr(value) {
    if (typeof value === 'string') return `'${value}'`;
    return this._stringify(value);
  }

  _clone(value) {
    if (value === null || value === undefined) return null;
    if (typeof value !== 'object') return value;
    if (value instanceof ModuleObject) return value;  // modules are shared
    if (Array.isArray(value)) return value.map(v => this._clone(v));
    const out = {};
    for (const [k, v] of Object.entries(value)) out[k] = this._clone(v);
    return out;
  }

  _cloneList(arr) {
    return arr.map(v => this._clone(v));
  }

  _iterate(value, line) {
    if (Array.isArray(value)) return value;
    if (typeof value === 'string') return Array.from(value);
    if (value && typeof value === 'object' && !(value instanceof ModuleObject)) {
      return Object.keys(value);
    }
    throw new PebbleError(`line ${line || 0}: cannot iterate over this type`);
  }

  _emit(text) {
    this.output.push(text);
    if (this.outputConsumer) this.outputConsumer(text);
  }
}
