# agent.py
"""Agent 入口
无参 → repl_loop（交互式 REPL）；带参 → agent_loop（一次性任务）。
"""

import sys

from repl import repl_loop
from loop import agent_loop


def main():
    if len(sys.argv) < 2:
        repl_loop()
        return
    user_request = sys.argv[1]
    print(f"[agent] 收到需求: {user_request}\n")
    agent_loop(user_request)    # 内部已保存记忆+对话；最终答案由 call_llm 流式实时打印


if __name__ == "__main__":
    main()
