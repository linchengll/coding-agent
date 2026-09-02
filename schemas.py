# schemas.py
"""工具 schema 与注册表：定义 TOOLS 列表与 TOOL_FUNCTIONS 映射。
仅依赖 tools 模块，不依赖其他业务模块。"""
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
