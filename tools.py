# tools.py
"""
工具实现：路径安全隔离 + read_file。
所有文件工具都共用 _resolve_workspace_path，这是安全基石。
"""
import os

WORKSPACE = os.environ.get("WORKSPACE", os.path.join(os.getcwd(), "workspace"))
# 规范化路径，防止符号链接 / 相对路径导致越界判断失败
WORKSPACE = os.path.realpath(WORKSPACE)
os.makedirs(WORKSPACE, exist_ok=True)

# 输出截断上限
MAX_OUTPUT_CHARS = 3000


def _resolve_workspace_path(relative_path: str) -> str:
    """将相对路径解析为工作区内的绝对路径，并阻止越界"""
    # os.path.realpath 会解析所有符号链接和 .. 段，防止目录穿越
    abs_path = os.path.realpath(os.path.join(WORKSPACE, relative_path))
    # 检查是否仍然在工作区内
    if not (abs_path == WORKSPACE or abs_path.startswith(WORKSPACE + os.sep)):
        raise PermissionError(
            f"越界访问被拒绝: {relative_path} -> {abs_path}，工作区: {WORKSPACE}"
        )
    return abs_path


def _truncate(text: str, max_len: int = MAX_OUTPUT_CHARS) -> str:
    """超长输出截断，防止上下文爆炸"""
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"\n... [输出已截断，共 {len(text)} 字符]"


def read_file(args: dict) -> str:
    """
    读取文件内容，返回纯文本。
    支持 offset（跳过前N行）和 limit（最多读取N行）。
    """
    path = args["path"]
    offset = int(args.get("offset", 0))
    limit = int(args.get("limit", 200))

    abs_path = _resolve_workspace_path(path)

    if not os.path.exists(abs_path):
        return f"[read_file 错误] 文件不存在: {path}"
    if os.path.isdir(abs_path):
        return f"[read_file 错误] 路径是目录，不是文件: {path}"
    if not os.path.isfile(abs_path):
        return f"[read_file 错误] 不是常规文件: {path}"

    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        total = len(lines)
        selected = lines[offset:offset + limit]
        content = "".join(selected)
        # 加上文件元信息
        result = f"文件: {path} | 总行数: {total} | 显示行 {offset+1}-{offset+len(selected)}\n"
        result += "---文件内容如下---\n"
        result += content
        result += "---文件内容结束---\n"
        return _truncate(result)
    except Exception as e:
        return f"[read_file 错误] 读取失败: {type(e).__name__}: {e}"
