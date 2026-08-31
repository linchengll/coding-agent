"""计算斐波那契数列前 N 项之和。

斐波那契数列定义为：1, 1, 2, 3, 5, 8, ...
"""


def fib_sum(n: int) -> int:
    """返回斐波那契数列前 n 项之和。

    数列以 1, 1 开头。前 10 项为：
    1, 1, 2, 3, 5, 8, 13, 21, 34, 55，和为 143。
    """
    if n <= 0:
        return 0

    total = 0
    a, b = 1, 1
    for _ in range(n):
        total += a
        a, b = b, a + b
    return total


def fib(n: int) -> int:
    """返回第 n 个斐波那契数（数列以 1, 1 开头）。

    第 1 项为 1，第 2 项为 1，第 3 项为 2，依此类推。
    n <= 0 时返回 0。
    """
    if n <= 0:
        return 0

    a, b = 1, 1
    for _ in range(n - 1):
        a, b = b, a + b
    return a


if __name__ == "__main__":
    n = 10
    print(f"斐波那契数列前 {n} 项之和为：{fib_sum(n)}")
    print(f"第 {n} 个斐波那契数为：{fib(n)}")
