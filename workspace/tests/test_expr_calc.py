"""Tests for the calc expression calculator package."""

import pytest

from calc import eval_expr


class TestSingleOperator:
    def test_addition(self):
        assert eval_expr("2 + 3") == 5

    def test_subtraction(self):
        assert eval_expr("10 - 4") == 6

    def test_multiplication(self):
        assert eval_expr("6 * 7") == 42

    def test_division(self):
        assert eval_expr("8 / 2") == 4


class TestMixedOperations:
    def test_multiplication_before_addition(self):
        assert eval_expr("2 + 3 * 4") == 14

    def test_multiplication_before_subtraction(self):
        assert eval_expr("20 - 4 * 3") == 8

    def test_division_and_addition(self):
        assert eval_expr("10 + 8 / 2") == 14


class TestParenthesesPrecedence:
    def test_parentheses_override_precedence(self):
        assert eval_expr("(2 + 3) * 4") == 20

    def test_parentheses_around_sum(self):
        assert eval_expr("2 * (3 + 4)") == 14

    def test_nested_parentheses(self):
        assert eval_expr("((2 + 3) * (4 + 1))") == 25


class TestDivisionByZero:
    def test_division_by_zero_raises(self):
        with pytest.raises(ZeroDivisionError):
            eval_expr("1 / 0")


class TestInvalidInput:
    def test_invalid_character_raises(self):
        with pytest.raises(ValueError):
            eval_expr("2 + 3 $")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            eval_expr("")
