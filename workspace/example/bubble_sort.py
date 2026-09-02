"""简单的冒泡排序算法实现。

包含基础冒泡排序与带提前终止优化的冒泡排序，
并提供可运行的示例。
"""

from typing import List


def bubble_sort(arr: List[int]) -> List[int]:
    """基础冒泡排序（升序）。

    每一轮把最大的元素“冒泡”到末尾。
    """
    n = len(arr)
    for i in range(n - 1):
        for j in range(n - 1 - i):
            if arr[j] > arr[j + 1]:
                arr[j], arr[j + 1] = arr[j + 1], arr[j]
    return arr


def bubble_sort_optimized(arr: List[int]) -> List[int]:
    """带提前终止优化的冒泡排序（升序）。

    若某一轮没有发生交换，说明数组已有序，可提前结束。
    """
    n = len(arr)
    for i in range(n - 1):
        swapped = False
        for j in range(n - 1 - i):
            if arr[j] > arr[j + 1]:
                arr[j], arr[j + 1] = arr[j + 1], arr[j]
                swapped = True
        if not swapped:
            break
    return arr


if __name__ == "__main__":
    sample = [5, 2, 9, 1, 5, 6]
    print("原始数组:", sample)
    print("基础冒泡排序:", bubble_sort(sample.copy()))
    print("优化冒泡排序:", bubble_sort_optimized(sample.copy()))
