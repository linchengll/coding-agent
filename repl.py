# repl.py
"""交互式 REPL：多轮对话 + 内置命令 /new /memory /undo /exit。
复用 loop._run_inner_loop 与 memory 记忆管理；_last_plan_step 归属 loop 模块。"""
import loop
import tools
from loop import _run_inner_loop
from memory import _build_system_prompt_with_memory, _save_memory_from_final


def _print_memory_status() -> None:
    """REPL /memory 命令：打印当前长期记忆状态（人类可读摘要）"""
    mem = tools.memory_load()
    if not mem:
        print("[REPL] 长期记忆为空（首次启动或尚无任务历史）")
        return
    print("[REPL] === 长期记忆状态 ===")
    for k, v in mem.items():
        if k == "task_log" and isinstance(v, list):
            print(f"- task_log（共 {len(v)} 条任务历史，最近 5 条）:")
            for i, t in enumerate(v[-5:], 1):
                ts = t.get("ts", "")
                req = str(t.get("request", ""))[:60]
                status = t.get("status", "")
                tools_used = t.get("tools_used", 0)
                print(f"    {i}. [{ts}] {req} -> {status}（{tools_used} 次工具）")
        elif isinstance(v, list):
            preview = ", ".join(map(str, v[:5]))
            more = f"... +{len(v)-5}" if len(v) > 5 else ""
            print(f"- {k}（{len(v)} 项）: {preview}{more}")
        else:
            print(f"- {k}: {str(v)[:200]}")
    # 当前任务计划状态
    plan = tools.get_plan_summary()
    if plan:
        print("\n[当前任务计划]")
        print(plan)


def repl_loop() -> None:
    """交互式 REPL（阶段4方向4）：python agent.py 无参进入。
    特性：
      - 多轮对话，复用 conversation 持久化机制，跨会话上下文连续
      - 内置命令：/new /memory /undo /exit
      - LLM 流式输出（call_llm 内部已用 stream=True）
    """
    print("=" * 60)
    print("编程伙伴已启动（REPL 模式）")
    print("内置命令：")
    print("  /new     开启新任务（保留长期记忆，清空当前对话+任务计划）")
    print("  /memory  查看长期记忆状态 + 当前任务计划")
    print("  /undo    撤销最近一次用户输入及其后续对话")
    print("  /exit    保存记忆+对话，退出（Ctrl+C / Ctrl+D 也可）")
    print("=" * 60)

    # 初始化上下文：system prompt（含长期记忆+上一轮对话摘要）
    messages = [
        {"role": "system", "content": _build_system_prompt_with_memory()},
    ]
    fail_streak = {"fingerprint": "", "count": 0}
    tool_call_count = 0
    last_user_request = ""
    last_final_answer = ""

    while True:
        try:
            user_input = input("\n你> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()    # Ctrl+D/Ctrl+C 视为 /exit
            user_input = "/exit"
        if not user_input:
            continue

        # ──────── 内置命令 ────────
        if user_input in ("/exit", "/quit", "/q"):
            # 保存最近一轮的最终回答到长期记忆（如果有）
            if last_final_answer:
                _save_memory_from_final(
                    last_user_request, last_final_answer, tool_call_count
                )
            try:
                tools.conversation_save(messages)
            except Exception:
                pass
            print("[REPL] 已保存记忆+对话，再见！")
            return
        elif user_input == "/new":
            messages = [
                {"role": "system", "content": _build_system_prompt_with_memory()},
            ]
            tools.reset_plan()
            loop._last_plan_step = None
            fail_streak = {"fingerprint": "", "count": 0}
            tool_call_count = 0
            last_user_request = ""
            last_final_answer = ""
            print("[REPL] 已开启新任务（长期记忆保留，当前对话+计划已清空）")
            continue
        elif user_input == "/memory":
            _print_memory_status()
            continue
        elif user_input == "/undo":
            # 撤销最近一次 user 输入 + 后续 assistant/tool 消息
            last_user_idx = -1
            for i in range(len(messages) - 1, -1, -1):
                if messages[i].get("role") == "user":
                    last_user_idx = i
                    break
            if last_user_idx > 0:    # 不能删 system
                removed = messages[last_user_idx:]
                messages = messages[:last_user_idx]
                fail_streak = {"fingerprint": "", "count": 0}
                print(f"[REPL] 已撤销最近一轮（移除 {len(removed)} 条消息）")
            else:
                print("[REPL] 没有可撤销的用户输入")
            continue
        elif user_input.startswith("/"):
            print(f"[REPL] 未知命令: {user_input}")
            print("       支持的命令: /new /memory /undo /exit")
            continue

        # ──────── 普通输入：作为本轮 user 消息跑内部循环 ────────
        last_user_request = user_input
        messages.append({"role": "user", "content": user_input})

        try:
            final_text, added = _run_inner_loop(messages, fail_streak)
        except KeyboardInterrupt:
            print("\n[REPL] 本轮被中断（已保留当前 messages，可继续输入或 /undo 撤销）")
            continue
        except Exception as e:
            print(f"\n[REPL] 本轮异常: {type(e).__name__}: {e}")
            continue

        tool_call_count += added
        last_final_answer = final_text

        # 每轮结束保存对话（防止意外退出丢上下文，复用 conversation 机制）
        try:
            tools.conversation_save(messages)
        except Exception:
            pass
