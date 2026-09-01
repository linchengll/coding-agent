"""Expression calculator package.

Public API:
    eval_expr(expr) -> numeric result
"""

from .lexer import tokenize
from .parser import parse
from .evaluator import eval_ast


def eval_expr(expr):
    """Evaluate an arithmetic expression string and return its result."""
    return eval_ast(parse(tokenize(expr)))


__all__ = ["eval_expr"]
