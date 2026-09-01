"""Recursive descent parser for arithmetic expressions."""

from typing import List, Optional

from .lexer import Token


class Num:
    """AST node representing an integer literal."""

    def __init__(self, value):
        self.value = value

    def __repr__(self):
        return f"Num({self.value})"


class BinOp:
    """AST node representing a binary operation."""

    def __init__(self, op, left, right):
        self.op = op
        self.left = left
        self.right = right

    def __repr__(self):
        return f"BinOp({self.op!r}, {self.left!r}, {self.right!r})"


class _Parser:
    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> Optional[Token]:
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return None

    def consume(self, expected_type: Optional[str] = None) -> Token:
        tok = self.peek()
        if tok is None:
            raise ValueError("Unexpected end of expression")
        if expected_type is not None and tok[0] != expected_type:
            raise ValueError(f"Expected {expected_type}, got {tok[0]}")
        self.pos += 1
        return tok

    def parse(self):
        if not self.tokens:
            raise ValueError("Empty expression")
        node = self.expr()
        if self.peek() is not None:
            raise ValueError(f"Unexpected token: {self.peek()[0]}")
        return node

    def expr(self):
        node = self.term()
        while self.peek() is not None and self.peek()[0] in ("PLUS", "MINUS"):
            op = self.consume()[1]
            right = self.term()
            node = BinOp(op, node, right)
        return node

    def term(self):
        node = self.factor()
        while self.peek() is not None and self.peek()[0] in ("MUL", "DIV"):
            op = self.consume()[1]
            right = self.factor()
            node = BinOp(op, node, right)
        return node

    def factor(self):
        tok = self.peek()
        if tok is None:
            raise ValueError("Unexpected end of expression")
        if tok[0] == "INTEGER":
            self.consume("INTEGER")
            return Num(tok[1])
        if tok[0] == "LPAREN":
            self.consume("LPAREN")
            node = self.expr()
            self.consume("RPAREN")
            return node
        raise ValueError(f"Unexpected token: {tok[0]}")


def parse(tokens: List[Token]):
    """Parse a token list into an AST (Num/BinOp)."""
    return _Parser(tokens).parse()
