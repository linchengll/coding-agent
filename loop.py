# loop.py
"""核心循环：Agent 主循环 + 阶段横幅+ 失败指纹强制换方案 + 历史压缩。
模块级全局 _last_plan_step 由 agent_loop 与 repl /new 重置。"""
import json

import tools
from logger import emitter
from config import MAX_TOOL_CALLS, MAX_TOTAL_TURNS, COMPRESS_THRESHOLD
from llm import call_llm
from executor import execute_tool_call
from memory import _build_system_prompt_with_memory, _save_memory_from_final

# 阶段横幅缓存：上次观察到的计划当前步，仅用于 stage_change 触发比较
_last_plan_step = None

# ─────────── 失败指纹与强制换方案（阶段4方向2）───────────
FAIL_REPEAT_THRESHOLD = 3   # 同一失败连续出现 N 次后强制换方案


def _tool_failure_fingerprint(fn_name: str, fn_args: dict, result: str) -> str:
    """计算失败指纹：工具名 + 关键参数 + 失败摘要。
    用于判断"是否同一失败连续出现"，触发强制换方案。"""
    # 提取关键参数（路径/命令/old_string 等决定行为的字段，去掉无关字段）
    key_fields = ("path", "command", "old_string", "pattern", "target", "message")
    key_args = {k: str(fn_args.get(k, ""))[:80] for k in key_fields if k in fn_args}
    # 解析结果里的 success + reason
    try:
        r = json.loads(result)
        success = r.get("success")
        reason = (r.get("reason") or "")[:120]
    except Exception:
        success = None
        reason = result[:120]
    # 失败才需要指纹；成功返回空串表示"无失败"
    if success is True:
        return ""
    return f"{fn_name}|{json.dumps(key_args, sort_keys=True, ensure_ascii=False)}|{reason}"


def _inject_force_pivot(messages: list, fingerprint: str) -> None:
    """注入一条强制换方案系统消息：连续 3 次同失败，必须换思路。"""
    msg = (
        "[系统强制] 检测到同一失败已连续出现 "
        f"{FAIL_REPEAT_THRESHOLD} 次（指纹：{fingerprint[:80]}）。\n"
        "你必须立即停止重试相同方案，改为换思路：\n"
        "  - 换算法/换实现方式/换数据结构\n"
        "  - 重新 update_plan 拆解，可能需要回退到更早步骤\n"
        "  - 如确实无法完成，直接输出 status=blocked 的最终 JSON\n"
        "继续重试相同操作将不会推进任务。"
    )
    messages.append({"role": "system", "content": msg})
    print(f"\n  [WARNING][agent] 同一失败连续 {FAIL_REPEAT_THRESHOLD} 次，注入强制换方案消息")


def _approx_chars(messages) -> int:
    """粗估消息总字符数（约 3 字符≈1 token）"""
    total = 0
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            total += len(c)
        total += len(str(m.get("tool_call_id", "")))
    return total


def _maybe_compress_history(messages: list) -> list:
    """短期记忆压缩：总字符超阈值时，把最旧的 tool 结果折叠成摘要。
    策略：保留 system + 第1条 user + 最近6条；中间的 tool 消息 content 截短为摘要。
    （LLM 真正的语义摘要可作为后续升级，这里用确定性截断，零额外调用）"""
    if _approx_chars(messages) <= COMPRESS_THRESHOLD:
        return messages
    if len(messages) <= 8:
        return messages   # 太少不压

    keep_recent = 6
    new_messages = [messages[0]]   # system
    # 找到第一条 user
    idx = 1
    while idx < len(messages) and messages[idx].get("role") != "user":
        new_messages.append(messages[idx])
        idx += 1
    if idx < len(messages):
        new_messages.append(messages[idx])   # first user
        idx += 1

    middle = messages[idx: len(messages) - keep_recent]
    tail = messages[len(messages) - keep_recent:]

    compressed_summary = ["[历史压缩] 以下为已折叠的旧轮次摘要（完整内容已省略以节省上下文）："]
    for m in middle:
        role = m.get("role")
        content = m.get("content", "")
        if role == "tool":
            snippet = (content[:150] + "...") if len(content) > 150 else content
            compressed_summary.append(f"- tool_call_id={m.get('tool_call_id','?')[:12]}: {snippet}")
        elif role == "assistant":
            tcs = m.get("tool_calls")
            if tcs:
                names = ",".join(tc["function"]["name"] for tc in tcs)
                compressed_summary.append(f"- assistant 调用工具: {names}")
            elif content:
                compressed_summary.append(f"- assistant: {content[:150]}")
    new_messages.append({"role": "system", "content": "\n".join(compressed_summary)})
    new_messages.extend(tail)
    print(f"[agent] 触发历史压缩：{len(messages)} -> {len(new_messages)} 条消息")
    return new_messages


def _emit_stage_change_if_advanced() -> None:
    """阶段横幅触发器：update_plan 执行后调用。
    比较 tools._PLAN_CURRENT 与模块级缓存 _last_plan_step，前进则发射 stage_change。
    计划源数据在 tools 模块全局（update_plan 返回的 result 字段是文本摘要，不可直接解析）。"""
    global _last_plan_step
    steps = tools._PLAN_STEPS
    if not steps:
        return
    current = tools._PLAN_CURRENT
    # current=0 或越界时，从 steps 的 status 推断当前阶段（兜底）
    if current <= 0 or current > len(steps):
        doing = [s for s in steps if s.get("status") == "doing"]
        if doing:
            current = doing[0].get("step", 1)
        else:
            not_done = [s for s in steps if s.get("status") != "done"]
            if not_done:
                current = not_done[0].get("step", 1)
            else:
                current = len(steps)   # 全部 done
    if current <= 0 or current > len(steps):
        return
    if current == _last_plan_step:
        return
    last = _last_plan_step or 0     # None -> 0
    if current <= last:
        # 后退（模型回退重做某步）：更新基准不补打
        _last_plan_step = current
        return
    # 前进：补打 last+1 .. current 全部横幅（统一处理首次建计划与中途跳步）
    for s in range(last + 1, current + 1):
        if s > len(steps):
            break
        emitter.emit("stage_change", {
            "current_step": s,
            "total_steps": len(steps),
            "desc": steps[s - 1].get("desc", ""),
        })
    _last_plan_step = current


def _run_inner_loop(messages: list, fail_streak: dict) -> tuple:
    """跑内部循环直到模型给出纯文本回答（无 tool_calls）。
    直接修改传入的 messages（追加 assistant/tool 消息）。
    返回 (final_answer_text, tool_call_count)。
    """
    tool_call_count = 0
    final_answer = ""

    for turn in range(MAX_TOTAL_TURNS):
        # 0. 每轮前尝试压缩历史（短期记忆管理）
        _ = _maybe_compress_history(messages)

        # 0b. 阶段4方向1：注入任务计划+进度到上下文（让模型每轮都看到当前进度）
        plan_summary = tools.get_plan_summary()
        plan_injected_idx = -1
        if plan_summary:
            messages.append({"role": "system", "content": plan_summary})
            plan_injected_idx = len(messages) - 1

        # 1. 调用模型（流式）
        assistant = call_llm(messages)

        # 1b. 用完即删：临时注入的 plan system 消息已服务完，下一轮重新注入最新版本
        if plan_injected_idx >= 0 and plan_injected_idx < len(messages):
            messages.pop(plan_injected_idx)

        # 2. 如果不返回 tool_calls，把纯文本回答加入历史并结束
        if not assistant.tool_calls:
            messages.append({
                "role": "assistant",
                "content": assistant.content or "（模型返回空内容）",
            })
            final_answer = assistant.content or ""
            return final_answer, tool_call_count

        # 3. 模型请求调用工具
        messages.append({
            "role": "assistant",
            "content": assistant.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in assistant.tool_calls
            ],
        })

        # 4. 逐个执行工具
        for tool_call in assistant.tool_calls:
            tool_call_count += 1
            if tool_call_count > MAX_TOOL_CALLS:
                print(f"[agent] 达到最大工具调用次数 {MAX_TOOL_CALLS}，强制终止")
                return json.dumps(
                    {
                        "status": "failed",
                        "summary": "达到最大工具调用次数，任务未完成",
                        "changed_files": [],
                        "test_results": None,
                        "unfinished": "agent 陷入循环，已强制终止",
                    },
                    ensure_ascii=False,
                    indent=2,
                ), tool_call_count

            tool_result = execute_tool_call(tool_call)
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": tool_result,
            })

            # 阶段横幅：update_plan 后若 current_step 变化，发射 stage_change
            # 计划源数据在 tools 模块全局，直接读取（update_plan 返回的 result 字段是文本摘要）
            if tool_call.function.name == "update_plan":
                _emit_stage_change_if_advanced()

            # 阶段4方向2：失败指纹累计+同错3次强制换方案
            try:
                fn_args = json.loads(tool_call.function.arguments or "{}")
            except Exception:
                fn_args = {}
            fp = _tool_failure_fingerprint(tool_call.function.name, fn_args, tool_result)
            if fp:
                if fp == fail_streak["fingerprint"]:
                    fail_streak["count"] += 1
                else:
                    fail_streak["fingerprint"] = fp
                    fail_streak["count"] = 1
                # 连续 N 次同失败：注入强制换方案
                if fail_streak["count"] >= FAIL_REPEAT_THRESHOLD:
                    _inject_force_pivot(messages, fp)
                    fail_streak["count"] = 0
                    fail_streak["fingerprint"] = ""
            else:
                fail_streak["count"] = 0
                fail_streak["fingerprint"] = ""

    print(f"[agent] 达到最大轮次 {MAX_TOTAL_TURNS}，强制终止")
    return "（达到最大轮次，agent 未给出最终回答）", tool_call_count


def agent_loop(user_request: str) -> str:
    """Agent 主循环：返回最终回答文本（一次性脚本入口）
    阶段4方向4：内部逻辑抽到 _run_inner_loop / _save_memory_from_final，供 REPL 复用。
    """
    # 阶段4：新任务开始前重置计划
    tools.reset_plan()
    global _last_plan_step
    _last_plan_step = None
    # 失败指纹累计器：(指纹 -> 连续次数, 上一次指纹)
    fail_streak = {"fingerprint": "", "count": 0}

    messages = [
        {"role": "system", "content": _build_system_prompt_with_memory()},
        {"role": "user", "content": user_request},
    ]

    final_answer, tool_call_count = _run_inner_loop(messages, fail_streak)

    # 保存长期记忆 + 对话历史（方案A：系统自动统一写入）
    _save_memory_from_final(user_request, final_answer, tool_call_count)
    try:
        tools.conversation_save(messages)
    except Exception:
        pass

    return final_answer
