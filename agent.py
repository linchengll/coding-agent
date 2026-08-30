# agent.py
"""
Agent 主循环 + LLM 调用。
职责：维护消息历史 → 调用模型 → 解析返回 → 执行工具 → 判断终止条件。
"""
import os
import json
import time
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

client = openai.OpenAI(api_key=API_KEY, base_url=BASE_URL)

# 读取系统提示词
with open(os.path.join(BASE_DIR, "system_prompt.txt"), encoding="utf-8") as f:
    SYSTEM_PROMPT = f.read()

# 导入工具注册表（后续阶段会在这里加更多工具）
import tools
TOOL_FUNCTIONS = {
    "read_file": tools.read_file,
    # 未来新增：write_file, run_command, git_commit ...
}

# 工具 Schema 定义（发送给模型做 function calling）
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取工作区内文本文件内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对于工作区的文件路径"},
                    "offset": {"type": "integer", "description": "跳过前N行，默认0"},
                    "limit": {"type": "integer", "description": "最多读取N行，默认200"},
                },
                "required": ["path"],
            },
        },
    }
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
                tool_choice="auto",   # 让模型自己决定是否调用工具
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
            # 请求格式错误，多半是 messages 结构问题
            raise SystemExit(f"请求格式错误: {e}")
    raise RuntimeError(f"LLM 调用重试 {RETRY_ATTEMPTS} 次后仍失败: {last_exc}")


def execute_tool_call(tool_call):
    """执行单个工具调用，返回文本结果"""
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
        print(f"  └─ 结果: {str(result)[:100]}{'...' if len(str(result)) > 100 else ''}")
        return str(result)
    except Exception as e:
        # 工具内部抛出未捕获异常时，也返回给模型，让模型能继续决策而不是崩溃
        return f"[执行异常] {type(e).__name__}: {e}"


def agent_loop(user_request: str) -> str:
    """Agent 主循环：返回最终回答文本"""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_request},
    ]

    tool_call_count = 0
    final_answer = ""

    for turn in range(MAX_TOTAL_TURNS):
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

        # 4. 逐个执行工具（并行执行在这里先简化为串行）
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

            # 5. 工具结果以 "tool" 角色加入历史
            #    注意：tool_call_id 必须对应 assistant 消息里给出的 id
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": tool_result,
            })

        # 6. 本轮结束，进入下一轮模型推理
        print(f"  [轮次 {turn+1} 完成，共已调用 {tool_call_count} 次工具]")
    else:
        # for 循环正常走完（没有 break），说明达到最大轮次仍未给出最终回答
        print(f"[agent] 达到最大轮次 {MAX_TOTAL_TURNS}，强制终止")
        final_answer = "（达到最大轮次，agent 未给出最终回答）"

    return final_answer


def main():
    import sys
    if len(sys.argv) < 2:
        print("用法: python agent.py \"你的需求描述\"")
        sys.exit(1)
    user_request = sys.argv[1]
    print(f"[agent] 收到需求: {user_request}\n")
    result = agent_loop(user_request)
    print("\n\n========== 最终输出 ==========")
    print(result)


if __name__ == "__main__":
    main()
