"""Mathematical utility functions.

This module provides factorial, Fibonacci and primality-test helpers.
"""

import math


def factorial(n):
    """Return the factorial of a non-negative integer n.

    Raises:
        ValueError: if n is negative.
    """
    if not isinstance(n, int):
        raise TypeError("n must be an integer")
    if n < 0:
        raise ValueError("n must be non-negative")
    result = 1
    for i in range(2, n + 1):
        result *= i
    return result


def fib(n):
    """Return the n-th Fibonacci number (0-indexed).

    fib(0) == 0, fib(1) == 1, fib(n) == fib(n-1) + fib(n-2).

    Raises:
        ValueError: if n is negative.
    """
    if not isinstance(n, int):
        raise TypeError("n must be an integer")
    if n < 0:
        raise ValueError("n must be non-negative")

    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a


def is_prime(n):
    """Return True if n is a prime number, False otherwise.

    Handles large integers efficiently by trial division using the
    6k +/- 1 optimisation up to sqrt(n).
    """
    if not isinstance(n, int):
        raise TypeError("n must be an integer")
    if n < 2:
        return False
    if n in (2, 3):
        return True
    if n % 2 == 0 or n % 3 == 0:
        return False

    limit = math.isqrt(n)
    k = 5
    while k <= limit:
        if n % k == 0 or n % (k + 2) == 0:
            return False
        k += 6
    return True
