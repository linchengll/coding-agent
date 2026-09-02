# memory.py
"""记忆管理：注入长期记忆+上一轮对话摘要到 system prompt；
任务结束时从 final_answer 抽取 goal/changed_files/test_results 写入长期记忆。"""
import datetime
import json

import tools
from config import SYSTEM_PROMPT


def _summarize_conversation(messages: list, max_chars: int = 3000, keep_recent: int = 15) -> str:
    """把上一轮对话历史压缩成紧凑文本摘要（上下文管理：控长度）"""
    relevant = [m for m in messages
                if m.get("role") in ("user", "assistant", "tool")][-keep_recent:]
    lines = []
    for m in relevant:
        role = m.get("role")
        if role == "user":
            lines.append(f"用户: {str(m.get('content', ''))[:200]}")
        elif role == "assistant":
            tcs = m.get("tool_calls")
            if tcs:
                names = ",".join(tc["function"]["name"] for tc in tcs)
                lines.append(f"助手调用工具: {names}")
            c = m.get("content")
            if c:
                lines.append(f"助手: {str(c)[:200]}")
        elif role == "tool":
            lines.append(f"  └工具结果: {str(m.get('content', ''))[:150]}")
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n... [摘要已截断，完整记录见 .agent_conversation.json]"
    return text


def _build_system_prompt_with_memory() -> str:
    """启动时注入：长期记忆(task_log 全历史) + 上一轮对话摘要"""
    parts = [SYSTEM_PROMPT]
    mem = tools.memory_load()
    if mem:
        lines = ["\n\n========== 项目长期记忆（跨会话，请结合继续） =========="]
        for k, v in mem.items():
            if k == "task_log" and isinstance(v, list):
                recent = v[-10:]
                lines.append(f"- 任务历史（共{len(v)}条，最近{len(recent)}条）:")
                for i, t in enumerate(recent, 1):
                    lines.append(f"    {i}. [{t.get('ts', '')}] "
                                 f"{str(t.get('request', ''))[:80]} -> {t.get('status', '')}")
            elif isinstance(v, list):
                lines.append(f"- {k}: {', '.join(map(str, v))}")
            else:
                lines.append(f"- {k}: {v}")
        lines.append("（以上记忆可能已过时，必要时用工具核实当前状态）")
        parts.append("\n".join(lines))
    # 上一轮对话摘要（上下文管理：压缩后注入，避免全量历史膨胀）
    prior = tools.conversation_load()
    if prior:
        summary = _summarize_conversation(prior)
        if summary:
            parts.append("\n\n========== 上一轮对话摘要（跨会话上下文） ==========\n" + summary)
    return "".join(parts)


def _save_memory_from_final(user_request: str, final_answer: str, tool_call_count: int) -> None:
    """从 final_answer 抽取 goal/changed_files/test_results 写入长期记忆。
    方案A：系统自动统一写入（模型无权直接调用 memory_save）。
    """
    goal = user_request[:300]   # 默认 goal 用原始需求
    parsed = None
    changed = []
    test_s = None
    if final_answer:
        try:
            # 兼容：final_answer 可能是纯 JSON，也可能是 Markdown 里嵌 JSON ```json ... ```
            raw = final_answer.strip()
            if raw.startswith("```"):
                lines = raw.splitlines()
                lines = lines[1:] if lines else []
                while lines and lines[-1].strip().startswith("```"):
                    lines = lines[:-1]
                raw = "\n".join(lines).strip()
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                if parsed.get("summary"):
                    goal = str(parsed["summary"])[:300]
                if isinstance(parsed.get("changed_files"), list):
                    changed = [str(x) for x in parsed["changed_files"]]
                if parsed.get("test_results"):
                    test_s = parsed["test_results"]
        except Exception:
            pass

    log_entry = {
        "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "request": user_request[:300],
        "status": (parsed or {}).get("status") or ("completed" if final_answer else "incomplete"),
        "tools_used": tool_call_count,
        "goal": goal,
        "changed_files": changed,
        "test_results": test_s,
        "final_answer": (final_answer or "")[:500],
    }
    updates = {"task_log": [log_entry]}
    updates["goal"] = goal
    if changed:
        updates["completed_modules"] = changed
    if test_s:
        if isinstance(test_s, dict):
            tpass = test_s.get("passed", 0)
            tfail = test_s.get("failed", 0)
            terr = test_s.get("error")
            updates["test_summary"] = f"最近测试: passed={tpass}, failed={tfail}" + (
                f", error={terr}" if terr else ""
            )
        else:
            updates["test_summary"] = str(test_s)[:200]

    try:
        tools.memory_save({"updates": updates})
    except Exception:
        pass
