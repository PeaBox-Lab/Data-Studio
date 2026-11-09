"""A small, safe measure expression language.

Semantic measures are typed expressions such as ``sum(amount) / count()`` or
``sum(units) where region = 'North'``. They are parsed into an AST and compiled to SQL
with quoted identifiers and escaped literals: project JSON never supplies raw SQL.

The grammar is deliberately tiny:

    expression := term (("+" | "-") term)*
    term       := factor (("*" | "/") factor)*
    factor     := ("+" | "-") factor | number | aggregate | "(" expression ")"
    aggregate  := NAME "(" ("*" | column) ")" ["where" condition]
    condition  := comparison (("and" | "or") comparison)*
    comparison := column OP literal

Supported aggregates: sum, avg/average, count, count_distinct, min, max, median,
stddev/stdev and variance. Column names may be qualified as ``table.column`` when a name
appears in more than one table.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

_SUPPORTED_AGGREGATES = {
    "sum": "SUM",
    "avg": "AVG",
    "average": "AVG",
    "count": "COUNT",
    "count_distinct": "COUNT",
    "min": "MIN",
    "max": "MAX",
    "median": "MEDIAN",
    "stddev": "STDDEV_SAMP",
    "stdev": "STDDEV_SAMP",
    "variance": "VAR_SAMP",
}
_NUMERIC_AGGREGATES = {
    "sum",
    "avg",
    "average",
    "median",
    "stddev",
    "stdev",
    "variance",
}
_COMPARISONS = {
    "=": "=",
    "!=": "<>",
    "<>": "<>",
    "<": "<",
    "<=": "<=",
    ">": ">",
    ">=": ">=",
}
_TOKEN = re.compile(
    r"""
    (?P<space>\s+)
  | (?P<number>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)
  | (?P<string>'(?:[^']|'')*')
  | (?P<ident>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)
  | (?P<star>\*)
  | (?P<op><=|>=|<>|!=|[+\-/(),=<>])
    """,
    re.VERBOSE,
)


class MeasureError(ValueError):
    """A measure expression could not be parsed or validated."""


@dataclass(frozen=True, slots=True)
class ResolvedColumn:
    """A column reference resolved by the semantic engine."""

    sql: str
    table: str
    name: str
    numeric: bool


@dataclass(frozen=True, slots=True)
class CompiledMeasure:
    """SQL produced for a measure expression."""

    sql: str
    tables: frozenset[str]
    numeric: bool = True


# -- AST -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Number:
    value: float


@dataclass(frozen=True, slots=True)
class _Text:
    value: str


@dataclass(frozen=True, slots=True)
class _Column:
    name: str


@dataclass(frozen=True, slots=True)
class _Comparison:
    column: _Column
    operator: str
    literal: _Number | _Text


@dataclass(frozen=True, slots=True)
class _Boolean:
    operator: str
    left: _Condition
    right: _Condition


_Condition = _Comparison | _Boolean


@dataclass(frozen=True, slots=True)
class _Aggregate:
    func: str
    column: _Column | None
    condition: _Condition | None = None


@dataclass(frozen=True, slots=True)
class _Unary:
    operator: str
    operand: _Node


@dataclass(frozen=True, slots=True)
class _Binary:
    operator: str
    left: _Node
    right: _Node


_Node = _Number | _Text | _Aggregate | _Unary | _Binary


class _TokenStream:
    def __init__(self, text: str) -> None:
        self.text = text
        self.tokens: list[tuple[str, str, int]] = []
        position = 0
        while position < len(text):
            match = _TOKEN.match(text, position)
            if match is None:
                raise MeasureError(
                    f"Unexpected character {text[position]!r} at position {position + 1}."
                )
            kind = match.lastgroup or ""
            value = match.group(0)
            if kind != "space":
                self.tokens.append((kind, value, match.start()))
            position = match.end()
        self.index = 0

    def peek(self) -> tuple[str, str, int] | None:
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def pop(self) -> tuple[str, str, int]:
        token = self.peek()
        if token is None:
            raise MeasureError("Unexpected end of measure expression.")
        self.index += 1
        return token

    def at_keyword(self, keyword: str) -> bool:
        token = self.peek()
        return bool(
            token and token[0] == "ident" and token[1].lower() == keyword
        )

    def at_op(self, operator: str) -> bool:
        token = self.peek()
        return bool(token and token[0] == "op" and token[1] == operator)

    def expect_op(self, operator: str) -> None:
        token = self.pop()
        if token[0] != "op" or token[1] != operator:
            raise MeasureError(
                f"Expected {operator!r} at position {token[2] + 1}, found {token[1]!r}."
            )

    def expect_ident(self) -> str:
        token = self.pop()
        if token[0] != "ident":
            raise MeasureError(
                f"Expected a column name at position {token[2] + 1}, "
                f"found {token[1]!r}."
            )
        return token[1]

    def done(self) -> bool:
        return self.index >= len(self.tokens)


class _Parser:
    def __init__(self, text: str) -> None:
        self.stream = _TokenStream(text)

    def parse(self) -> _Node:
        if not self.stream.tokens:
            raise MeasureError("Measure expression must not be empty.")
        node = self._expression()
        if not self.stream.done():
            token = self.stream.peek()
            assert token is not None
            raise MeasureError(
                f"Unexpected token {token[1]!r} at position {token[2] + 1}."
            )
        return node

    def _expression(self) -> _Node:
        node = self._term()
        while (
            self.stream.peek()
            and self.stream.peek()[0] == "op"
            and self.stream.peek()[1] in ("+", "-")
        ):
            operator = self.stream.pop()[1]
            node = _Binary(operator, node, self._term())
        return node

    def _term(self) -> _Node:
        node = self._factor()
        while (
            self.stream.peek()
            and self.stream.peek()[0] == "op"
            and self.stream.peek()[1] in ("*", "/")
        ):
            operator = self.stream.pop()[1]
            node = _Binary(operator, node, self._factor())
        return node

    def _factor(self) -> _Node:
        token = self.stream.peek()
        if token and token[0] == "op" and token[1] in ("+", "-"):
            operator = self.stream.pop()[1]
            return _Unary(operator, self._factor())
        return self._primary()

    def _primary(self) -> _Node:
        token = self.stream.peek()
        if token is None:
            raise MeasureError("Unexpected end of measure expression.")
        if token[0] == "number":
            self.stream.pop()
            value = float(token[1])
            return _Number(value)
        if token[0] == "op" and token[1] == "(":
            self.stream.pop()
            node = self._expression()
            self.stream.expect_op(")")
            return node
        if token[0] == "ident":
            name = token[1].lower()
            if name in _SUPPORTED_AGGREGATES:
                return self._aggregate()
        raise MeasureError(
            f"Expected a number, aggregate or '(' at position {token[2] + 1}, "
            f"found {token[1]!r}."
        )

    def _aggregate(self) -> _Aggregate:
        func = self.stream.expect_ident().lower()
        self.stream.expect_op("(")
        token = self.stream.peek()
        if token is None:
            raise MeasureError("Unexpected end of aggregate call.")
        if token[0] == "star":
            self.stream.pop()
            column = None
        elif token[0] == "op" and token[1] == ")":
            column = None
        else:
            column = _Column(self.stream.expect_ident())
        self.stream.expect_op(")")
        condition = None
        if self.stream.at_keyword("where"):
            self.stream.pop()
            condition = self._condition()
        return _Aggregate(func, column, condition)

    def _condition(self) -> _Condition:
        left = self._comparison()
        while (
            self.stream.peek()
            and self.stream.peek()[0] == "ident"
            and self.stream.peek()[1].lower() in ("and", "or")
        ):
            operator = self.stream.pop()[1].lower()
            left = _Boolean(operator, left, self._comparison())
        return left

    def _comparison(self) -> _Comparison:
        column = _Column(self.stream.expect_ident())
        token = self.stream.pop()
        if token[0] != "op" or token[1] not in _COMPARISONS:
            raise MeasureError(
                f"Expected a comparison operator at position {token[2] + 1}, "
                f"found {token[1]!r}."
            )
        operator = _COMPARISONS[token[1]]
        literal = self._literal()
        return _Comparison(column, operator, literal)

    def _literal(self) -> _Number | _Text:
        sign = 1.0
        token = self.stream.peek()
        if token and token[0] == "op" and token[1] in ("+", "-"):
            sign = -1.0 if self.stream.pop()[1] == "-" else 1.0
            token = self.stream.peek()
        if token is None:
            raise MeasureError("Expected a literal value.")
        if token[0] == "number":
            self.stream.pop()
            return _Number(sign * float(token[1]))
        if token[0] == "string":
            self.stream.pop()
            return _Text(token[1][1:-1].replace("''", "'"))
        raise MeasureError(
            f"Expected a number or quoted string at position {token[2] + 1}, "
            f"found {token[1]!r}."
        )


# -- compiler --------------------------------------------------------------


def _sql_literal(value: float | str) -> str:
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    if value == int(value):
        return str(int(value))
    return repr(value)


class _Compiler:
    def __init__(self, resolve: Callable[[str], ResolvedColumn]) -> None:
        self.resolve = resolve
        self.tables: set[str] = set()

    def column(self, node: _Column) -> ResolvedColumn:
        column = self.resolve(node.name)
        self.tables.add(column.table)
        return column

    def expression(self, node: _Node) -> tuple[str, bool]:
        if isinstance(node, _Number):
            return _sql_literal(node.value), True
        if isinstance(node, _Text):
            return _sql_literal(node.value), False
        if isinstance(node, _Aggregate):
            return self.aggregate(node)
        if isinstance(node, _Unary):
            sql, numeric = self.expression(node.operand)
            if not numeric:
                raise MeasureError("Unary +/- requires a numeric expression.")
            return f"({node.operator}{sql})", True
        if isinstance(node, _Binary):
            left_sql, left_numeric = self.expression(node.left)
            right_sql, right_numeric = self.expression(node.right)
            if not (left_numeric and right_numeric):
                raise MeasureError("Arithmetic requires numeric operands.")
            return f"({left_sql} {node.operator} {right_sql})", True
        raise MeasureError("Unknown expression node.")

    def aggregate(self, node: _Aggregate) -> tuple[str, bool]:
        name = node.func
        if name not in _SUPPORTED_AGGREGATES:
            raise MeasureError(f"Unsupported aggregate {name!r}.")
        function = _SUPPORTED_AGGREGATES[name]
        if name == "count_distinct":
            if node.column is None:
                raise MeasureError("count_distinct requires a column name.")
            column = self.column(node.column)
            sql = f"COUNT(DISTINCT {column.sql})"
        elif node.column is None:
            if name != "count":
                raise MeasureError(f"{name} requires a column name.")
            sql = "COUNT(*)"
        else:
            column = self.column(node.column)
            if name in _NUMERIC_AGGREGATES and not column.numeric:
                raise MeasureError(
                    f"{name} requires a numeric column; {column.name!r} is not numeric."
                )
            sql = f"{function}({column.sql})"
        if node.condition is not None:
            sql = f"{sql} FILTER (WHERE {self.condition(node.condition)})"
        return sql, True

    def condition(self, node: _Condition) -> str:
        if isinstance(node, _Comparison):
            column = self.column(node.column)
            literal = node.literal
            if isinstance(literal, _Number) and not column.numeric:
                raise MeasureError(
                    f"Cannot compare text column {column.name!r} to a number."
                )
            sql_literal = _sql_literal(literal.value)
            return f"{column.sql} {node.operator} {sql_literal}"
        if isinstance(node, _Boolean):
            return (
                f"({self.condition(node.left)} {node.operator.upper()} "
                f"{self.condition(node.right)})"
            )
        raise MeasureError("Unknown condition node.")


def compile_measure(
    expression: str,
    resolve: Callable[[str], ResolvedColumn],
) -> CompiledMeasure:
    """Parse and compile a measure expression to SQL.

    ``resolve`` maps a column name (optionally ``table.column``) to a
    `ResolvedColumn`; it is responsible for rejecting unknown or ambiguous columns.
    """

    if not isinstance(expression, str) or not expression.strip():
        raise MeasureError("Measure expression must be a non-empty string.")
    tree = _Parser(expression).parse()
    compiler = _Compiler(resolve)
    sql, numeric = compiler.expression(tree)
    return CompiledMeasure(sql, frozenset(compiler.tables), numeric)


def referenced_columns(expression: str) -> tuple[str, ...]:
    """Return column names referenced by an expression, without resolving them."""

    tree = _Parser(expression).parse()
    names: list[str] = []

    def visit(node: _Node) -> None:
        if isinstance(node, _Aggregate):
            if node.column is not None:
                names.append(node.column.name)
            if node.condition is not None:
                visit_condition(node.condition)
        elif isinstance(node, _Unary):
            visit(node.operand)
        elif isinstance(node, _Binary):
            visit(node.left)
            visit(node.right)

    def visit_condition(node: _Condition) -> None:
        if isinstance(node, _Comparison):
            names.append(node.column.name)
        else:
            visit_condition(node.left)
            visit_condition(node.right)

    visit(tree)
    unique: list[str] = []
    for name in names:
        if name not in unique:
            unique.append(name)
    return tuple(unique)


__all__ = [
    "CompiledMeasure",
    "MeasureError",
    "ResolvedColumn",
    "compile_measure",
    "referenced_columns",
]
