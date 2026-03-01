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
    value: int
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
    | ListExpr
    | IndexExpr
    | DictExpr
)
Value = int | bool | None | str | list["Value"] | dict["Value", "Value"]
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
)


@dataclass
class UserFunction:
    name: str
    params: list[str]
    body: list[Stmt]
    line_number: int


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
        if isinstance(expr, CallExpr):
            self.index += 1
            return ExprStmt(expr=expr, line_number=line.number)

        raise PebbleError(
            f"line {line.number}: expected assignment, print, if, while, for, def, return, pass, break, continue, or a function call"
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
            if not isinstance(node.func, ast.Name):
                raise PebbleError(f"line {line_number}: only simple function calls are supported")
            return CallExpr(
                name=node.func.id,
                args=[self._convert_expr_node(arg, line_number) for arg in node.args],
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
        self.globals: dict[str, Value] = {}
        self.functions: dict[str, UserFunction] = {}
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
            if expr.op == "-" and type(value) is int:
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
            if type(left) is int and type(right) is int:
                return left + right
            if isinstance(left, str) and isinstance(right, str):
                return left + right
            if isinstance(left, list) and isinstance(right, list):
                return self._clone_list(left) + self._clone_list(right)
            raise PebbleError(f"line {line_number}: '+' requires matching int, string, or list types")
        if op == "-":
            if type(left) is int and type(right) is int:
                return left - right
            raise PebbleError(f"line {line_number}: '-' requires integers")
        if op == "*":
            if type(left) is int and type(right) is int:
                return left * right
            raise PebbleError(f"line {line_number}: '*' requires integers")
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
        if expr.name in {
            "len",
            "append",
            "range",
            "read_file",
            "write_file",
            "str",
            "int",
            "input",
            "argv",
            "keys",
        } or expr.name in self.host_functions:
            return self._call_builtin(expr, local_env)
        return self._call_function(expr, local_env)

    def _call_builtin(self, expr: CallExpr, local_env: dict[str, Value] | None) -> Value:
        args = [self._eval_expr(arg, local_env) for arg in expr.args]

        if expr.name == "len":
            self._require_arity(expr, args, 1)
            if not isinstance(args[0], (str, list, dict)):
                raise PebbleError(f"line {expr.line_number}: len() expects a string, list, or dict")
            return len(args[0])

        if expr.name == "append":
            self._require_arity(expr, args, 2)
            if not isinstance(args[0], list):
                raise PebbleError(f"line {expr.line_number}: append() expects a list as the first argument")
            args[0].append(self._clone_value(args[1]))
            return args[0]

        if expr.name == "range":
            if not 1 <= len(args) <= 3 or not all(isinstance(arg, int) for arg in args):
                raise PebbleError(
                    f"line {expr.line_number}: range() expects 1, 2, or 3 integer arguments"
                )
            if len(args) == 1:
                start, stop, step = 0, args[0], 1
            elif len(args) == 2:
                start, stop, step = args[0], args[1], 1
            else:
                start, stop, step = args[0], args[1], args[2]
            if step == 0:
                raise PebbleError(f"line {expr.line_number}: range() step cannot be zero")
            return list(range(start, stop, step))

        if expr.name == "read_file":
            self._require_arity(expr, args, 1)
            path = self._resolve_file_arg(args[0], expr.line_number)
            try:
                return path.read_text(encoding="utf-8")
            except FileNotFoundError as exc:
                raise PebbleError(f"line {expr.line_number}: file '{path.name}' does not exist") from exc

        if expr.name == "write_file":
            self._require_arity(expr, args, 2)
            path = self._resolve_file_arg(args[0], expr.line_number)
            text = self._coerce_to_text(args[1], expr.line_number)
            path.write_text(text, encoding="utf-8")
            return text

        if expr.name == "str":
            self._require_arity(expr, args, 1)
            return self._stringify(args[0])

        if expr.name == "int":
            self._require_arity(expr, args, 1)
            if isinstance(args[0], int):
                return args[0]
            if isinstance(args[0], str):
                try:
                    return int(args[0])
                except ValueError as exc:
                    raise PebbleError(f"line {expr.line_number}: int() could not parse '{args[0]}'") from exc
            raise PebbleError(f"line {expr.line_number}: int() expects a string or integer")

        if expr.name == "input":
            self._require_arity(expr, args, 1)
            if not isinstance(args[0], str):
                raise PebbleError(f"line {expr.line_number}: input() expects a string prompt")
            if self.input_provider is None:
                raise PebbleError(f"line {expr.line_number}: input() is not available in this runtime")
            return self.input_provider(args[0])

        if expr.name == "argv":
            self._require_arity(expr, args, 1)
            if not isinstance(args[0], int):
                raise PebbleError(f"line {expr.line_number}: argv() expects an integer index")
            argv_value = self.globals.get("ARGV", [])
            if not isinstance(argv_value, list):
                raise PebbleError(f"line {expr.line_number}: ARGV is not available")
            try:
                value = argv_value[args[0]]
            except IndexError as exc:
                raise PebbleError(f"line {expr.line_number}: argv index out of range") from exc
            if not isinstance(value, str):
                raise PebbleError(f"line {expr.line_number}: argv values must be strings")
            return value

        if expr.name == "keys":
            self._require_arity(expr, args, 1)
            if not isinstance(args[0], dict):
                raise PebbleError(f"line {expr.line_number}: keys() expects a dict")
            return list(args[0].keys())

        host_function = self.host_functions.get(expr.name)
        if host_function is not None:
            return host_function(args, expr.line_number)

        raise PebbleError(f"line {expr.line_number}: unknown builtin '{expr.name}'")

    def _call_function(self, expr: CallExpr, local_env: dict[str, Value] | None) -> Value:
        function = self.functions.get(expr.name)
        if function is None:
            raise PebbleError(f"line {expr.line_number}: unknown function '{expr.name}'")
        if len(expr.args) != len(function.params):
            raise PebbleError(
                f"line {expr.line_number}: function '{expr.name}' expected "
                f"{len(function.params)} arguments but got {len(expr.args)}"
            )

        frame = {
            name: self._clone_value(self._eval_expr(arg, local_env))
            for name, arg in zip(function.params, expr.args)
        }

        try:
            self._execute_block(function.body, frame)
        except ReturnSignal as signal:
            return self._clone_value(signal.value)

        return 0

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
        if isinstance(value, str):
            return value
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
        if "/" in name or "\\" in name or name in {".", ".."}:
            raise PebbleError(f"line {line_number}: file name must stay within the flat filesystem")
        return self.fs_root / name

    def _emit_output(self, text: str) -> None:
        self.output.append(text)
        if self.output_consumer is not None:
            self.output_consumer(text)
