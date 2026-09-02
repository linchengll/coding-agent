# executor.py
"""工具执行器：执行单个工具调用，发射 tool_call/tool_result 事件。
含 update_plan 预览精简逻辑。"""
import json

from logger import emitter
from schemas import TOOL_FUNCTIONS


def execute_tool_call(tool_call):
    """执行单个工具调用，返回文本结果（工具统一返回 JSON 字符串）"""
    fn_name = tool_call.function.name
    try:
        fn_args = json.loads(tool_call.function.arguments or "{}")
    except json.JSONDecodeError:
        return f"[执行错误] 工具参数不是合法 JSON: {tool_call.function.arguments}"

    if fn_name not in TOOL_FUNCTIONS:
        return f"[执行错误] 未知工具: {fn_name}"

    args_preview = json.dumps(fn_args, ensure_ascii=False)[:80]
    # update_plan 的 steps 数组冗长且常被截断（desc 已在阶段横幅显示），精简为步号
    if fn_name == "update_plan":
        _steps = fn_args.get("steps") or []
        _cs = fn_args.get("current_step", 0)
        args_preview = (f"current_step={_cs}/{len(_steps)}"
                        if _steps and _cs else
                        f"steps={len(_steps)}项, current_step={_cs}")
    emitter.emit("tool_call", {
        "name": fn_name,
        "args_preview": args_preview,
    })
    try:
        result = TOOL_FUNCTIONS[fn_name](fn_args)
        # 解析统一 JSON 返回取 success/reason；失败则降级用 result 原文
        try:
            r = json.loads(result)
            success = bool(r.get("success"))
            reason = str(r.get("reason") or "")[:60]
        except Exception:
            success = False
            reason = str(result)[:60]
        emitter.emit("tool_result", {
            "success": success,
            "reason_preview": reason,
        })
        return str(result)
    except Exception as e:
        emitter.emit("tool_result", {
            "success": False,
            "reason_preview": f"{type(e).__name__}: {e}"[:60],
        })
        return f"[执行异常] {type(e).__name__}: {e}"
