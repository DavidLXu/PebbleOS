from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


class PebbleError(Exception):
    """Raised when Pebble source is invalid or fails at runtime."""


IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DEF_RE = re.compile(r"^def\s+([A-Za-z_][A-Za-z0-9_]*)\((.*)\):$")
FOR_RE = re.compile(r"^for\s+([A-Za-z_][A-Za-z0-9_]*)\s+in\s+(.+):$")
ASSIGN_RE = re.compile(r"^(.+?)\s*=(?!=)\s*(.+)$")


@dataclass
class SourceLine:
    number: int
    indent: int
    text: str


@dataclass
class NumberExpr:
    value: int | float
    line_number: int


@dataclass
class StringExpr:
    value: str
    line_number: int


@dataclass
class BoolExpr:
    value: bool
    line_number: int


@dataclass
class NoneExpr:
    line_number: int


@dataclass
class NameExpr:
    name: str
    line_number: int


@dataclass
class UnaryExpr:
    op: str
    operand: "Expr"
    line_number: int


@dataclass
class BinaryExpr:
    left: "Expr"
    op: str
    right: "Expr"
    line_number: int


@dataclass
class BoolOpExpr:
    left: "Expr"
    op: str
    right: "Expr"
    line_number: int


@dataclass
class CallExpr:
    name: str
    args: list["Expr"]
    line_number: int


@dataclass
class AttrCallExpr:
    value: "Expr"
    attr: str
    args: list["Expr"]
    line_number: int


@dataclass
class AttrExpr:
    value: "Expr"
    attr: str
    line_number: int


@dataclass
class ListExpr:
    items: list["Expr"]
    line_number: int


@dataclass
class IndexExpr:
    value: "Expr"
    index: "Expr"
    line_number: int


@dataclass
class DictExpr:
    items: list[tuple["Expr", "Expr"]]
    line_number: int


Expr = (
    NumberExpr
    | StringExpr
    | BoolExpr
    | NoneExpr
    | NameExpr
    | UnaryExpr
    | BinaryExpr
    | BoolOpExpr
    | CallExpr
    | AttrCallExpr
    | AttrExpr
    | ListExpr
    | IndexExpr
    | DictExpr
)


@dataclass
class ModuleObject:
    name: str
    builtins: dict[str, str]
    values: dict[str, "Value"]
    functions: dict[str, object]


Value = int | float | bool | None | str | ModuleObject | list["Value"] | dict["Value", "Value"]
HostFunction = Callable[[list[Value], int], Value]


@dataclass
class NameTarget:
    name: str
    line_number: int


@dataclass
class IndexTarget:
    value: Expr
    index: Expr
    line_number: int


Target = NameTarget | IndexTarget


@dataclass
class AssignStmt:
    target: Target
    expr: Expr
    line_number: int


@dataclass
class PrintStmt:
    expr: Expr
    line_number: int


@dataclass
class ExprStmt:
    expr: Expr
    line_number: int


@dataclass
class PassStmt:
    line_number: int


@dataclass
class BreakStmt:
    line_number: int


@dataclass
class ContinueStmt:
    line_number: int


@dataclass
class IfBranch:
    condition: Expr
    body: list["Stmt"]
    line_number: int


@dataclass
class IfStmt:
    branches: list[IfBranch]
    else_body: list["Stmt"] | None
    line_number: int


@dataclass
class WhileStmt:
    condition: Expr
    body: list["Stmt"]
    line_number: int


@dataclass
class ForStmt:
    name: str
    iterable: Expr
    body: list["Stmt"]
    line_number: int


@dataclass
class FunctionDefStmt:
    name: str
    params: list[str]
    body: list["Stmt"]
    line_number: int


@dataclass
class ReturnStmt:
    expr: Expr
    line_number: int


@dataclass
class ImportStmt:
    module: str
    line_number: int


Stmt = (
    AssignStmt
    | PrintStmt
    | ExprStmt
    | PassStmt
    | BreakStmt
    | ContinueStmt
    | IfStmt
    | WhileStmt
    | ForStmt
    | FunctionDefStmt
    | ReturnStmt
    | ImportStmt
)


@dataclass
class UserFunction:
    name: str
    params: list[str]
    body: list[Stmt]
    line_number: int


@dataclass
class BytecodeFunction:
    name: str
    params: list[str]
    code: list[tuple]
    line_number: int


@dataclass
class VMFrame:
    name: str
    locals: dict[str, "Value"]
    line_number: int


@dataclass
class VMState:
    value_stack: list["Value"]
    frame_stack: list[VMFrame]


class ReturnSignal(Exception):
    def __init__(self, value: Value) -> None:
        super().__init__()
        self.value = value


class BreakSignal(Exception):
    pass


class ContinueSignal(Exception):
    pass


class Parser:
    def __init__(self, source: str) -> None:
        self.lines = self._prepare_lines(source)
        self.index = 0

    def parse(self) -> list[Stmt]:
        return self._parse_block(expected_indent=0)

    def _prepare_lines(self, source: str) -> list[SourceLine]:
        prepared: list[SourceLine] = []
        for number, raw_line in enumerate(source.splitlines(), start=1):
            if "\t" in raw_line:
                raise PebbleError(f"line {number}: tabs are not allowed; use four spaces")
            cleaned_line = self._strip_comment(raw_line)

            if not cleaned_line.strip():
                continue

            stripped = cleaned_line.lstrip(" ")
            indent_spaces = len(cleaned_line) - len(stripped)
            if indent_spaces % 4 != 0:
                raise PebbleError(f"line {number}: indentation must use multiples of four spaces")

            prepared.append(
                SourceLine(number=number, indent=indent_spaces // 4, text=stripped.rstrip())
            )
        return prepared

    def _strip_comment(self, raw_line: str) -> str:
        out = ""
        in_string = 0
        quote = ""
        i = 0
        while i < len(raw_line):
            ch = raw_line[i]
            if in_string:
                out = out + ch
                if ch == quote:
                    in_string = 0
                    quote = ""
                i = i + 1
                continue
            if ch == '"' or ch == "'":
                in_string = 1
                quote = ch
                out = out + ch
                i = i + 1
                continue
            if ch == "#":
                return out.rstrip()
            out = out + ch
            i = i + 1
        return out.rstrip()

    def _parse_block(self, expected_indent: int) -> list[Stmt]:
        statements: list[Stmt] = []
        while self.index < len(self.lines):
            line = self.lines[self.index]
            if line.indent < expected_indent:
                break
            if line.indent > expected_indent:
                raise PebbleError(f"line {line.number}: unexpected indentation")
            statements.append(self._parse_statement(line))
        return statements

    def _parse_statement(self, line: SourceLine) -> Stmt:
        text = line.text

        if text.startswith("print "):
            self.index += 1
            return PrintStmt(expr=self._parse_expression(text[6:], line.number), line_number=line.number)

        if text.startswith("if "):
            return self._parse_if_statement(line)

        if text.startswith("elif "):
            raise PebbleError(f"line {line.number}: elif without matching if")

        if text == "else:":
            raise PebbleError(f"line {line.number}: else without matching if")

        if text.startswith("while "):
            if not text.endswith(":"):
                raise PebbleError(f"line {line.number}: while statements must end with ':'")
            condition_text = text[6:-1].strip()
            if not condition_text:
                raise PebbleError(f"line {line.number}: missing while condition")
            self.index += 1
            return WhileStmt(
                condition=self._parse_expression(condition_text, line.number),
                body=self._parse_indented_block(line),
                line_number=line.number,
            )

        if text.startswith("for "):
            match = FOR_RE.match(text)
            if not match:
                raise PebbleError(f"line {line.number}: invalid for loop syntax")
            self.index += 1
            return ForStmt(
                name=match.group(1),
                iterable=self._parse_expression(match.group(2), line.number),
                body=self._parse_indented_block(line),
                line_number=line.number,
            )

        if text.startswith("def "):
            match = DEF_RE.match(text)
            if not match:
                raise PebbleError(f"line {line.number}: invalid function definition")
            params_text = match.group(2).strip()
            params: list[str] = []
            if params_text:
                params = [part.strip() for part in params_text.split(",")]
                if any(not IDENT_RE.match(param) for param in params):
                    raise PebbleError(f"line {line.number}: invalid parameter list")
                if len(set(params)) != len(params):
                    raise PebbleError(f"line {line.number}: duplicate parameter names are not allowed")
            self.index += 1
            return FunctionDefStmt(
                name=match.group(1),
                params=params,
                body=self._parse_indented_block(line),
                line_number=line.number,
            )

        if text.startswith("return "):
            self.index += 1
            return ReturnStmt(expr=self._parse_expression(text[7:], line.number), line_number=line.number)

        if text.startswith("import "):
            module = text[7:].strip()
            if not IDENT_RE.match(module):
                raise PebbleError(f"line {line.number}: invalid import target")
            self.index += 1
            return ImportStmt(module=module, line_number=line.number)

        if text == "pass":
            self.index += 1
            return PassStmt(line_number=line.number)

        if text == "break":
            self.index += 1
            return BreakStmt(line_number=line.number)

        if text == "continue":
            self.index += 1
            return ContinueStmt(line_number=line.number)

        match = ASSIGN_RE.match(text)
        if match:
            self.index += 1
            return AssignStmt(
                target=self._parse_target(match.group(1).strip(), line.number),
                expr=self._parse_expression(match.group(2), line.number),
                line_number=line.number,
            )

        expr = self._parse_expression(text, line.number)
        if isinstance(expr, (CallExpr, AttrCallExpr)):
            self.index += 1
            return ExprStmt(expr=expr, line_number=line.number)

        raise PebbleError(
            f"line {line.number}: expected assignment, print, if, while, for, def, return, import, pass, break, continue, or a function call"
        )

    def _parse_target(self, text: str, line_number: int) -> Target:
        try:
            parsed = ast.parse(text, mode="eval")
        except SyntaxError as exc:
            raise PebbleError(f"line {line_number}: invalid assignment target") from exc
        return self._convert_target_node(parsed.body, line_number)

    def _convert_target_node(self, node: ast.AST, line_number: int) -> Target:
        if isinstance(node, ast.Name):
            return NameTarget(name=node.id, line_number=line_number)
        if isinstance(node, ast.Subscript):
            return IndexTarget(
                value=self._convert_expr_node(node.value, line_number),
                index=self._convert_slice_node(node.slice, line_number),
                line_number=line_number,
            )
        raise PebbleError(f"line {line_number}: invalid assignment target")

    def _parse_if_statement(self, line: SourceLine) -> IfStmt:
        branches: list[IfBranch] = []
        else_body: list[Stmt] | None = None
        current_line = line
        keyword = "if"

        while True:
            branches.append(
                IfBranch(
                    condition=self._parse_if_condition(current_line, keyword),
                    body=self._parse_body_after_header(current_line),
                    line_number=current_line.number,
                )
            )

            if self.index >= len(self.lines):
                break

            next_line = self.lines[self.index]
            if next_line.indent != line.indent:
                break

            if next_line.text.startswith("elif "):
                current_line = next_line
                keyword = "elif"
                continue

            if next_line.text == "else:":
                self.index += 1
                else_body = self._parse_indented_block(next_line)
                break

            break

        return IfStmt(branches=branches, else_body=else_body, line_number=line.number)

    def _parse_if_condition(self, line: SourceLine, keyword: str) -> Expr:
        if not line.text.endswith(":"):
            raise PebbleError(f"line {line.number}: {keyword} statements must end with ':'")
        condition_text = line.text[len(keyword) : -1].strip()
        if not condition_text:
            raise PebbleError(f"line {line.number}: missing {keyword} condition")
        return self._parse_expression(condition_text, line.number)

    def _parse_body_after_header(self, line: SourceLine) -> list[Stmt]:
        self.index += 1
        return self._parse_indented_block(line)

    def _parse_indented_block(self, parent_line: SourceLine) -> list[Stmt]:
        if self.index >= len(self.lines):
            raise PebbleError(f"line {parent_line.number}: expected an indented block")

        next_line = self.lines[self.index]
        expected_indent = parent_line.indent + 1
        if next_line.indent <= parent_line.indent:
            raise PebbleError(f"line {parent_line.number}: expected an indented block")
        if next_line.indent != expected_indent:
            raise PebbleError(
                f"line {next_line.number}: indentation must increase by exactly four spaces"
            )
        return self._parse_block(expected_indent)

    def _parse_expression(self, expr_text: str, line_number: int) -> Expr:
        try:
            parsed = ast.parse(expr_text.strip(), mode="eval")
        except SyntaxError as exc:
            raise PebbleError(f"line {line_number}: invalid expression") from exc
        return self._convert_expr_node(parsed.body, line_number)

    def _convert_expr_node(self, node: ast.AST, line_number: int) -> Expr:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                return BoolExpr(value=node.value, line_number=line_number)
            if node.value is None:
                return NoneExpr(line_number=line_number)
            if isinstance(node.value, int):
                return NumberExpr(value=node.value, line_number=line_number)
            if isinstance(node.value, float):
                return NumberExpr(value=node.value, line_number=line_number)
            if isinstance(node.value, str):
                return StringExpr(value=node.value, line_number=line_number)
            raise PebbleError(f"line {line_number}: unsupported constant")

        if isinstance(node, ast.Name):
            return NameExpr(name=node.id, line_number=line_number)

        if isinstance(node, ast.UnaryOp):
            if isinstance(node.op, ast.USub):
                return UnaryExpr(
                    op="-",
                    operand=self._convert_expr_node(node.operand, line_number),
                    line_number=line_number,
                )
            if isinstance(node.op, ast.Not):
                return UnaryExpr(
                    op="not",
                    operand=self._convert_expr_node(node.operand, line_number),
                    line_number=line_number,
                )

        if isinstance(node, ast.BinOp):
            op = self._convert_binary_op(node.op, line_number)
            return BinaryExpr(
                left=self._convert_expr_node(node.left, line_number),
                op=op,
                right=self._convert_expr_node(node.right, line_number),
                line_number=line_number,
            )

        if isinstance(node, ast.BoolOp):
            if len(node.values) < 2:
                raise PebbleError(f"line {line_number}: invalid boolean expression")
            expr = self._convert_expr_node(node.values[0], line_number)
            op = self._convert_bool_op(node.op, line_number)
            i = 1
            while i < len(node.values):
                expr = BoolOpExpr(
                    left=expr,
                    op=op,
                    right=self._convert_expr_node(node.values[i], line_number),
                    line_number=line_number,
                )
                i = i + 1
            return expr

        if isinstance(node, ast.Compare):
            if len(node.ops) != 1 or len(node.comparators) != 1:
                raise PebbleError(f"line {line_number}: chained comparisons are not supported")
            return BinaryExpr(
                left=self._convert_expr_node(node.left, line_number),
                op=self._convert_compare_op(node.ops[0], line_number),
                right=self._convert_expr_node(node.comparators[0], line_number),
                line_number=line_number,
            )

        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                return CallExpr(
                    name=node.func.id,
                    args=[self._convert_expr_node(arg, line_number) for arg in node.args],
                    line_number=line_number,
                )
            if isinstance(node.func, ast.Attribute):
                return AttrCallExpr(
                    value=self._convert_expr_node(node.func.value, line_number),
                    attr=node.func.attr,
                    args=[self._convert_expr_node(arg, line_number) for arg in node.args],
                    line_number=line_number,
                )
            raise PebbleError(f"line {line_number}: only simple or module function calls are supported")

        if isinstance(node, ast.Attribute):
            return AttrExpr(
                value=self._convert_expr_node(node.value, line_number),
                attr=node.attr,
                line_number=line_number,
            )

        if isinstance(node, ast.List):
            return ListExpr(
                items=[self._convert_expr_node(item, line_number) for item in node.elts],
                line_number=line_number,
            )

        if isinstance(node, ast.Dict):
            items: list[tuple[Expr, Expr]] = []
            for key, value in zip(node.keys, node.values):
                if key is None:
                    raise PebbleError(f"line {line_number}: dict unpacking is not supported")
                items.append(
                    (
                        self._convert_expr_node(key, line_number),
                        self._convert_expr_node(value, line_number),
                    )
                )
            return DictExpr(items=items, line_number=line_number)

        if isinstance(node, ast.Subscript):
            return IndexExpr(
                value=self._convert_expr_node(node.value, line_number),
                index=self._convert_slice_node(node.slice, line_number),
                line_number=line_number,
            )

        raise PebbleError(f"line {line_number}: unsupported expression")

    def _convert_slice_node(self, node: ast.AST, line_number: int) -> Expr:
        if isinstance(node, ast.Slice):
            raise PebbleError(f"line {line_number}: slicing is not supported")
        return self._convert_expr_node(node, line_number)

    def _convert_binary_op(self, op: ast.operator, line_number: int) -> str:
        if isinstance(op, ast.Add):
            return "+"
        if isinstance(op, ast.Sub):
            return "-"
        if isinstance(op, ast.Mult):
            return "*"
        if isinstance(op, ast.Div):
            return "/"
        raise PebbleError(f"line {line_number}: unsupported operator")

    def _convert_compare_op(self, op: ast.cmpop, line_number: int) -> str:
        if isinstance(op, ast.Lt):
            return "<"
        if isinstance(op, ast.Gt):
            return ">"
        if isinstance(op, ast.Eq):
            return "=="
        if isinstance(op, ast.NotEq):
            return "!="
        if isinstance(op, ast.LtE):
            return "<="
        if isinstance(op, ast.GtE):
            return ">="
        raise PebbleError(f"line {line_number}: unsupported comparison operator")

    def _convert_bool_op(self, op: ast.boolop, line_number: int) -> str:
        if isinstance(op, ast.And):
            return "and"
        if isinstance(op, ast.Or):
            return "or"
        raise PebbleError(f"line {line_number}: unsupported boolean operator")


class BytecodeCompiler:
    def compile(self, statements: list[Stmt]) -> list[tuple]:
        return [self._compile_stmt(stmt) for stmt in statements]

    def _compile_stmt(self, stmt: Stmt) -> tuple:
        if isinstance(stmt, AssignStmt):
            target = self._compile_target(stmt.target)
            return ("ASSIGN", target, self._compile_expr(stmt.expr), stmt.line_number)
        if isinstance(stmt, PrintStmt):
            return ("PRINT", self._compile_expr(stmt.expr), stmt.line_number)
        if isinstance(stmt, ExprStmt):
            return ("EXPR", self._compile_expr(stmt.expr), stmt.line_number)
        if isinstance(stmt, PassStmt):
            return ("PASS", stmt.line_number)
        if isinstance(stmt, BreakStmt):
            return ("BREAK", stmt.line_number)
        if isinstance(stmt, ContinueStmt):
            return ("CONTINUE", stmt.line_number)
        if isinstance(stmt, IfStmt):
            branches = []
            for branch in stmt.branches:
                branches.append((self._compile_expr(branch.condition), self.compile(branch.body), branch.line_number))
            else_code = None
            if stmt.else_body is not None:
                else_code = self.compile(stmt.else_body)
            return ("IF", branches, else_code, stmt.line_number)
        if isinstance(stmt, WhileStmt):
            return ("WHILE", self._compile_expr(stmt.condition), self.compile(stmt.body), stmt.line_number)
        if isinstance(stmt, ForStmt):
            return ("FOR", stmt.name, self._compile_expr(stmt.iterable), self.compile(stmt.body), stmt.line_number)
        if isinstance(stmt, FunctionDefStmt):
            return (
                "FUNCTION",
                BytecodeFunction(stmt.name, stmt.params, self.compile(stmt.body), stmt.line_number),
                stmt.line_number,
            )
        if isinstance(stmt, ReturnStmt):
            return ("RETURN", self._compile_expr(stmt.expr), stmt.line_number)
        if isinstance(stmt, ImportStmt):
            return ("IMPORT", stmt.module, stmt.line_number)
        raise PebbleError(f"line {stmt.line_number}: unknown statement type")

    def _compile_target(self, target: Target) -> tuple:
        if isinstance(target, NameTarget):
            return ("NAME", target.name, target.line_number)
        if isinstance(target, IndexTarget):
            return ("INDEX", self._compile_expr(target.value), self._compile_expr(target.index), target.line_number)
        raise PebbleError("unknown assignment target")

    def _compile_expr(self, expr: Expr) -> list[tuple]:
        if isinstance(expr, NumberExpr):
            return [("CONST", expr.value, expr.line_number)]
        if isinstance(expr, StringExpr):
            return [("CONST", expr.value, expr.line_number)]
        if isinstance(expr, BoolExpr):
            return [("CONST", expr.value, expr.line_number)]
        if isinstance(expr, NoneExpr):
            return [("CONST", None, expr.line_number)]
        if isinstance(expr, NameExpr):
            return [("LOAD_NAME", expr.name, expr.line_number)]
        if isinstance(expr, UnaryExpr):
            return self._compile_expr(expr.operand) + [("UNARY", expr.op, expr.line_number)]
        if isinstance(expr, BinaryExpr):
            return self._compile_expr(expr.left) + self._compile_expr(expr.right) + [("BINARY", expr.op, expr.line_number)]
        if isinstance(expr, BoolOpExpr):
            return self._compile_expr(expr.left) + self._compile_expr(expr.right) + [("BOOL", expr.op, expr.line_number)]
        if isinstance(expr, CallExpr):
            code: list[tuple] = []
            for arg in expr.args:
                code.extend(self._compile_expr(arg))
            code.append(("CALL", expr.name, len(expr.args), expr.line_number))
            return code
        if isinstance(expr, AttrCallExpr):
            code = self._compile_expr(expr.value)
            for arg in expr.args:
                code.extend(self._compile_expr(arg))
            code.append(("ATTR_CALL", expr.attr, len(expr.args), expr.line_number))
            return code
        if isinstance(expr, AttrExpr):
            return self._compile_expr(expr.value) + [("ATTR", expr.attr, expr.line_number)]
        if isinstance(expr, ListExpr):
            code = []
            for item in expr.items:
                code.extend(self._compile_expr(item))
            code.append(("LIST", len(expr.items), expr.line_number))
            return code
        if isinstance(expr, DictExpr):
            code = []
            for key_expr, value_expr in expr.items:
                code.extend(self._compile_expr(key_expr))
                code.extend(self._compile_expr(value_expr))
            code.append(("DICT", len(expr.items), expr.line_number))
            return code
        if isinstance(expr, IndexExpr):
            return self._compile_expr(expr.value) + self._compile_expr(expr.index) + [("INDEX", expr.line_number)]
        raise PebbleError("unknown expression type")


class PebbleInterpreter:
    """Runs Pebble with Python-style blocks and a small bootstrap runtime."""

    def __init__(
        self,
        fs_root: Path | None = None,
        input_provider: Callable[[str], str] | None = None,
        output_consumer: Callable[[str], None] | None = None,
        path_resolver: Callable[[str], Path] | None = None,
        host_functions: dict[str, HostFunction] | None = None,
    ) -> None:
        self.fs_root = fs_root or (Path.cwd() / "disk")
        self.input_provider = input_provider
        self.output_consumer = output_consumer
        self.path_resolver = path_resolver
        self.host_functions = host_functions or {}

    def execute(self, source: str, initial_globals: dict[str, Value] | None = None) -> list[str]:
        statements = Parser(source).parse()
        self.output: list[str] = []
        self.globals: dict[str, Value] = {
            "CWD": "/",
            "FS_MODE": "hostfs",
            "MFS_READY": 0,
            "MFS_DB": {"files": {}},
        }
        self.functions: dict[str, UserFunction] = {}
        self.module_cache: dict[str, ModuleObject] = {}
        self.module_loading: set[str] = set()
        self.fs_root.mkdir(parents=True, exist_ok=True)
        if initial_globals:
            for name, value in initial_globals.items():
                self.globals[name] = self._clone_value(value)

        self._execute_block(statements, local_env=None)
        return self.output

    def _execute_block(self, statements: list[Stmt], local_env: dict[str, Value] | None) -> None:
        for statement in statements:
            self._execute_statement(statement, local_env)

    def _execute_statement(self, statement: Stmt, local_env: dict[str, Value] | None) -> None:
        if isinstance(statement, AssignStmt):
            self._assign_target(statement.target, self._eval_expr(statement.expr, local_env), local_env)
            return

        if isinstance(statement, PrintStmt):
            self._emit_output(self._stringify(self._eval_expr(statement.expr, local_env)))
            return

        if isinstance(statement, ExprStmt):
            self._eval_expr(statement.expr, local_env)
            return

        if isinstance(statement, PassStmt):
            return

        if isinstance(statement, BreakStmt):
            raise BreakSignal()

        if isinstance(statement, ContinueStmt):
            raise ContinueSignal()

        if isinstance(statement, IfStmt):
            for branch in statement.branches:
                if self._truthy(self._eval_expr(branch.condition, local_env)):
                    self._execute_block(branch.body, local_env)
                    return
            if statement.else_body is not None:
                self._execute_block(statement.else_body, local_env)
            return

        if isinstance(statement, WhileStmt):
            while self._truthy(self._eval_expr(statement.condition, local_env)):
                try:
                    self._execute_block(statement.body, local_env)
                except ContinueSignal:
                    continue
                except BreakSignal:
                    break
            return

        if isinstance(statement, ForStmt):
            iterable = self._eval_expr(statement.iterable, local_env)
            if isinstance(iterable, dict):
                iterable = list(iterable.keys())
            elif isinstance(iterable, str):
                chars: list[Value] = []
                i = 0
                while i < len(iterable):
                    chars.append(iterable[i])
                    i = i + 1
                iterable = chars
            if not isinstance(iterable, list):
                raise PebbleError(f"line {statement.line_number}: for loops must iterate over a list or range(...)")
            for value in iterable:
                self._write_variable(statement.name, self._clone_value(value), local_env)
                try:
                    self._execute_block(statement.body, local_env)
                except ContinueSignal:
                    continue
                except BreakSignal:
                    break
            return

        if isinstance(statement, FunctionDefStmt):
            self.functions[statement.name] = UserFunction(
                name=statement.name,
                params=statement.params,
                body=statement.body,
                line_number=statement.line_number,
            )
            return

        if isinstance(statement, ReturnStmt):
            if local_env is None:
                raise PebbleError(f"line {statement.line_number}: return is only allowed inside functions")
            raise ReturnSignal(self._eval_expr(statement.expr, local_env))

        if isinstance(statement, ImportStmt):
            self._write_variable(statement.module, self._import_module(statement.module, statement.line_number), local_env)
            return

        raise PebbleError(f"line {statement.line_number}: unknown statement type")

    def _assign_target(
        self,
        target: Target,
        value: Value,
        local_env: dict[str, Value] | None,
    ) -> None:
        if isinstance(target, NameTarget):
            self._write_variable(target.name, value, local_env)
            return
        if isinstance(target, IndexTarget):
            container = self._eval_expr(target.value, local_env)
            index_value = self._eval_expr(target.index, local_env)
            if isinstance(container, dict):
                container[index_value] = self._clone_value(value)
                return
            if not isinstance(index_value, int):
                raise PebbleError(f"line {target.line_number}: list index must be an integer")
            if not isinstance(container, list):
                raise PebbleError(f"line {target.line_number}: indexed assignment requires a list or dict")
            try:
                container[index_value] = self._clone_value(value)
            except IndexError as exc:
                raise PebbleError(f"line {target.line_number}: list index out of range") from exc
            return
        raise PebbleError("unknown assignment target")

    def _eval_expr(self, expr: Expr, local_env: dict[str, Value] | None) -> Value:
        if isinstance(expr, NumberExpr):
            return expr.value
        if isinstance(expr, BoolExpr):
            return expr.value
        if isinstance(expr, NoneExpr):
            return None
        if isinstance(expr, StringExpr):
            return expr.value
        if isinstance(expr, NameExpr):
            return self._read_variable(expr.name, expr.line_number, local_env)
        if isinstance(expr, UnaryExpr):
            value = self._eval_expr(expr.operand, local_env)
            if expr.op == "-" and type(value) in {int, float}:
                return -value
            if expr.op == "not":
                return not self._truthy(value)
            raise PebbleError(f"line {expr.line_number}: unsupported unary operator '{expr.op}'")
        if isinstance(expr, BinaryExpr):
            left = self._eval_expr(expr.left, local_env)
            right = self._eval_expr(expr.right, local_env)
            return self._eval_binary(expr.op, left, right, expr.line_number)
        if isinstance(expr, BoolOpExpr):
            left = self._eval_expr(expr.left, local_env)
            if expr.op == "and":
                if self._truthy(left):
                    return self._eval_expr(expr.right, local_env)
                return left
            if expr.op == "or":
                if self._truthy(left):
                    return left
                return self._eval_expr(expr.right, local_env)
            raise PebbleError(f"line {expr.line_number}: unsupported boolean operator '{expr.op}'")
        if isinstance(expr, CallExpr):
            return self._call(expr, local_env)
        if isinstance(expr, AttrCallExpr):
            target = self._eval_expr(expr.value, local_env)
            args = [self._eval_expr(arg, local_env) for arg in expr.args]
            return self._call_module_member(target, expr.attr, args, expr.line_number, local_env)
        if isinstance(expr, AttrExpr):
            target = self._eval_expr(expr.value, local_env)
            return self._get_module_member(target, expr.attr, expr.line_number)
        if isinstance(expr, ListExpr):
            return [self._clone_value(self._eval_expr(item, local_env)) for item in expr.items]
        if isinstance(expr, DictExpr):
            out: dict[Value, Value] = {}
            for key_expr, value_expr in expr.items:
                out[self._eval_expr(key_expr, local_env)] = self._clone_value(
                    self._eval_expr(value_expr, local_env)
                )
            return out
        if isinstance(expr, IndexExpr):
            container = self._eval_expr(expr.value, local_env)
            index_value = self._eval_expr(expr.index, local_env)
            if isinstance(container, dict):
                try:
                    return container[index_value]
                except KeyError as exc:
                    raise PebbleError(f"line {expr.line_number}: dict key not found") from exc
            if not isinstance(index_value, int):
                raise PebbleError(f"line {expr.line_number}: index must be an integer")
            if not isinstance(container, (list, str)):
                raise PebbleError(f"line {expr.line_number}: only strings, lists, and dicts can be indexed")
            try:
                return container[index_value]
            except IndexError as exc:
                raise PebbleError(f"line {expr.line_number}: index out of range") from exc
        raise PebbleError("unknown expression type")

    def _eval_binary(self, op: str, left: Value, right: Value, line_number: int) -> Value:
        if op == "+":
            if type(left) in {int, float} and type(right) in {int, float}:
                return left + right
            if isinstance(left, str) and isinstance(right, str):
                return left + right
            if isinstance(left, list) and isinstance(right, list):
                return self._clone_list(left) + self._clone_list(right)
            raise PebbleError(f"line {line_number}: '+' requires numeric, string, or list operands")
        if op == "-":
            if type(left) in {int, float} and type(right) in {int, float}:
                return left - right
            raise PebbleError(f"line {line_number}: '-' requires numeric operands")
        if op == "*":
            if type(left) in {int, float} and type(right) in {int, float}:
                return left * right
            raise PebbleError(f"line {line_number}: '*' requires numeric operands")
        if op == "/":
            if type(left) in {int, float} and type(right) in {int, float}:
                if right == 0:
                    raise PebbleError(f"line {line_number}: division by zero")
                return left / right
            raise PebbleError(f"line {line_number}: '/' requires numeric operands")
        if op == "<":
            return int(left < right)
        if op == ">":
            return int(left > right)
        if op == "==":
            return int(left == right)
        if op == "!=":
            return int(left != right)
        if op == "<=":
            return int(left <= right)
        if op == ">=":
            return int(left >= right)
        raise PebbleError(f"line {line_number}: unsupported operator '{op}'")

    def _call(self, expr: CallExpr, local_env: dict[str, Value] | None) -> Value:
        args = [self._eval_expr(arg, local_env) for arg in expr.args]
        return self._invoke_name(expr.name, args, expr.line_number, local_env)

    def _invoke_name(
        self,
        name: str,
        args: list[Value],
        line_number: int,
        local_env: dict[str, Value] | None,
    ) -> Value:
        if name in self.functions:
            return self._call_function_by_name(name, args, line_number, local_env)
        return self._call_builtin_args(name, args, line_number)

    def _call_builtin_args(self, name: str, args: list[Value], line_number: int) -> Value:
        expr = CallExpr(name=name, args=[], line_number=line_number)
        if name == "len":
            self._require_arity(expr, args, 1)
            if not isinstance(args[0], (str, list, dict)):
                raise PebbleError(f"line {line_number}: len() expects a string, list, or dict")
            return len(args[0])

        if name == "append":
            self._require_arity(expr, args, 2)
            if not isinstance(args[0], list):
                raise PebbleError(f"line {line_number}: append() expects a list as the first argument")
            args[0].append(self._clone_value(args[1]))
            return args[0]

        if name == "range":
            if not 1 <= len(args) <= 3 or not all(isinstance(arg, int) for arg in args):
                raise PebbleError(f"line {line_number}: range() expects 1, 2, or 3 integer arguments")
            if len(args) == 1:
                start, stop, step = 0, args[0], 1
            elif len(args) == 2:
                start, stop, step = args[0], args[1], 1
            else:
                start, stop, step = args[0], args[1], args[2]
            if step == 0:
                raise PebbleError(f"line {line_number}: range() step cannot be zero")
            return list(range(start, stop, step))

        if name == "read_file":
            self._require_arity(expr, args, 1)
            path = self._resolve_file_arg(args[0], line_number)
            try:
                return path.read_text(encoding="utf-8")
            except FileNotFoundError as exc:
                raise PebbleError(f"line {line_number}: file '{path.name}' does not exist") from exc

        if name == "write_file":
            self._require_arity(expr, args, 2)
            path = self._resolve_file_arg(args[0], line_number)
            text = self._coerce_to_text(args[1], line_number)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            return text

        if name == "str":
            self._require_arity(expr, args, 1)
            return self._stringify(args[0])

        if name == "int":
            self._require_arity(expr, args, 1)
            if isinstance(args[0], int):
                return args[0]
            if isinstance(args[0], float):
                return int(args[0])
            if isinstance(args[0], str):
                try:
                    return int(args[0])
                except ValueError as exc:
                    raise PebbleError(f"line {line_number}: int() could not parse '{args[0]}'") from exc
            raise PebbleError(f"line {line_number}: int() expects a string, integer, or float")

        if name == "float":
            self._require_arity(expr, args, 1)
            if isinstance(args[0], float):
                return args[0]
            if isinstance(args[0], int):
                return float(args[0])
            if isinstance(args[0], str):
                try:
                    return float(args[0])
                except ValueError as exc:
                    raise PebbleError(f"line {line_number}: float() could not parse '{args[0]}'") from exc
            raise PebbleError(f"line {line_number}: float() expects a string, integer, or float")

        if name == "input":
            self._require_arity(expr, args, 1)
            if not isinstance(args[0], str):
                raise PebbleError(f"line {line_number}: input() expects a string prompt")
            if self.input_provider is None:
                raise PebbleError(f"line {line_number}: input() is not available in this runtime")
            return self.input_provider(args[0])

        if name == "argv":
            self._require_arity(expr, args, 1)
            if not isinstance(args[0], int):
                raise PebbleError(f"line {line_number}: argv() expects an integer index")
            argv_value = self.globals.get("ARGV", [])
            if not isinstance(argv_value, list):
                raise PebbleError(f"line {line_number}: ARGV is not available")
            try:
                value = argv_value[args[0]]
            except IndexError as exc:
                raise PebbleError(f"line {line_number}: argv index out of range") from exc
            if not isinstance(value, str):
                raise PebbleError(f"line {line_number}: argv values must be strings")
            return value

        if name == "keys":
            self._require_arity(expr, args, 1)
            if not isinstance(args[0], dict):
                raise PebbleError(f"line {line_number}: keys() expects a dict")
            return list(args[0].keys())

        host_function = self.host_functions.get(name)
        if host_function is not None:
            return host_function(args, line_number)

        raise PebbleError(f"line {line_number}: unknown builtin '{name}'")

    def _call_function_by_name(
        self,
        name: str,
        args: list[Value],
        line_number: int,
        local_env: dict[str, Value] | None,
    ) -> Value:
        function = self.functions.get(name)
        if function is None:
            raise PebbleError(f"line {line_number}: unknown function '{name}'")
        if len(args) != len(function.params):
            raise PebbleError(
                f"line {line_number}: function '{name}' expected "
                f"{len(function.params)} arguments but got {len(args)}"
            )

        frame = {param: self._clone_value(value) for param, value in zip(function.params, args)}

        try:
            self._execute_block(function.body, frame)
        except ReturnSignal as signal:
            return self._clone_value(signal.value)

        return 0

    def _call_function(self, expr: CallExpr, local_env: dict[str, Value] | None) -> Value:
        args = [self._eval_expr(arg, local_env) for arg in expr.args]
        return self._call_function_by_name(expr.name, args, expr.line_number, local_env)

    def _import_module(self, name: str, line_number: int) -> ModuleObject:
        cached = getattr(self, "module_cache", {}).get(name)
        if cached is not None:
            return cached
        if name == "math":
            module = ModuleObject(
                "math",
                {
                    "abs": "abs",
                    "pow": "pow",
                    "sqrt": "sqrt",
                    "sin": "sin",
                    "cos": "cos",
                    "tan": "tan",
                },
                {},
                {},
            )
            self.module_cache[name] = module
            return module
        if name == "text":
            module = ModuleObject(
                "text",
                {
                    "len": "text_len",
                    "repeat": "text_repeat",
                    "lines": "text_lines",
                    "join": "text_join",
                    "first_line": "text_first_line",
                },
                {},
                {},
            )
            self.module_cache[name] = module
            return module
        if name == "os":
            module = ModuleObject(
                "os",
                {
                    "list": "os_list",
                    "exists": "os_exists",
                    "read": "os_read",
                    "write": "os_write",
                    "delete": "os_delete",
                    "time": "os_time",
                },
                {},
                {},
            )
            self.module_cache[name] = module
            return module
        if name == "random":
            module = ModuleObject(
                "random",
                {
                    "seed": "random_seed",
                    "next": "random_next",
                    "range": "random_range",
                },
                {},
                {},
            )
            self.module_cache[name] = module
            return module
        if name == "memory":
            module = ModuleObject(
                "memory",
                {
                    "init": "memory_init",
                    "size": "memory_size",
                    "read": "memory_read",
                    "write": "memory_write",
                    "clear": "memory_clear",
                    "fill": "memory_fill",
                    "copy": "memory_copy",
                    "slice": "memory_slice",
                    "store": "memory_store",
                    "dump": "memory_dump",
                    "alloc": "memory_alloc",
                    "top": "memory_top",
                },
                {},
                {},
            )
            self.module_cache[name] = module
            return module
        if name == "heap":
            module = ModuleObject(
                "heap",
                {
                    "init": "heap_init",
                    "capacity": "heap_capacity",
                    "used": "heap_used",
                    "count": "heap_count",
                    "alloc": "heap_alloc",
                    "kind": "heap_kind",
                    "size": "heap_size",
                    "read": "heap_read",
                    "write": "heap_write",
                    "store": "heap_store",
                    "slice": "heap_slice",
                },
                {},
                {},
            )
            self.module_cache[name] = module
            return module
        if name in self.module_loading:
            raise PebbleError(f"line {line_number}: circular import for module '{name}'")
        path = self._resolve_file_arg(name + ".peb", line_number)
        try:
            source = path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise PebbleError(f"line {line_number}: unknown module '{name}'") from exc
        self.module_loading.add(name)
        try:
            module = self._load_user_module(name, source, line_number)
            self.module_cache[name] = module
            return module
        finally:
            self.module_loading.remove(name)

    def _load_user_module(self, name: str, source: str, line_number: int) -> ModuleObject:
        child = self.__class__(
            self.fs_root,
            input_provider=self.input_provider,
            output_consumer=None,
            path_resolver=self.path_resolver,
            host_functions=self.host_functions,
        )
        child.module_cache = self.module_cache
        child.module_loading = self.module_loading
        child.execute(source)
        return ModuleObject(name, {}, child.globals, child.functions)

    def _get_module_member(self, target: Value, attr: str, line_number: int) -> Value:
        if not isinstance(target, ModuleObject):
            raise PebbleError(f"line {line_number}: attribute access requires a module")
        if attr in target.values:
            return target.values[attr]
        if attr in target.functions:
            return "<function " + target.name + "." + attr + ">"
        if attr in target.builtins:
            return "<builtin " + target.name + "." + attr + ">"
        raise PebbleError(f"line {line_number}: module '{target.name}' has no member '{attr}'")

    def _call_module_member(
        self,
        target: Value,
        attr: str,
        args: list[Value],
        line_number: int,
        local_env: dict[str, Value] | None,
    ) -> Value:
        if not isinstance(target, ModuleObject):
            raise PebbleError(f"line {line_number}: attribute calls require a module")
        member = target.builtins.get(attr)
        if member is not None:
            return self._invoke_name(member, args, line_number, local_env)
        function = target.functions.get(attr)
        if function is not None:
            return self._call_module_function(target, function, args, line_number)
        raise PebbleError(f"line {line_number}: module '{target.name}' has no callable member '{attr}'")

    def _call_module_function(
        self,
        module: ModuleObject,
        function: object,
        args: list[Value],
        line_number: int,
    ) -> Value:
        old_globals = self.globals
        old_functions = self.functions
        try:
            self.globals = module.values
            self.functions = module.functions
            if isinstance(function, UserFunction):
                return self._call_function_by_name(function.name, args, line_number, None)
            return self._call_function_by_name(function.name, args, line_number, None)
        finally:
            self.globals = old_globals
            self.functions = old_functions

    def _require_arity(self, expr: CallExpr, args: list[Value], expected: int) -> None:
        if len(args) != expected:
            raise PebbleError(
                f"line {expr.line_number}: function '{expr.name}' expected {expected} arguments but got {len(args)}"
            )

    def _read_variable(
        self,
        name: str,
        line_number: int,
        local_env: dict[str, Value] | None,
    ) -> Value:
        if local_env is not None and name in local_env:
            return local_env[name]
        if name in self.globals:
            return self.globals[name]
        raise PebbleError(f"line {line_number}: unknown variable '{name}'")

    def _write_variable(self, name: str, value: Value, local_env: dict[str, Value] | None) -> None:
        if local_env is None:
            self.globals[name] = self._clone_value(value)
        else:
            local_env[name] = self._clone_value(value)

    def _clone_value(self, value: Value) -> Value:
        if isinstance(value, ModuleObject):
            return value
        if isinstance(value, list):
            return self._clone_list(value)
        if isinstance(value, dict):
            cloned: dict[Value, Value] = {}
            for key, item in value.items():
                cloned[self._clone_value(key)] = self._clone_value(item)
            return cloned
        return value

    def _clone_list(self, value: list[Value]) -> list[Value]:
        return [self._clone_value(item) for item in value]

    def _truthy(self, value: Value) -> bool:
        return value not in {0, "", None, False} and value != [] and value != {}

    def _stringify(self, value: Value) -> str:
        if value is None:
            return "None"
        if isinstance(value, bool):
            if value:
                return "True"
            return "False"
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float):
            text = str(value)
            if "." in text:
                while text.endswith("0"):
                    text = text[:-1]
                if text.endswith("."):
                    text = text + "0"
            return text
        if isinstance(value, str):
            return value
        if isinstance(value, ModuleObject):
            return "<module " + value.name + ">"
        if isinstance(value, dict):
            parts: list[str] = []
            for key, item in value.items():
                parts.append(self._stringify(key) + ": " + self._stringify(item))
            return "{" + ", ".join(parts) + "}"
        return "[" + ", ".join(self._stringify(item) for item in value) + "]"

    def _coerce_to_text(self, value: Value, line_number: int) -> str:
        if not isinstance(value, str):
            raise PebbleError(f"line {line_number}: write_file() expects text content")
        return value

    def _resolve_file_arg(self, value: Value, line_number: int) -> Path:
        if not isinstance(value, str):
            raise PebbleError(f"line {line_number}: file name must be a string")
        name = value.strip()
        if not name:
            raise PebbleError(f"line {line_number}: file name must not be empty")
        if self.path_resolver is not None:
            try:
                return self.path_resolver(name)
            except Exception as exc:
                raise PebbleError(f"line {line_number}: {exc}") from exc
        if "\\" in name or name in {".", ".."} or any(part in {"", ".", ".."} for part in name.split("/")):
            raise PebbleError(f"line {line_number}: file path must stay within the Pebble OS root")
        return self.fs_root / name

    def _emit_output(self, text: str) -> None:
        self.output.append(text)
        if self.output_consumer is not None:
            self.output_consumer(text)


class PebbleBytecodeInterpreter(PebbleInterpreter):
    """Compiles Pebble source to a compact instruction form, then executes it."""

    def execute(self, source: str, initial_globals: dict[str, Value] | None = None) -> list[str]:
        statements = Parser(source).parse()
        code = BytecodeCompiler().compile(statements)
        self.output = []
        self.globals = {}
        self.functions: dict[str, BytecodeFunction] = {}
        self.module_cache: dict[str, ModuleObject] = {}
        self.module_loading: set[str] = set()
        self.vm_state = VMState(value_stack=[], frame_stack=[])
        self.fs_root.mkdir(parents=True, exist_ok=True)
        if initial_globals:
            for name, value in initial_globals.items():
                self.globals[name] = self._clone_value(value)
        self._execute_code(code, local_env=None)
        return self.output

    def _execute_code(self, code: list[tuple], local_env: dict[str, Value] | None) -> None:
        for instr in code:
            self._execute_instr(instr, local_env)

    def _execute_instr(self, instr: tuple, local_env: dict[str, Value] | None) -> None:
        op = instr[0]
        if op == "ASSIGN":
            self._assign_compiled_target(instr[1], self._eval_compiled_expr(instr[2], local_env), local_env)
            return
        if op == "PRINT":
            self._emit_output(self._stringify(self._eval_compiled_expr(instr[1], local_env)))
            return
        if op == "EXPR":
            self._eval_compiled_expr(instr[1], local_env)
            return
        if op == "PASS":
            return
        if op == "BREAK":
            raise BreakSignal()
        if op == "CONTINUE":
            raise ContinueSignal()
        if op == "IF":
            branches = instr[1]
            else_code = instr[2]
            for condition_code, body_code, _line_number in branches:
                if self._truthy(self._eval_compiled_expr(condition_code, local_env)):
                    self._execute_code(body_code, local_env)
                    return
            if else_code is not None:
                self._execute_code(else_code, local_env)
            return
        if op == "WHILE":
            condition_code = instr[1]
            body_code = instr[2]
            while self._truthy(self._eval_compiled_expr(condition_code, local_env)):
                try:
                    self._execute_code(body_code, local_env)
                except ContinueSignal:
                    continue
                except BreakSignal:
                    break
            return
        if op == "FOR":
            name = instr[1]
            iterable = self._eval_compiled_expr(instr[2], local_env)
            body_code = instr[3]
            if isinstance(iterable, dict):
                iterable = list(iterable.keys())
            elif isinstance(iterable, str):
                chars: list[Value] = []
                i = 0
                while i < len(iterable):
                    chars.append(iterable[i])
                    i = i + 1
                iterable = chars
            if not isinstance(iterable, list):
                raise PebbleError(f"line {instr[4]}: for loops must iterate over a list or range(...)")
            for value in iterable:
                self._write_variable(name, self._clone_value(value), local_env)
                try:
                    self._execute_code(body_code, local_env)
                except ContinueSignal:
                    continue
                except BreakSignal:
                    break
            return
        if op == "FUNCTION":
            fn = instr[1]
            self.functions[fn.name] = fn
            return
        if op == "RETURN":
            if local_env is None:
                raise PebbleError(f"line {instr[2]}: return is only allowed inside functions")
            raise ReturnSignal(self._eval_compiled_expr(instr[1], local_env))
        if op == "IMPORT":
            self._write_variable(instr[1], self._import_module(instr[1], instr[2]), local_env)
            return
        raise PebbleError(f"line {instr[-1]}: unknown bytecode instruction '{op}'")

    def _assign_compiled_target(self, target: tuple, value: Value, local_env: dict[str, Value] | None) -> None:
        if target[0] == "NAME":
            self._write_variable(target[1], value, local_env)
            return
        if target[0] == "INDEX":
            container = self._eval_compiled_expr(target[1], local_env)
            index_value = self._eval_compiled_expr(target[2], local_env)
            line_number = target[3]
            if isinstance(container, dict):
                container[index_value] = self._clone_value(value)
                return
            if not isinstance(index_value, int):
                raise PebbleError(f"line {line_number}: list index must be an integer")
            if not isinstance(container, list):
                raise PebbleError(f"line {line_number}: indexed assignment requires a list or dict")
            try:
                container[index_value] = self._clone_value(value)
            except IndexError as exc:
                raise PebbleError(f"line {line_number}: list index out of range") from exc
            return
        raise PebbleError("unknown assignment target")

    def _eval_compiled_expr(self, code: list[tuple], local_env: dict[str, Value] | None) -> Value:
        stack = self.vm_state.value_stack
        base = len(stack)
        for instr in code:
            op = instr[0]
            if op == "CONST":
                stack.append(self._clone_value(instr[1]))
            elif op == "LOAD_NAME":
                stack.append(self._read_variable(instr[1], instr[2], local_env))
            elif op == "UNARY":
                value = stack.pop()
                if instr[1] == "-" and type(value) is int:
                    stack.append(-value)
                elif instr[1] == "not":
                    stack.append(not self._truthy(value))
                else:
                    raise PebbleError(f"line {instr[2]}: unsupported unary operator '{instr[1]}'")
            elif op == "BINARY":
                right = stack.pop()
                left = stack.pop()
                stack.append(self._eval_binary(instr[1], left, right, instr[2]))
            elif op == "BOOL":
                right = stack.pop()
                left = stack.pop()
                if instr[1] == "and":
                    if self._truthy(left):
                        stack.append(right)
                    else:
                        stack.append(left)
                elif instr[1] == "or":
                    if self._truthy(left):
                        stack.append(left)
                    else:
                        stack.append(right)
                else:
                    raise PebbleError(f"line {instr[2]}: unsupported boolean operator '{instr[1]}'")
            elif op == "CALL":
                argc = instr[2]
                args = stack[-argc:] if argc else []
                if argc:
                    del stack[-argc:]
                stack.append(self._invoke_name(instr[1], args, instr[3], local_env))
            elif op == "ATTR_CALL":
                argc = instr[2]
                args = stack[-argc:] if argc else []
                if argc:
                    del stack[-argc:]
                target = stack.pop()
                stack.append(self._call_module_member(target, instr[1], args, instr[3], local_env))
            elif op == "ATTR":
                target = stack.pop()
                stack.append(self._get_module_member(target, instr[1], instr[2]))
            elif op == "LIST":
                count = instr[1]
                items = stack[-count:] if count else []
                if count:
                    del stack[-count:]
                stack.append([self._clone_value(item) for item in items])
            elif op == "DICT":
                count = instr[1]
                raw = stack[-(count * 2):] if count else []
                if count:
                    del stack[-(count * 2):]
                out: dict[Value, Value] = {}
                i = 0
                while i < len(raw):
                    out[raw[i]] = self._clone_value(raw[i + 1])
                    i = i + 2
                stack.append(out)
            elif op == "INDEX":
                index_value = stack.pop()
                container = stack.pop()
                if isinstance(container, dict):
                    try:
                        stack.append(container[index_value])
                    except KeyError as exc:
                        raise PebbleError(f"line {instr[1]}: dict key not found") from exc
                else:
                    if not isinstance(index_value, int):
                        raise PebbleError(f"line {instr[1]}: index must be an integer")
                    if not isinstance(container, (list, str)):
                        raise PebbleError(f"line {instr[1]}: only strings, lists, and dicts can be indexed")
                    try:
                        stack.append(container[index_value])
                    except IndexError as exc:
                        raise PebbleError(f"line {instr[1]}: index out of range") from exc
            else:
                raise PebbleError(f"line {instr[-1]}: unknown expression opcode '{op}'")
        if len(stack) != base + 1:
            raise PebbleError("invalid bytecode expression state")
        result = stack.pop()
        if len(stack) != base:
            raise PebbleError("bytecode value stack leak")
        return result

    def _call_builtin_args(self, name: str, args: list[Value], line_number: int) -> Value:
        expr = CallExpr(name=name, args=[], line_number=line_number)
        if name == "len":
            self._require_arity(expr, args, 1)
            if not isinstance(args[0], (str, list, dict)):
                raise PebbleError(f"line {line_number}: len() expects a string, list, or dict")
            return len(args[0])
        if name == "append":
            self._require_arity(expr, args, 2)
            if not isinstance(args[0], list):
                raise PebbleError(f"line {line_number}: append() expects a list as the first argument")
            args[0].append(self._clone_value(args[1]))
            return args[0]
        if name == "range":
            if not 1 <= len(args) <= 3 or not all(isinstance(arg, int) for arg in args):
                raise PebbleError(f"line {line_number}: range() expects 1, 2, or 3 integer arguments")
            if len(args) == 1:
                start, stop, step = 0, args[0], 1
            elif len(args) == 2:
                start, stop, step = args[0], args[1], 1
            else:
                start, stop, step = args[0], args[1], args[2]
            if step == 0:
                raise PebbleError(f"line {line_number}: range() step cannot be zero")
            return list(range(start, stop, step))
        if name == "read_file":
            self._require_arity(expr, args, 1)
            path = self._resolve_file_arg(args[0], line_number)
            try:
                return path.read_text(encoding="utf-8")
            except FileNotFoundError as exc:
                raise PebbleError(f"line {line_number}: file '{path.name}' does not exist") from exc
        if name == "write_file":
            self._require_arity(expr, args, 2)
            path = self._resolve_file_arg(args[0], line_number)
            text = self._coerce_to_text(args[1], line_number)
            path.write_text(text, encoding="utf-8")
            return text
        if name == "str":
            self._require_arity(expr, args, 1)
            return self._stringify(args[0])
        if name == "int":
            self._require_arity(expr, args, 1)
            if isinstance(args[0], int):
                return args[0]
            if isinstance(args[0], float):
                return int(args[0])
            if isinstance(args[0], str):
                try:
                    return int(args[0])
                except ValueError as exc:
                    raise PebbleError(f"line {line_number}: int() could not parse '{args[0]}'") from exc
            raise PebbleError(f"line {line_number}: int() expects a string, integer, or float")
        if name == "float":
            self._require_arity(expr, args, 1)
            if isinstance(args[0], float):
                return args[0]
            if isinstance(args[0], int):
                return float(args[0])
            if isinstance(args[0], str):
                try:
                    return float(args[0])
                except ValueError as exc:
                    raise PebbleError(f"line {line_number}: float() could not parse '{args[0]}'") from exc
            raise PebbleError(f"line {line_number}: float() expects a string, integer, or float")
        if name == "input":
            self._require_arity(expr, args, 1)
            if not isinstance(args[0], str):
                raise PebbleError(f"line {line_number}: input() expects a string prompt")
            if self.input_provider is None:
                raise PebbleError(f"line {line_number}: input() is not available in this runtime")
            return self.input_provider(args[0])
        if name == "argv":
            self._require_arity(expr, args, 1)
            if not isinstance(args[0], int):
                raise PebbleError(f"line {line_number}: argv() expects an integer index")
            argv_value = self.globals.get("ARGV", [])
            if not isinstance(argv_value, list):
                raise PebbleError(f"line {line_number}: ARGV is not available")
            try:
                value = argv_value[args[0]]
            except IndexError as exc:
                raise PebbleError(f"line {line_number}: argv index out of range") from exc
            if not isinstance(value, str):
                raise PebbleError(f"line {line_number}: argv values must be strings")
            return value
        if name == "keys":
            self._require_arity(expr, args, 1)
            if not isinstance(args[0], dict):
                raise PebbleError(f"line {line_number}: keys() expects a dict")
            return list(args[0].keys())
        host_function = self.host_functions.get(name)
        if host_function is not None:
            return host_function(args, line_number)
        raise PebbleError(f"line {line_number}: unknown builtin '{name}'")

    def _call_function_by_name(
        self,
        name: str,
        args: list[Value],
        line_number: int,
        local_env: dict[str, Value] | None,
    ) -> Value:
        function = self.functions.get(name)
        if function is None:
            raise PebbleError(f"line {line_number}: unknown function '{name}'")
        if len(args) != len(function.params):
            raise PebbleError(
                f"line {line_number}: function '{name}' expected "
                f"{len(function.params)} arguments but got {len(args)}"
            )
        frame = {name: self._clone_value(value) for name, value in zip(function.params, args)}
        self.vm_state.frame_stack.append(VMFrame(name=name, locals=frame, line_number=line_number))
        try:
            self._execute_code(function.code, frame)
        except ReturnSignal as signal:
            return self._clone_value(signal.value)
        finally:
            self.vm_state.frame_stack.pop()
        return 0
