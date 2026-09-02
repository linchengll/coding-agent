"""简单的贪心算法实现。

本模块包含几个经典贪心算法的 Python 实现：

1. 活动选择问题（Activity Selection）
2. 找零问题（Coin Change，适用于规范硬币体系）
3. 分数背包问题（Fractional Knapsack）
"""

from typing import List, Tuple


def activity_selection(activities: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """活动选择问题：在给定时间内选择尽可能多的不冲突活动。

    每个活动用 (开始时间, 结束时间) 表示。贪心策略：按结束时间最早优先。

    Args:
        activities: 活动列表，每个元素为 (start, end)。

    Returns:
        被选中的活动列表，按结束时间排序。
    """
    if not activities:
        return []

    # 按结束时间升序排列
    sorted_activities = sorted(activities, key=lambda item: item[1])

    selected = [sorted_activities[0]]
    last_end = sorted_activities[0][1]

    for start, end in sorted_activities[1:]:
        if start >= last_end:
            selected.append((start, end))
            last_end = end

    return selected


def coin_change(coins: List[int], amount: int) -> List[int]:
    """找零问题：用最少硬币凑出指定金额（适用于规范硬币体系）。

    贪心策略：每次优先使用面值最大的硬币。对于人民币、美元等规范体系
    该贪心能得到最优解；对于某些非规范体系可能不是最优解。

    Args:
        coins: 可用硬币面值列表。
        amount: 需要凑出的总金额。

    Returns:
        使用到的硬币面值列表；若无法恰好凑出则返回空列表。
    """
    if amount < 0:
        return []

    # 面值从大到小排序
    sorted_coins = sorted(coins, reverse=True)
    result = []
    remaining = amount

    for coin in sorted_coins:
        while remaining >= coin:
            result.append(coin)
            remaining -= coin

    return result if remaining == 0 else []


def fractional_knapsack(
    items: List[Tuple[float, float]], capacity: float
) -> Tuple[float, List[float]]:
    """分数背包问题：物品可以分割，求装入背包的最大总价值。

    每个物品用 (价值, 重量) 表示。贪心策略：按单位重量价值（价值/重量）
    从高到低依次装入，最后一件物品可只取一部分。

    Args:
        items: 物品列表，每个元素为 (value, weight)。
        capacity: 背包容量。

    Returns:
        (最大总价值, 每件物品的装入比例列表)，比例取值范围 [0, 1]。
    """
    if capacity <= 0 or not items:
        return 0.0, []

    # 计算单位价值并排序（价值/重量 从高到低）
    indexed = [(value / weight, value, weight, i) for i, (value, weight) in enumerate(items)]
    indexed.sort(reverse=True)

    total_value = 0.0
    remaining = capacity
    fractions = [0.0] * len(items)

    for ratio, value, weight, i in indexed:
        if remaining <= 0:
            break
        take = min(weight, remaining)
        fractions[i] = take / weight
        total_value += ratio * take
        remaining -= take

    return total_value, fractions


if __name__ == "__main__":
    # 1. 活动选择
    acts = [(1, 4), (3, 5), (0, 6), (5, 7), (3, 9), (5, 9), (6, 10), (8, 11), (8, 12), (2, 14), (12, 16)]
    print("活动选择结果:", activity_selection(acts))

    # 2. 找零（人民币硬币体系）
    print("找零 63 元:", coin_change([1, 5, 10, 20, 50], 63))

    # 3. 分数背包
    items = [(60, 10), (100, 20), (120, 30)]
    value, fractions = fractional_knapsack(items, 50)
    print("分数背包最大价值:", value, "装入比例:", fractions)
