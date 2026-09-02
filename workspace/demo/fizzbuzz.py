"""FizzBuzz 实现：对 1 到 15 的数字生成 FizzBuzz 序列。"""


def fizzbuzz_value(n: int) -> str:
    """返回单个数字对应的 FizzBuzz 结果字符串。"""
    if n % 15 == 0:
        return "FizzBuzz"
    if n % 3 == 0:
        return "Fizz"
    if n % 5 == 0:
        return "Buzz"
    return str(n)


def fizzbuzz(start: int = 1, end: int = 15) -> list[str]:
    """返回 [start, end] 区间内每个数字的 FizzBuzz 结果列表。"""
    return [fizzbuzz_value(i) for i in range(start, end + 1)]


if __name__ == "__main__":
    for line in fizzbuzz(1, 15):
        print(line)
