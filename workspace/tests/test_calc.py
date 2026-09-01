"""Tests for math_utils.calc."""
import pytest

from math_utils.calc import factorial, fib, is_prime


class TestFactorial:
    def test_factorial_zero(self):
        assert factorial(0) == 1

    def test_factorial_one(self):
        assert factorial(1) == 1

    def test_factorial_normal(self):
        assert factorial(5) == 120
        assert factorial(10) == 3628800

    def test_factorial_negative_raises(self):
        with pytest.raises(ValueError):
            factorial(-1)


class TestFib:
    def test_fib_zero(self):
        assert fib(0) == 0

    def test_fib_one(self):
        assert fib(1) == 1

    def test_fib_normal(self):
        assert fib(2) == 1
        assert fib(10) == 55

    def test_fib_negative_raises(self):
        with pytest.raises(ValueError):
            fib(-1)


class TestIsPrime:
    def test_is_prime_less_than_two(self):
        assert is_prime(0) is False
        assert is_prime(1) is False

    def test_is_prime_small_numbers(self):
        assert is_prime(2) is True
        assert is_prime(3) is True
        assert is_prime(4) is False
        assert is_prime(9) is False

    def test_is_prime_large_prime(self):
        assert is_prime(9999991) is True

    def test_is_prime_large_composite(self):
        assert is_prime(9999993) is False
