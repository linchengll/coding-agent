"""简单的 DFS 与 BFS 图遍历算法实现。

图使用邻接表表示，例如：
    graph = {
        "A": ["B", "C"],
        "B": ["D", "E"],
        "C": ["F"],
        "D": [],
        "E": ["F"],
        "F": [],
    }
"""

from collections import deque
from typing import Dict, Hashable, List


def dfs(graph: Dict[Hashable, List[Hashable]], start: Hashable) -> List[Hashable]:
    """深度优先搜索（迭代实现，使用栈）。

    从 start 节点出发，返回按 DFS 顺序访问到的节点列表。
    """
    if start not in graph:
        return []

    visited = set()
    stack = [start]
    order = []

    while stack:
        node = stack.pop()
        if node in visited:
            continue
        visited.add(node)
        order.append(node)

        for neighbor in reversed(graph.get(node, [])):
            if neighbor not in visited:
                stack.append(neighbor)

    return order


def bfs(graph: Dict[Hashable, List[Hashable]], start: Hashable) -> List[Hashable]:
    """广度优先搜索（迭代实现，使用队列）。

    从 start 节点出发，返回按 BFS 顺序访问到的节点列表。
    """
    if start not in graph:
        return []

    visited = {start}
    queue = deque([start])
    order = []

    while queue:
        node = queue.popleft()
        order.append(node)

        for neighbor in graph.get(node, []):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)

    return order


if __name__ == "__main__":
    sample_graph = {
        "A": ["B", "C"],
        "B": ["D", "E"],
        "C": ["F"],
        "D": [],
        "E": ["F"],
        "F": [],
    }

    print("DFS from A:", dfs(sample_graph, "A"))
    print("BFS from A:", bfs(sample_graph, "A"))
