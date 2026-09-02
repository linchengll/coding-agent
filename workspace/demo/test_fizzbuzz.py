"""FizzBuzz 单元测试：覆盖能被 3/5/15 整除的情况。"""

from demo.fizzbuzz import fizzbuzz, fizzbuzz_value


def test_divisible_by_3_returns_fizz():
    assert fizzbuzz_value(3) == "Fizz"
    assert fizzbuzz_value(6) == "Fizz"
    assert fizzbuzz_value(9) == "Fizz"


def test_divisible_by_5_returns_buzz():
    assert fizzbuzz_value(5) == "Buzz"
    assert fizzbuzz_value(10) == "Buzz"


def test_divisible_by_15_returns_fizzbuzz():
    assert fizzbuzz_value(15) == "FizzBuzz"


def test_not_divisible_returns_number():
    assert fizzbuzz_value(1) == "1"
    assert fizzbuzz_value(2) == "2"
    assert fizzbuzz_value(4) == "4"


def test_full_sequence_1_to_15():
    assert fizzbuzz(1, 15) == [
        "1", "2", "Fizz", "4", "Buzz", "Fizz", "7", "8",
        "Fizz", "Buzz", "11", "Fizz", "13", "14", "FizzBuzz",
    ]
