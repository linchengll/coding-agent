# -*- coding: utf-8 -*-
"""简单的二分查找实现。

二分查找适用于有序数组，每次将搜索区间缩小一半，时间复杂度 O(log n)。
本模块提供迭代版与递归版两种实现。
"""


def binary_search(arr, target):
    """在有序数组 arr 中查找 target，返回其下标；未找到返回 -1（迭代实现）。"""
    low, high = 0, len(arr) - 1
    while low <= high:
        mid = (low + high) // 2
        if arr[mid] == target:
            return mid
        elif arr[mid] < target:
            low = mid + 1
        else:
            high = mid - 1
    return -1


def binary_search_recursive(arr, target, low=0, high=None):
    """在有序数组 arr 中查找 target，返回其下标；未找到返回 -1（递归实现）。"""
    if high is None:
        high = len(arr) - 1
    if low > high:
        return -1
    mid = (low + high) // 2
    if arr[mid] == target:
        return mid
    elif arr[mid] < target:
        return binary_search_recursive(arr, target, mid + 1, high)
    else:
        return binary_search_recursive(arr, target, low, mid - 1)


if __name__ == "__main__":
    nums = [1, 3, 5, 7, 9, 11, 13, 15]
    print("有序数组:", nums)
    for value in [7, 3, 10]:
        idx = binary_search(nums, value)
        ridx = binary_search_recursive(nums, value)
        print(f"查找 {value} -> 迭代下标 {idx}, 递归下标 {ridx}")
