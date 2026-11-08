"""A small, safe expression language compiled to DuckDB SQL.

Users write familiar Python-like expressions (``amount * 1.1``,
``lower(region) == "north"``, ``if_else(amount > 0, amount, 0)``). The expression is
parsed with Python's AST, every node is checked against a strict allow-list, column
names are validated against the table schema, and the result is emitted as SQL.

No arbitrary SQL is ever accepted: identifiers come from the schema and values are
quoted literals.
"""

from __future__ import annotations

import ast

from ..engine.duckdb_engine import quote_identifier, sql_literal
from ..kernel.schema import TableSchema
from .registry import StepError
from .sql import sql_type

_MAX_EXPRESSION_LENGTH = 4000

_BIN_OPS: dict[type[ast.operator], str] = {
    ast.Add: "+",
    ast.Sub: "-",
    ast.Mult: "*",
    ast.Div: "/",
    ast.Mod: "%",
}
_CMP_OPS: dict[type[ast.cmpop], str] = {
    ast.Eq: "=",
    ast.NotEq: "<>",
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Gt: ">",
    ast.GtE: ">=",
}

# name -> (SQL function, min args, max args)
_FUNCTIONS: dict[str, tuple[str, int, int | None]] = {
    "abs": ("ABS", 1, 1),
    "ceil": ("CEIL", 1, 1),
    "ceiling": ("CEIL", 1, 1),
    "floor": ("FLOOR", 1, 1),
    "round": ("ROUND", 1, 2),
    "sqrt": ("SQRT", 1, 1),
    "exp": ("EXP", 1, 1),
    "ln": ("LN", 1, 1),
    "log": ("LOG", 1, 1),
    "log10": ("LOG10", 1, 1),
    "pow": ("POWER", 2, 2),
    "power": ("POWER", 2, 2),
    "greatest": ("GREATEST", 1, None),
    "least": ("LEAST", 1, None),
    "coalesce": ("COALESCE", 1, None),
    "nullif": ("NULLIF", 2, 2),
    "lower": ("LOWER", 1, 1),
    "upper": ("UPPER", 1, 1),
    "trim": ("TRIM", 1, 1),
    "ltrim": ("LTRIM", 1, 1),
    "rtrim": ("RTRIM", 1, 1),
    "length": ("LENGTH", 1, 1),
    "concat": ("CONCAT", 1, None),
    "replace": ("REPLACE", 3, 3),
    "substr": ("SUBSTR", 2, 3),
    "substring": ("SUBSTR", 2, 3),
    "contains": ("CONTAINS", 2, 2),
    "starts_with": ("STARTS_WITH", 2, 2),
    "ends_with": ("ENDS_WITH", 2, 2),
    "regexp_matches": ("REGEXP_MATCHES", 2, 2),
    "regexp_replace": ("REGEXP_REPLACE", 3, 4),
    "date_part": ("DATE_PART", 2, 2),
    "date_parts": ("DATE_PART", 2, 2),
    "date_trunc": ("DATE_TRUNC", 2, 2),
    "strptime": ("STRPTIME", 2, 2),
    "try_strptime": ("TRY_STRPTIME", 2, 2),
    "now": ("NOW", 0, 0),
    "random": ("RANDOM", 0, 0),
    "sign": ("SIGN", 1, 1),
}

_COLUMN_FUNCTIONS = {"col", "column", "field"}
_CONSTANTS = {"current_date": "CURRENT_DATE", "current_timestamp": "CURRENT_TIMESTAMP"}


def _is_string_literal(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def compile_expression(expression: str, schema: TableSchema) -> str:
    """Compile a checked expression against ``schema`` into DuckDB SQL."""

    if not isinstance(expression, str) or not expression.strip():
        raise StepError("Expression must be a non-empty string.")
    if len(expression) > _MAX_EXPRESSION_LENGTH:
        raise StepError(f"Expression is longer than {_MAX_EXPRESSION_LENGTH} characters.")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise StepError(f"Invalid expression: {exc.msg}.") from exc
    compiler = _Compiler(schema)
    return compiler.visit(tree.body)


class _Compiler:
    def __init__(self, schema: TableSchema) -> None:
        self.schema = schema

    def visit(self, node: ast.AST) -> str:
        method = getattr(self, f"visit_{type(node).__name__}", None)
        if method is None:
            raise StepError(
                f"Unsupported expression element {type(node).__name__}; "
                "use columns, literals, arithmetic, comparisons and approved functions."
            )
        return method(node)

    def visit_Constant(self, node: ast.Constant) -> str:
        value = node.value
        if value is None or isinstance(value, (bool, int, float, str)):
            return sql_literal(value)
        raise StepError(f"Unsupported literal {value!r}.")

    def visit_Name(self, node: ast.Name) -> str:
        if node.id in self.schema:
            return quote_identifier(node.id)
        available = ", ".join(self.schema.names) or "(none)"
        raise StepError(
            f"Unknown column {node.id!r} in expression. Available columns: {available}."
        )

    def visit_BinOp(self, node: ast.BinOp) -> str:
        if isinstance(node.op, ast.Pow):
            return f"POWER({self.visit(node.left)}, {self.visit(node.right)})"
        if isinstance(node.op, ast.FloorDiv):
            return f"FLOOR({self.visit(node.left)} / {self.visit(node.right)})"
        if isinstance(node.op, ast.Mult) and (
            _is_string_literal(node.left) or _is_string_literal(node.right)
        ):
            raise StepError("Text repetition with '*' is not supported.")
        operator = _BIN_OPS.get(type(node.op))
        if operator is None:
            raise StepError("Unsupported arithmetic operator.")
        return f"({self.visit(node.left)} {operator} {self.visit(node.right)})"

    def visit_UnaryOp(self, node: ast.UnaryOp) -> str:
        if isinstance(node.op, ast.USub):
            return f"(-{self.visit(node.operand)})"
        if isinstance(node.op, ast.UAdd):
            return f"(+{self.visit(node.operand)})"
        if isinstance(node.op, ast.Not):
            return f"(NOT {self.visit(node.operand)})"
        raise StepError("Unsupported unary operator.")

    def visit_BoolOp(self, node: ast.BoolOp) -> str:
        operator = " AND " if isinstance(node.op, ast.And) else " OR "
        if not isinstance(node.op, (ast.And, ast.Or)):
            raise StepError("Unsupported boolean operator.")
        parts = [self.visit(value) for value in node.values]
        return "(" + operator.join(parts) + ")"

    def visit_Compare(self, node: ast.Compare) -> str:
        left = self.visit(node.left)
        parts: list[str] = []
        for operator, comparator in zip(node.ops, node.comparators, strict=True):
            if isinstance(operator, (ast.In, ast.NotIn)):
                values = self._literal_sequence(comparator)
                keyword = "IN" if isinstance(operator, ast.In) else "NOT IN"
                parts.append(f"{left} {keyword} ({', '.join(values)})")
                continue
            right = self.visit(comparator)
            if isinstance(operator, (ast.Is, ast.IsNot)):
                if not (isinstance(comparator, ast.Constant) and comparator.value is None):
                    raise StepError("The 'is' operator is only supported with None.")
                keyword = "IS NULL" if isinstance(operator, ast.Is) else "IS NOT NULL"
                parts.append(f"{left} {keyword}")
            else:
                symbol = _CMP_OPS.get(type(operator))
                if symbol is None:
                    raise StepError("Unsupported comparison operator.")
                parts.append(f"{left} {symbol} {right}")
            left = right
        return "(" + " AND ".join(parts) + ")"

    def visit_IfExp(self, node: ast.IfExp) -> str:
        return (
            f"(CASE WHEN {self.visit(node.test)} THEN {self.visit(node.body)} "
            f"ELSE {self.visit(node.orelse)} END)"
        )

    def visit_Call(self, node: ast.Call) -> str:
        if not isinstance(node.func, ast.Name):
            raise StepError("Only direct function calls are allowed in expressions.")
        if node.keywords:
            raise StepError("Keyword arguments are not supported in expressions.")
        name = node.func.id
        args = node.args

        if name in _COLUMN_FUNCTIONS:
            return self._column_argument(name, args)
        if name in {"cast", "try_cast"}:
            return self._cast(name, args)
        if name in {"if_else", "iif"}:
            self._arity(name, args, 3, 3)
            return (
                f"(CASE WHEN {self.visit(args[0])} THEN {self.visit(args[1])} "
                f"ELSE {self.visit(args[2])} END)"
            )
        if name == "is_null":
            self._arity(name, args, 1, 1)
            return f"({self.visit(args[0])} IS NULL)"
        if name == "not_null":
            self._arity(name, args, 1, 1)
            return f"({self.visit(args[0])} IS NOT NULL)"
        if name in {"date", "timestamp"}:
            self._arity(name, args, 1, 1)
            if not (isinstance(args[0], ast.Constant) and isinstance(args[0].value, str)):
                raise StepError(f"{name}() requires a string literal.")
            keyword = "DATE" if name == "date" else "TIMESTAMP"
            return f"{keyword} {sql_literal(args[0].value)}"
        if name in _CONSTANTS:
            self._arity(name, args, 0, 0)
            return _CONSTANTS[name]

        spec = _FUNCTIONS.get(name)
        if spec is None:
            raise StepError(f"Unsupported function {name!r} in expression.")
        sql_name, minimum, maximum = spec
        self._arity(name, args, minimum, maximum)
        return f"{sql_name}({', '.join(self.visit(arg) for arg in args)})"

    def _arity(
        self,
        name: str,
        args: list[ast.expr],
        minimum: int,
        maximum: int | None,
    ) -> None:
        if len(args) < minimum or (maximum is not None and len(args) > maximum):
            if maximum is None:
                expected = f"at least {minimum}"
            elif minimum == maximum:
                expected = str(minimum)
            else:
                expected = f"{minimum}-{maximum}"
            raise StepError(f"{name}() expects {expected} argument(s).")

    def _column_argument(self, name: str, args: list[ast.expr]) -> str:
        self._arity(name, args, 1, 1)
        argument = args[0]
        if not (isinstance(argument, ast.Constant) and isinstance(argument.value, str)):
            raise StepError(f"{name}() requires a column-name string literal.")
        column = argument.value
        if column not in self.schema:
            available = ", ".join(self.schema.names) or "(none)"
            raise StepError(f"Unknown column {column!r}. Available columns: {available}.")
        return quote_identifier(column)

    def _cast(self, name: str, args: list[ast.expr]) -> str:
        self._arity(name, args, 2, 2)
        dtype = args[1]
        if not (isinstance(dtype, ast.Constant) and isinstance(dtype.value, str)):
            raise StepError(f"{name}() requires a type string as its second argument.")
        function = "TRY_CAST" if name == "try_cast" else "CAST"
        return f"{function}({self.visit(args[0])} AS {sql_type(dtype.value)})"

    def _literal_sequence(self, node: ast.expr) -> list[str]:
        if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            raise StepError("The right side of 'in' must be a list, tuple or set.")
        return [self.visit(element) for element in node.elts]


def validate_expression(expression: str, schema: TableSchema) -> None:
    """Raise `StepError` if an expression is unsafe or references unknown columns."""

    compile_expression(expression, schema)


__all__ = ["compile_expression", "validate_expression"]
