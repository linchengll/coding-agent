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
import sys
from types import SimpleNamespace
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
    # 模块6 任务规划（阶段4方向1）
    "update_plan": tools.update_plan,
    # 模块7 结构化测试（阶段4方向2）
    "run_tests": tools.run_tests,
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
    # 模块6 任务规划（阶段4方向1）
    _schema("update_plan", "更新任务计划。接到需求后必须先调用以拆解子任务清单；每完成一步或换方案时再调用更新状态。整体替换式更新。",
            [_s("steps", "子任务列表，每项 {step:1起的序号, desc:描述, status:pending|doing|done|blocked}", ptype="array", required=True),
             _s("current_step", "当前进行到第几步（1起，0=未开始）", ptype="integer"),
             _s("replace", "true=整体替换(默认)，false=只更新状态", ptype="boolean")]),
    # 模块7 结构化测试（阶段4方向2）
    _schema("run_tests", "运行 pytest 并解析输出为结构化结果（失败用例清单+根因片段），比 run_command('pytest...') 更适合定位失败。",
            [_s("target", "测试目标，文件或目录，默认 'tests/'"),
             _s("args_str", "额外 pytest 参数，默认 '-v --tb=short'")]),
    # memory_save：方案A下不由模型直接调用，系统自动在任务结束时统一写入
]
# ─────────── 配置区结束 ───────────


def _sanitize_text(s):
    """清理 UTF-16 代理字符（surrogate），避免 openai 客户端 json.dumps 抛 UnicodeEncodeError。
    旧 .agent_conversation.json 可能含 GBK 解码错误产生的代理对（如 \\udcac），用 utf-8 编码再
    解码遇到代理字符会自动替换为 U+FFFD。"""
    if not isinstance(s, str):
        return s
    try:
        return s.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
    except Exception:
        return s


def _sanitize_messages_for_api(messages: list) -> list:
    """深拷贝并清理 messages 中所有字符串字段（content / tool_calls.function.arguments）。
    不修改原 messages，返回清理后的副本。"""
    cleaned = []
    for m in messages:
        cm = dict(m)    # 浅拷贝顶层
        if isinstance(cm.get("content"), str):
            cm["content"] = _sanitize_text(cm["content"])
        tcs = cm.get("tool_calls")
        if isinstance(tcs, list):
            new_tcs = []
            for tc in tcs:
                ntc = dict(tc)
                fn = dict(ntc.get("function") or {})
                if isinstance(fn.get("arguments"), str):
                    fn["arguments"] = _sanitize_text(fn["arguments"])
                ntc["function"] = fn
                new_tcs.append(ntc)
            cm["tool_calls"] = new_tcs
        cleaned.append(cm)
    return cleaned


def call_llm(messages):
    """流式调用模型（stream=True），实时打印 content，聚合 tool_calls。
    兼容旧调用点：返回的 message 对象有 .content 和 .tool_calls 属性。
    流式输出让 REPL 体验质变（边生成边显示，不再卡死等整段返回）。"""
    last_exc = None
    # 防御：清理 messages 里可能残留的 UTF-16 代理字符（旧 .agent_conversation.json
    # 含 GBK 解码错误产生的代理对，会让 openai 客户端 json.dumps 抛 UnicodeEncodeError）
    safe_messages = _sanitize_messages_for_api(messages)
    for attempt in range(RETRY_ATTEMPTS):
        try:
            stream = client.chat.completions.create(
                model=MODEL,
                messages=safe_messages,
                tools=TOOLS,
                tool_choice="auto",
                temperature=0.0,
                stream=True,
            )
            content_parts = []
            tool_calls_acc = {}     # index -> 累积中的工具调用
            started_typing = False
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                # 1. content 流式打印
                if delta.content:
                    if not started_typing:
                        print()    # 留一行做流式输出区
                        started_typing = True
                    print(delta.content, end="", flush=True)
                    content_parts.append(delta.content)
                # 2. tool_calls 分片累积（按 index 合并 name/arguments）
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index if tc.index is not None else 0
                        slot = tool_calls_acc.setdefault(
                            idx,
                            {"id": "", "type": "function",
                             "function": {"name": "", "arguments": ""}}
                        )
                        if tc.id:
                            slot["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                slot["function"]["name"] += tc.function.name
                            if tc.function.arguments:
                                slot["function"]["arguments"] += tc.function.arguments
            if started_typing:
                print()    # 流式输出结束换行
            # 组装成兼容旧 API 的 message 对象
            tcs_list = [tool_calls_acc[k] for k in sorted(tool_calls_acc.keys())]
            tool_calls_objs = None
            if tcs_list and any(t["function"]["name"] for t in tcs_list):
                tool_calls_objs = [
                    SimpleNamespace(
                        id=t["id"] or f"call_{i}",
                        type="function",
                        function=SimpleNamespace(
                            name=t["function"]["name"],
                            arguments=t["function"]["arguments"],
                        ),
                    )
                    for i, t in enumerate(tcs_list)
                ]
            return SimpleNamespace(
                content="".join(content_parts) or None,
                tool_calls=tool_calls_objs,
            )
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
    """Agent 主循环：返回最终回答文本（一次性脚本入口）
    阶段4方向4：内部逻辑抽到 _run_inner_loop / _save_memory_from_final，供 REPL 复用。
    """
    # 阶段4：新任务开始前重置计划
    tools.reset_plan()
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
            print(f"\n[agent] 模型已给出最终回答，循环结束（第 {turn+1} 轮）")
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

        print(f"  [轮次 {turn+1} 完成，共已调用 {tool_call_count} 次工具]")

    print(f"[agent] 达到最大轮次 {MAX_TOTAL_TURNS}，强制终止")
    return "（达到最大轮次，agent 未给出最终回答）", tool_call_count


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


def main():
    if len(sys.argv) < 2:
        repl_loop()
        return
    user_request = sys.argv[1]
    print(f"[agent] 收到需求: {user_request}\n")
    result = agent_loop(user_request)
    print("\n\n========== 最终输出 ==========")
    print(result)


if __name__ == "__main__":
    main()
