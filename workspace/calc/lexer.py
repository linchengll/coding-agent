"""Lexer for arithmetic expressions."""

from typing import List, Tuple

# A token is represented as a (type, value) tuple, e.g. ("INTEGER", 42).
Token = Tuple[str, object]

_OPERATORS = {
    "+": "PLUS",
    "-": "MINUS",
    "*": "MUL",
    "/": "DIV",
    "(": "LPAREN",
    ")": "RPAREN",
}


def tokenize(expr: str) -> List[Token]:
    """Convert an expression string into a list of tokens.

    Supported token types: INTEGER, PLUS, MINUS, MUL, DIV, LPAREN, RPAREN.
    Whitespace is skipped. Any other character raises ValueError.
    """
    tokens: List[Token] = []
    i = 0
    n = len(expr)

    while i < n:
        ch = expr[i]

        if ch.isspace():
            i += 1
            continue

        if ch.isdigit():
            j = i
            while j < n and expr[j].isdigit():
                j += 1
            tokens.append(("INTEGER", int(expr[i:j])))
            i = j
            continue

        if ch in _OPERATORS:
            tokens.append((_OPERATORS[ch], ch))
            i += 1
            continue

        raise ValueError(f"Invalid character: {ch!r}")

    return tokens
