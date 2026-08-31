# agent.py
"""
Agent 主循环 + LLM 调用（阶段3 完整工具链版）。
职责：维护消息历史 → 历史压缩 → 调用模型 → 解析返回 → 执行工具 → 判断终止条件。
启动时加载长期记忆注入 system prompt；运行中超过阈值压缩历史。
"""
import os
import json
import time
import datetime
import openai
from dotenv import load_dotenv

# 脚本所在目录，用于定位 .env / system_prompt.txt
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 启动时自动读取 .env 文件（API Key 等配置写在那里）
load_dotenv(os.path.join(BASE_DIR, ".env"))

# ─────────── 配置区 ───────────
API_KEY = os.environ["DEEPSEEK_API_KEY"]
if not API_KEY or API_KEY.startswith("sk-在此"):
    raise SystemExit("请在项目根目录的 .env 文件中填入真实的 DEEPSEEK_API_KEY")
BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
MAX_TOOL_CALLS = int(os.environ.get("MAX_TOOL_CALLS", "30"))
MAX_TOTAL_TURNS = int(os.environ.get("MAX_TOTAL_TURNS", "50"))
RETRY_ATTEMPTS = 3
COMPRESS_THRESHOLD = int(os.environ.get("COMPRESS_THRESHOLD_CHARS", "20000"))

client = openai.OpenAI(api_key=API_KEY, base_url=BASE_URL)

# 读取系统提示词
with open(os.path.join(BASE_DIR, "system_prompt.txt"), encoding="utf-8") as f:
    SYSTEM_PROMPT = f.read()

# ─────────── 工具注册表 + Schema ───────────
import tools

TOOL_FUNCTIONS = {
    # 模块1 文件读写
    "read_file": tools.read_file,
    "write_file": tools.write_file,
    "edit_file": tools.edit_file,
    "list_dir": tools.list_dir,
    # 模块3 代码检索
    "grep": tools.grep,
    "list_symbols": tools.list_symbols,
    # 模块2 Shell 沙盒
    "run_command": tools.run_command,
    # 模块4 Git
    "git_status": tools.git_status,
    "git_commit": tools.git_commit,
    "git_diff": tools.git_diff,
    "git_revert": tools.git_revert,
    # 模块5 记忆：仅系统在任务结束时统一写入，模型无权直接调用 memory_save
}


def _prop(name, desc, ptype="string", required=False, **extra):
    """构造单个参数 schema。返回 (name, prop_dict, required)。"""
    p = {"type": ptype, "description": desc}
    p.update(extra)
    return name, p, required


def _schema(name, desc, params):
    """构造工具 schema。params: [(pname, prop, required), ...]"""
    properties = {}
    required = []
    for pname, prop, req in params:
        properties[pname] = prop
        if req:
            required.append(pname)
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


_s = _prop
TOOLS = [
    _schema("read_file", "读取工作区内文本文件内容。修改文件前必须先调用。",
            [_s("path", "相对于工作区的文件路径", required=True),
             _s("offset", "跳过前N行，默认0", ptype="integer"),
             _s("limit", "最多读取N行，默认200", ptype="integer")]),
    _schema("write_file", "全量覆写或新建文件。覆盖已存在文件前必须先 read_file。必须携带 justification。",
            [_s("path", "相对于工作区的文件路径", required=True),
             _s("content", "文件完整新内容", required=True),
             _s("justification", "修改理由（必填）", required=True)]),
    _schema("edit_file", "精准替换文件内一段文本（仅替换第一处）。比 write_file 省 token。必须先 read_file。",
            [_s("path", "相对于工作区的文件路径", required=True),
             _s("old_string", "要被替换的原文（需精确匹配且唯一）", required=True),
             _s("new_string", "替换后的新文本", required=True),
             _s("justification", "修改理由（必填）", required=True)]),
    _schema("list_dir", "列出目录内容，标注文件/目录/大小。",
            [_s("path", "相对于工作区的目录路径，默认 '.'")]),
    _schema("grep", "正则搜索工作区内文件内容，返回 文件:行号:匹配行。",
            [_s("pattern", "正则表达式", required=True),
             _s("path", "搜索范围，文件或目录，默认 '.'")]),
    _schema("list_symbols", "用 AST 解析 Python 文件，返回类/函数/方法签名与行号，快速了解结构。",
            [_s("path", "相对工作区的 .py 文件路径", required=True)]),
    _schema("run_command", "在沙盒内执行命令（白名单+黑名单+超时30s）。用于运行测试/脚本。",
            [_s("command", "要执行的命令（须以白名单前缀开头，如 python/pytest/git）", required=True)]),
    _schema("git_status", "查看工作区 git 状态（变更文件列表）。", []),
    _schema("git_diff", "查看未提交的修改 diff。",
            [_s("staged", "是否查看已暂存的 diff，默认false", ptype="boolean")]),
    _schema("git_commit", "提交变更。无 diff 时拒绝。必须携带可读 message。",
            [_s("message", "提交信息，如 '修复#2：空指针'", required=True)]),
    _schema("git_revert", "回滚到上一个提交（reset --hard HEAD~1，丢弃最后一次提交的改动）。", []),
    # memory_save：方案A下不由模型直接调用，系统自动在任务结束时统一写入
]
# ─────────── 配置区结束 ───────────


def call_llm(messages):
    """调用模型，带指数退避重试"""
    last_exc = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
                temperature=0.0,
            )
            return response.choices[0].message
        except openai.RateLimitError as e:
            wait = 2 ** attempt + 1
            print(f"[agent] 触发限流，{wait}s 后重试 ({attempt+1}/{RETRY_ATTEMPTS})")
            time.sleep(wait)
            last_exc = e
        except openai.APIConnectionError as e:
            print(f"[agent] 网络错误，重试 ({attempt+1}/{RETRY_ATTEMPTS})")
            time.sleep(2 ** attempt + 1)
            last_exc = e
        except openai.AuthenticationError:
            raise SystemExit("API Key 无效，请检查 DEEPSEEK_API_KEY 环境变量")
        except openai.BadRequestError as e:
            raise SystemExit(f"请求格式错误: {e}")
    raise RuntimeError(f"LLM 调用重试 {RETRY_ATTEMPTS} 次后仍失败: {last_exc}")


def execute_tool_call(tool_call):
    """执行单个工具调用，返回文本结果（工具统一返回 JSON 字符串）"""
    fn_name = tool_call.function.name
    try:
        fn_args = json.loads(tool_call.function.arguments or "{}")
    except json.JSONDecodeError:
        return f"[执行错误] 工具参数不是合法 JSON: {tool_call.function.arguments}"

    if fn_name not in TOOL_FUNCTIONS:
        return f"[执行错误] 未知工具: {fn_name}"

    print(f"\n  ┌─ 调用工具: {fn_name}({json.dumps(fn_args, ensure_ascii=False)})")
    try:
        result = TOOL_FUNCTIONS[fn_name](fn_args)
        preview = str(result)[:120]
        print(f"  └─ 结果: {preview}{'...' if len(str(result)) > 120 else ''}")
        return str(result)
    except Exception as e:
        return f"[执行异常] {type(e).__name__}: {e}"


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


def agent_loop(user_request: str) -> str:
    """Agent 主循环：返回最终回答文本"""
    messages = [
        {"role": "system", "content": _build_system_prompt_with_memory()},
        {"role": "user", "content": user_request},
    ]

    tool_call_count = 0
    final_answer = ""

    for turn in range(MAX_TOTAL_TURNS):
        # 0. 每轮前尝试压缩历史（短期记忆管理）
        messages = _maybe_compress_history(messages)

        # 1. 调用模型
        assistant = call_llm(messages)

        # 2. 如果不返回 tool_calls，把纯文本回答加入历史并结束
        if not assistant.tool_calls:
            messages.append({
                "role": "assistant",
                "content": assistant.content or "（模型返回空内容）",
            })
            final_answer = assistant.content or ""
            print(f"\n[agent] 模型已给出最终回答，循环结束（第 {turn+1} 轮）")
            break

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
                )

            tool_result = execute_tool_call(tool_call)
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": tool_result,
            })

        print(f"  [轮次 {turn+1} 完成，共已调用 {tool_call_count} 次工具]")
    else:
        print(f"[agent] 达到最大轮次 {MAX_TOTAL_TURNS}，强制终止")
        final_answer = "（达到最大轮次，agent 未给出最终回答）"

    # 方案A：系统自动统一写入记忆（模型无权直接调用 memory_save）
    # 1. 从 final_answer JSON 抽取 goal / changed_files / test_summary
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
                # 去掉首行围栏 ```json / ```
                lines = lines[1:] if lines else []
                # 去掉末尾围栏
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
            pass    # final_answer 不是合法 JSON 就忽略

    # 2. 组装记忆更新：task_log 追加 + 标量字段覆写 + 字符串列表去重追加
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
    # goal 存最新值
    updates["goal"] = goal
    # completed_modules 从 changed_files 去重累积
    if changed:
        updates["completed_modules"] = changed
    # test_summary 存最近一次测试结果（人类可读）
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
        tools.conversation_save(messages)
    except Exception:
        pass

    return final_answer


def main():
    import sys
    if len(sys.argv) < 2:
        print('用法: python agent.py "你的需求描述"')
        sys.exit(1)
    user_request = sys.argv[1]
    print(f"[agent] 收到需求: {user_request}\n")
    result = agent_loop(user_request)
    print("\n\n========== 最终输出 ==========")
    print(result)


if __name__ == "__main__":
    main()
