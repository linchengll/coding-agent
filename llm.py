# llm.py
"""LLM 调用：消息消毒（去除 UTF-16 代理字符）+ 流式调用 + 重试。
兼容旧 API：返回带 .content/.tool_calls 的 SimpleNamespace。"""
import time
from types import SimpleNamespace
import openai

from config import MODEL, RETRY_ATTEMPTS, client
from schemas import TOOLS


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
        except openai.InternalServerError as e:
            # 服务端错误（503 过载 / 500 / 502），指数退避重试
            wait = 2 ** attempt + 2
            print(f"[agent] 服务端错误（{getattr(e, 'status_code', '?')}），{wait}s 后重试 ({attempt+1}/{RETRY_ATTEMPTS})")
            time.sleep(wait)
            last_exc = e
        except openai.AuthenticationError:
            raise SystemExit("API Key 无效，请检查 DEEPSEEK_API_KEY 环境变量")
        except openai.BadRequestError as e:
            raise SystemExit(f"请求格式错误: {e}")
    raise RuntimeError(f"LLM 调用重试 {RETRY_ATTEMPTS} 次后仍失败: {last_exc}")
