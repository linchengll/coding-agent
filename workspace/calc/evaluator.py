"""Evaluator for arithmetic ASTs."""

from . import lexer  # noqa: F401  (evaluator depends on the lexer per spec)
from .parser import BinOp, Num


def eval_ast(node):
    """Recursively evaluate an AST node and return its numeric value."""
    if isinstance(node, Num):
        return node.value

    if isinstance(node, BinOp):
        left = eval_ast(node.left)
        right = eval_ast(node.right)

        if node.op == "+":
            return left + right
        if node.op == "-":
            return left - right
        if node.op == "*":
            return left * right
        if node.op == "/":
            return left / right

        raise ValueError(f"Unknown operator: {node.op}")

    raise TypeError(f"Unknown AST node: {node!r}")
