# tools.py
"""
阶段3 完整工具链：文件读写 / Shell沙盒 / 代码检索 / Git / 记忆。
所有工具返回统一 JSON 字符串：
  {"success": bool, "reason": str, "result": str, "context_hint": str}
安全基石：_resolve_workspace_path 路径锁定 + _is_forbidden_path 禁止目录。
"""
import os
import re
import json
import ast
import io
import shlex
import locale
import subprocess

# ─────────── 全局配置 ───────────
WORKSPACE = os.environ.get("WORKSPACE", os.path.join(os.getcwd(), "workspace"))
WORKSPACE = os.path.realpath(WORKSPACE)          # 规范化，防符号链接/相对路径绕过
os.makedirs(WORKSPACE, exist_ok=True)

MAX_OUTPUT_CHARS = 3000          # 单次工具输出截断上限
CMD_TIMEOUT = int(os.environ.get("CMD_TIMEOUT", "30"))   # 单命令超时
COMPRESS_THRESHOLD = int(os.environ.get("COMPRESS_THRESHOLD_CHARS", "20000"))

# 禁止写入的目录（防止损坏元数据 / 第三方依赖）
FORBIDDEN_DIRS = (".git", "node_modules", "__pycache__")

# 二进制文件后缀：禁止文本读写，命令模型改用 shell 处理
BINARY_EXT = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".pdf",
              ".zip", ".gz", ".tar", ".7z", ".rar",
              ".pyc", ".pyo",
              ".exe", ".dll", ".so", ".dylib",
              ".class", ".jar", ".war", ".ear",
              ".o", ".obj", ".a", ".lib", ".elf",
              ".wasm", ".bin", ".dat",
              ".mp3", ".mp4", ".avi", ".mov", ".wav", ".flac",
              ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
              ".psd", ".ai", ".svgz",)

# ─────────── 危险命令黑名单（正则）───────────
DANGEROUS_PATTERNS = [
    r"\brm\s+-rf\b", r"\brm\s+/\b", r"\brm\s+-rf\s+/",
    r"\bsudo\b", r"\bmkfs\b", r"\bdd\b\s+if=",
    r"\bshutdown\b", r"\breboot\b", r"\bhalt\b",
    r":\s*\(\)\s*\{",                       # fork bomb :(){:|:&};:
    r"\bcurl\b[^\|]*\|\s*(sh|bash)\b",      # curl ... | sh
    r"\bwget\b[^\|]*\|\s*(sh|bash)\b",
    r"--no-preserve-root", r"\b>\s*/dev/sd",
    r"\bmkfs\.", r"\bshred\b",
]

# 命令白名单前缀（只允许这些可执行文件启动）
# Python：python/pytest/pip
# 版本控制：git
# 基础文件工具：ls/dir/cat/type/echo/find/grep/rg/mkdir/touch/test
# 前端/Node：node/npm/npx
# Go / Rust：go/cargo/rustc
# Java：javac(编译)/java(运行)/jar(打包)；构建工具 maven(mvn)/gradle
# C / C++：gcc/g++/clang/clang++/make/cmake
# C# / dotnet（可选，常用）：dotnet/csc
# Windows 上常见的是带 .exe 后缀，_check_command_safety 已把 .exe/.bat 后缀剥掉再比对
COMMAND_WHITELIST = {
    "python", "python3", "py", "pytest", "py.test",
    "pip", "pip3",
    "git",
    "ls", "dir", "cat", "type", "echo", "find", "grep", "rg",
    "node", "npm", "npx",
    "go", "cargo", "rustc",
    "mkdir", "touch", "test",
    # Java
    "javac", "java", "jar", "mvn", "gradle", "gradlew", "javaw",
    # C / C++
    "gcc", "g++", "clang", "clang++", "cc", "c++", "make", "cmake", "mingw32-make",
    "ld", "ar", "strip", "objdump",
    # .NET / C#（便于扩展，用户若不用可忽略）
    "dotnet", "csc",
}

# ─────────── 会话级状态 ───────────
_READ_FILES = set()        # 已 read 过的相对路径（write/edit 前置校验用）
_FILE_VERSIONS = {}        # 相对路径 -> 版本号(int)，写一次自增
MEMORY_FILE = ".agent_memory.json"
CONVERSATION_FILE = ".agent_conversation.json"

# 任务计划状态（会话级，update_plan 工具读写，agent_loop 注入到 messages）
_PLAN_STEPS = []           # [{step:int, desc:str, status:str}]  status: pending/doing/done/blocked
_PLAN_CURRENT = 0          # 当前进行到第几步（1-based，0=未开始）


# ════════════ 基础工具函数 ════════════

def _resolve_workspace_path(relative_path: str) -> str:
    """相对路径 -> 工作区内绝对路径，阻止越界"""
    abs_path = os.path.realpath(os.path.join(WORKSPACE, relative_path))
    if not (abs_path == WORKSPACE or abs_path.startswith(WORKSPACE + os.sep)):
        raise PermissionError(
            f"越界访问被拒绝: {relative_path} -> {abs_path}，工作区: {WORKSPACE}"
        )
    return abs_path


def _is_forbidden_path(relative_path: str) -> bool:
    """是否落在禁止写入目录内（.git/ node_modules/ __pycache__/）"""
    norm = relative_path.replace("\\", "/").lstrip("./")
    for fb in FORBIDDEN_DIRS:
        if norm == fb or norm.startswith(fb + "/"):
            return True
    return False


def _truncate(text: str, max_len: int = MAX_OUTPUT_CHARS) -> str:
    """超长输出尾部截断"""
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"\n... [输出已截断，共 {len(text)} 字符]"


def _smart_truncate(text: str, head: int = 600, tail: int = 600) -> str:
    """智能截断：保留头部+尾部（报错常在末尾），中间省略"""
    if len(text) <= head + tail + 50:
        return _truncate(text)
    return (text[:head] + f"\n... [中间省略 {len(text) - head - tail} 字符] ...\n"
            + text[-tail:])


def _ok(result, reason: str = "", context_hint: str = "") -> str:
    """统一成功返回"""
    return json.dumps({
        "success": True,
        "reason": reason,
        "result": result,
        "context_hint": context_hint,
    }, ensure_ascii=False)


def _err(reason: str, context_hint: str = "") -> str:
    """统一失败返回"""
    return json.dumps({
        "success": False,
        "reason": reason,
        "result": "",
        "context_hint": context_hint,
    }, ensure_ascii=False)


def _is_binary(path: str) -> bool:
    return path.lower().endswith(BINARY_EXT)


def _bump_version(path: str) -> int:
    _FILE_VERSIONS[path] = _FILE_VERSIONS.get(path, 0) + 1
    return _FILE_VERSIONS[path]


def _get_version(path: str) -> int:
    return _FILE_VERSIONS.get(path, 0)


# ════════════ 模块1：文件读写工具 ════════════

def read_file(args: dict) -> str:
    """读取文件内容（统一JSON返回，记录已读+版本号）"""
    path = args["path"]
    offset = int(args.get("offset", 0))
    limit = int(args.get("limit", 200))

    try:
        abs_path = _resolve_workspace_path(path)
    except PermissionError as e:
        return _err(str(e))

    if _is_binary(path):
        return _err(f"二进制文件，请改用 run_command 处理: {path}",
                    context_hint="二进制文件不能用文本工具读写")

    if not os.path.exists(abs_path):
        return _err(f"文件不存在: {path}")
    if os.path.isdir(abs_path):
        return _err(f"路径是目录，请用 list_dir: {path}")

    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        total = len(lines)
        selected = lines[offset:offset + limit]
        content = "".join(selected)
        _READ_FILES.add(path.replace("\\", "/"))      # 记录已读，供 write/edit 前置校验
        ver = _get_version(path)
        header = (f"文件: {path} | 总行数: {total} | 显示行 {offset+1}-{offset+len(selected)}"
                  + (f" | 版本v{ver}" if ver else " | 原始版本"))
        body = header + "\n---文件内容如下---\n" + content + "---文件内容结束---\n"
        return _ok(_truncate(body), reason="成功读取文件",
                   context_hint="修改前请先 read_file 确认上下文")
    except Exception as e:
        return _err(f"读取失败: {type(e).__name__}: {e}")


def write_file(args: dict) -> str:
    """全量覆写/新建文件。要求 justification；已存在文件须先 read_file。"""
    path = args["path"]
    content = args["content"]
    justification = args.get("justification", "")

    if not justification:
        return _err("缺少 justification 参数，必须说明修改理由",
                    context_hint="write_file 需携带 justification 字段")
    if _is_forbidden_path(path):
        return _err(f"禁止写入受保护目录: {path}")
    if _is_binary(path):
        return _err(f"二进制文件，请改用 run_command 处理: {path}")

    try:
        abs_path = _resolve_workspace_path(path)
    except PermissionError as e:
        return _err(str(e))

    exists = os.path.exists(abs_path)
    norm = path.replace("\\", "/")
    if exists and norm not in _READ_FILES:
        return _err(f"覆盖已存在文件前必须先 read_file 确认上下文: {path}",
                    context_hint="先调用 read_file 读取该文件，再 write_file")

    try:
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)
        ver = _bump_version(path)
        _READ_FILES.discard(norm)     # 写后旧版本失效，须重新 read
        return _ok(f"已写入 {len(content)} 字符，v{ver}",
                   reason=f"{'新建' if not exists else '覆写'}文件成功：{justification}",
                   context_hint=f"文件已更新到 v{ver}，模型缓存的旧内容已过期，需重新 read_file")
    except Exception as e:
        return _err(f"写入失败: {type(e).__name__}: {e}")


def edit_file(args: dict) -> str:
    """精准替换：把文件中的 old_string 替换为 new_string（仅替换第一处）。
    比 write_file 省 token、风险更可控。要求 justification + 先 read_file。"""
    path = args["path"]
    old_string = args["old_string"]
    new_string = args["new_string"]
    justification = args.get("justification", "")

    if not justification:
        return _err("缺少 justification 参数")
    if _is_forbidden_path(path):
        return _err(f"禁止写入受保护目录: {path}")

    try:
        abs_path = _resolve_workspace_path(path)
    except PermissionError as e:
        return _err(str(e))

    norm = path.replace("\\", "/")
    if norm not in _READ_FILES:
        return _err(f"编辑前必须先 read_file: {path}",
                    context_hint="先 read_file 再 edit_file，确保 old_string 精准匹配")

    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        count = content.count(old_string)
        if count == 0:
            return _err("old_string 在文件中未找到，可能内容已变化",
                        context_hint="请重新 read_file 获取最新内容后再 edit_file")
        if count > 1:
            return _err(f"old_string 匹配到 {count} 处，请提供更长的唯一上下文",
                        context_hint="edit_file 只替换第一处，多处匹配会报错以保安全")
        new_content = content.replace(old_string, new_string, 1)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        ver = _bump_version(path)
        _READ_FILES.discard(norm)
        return _ok(f"已替换，v{ver}",
                   reason=f"编辑成功：{justification}",
                   context_hint=f"文件已更新到 v{ver}，请重新 read_file 查看结果")
    except Exception as e:
        return _err(f"编辑失败: {type(e).__name__}: {e}")


def list_dir(args: dict) -> str:
    """列出目录内容，标注文件/目录/大小"""
    path = args.get("path", ".")
    try:
        abs_path = _resolve_workspace_path(path)
    except PermissionError as e:
        return _err(str(e))
    if not os.path.exists(abs_path):
        return _err(f"路径不存在: {path}")
    if not os.path.isdir(abs_path):
        return _err(f"不是目录: {path}")

    try:
        entries = sorted(os.listdir(abs_path), key=lambda x: (not os.path.isdir(os.path.join(abs_path, x)), x.lower()))
        lines = [f"目录: {path} | 共 {len(entries)} 项"]
        for name in entries:
            full = os.path.join(abs_path, name)
            tag = "[DIR] " if os.path.isdir(full) else "      "
            size = os.path.getsize(full) if os.path.isfile(full) else 0
            lines.append(f"{tag}{name}" + (f"  ({size}B)" if os.path.isfile(full) else ""))
        return _ok(_truncate("\n".join(lines)), reason="列出目录成功")
    except Exception as e:
        return _err(f"列目录失败: {type(e).__name__}: {e}")


# ════════════ 模块3：代码检索工具 ════════════

def grep(args: dict) -> str:
    """正则搜索工作区内文件内容，返回 文件:行号:匹配行"""
    pattern = args["pattern"]
    path = args.get("path", ".")
    try:
        regex = re.compile(pattern)
    except re.error as e:
        return _err(f"非法正则: {e}")

    try:
        base = _resolve_workspace_path(path)
    except PermissionError as e:
        return _err(str(e))
    if not os.path.exists(base):
        return _err(f"路径不存在: {path}")

    matches = []
    files_scanned = 0
    try:
        if os.path.isfile(base):
            targets = [base]
        else:
            targets = []
            for root, dirs, files in os.walk(base):
                dirs[:] = [d for d in dirs if d not in FORBIDDEN_DIRS]
                for fn in files:
                    if not fn.endswith(BINARY_EXT):
                        targets.append(os.path.join(root, fn))
        for fp in targets:
            files_scanned += 1
            rel = os.path.relpath(fp, WORKSPACE).replace("\\", "/")
            try:
                with open(fp, "r", encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f, 1):
                        if regex.search(line):
                            matches.append(f"{rel}:{i}: {line.rstrip()}")
                            if len(matches) >= 50:
                                break
            except Exception:
                continue
            if len(matches) >= 50:
                break
        if not matches:
            return _ok("无匹配", reason=f"扫描 {files_scanned} 个文件，未找到匹配",
                       context_hint="可放宽正则或换路径")
        body = f"扫描 {files_scanned} 文件，命中 {len(matches)} 行:\n" + "\n".join(matches)
        return _ok(_truncate(body), reason="grep 完成")
    except Exception as e:
        return _err(f"grep 失败: {type(e).__name__}: {e}")


def list_symbols(args: dict) -> str:
    """用 AST 解析 Python 文件，返回类/函数/方法签名，帮助快速了解结构"""
    path = args["path"]
    try:
        abs_path = _resolve_workspace_path(path)
    except PermissionError as e:
        return _err(str(e))
    if not os.path.exists(abs_path):
        return _err(f"文件不存在: {path}")
    if os.path.isdir(abs_path):
        return _err(f"是目录，请指定文件: {path}")
    if not path.lower().endswith(".py"):
        return _err("list_symbols 仅支持 .py 文件（非 Python 文件可用 grep）")

    try:
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            source = f.read()
        tree = ast.parse(source)
    except SyntaxError as e:
        return _err(f"语法错误无法解析: {e}")
    except Exception as e:
        return _err(f"解析失败: {type(e).__name__}: {e}")

    lines = [f"文件: {path} 符号表"]
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args_list = [a.arg for a in node.args.args]
            lines.append(f"  def {node.name}({', '.join(args_list)})  L{node.lineno}")
        elif isinstance(node, ast.ClassDef):
            lines.append(f"  class {node.name}  L{node.lineno}")
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    a2 = [a.arg for a in item.args.args]
                    lines.append(f"    def {item.name}({', '.join(a2)})  L{item.lineno}")
    if len(lines) == 1:
        lines.append("  (无类/函数定义)")
    return _ok(_truncate("\n".join(lines)), reason="符号表解析完成",
               context_hint="需要看实现细节时再 read_file 对应行")


# ════════════ 模块2：Shell 沙盒执行 ════════════

def _normalize_first_token(first: str) -> str:
    """把首个命令 token 规范化用于白名单比对。
    处理：去掉 Windows/Unix 路径前缀（./ ../ .\\ subdir/）+ 去掉 .exe/.bat/.cmd/.com/.ps1 扩展名。
    例：'c_demo/main.exe' -> 'main'；'.\\run.bat' -> 'run'；'../foo.exe' -> 'foo'；'gcc' -> 'gcc'
    同时如果首 token 看起来是纯路径（含 / 或 \\）且规范化后不匹配白名单，则允许（Agent 在工作区内编译出的
    自定义可执行文件，文件名本身不在白名单里；路径锁定 cwd=WORKSPACE 已保证其位于工作区）。"""
    if not first:
        return ""
    # 去掉路径前缀：最后一段文件名
    sep = "/" if "/" in first else "\\" if "\\" in first else None
    if sep:
        basename = first.rsplit(sep, 1)[-1]
    else:
        basename = first
    # 去掉 Windows 脚本后缀
    for suf in (".exe", ".bat", ".cmd", ".com", ".ps1"):
        if basename.lower().endswith(suf):
            basename = basename[:-len(suf)]
            break
    return basename


def _looks_like_workspace_executable(first: str) -> bool:
    """首 token 是否像"工作区内的编译产物路径"：包含路径分隔符，或扩展名是 .exe/.bat/.cmd/.out。
    这类路径已由 cwd=WORKSPACE 限制在工作区内，允许执行（相当于 Agent 自己生成的程序）。"""
    if not first:
        return False
    low = first.lower()
    has_sep = ("/" in first) or ("\\" in first) or first.startswith(".")
    is_exe_ext = low.endswith((".exe", ".bat", ".cmd", ".com", ".ps1", ".out"))
    return has_sep or is_exe_ext


def _check_command_safety(command: str) -> str | None:
    """返回拒绝原因字符串；通过则返回 None"""
    for pat in DANGEROUS_PATTERNS:
        if re.search(pat, command):
            return f"命中危险命令黑名单: {pat}"
    try:
        parts = shlex.split(command, posix=(os.name != "nt"))
    except ValueError:
        parts = command.split()
    first = parts[0] if parts else ""

    # 路径规范：先对白名单友好的"去路径+去后缀"形式比对
    normalized = _normalize_first_token(first)
    if normalized and normalized in COMMAND_WHITELIST:
        return None

    # Windows: 保留 .exe 后缀再比一次（规范化已截掉）
    first_no_ext = first
    for suf in (".exe", ".bat", ".cmd", ".com", ".ps1"):
        if first_no_ext.lower().endswith(suf):
            first_no_ext = first_no_ext[:-len(suf)]
    if first_no_ext in COMMAND_WHITELIST:
        return None

    # 白名单之外：如果是工作区内的自定义可执行文件（Agent 自编译产物），放行
    if _looks_like_workspace_executable(first):
        return None

    base = normalized or first_no_ext or first
    return (f"命令不在白名单: '{first}'（允许: "
            f"{', '.join(sorted(COMMAND_WHITELIST))}"
            f"；另：工作区内编译产物路径如 subdir/prog[.exe] 可直接执行）")


def _build_safe_env() -> dict:
    """构造子进程环境，剔除凭据类变量"""
    env = os.environ.copy()
    for k in list(env.keys()):
        if any(s in k.upper() for s in ("API_KEY", "TOKEN", "SECRET", "PASSWORD")):
            env.pop(k, None)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _split_command_segments(command: str) -> list:
    """按 shell 命令连接符 && / || / & | ; 拆分，返回 [(seg, sep_after), ...]。"""
    seps = ["&&", "||", "&", "|", ";"]
    segments = []
    i = 0
    buf = []
    n = len(command)
    while i < n:
        ch = command[i]
        two = command[i:i+2]
        matched = False
        for s in seps:
            if command.startswith(s, i):
                seg = "".join(buf); buf.clear()
                segments.append((seg, s))
                i += len(s)
                matched = True
                break
        if not matched:
            buf.append(ch)
            i += 1
    last = "".join(buf)
    if last or segments:
        segments.append((last, ""))
    return segments


def _first_token_range(segment: str) -> tuple[int, int]:
    """找到 segment 首 token 在原始字符串中的起止位置（能精确还原剩下部分，避免扩展名被改变）。
    策略：逐字符扫描，支持带单引号/双引号包裹的 token，能识别终止空白字符。"""
    if not segment:
        return 0, 0
    n = len(segment)
    i = 0
    # 跳过前导空白（一般不会有，但防御）
    while i < n and segment[i] in " \t":
        i += 1
    start = i
    in_s = None     # 引号状态: "'" / '"' / None
    while i < n:
        ch = segment[i]
        if in_s:
            if ch == in_s:
                in_s = None
            i += 1
            continue
        if ch in ("'", '"'):
            in_s = ch
            i += 1
            continue
        if ch in " \t":   # 空白终止
            break
        i += 1
    return start, i


def _fix_one_segment_exec(segment: str) -> str:
    """对单段命令（不含连接符）做 Windows 路径规范化：首 token '/'->'\\' + '.\\' 前缀。"""
    if not segment.strip():
        return segment
    start, end = _first_token_range(segment)
    if start == end:
        return segment
    first = segment[start:end]
    looks = _looks_like_workspace_executable(first)
    has_sep = ("/" in first) or ("\\" in first)
    if not looks and not has_sep:
        return segment
    fixed = first.replace("/", "\\")
    if (len(fixed) >= 2 and (fixed[1] != ":")       # 非盘符开头
        and not fixed.startswith("\\")
        and not fixed.startswith(".")):
        fixed = ".\\" + fixed
    # 精确位置替换：保留 segment 其余部分一字不差（扩展名绝不会多复制字符）
    return segment[:start] + fixed + segment[end:]


def _windows_fix_exec_path(command: str) -> str:
    """Windows cmd.exe 不把 '/' 识别为路径分隔符。
    对命令按 &&/||/&/|/; 分段，每一段的首 token 独立做路径规范化。
    这样 `g++ a.cpp -o prog.exe && prog.exe arg1` 两段都会被修复。"""
    if os.name != "nt":
        return command
    segs = _split_command_segments(command)
    rebuilt = []
    for seg, sep in segs:
        rebuilt.append(_fix_one_segment_exec(seg))
        rebuilt.append(sep)
    return "".join(rebuilt)


def _decode_bytes(raw: bytes) -> str:
    """把子进程字节输出解码为字符串：优先系统本地编码→UTF-8→兜底 replace。
    解决 Windows cmd 中文 GBK 被强制按 UTF-8 解码的乱码问题。"""
    if raw is None:
        return ""
    candidates = []
    # 1) 系统区域设置的默认编码（Windows 中文=GBK/CP936）
    try:
        pref = locale.getpreferredencoding(False)
        if pref:
            candidates.append(pref)
    except Exception:
        pass
    # 2) 常见 Windows OEM / ANSI 编码
    for cp in ("cp936", "gbk", "gb18030", "big5", "cp950", "shift_jis", "euc-kr"):
        if cp not in candidates:
            candidates.append(cp)
    # 3) UTF-8 保底
    if "utf-8" not in candidates:
        candidates.append("utf-8")
    last_exc = None
    for enc in candidates:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError as e:
            last_exc = e
            continue
        except LookupError:
            continue
    # 都失败：按 UTF-8 replace 兜底（绝不会崩）
    try:
        return raw.decode("utf-8", errors="replace")
    except Exception as e:
        return f"<decode failed: {e}>"


def _build_safe_env_utf8() -> dict:
    """构造子进程环境，并优先强制控制台输出 UTF-8，减少中文乱码概率。"""
    env = _build_safe_env()
    # 让 Java/Python/Go 等应用输出 UTF-8（这些语言会尊重 PYTHONIOENCODING/SetConsoleCP）
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("JAVA_TOOL_OPTIONS", "-Dfile.encoding=UTF-8 -Dstdout.encoding=UTF-8 -Dstderr.encoding=UTF-8")
    env.setdefault("LANG", "C.UTF-8")
    return env


def _chcp_utf8_prefix() -> str:
    """Windows 下命令前置：切控制台输出代码页 65001(UTF-8)，丢弃 chcp 自身的输出。"""
    if os.name != "nt":
        return ""
    # cmd.exe：`>nul` 重定向 stdout，`2>nul` 重定向 stderr，不污染命令真实输出
    return "chcp 65001 >nul 2>nul && "


def _check_executable_in_workspace(command: str) -> str | None:
    """若命令首 token 是一个路径形式，解析其相对 WORKSPACE 的绝对位置；
    若越出 WORKSPACE，则返回拒绝原因。（防范 ../outside.exe）"""
    try:
        parts = shlex.split(command, posix=False)
    except ValueError:
        parts = command.split()
    if not parts:
        return None
    first = parts[0].replace("/", "\\" if os.name == "nt" else "/")
    if not _looks_like_workspace_executable(parts[0]):
        return None   # 非路径形式（如 gcc/java），跳过
    try:
        abs_path = os.path.realpath(os.path.join(WORKSPACE, first))
    except Exception:
        return "路径解析异常，拒绝执行"
    inside = (abs_path == WORKSPACE) or abs_path.startswith(WORKSPACE + os.sep)
    if not inside:
        return f"路径越界：编译产物必须位于工作区内，禁止访问 {first}"
    return None


def run_command(args: dict) -> str:
    """在沙盒内执行命令：白名单+黑名单+超时+智能截断+统一JSON。
    额外安全：路径形式的编译产物会先做 WORKSPACE 边界判定，越界直接拒绝。
    编码：Windows 下先切控制台代码页 UTF-8；子进程输出按本地编码解码，避免中文乱码。"""
    command = args["command"]

    reject = _check_command_safety(command)
    if reject:
        return _err(reject, context_hint="换用白名单内命令，或拆解后重试")
    # 第二层：路径形式编译产物的越界拦截（按 &&/||/; 拆分的每段都检查）
    for seg, _sep in _split_command_segments(command):
        reject2 = _check_executable_in_workspace(seg.strip())
        if reject2:
            return _err(reject2, context_hint="请改为工作区内的路径，不要用 ../ 越界")
    # Windows：每段命令 '/' -> '\\' 规范化 + 前缀 chcp 65001 切 UTF-8
    fixed_cmd = _chcp_utf8_prefix() + _windows_fix_exec_path(command)

    try:
        proc = subprocess.run(
            fixed_cmd,
            shell=True,
            cwd=WORKSPACE,
            timeout=CMD_TIMEOUT,
            capture_output=True,
            # 不指定 text/encoding：拿到 raw bytes，交给 _decode_bytes 多编码尝试
            env=_build_safe_env_utf8(),
        )
        stdout = _smart_truncate(_decode_bytes(proc.stdout) or "")
        stderr = _smart_truncate(_decode_bytes(proc.stderr) or "")
        result = json.dumps({
            "exit_code": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }, ensure_ascii=False)
        success = proc.returncode == 0
        reason = "命令执行成功" if success else f"命令退出码 {proc.returncode}"
        hint = "" if success else "命令失败，请阅读 stderr 分析根因后修改代码重试"
        return _ok(result, reason=reason, context_hint=hint)
    except subprocess.TimeoutExpired:
        return _err(f"命令超时（>{CMD_TIMEOUT}s），已强制终止",
                    context_hint="超时说明命令卡死或死循环，必须换方案，不要重试相同命令")
    except Exception as e:
        return _err(f"执行异常: {type(e).__name__}: {e}")


# ════════════ 模块4：Git 版本控制 ════════════

def _git(args_list: list, timeout: int = 15) -> dict:
    """执行 git 子命令的内部封装，返回 {ok, stdout, stderr}"""
    try:
        proc = subprocess.run(
            ["git"] + args_list,
            cwd=WORKSPACE,
            timeout=timeout,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_build_safe_env(),
        )
        return {"ok": proc.returncode == 0,
                "stdout": proc.stdout or "",
                "stderr": proc.stderr or "",
                "code": proc.returncode}
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "", "stderr": "git 命令超时", "code": -1}
    except FileNotFoundError:
        return {"ok": False, "stdout": "", "stderr": "未安装 git", "code": -1}
    except Exception as e:
        return {"ok": False, "stdout": "", "stderr": f"{type(e).__name__}: {e}", "code": -1}


def git_status(args: dict) -> str:
    r = _git(["status", "--short", "--branch"])
    if not r["ok"]:
        return _err(f"git status 失败: {r['stderr']}",
                    context_hint="若工作区不是 git 仓库，请先 run_command 'git init'")
    out = r["stdout"].strip() or "(工作区干净，无变更)"
    return _ok(_truncate(out), reason="工作区状态", context_hint="有 M/D/?? 标记的文件是变更项")


def git_diff(args: dict) -> str:
    staged = bool(args.get("staged", False))
    flag = ["--cached"] if staged else []
    r = _git(["diff", "--stat"] + flag)
    if not r["ok"]:
        return _err(f"git diff 失败: {r['stderr']}")
    stat = r["stdout"].strip() or "(无差异)"
    r2 = _git(["diff"] + flag)
    full = r2["stdout"].strip()
    body = "=== diff 统计 ===\n" + stat + "\n\n=== 完整 diff ===\n" + full
    return _ok(_truncate(body), reason="未提交修改", context_hint="commit 前可查看 diff")


def git_commit(args: dict) -> str:
    """提交变更。无 diff 时拒绝；要求 message。"""
    message = args.get("message", "")
    if not message:
        return _err("缺少 message 参数", context_hint="commit 必须携带可读信息，如 '修复#2：空指针'")
    # 检查是否有变更
    r = _git(["status", "--short"])
    if r["ok"] and not r["stdout"].strip():
        return _err("工作区无变更，禁止空提交", context_hint="先修改文件产生 diff，再 commit")
    _git(["add", "-A"])
    r = _git(["commit", "-m", message])
    if not r["ok"]:
        return _err(f"git commit 失败: {r['stderr']}",
                    context_hint="可能需要配置 user.name/user.email，或无变更")
    # 取短 hash
    rh = _git(["rev-parse", "--short", "HEAD"])
    short = rh["stdout"].strip() if rh["ok"] else "?"
    return _ok(f"提交 {short}: {message}", reason="提交成功",
               context_hint="可继续修改，或用 git_diff 查看后续变更")


def git_revert(args: dict) -> str:
    """回滚到上一版本（reset --hard HEAD~1，会丢弃最后一次提交的改动）"""
    # 安全检查：确认有可回退的提交
    rh = _git(["rev-parse", "--short", "HEAD~1"])
    if not rh["ok"]:
        return _err("没有可回退的历史提交", context_hint="已是初始提交，无法再回退")
    r = _git(["reset", "--hard", "HEAD~1"])
    if not r["ok"]:
        return _err(f"git reset 失败: {r['stderr']}")
    _READ_FILES.clear()     # 所有缓存内容失效
    rh2 = _git(["rev-parse", "--short", "HEAD"])
    cur = rh2["stdout"].strip() if rh2["ok"] else "?"
    return _ok(f"已回退到 {cur}", reason="回滚成功（丢弃最后一次提交）",
               context_hint="工作区文件已变，须重新 read_file")


# ════════════ 模块5：记忆模块 ════════════

def memory_load() -> dict:
    """加载长期记忆（启动时注入 system prompt）"""
    path = os.path.join(WORKSPACE, MEMORY_FILE)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def memory_save(args: dict) -> str:
    """更新长期记忆。标量字段覆写；列表字段追加（字符串去重、dict 直连追加）。
    这样 task_log 会累积全部历史，goal/status 等保留最新值。"""
    updates = args.get("updates") if isinstance(args, dict) else args
    if not isinstance(updates, dict) or not updates:
        return _err("updates 必须是字段字典", context_hint="如 {goal, completed_modules, known_pitfalls, task_log}")
    data = memory_load()
    for k, v in updates.items():
        if k in data and isinstance(data[k], list) and isinstance(v, list):
            if all(isinstance(x, str) for x in v):
                for x in v:                       # 字符串列表去重追加
                    if x not in data[k]:
                        data[k].append(x)
            else:
                data[k].extend(v)                # task_log（dict 项）直接追加
        else:
            data[k] = v                           # 标量覆写
    path = os.path.join(WORKSPACE, MEMORY_FILE)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return _ok(f"已更新 {len(updates)} 个字段：{list(updates.keys())}",
                   reason="长期记忆已更新（列表追加/标量覆写）",
                   context_hint="下次启动 Agent 会自动读取这些记忆")
    except Exception as e:
        return _err(f"记忆写入失败: {type(e).__name__}: {e}")


def conversation_save(messages: list) -> str:
    """持久化上一轮完整对话历史到 .agent_conversation.json，供下次会话注入"""
    path = os.path.join(WORKSPACE, CONVERSATION_FILE)
    try:
        slim = []
        for m in messages:
            slim.append({
                "role": m.get("role"),
                "content": m.get("content"),
                "tool_calls": m.get("tool_calls"),
                "tool_call_id": m.get("tool_call_id"),
            })
        with open(path, "w", encoding="utf-8") as f:
            json.dump(slim, f, ensure_ascii=False, indent=2)
        return _ok(f"已保存 {len(slim)} 条消息", reason="对话历史已持久化")
    except Exception as e:
        return _err(f"对话保存失败: {type(e).__name__}: {e}")


def conversation_load() -> list:
    """加载上一轮对话历史；不存在/损坏返回空列表"""
    path = os.path.join(WORKSPACE, CONVERSATION_FILE)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


# ════════════ 模块6：任务规划（阶段4方向1）════════════

def update_plan(args: dict) -> str:
    """更新任务计划。Agent 接到需求后必须先调用此工具拆解子任务清单。
    每完成一步或换方案时再调用，更新步骤状态。agent_loop 每轮把计划注入上下文。
    参数：
      steps: [{step:int(从1起), desc:str, status:"pending"|"doing"|"done"|"blocked"}, ...]
             （整体替换式更新；不传则只更新 current_step）
      current_step: int（可选，当前进行到第几步；0=未开始）
      replace: bool（可选，默认true=整体替换；false=只更新状态）
    """
    global _PLAN_STEPS, _PLAN_CURRENT
    steps_in = args.get("steps")
    current = args.get("current_step")
    replace = args.get("replace", True)

    if steps_in is not None:
        if not isinstance(steps_in, list):
            return _err("steps 必须是数组")
        # 校验+规范化
        new_steps = []
        for i, s in enumerate(steps_in, 1):
            if not isinstance(s, dict):
                return _err(f"steps[{i}] 不是对象")
            new_steps.append({
                "step": int(s.get("step", i)),
                "desc": str(s.get("desc", ""))[:200],
                "status": s.get("status", "pending") if s.get("status") in
                          ("pending", "doing", "done", "blocked") else "pending",
            })
        if replace:
            _PLAN_STEPS = new_steps
        else:
            # 合并：按 step 编号对齐，只更新 status
            for ns in new_steps:
                for ps in _PLAN_STEPS:
                    if ps["step"] == ns["step"]:
                        ps["status"] = ns["status"]
                        break
    if current is not None:
        _PLAN_CURRENT = int(current)

    # 返回当前完整计划+进度
    summary = get_plan_summary()
    return _ok(summary, reason="计划已更新",
               context_hint=f"当前第{_PLAN_CURRENT}步" if _PLAN_STEPS else "计划已建立，开始执行第1步")


def get_plan_summary() -> str:
    """生成计划+进度的可读文本（供 agent_loop 注入到 messages）"""
    if not _PLAN_STEPS:
        return ""
    done = sum(1 for s in _PLAN_STEPS if s["status"] == "done")
    total = len(_PLAN_STEPS)
    lines = [f"任务计划（{done}/{total} 已完成，当前第 {_PLAN_CURRENT} 步）："]
    for s in _PLAN_STEPS:
        mark = {"done": "x", "doing": ">", "blocked": "!", "pending": " "}.get(s["status"], " ")
        cur = " <-- 当前" if s["step"] == _PLAN_CURRENT else ""
        lines.append(f"  [{mark}] {s['step']}. {s['desc']}{cur}")
    return "\n".join(lines)


def reset_plan() -> None:
    """重置计划状态（新任务开始前调用）"""
    global _PLAN_STEPS, _PLAN_CURRENT
    _PLAN_STEPS = []
    _PLAN_CURRENT = 0


# ════════════ 模块7：结构化测试工具（阶段4方向2）════════════

def _parse_pytest_output(stdout: str, stderr: str) -> dict:
    """解析 pytest 输出，提取结构化测试结果。
    返回：{passed:int, failed:int, errors:int, warnings:int,
           failed_items:[{name:str, file:str, line:int, error:str}], summary_line:str}
    """
    combined = (stdout or "") + "\n" + (stderr or "")
    result = {
        "passed": 0, "failed": 0, "errors": 0, "warnings": 0,
        "failed_items": [], "summary_line": "",
    }
    # 1. 汇总行：===== 3 passed, 2 failed, 1 error in 1.23s =====
    # 注意：旧版用 re.search 单正则匹配，但每个 group 都加了 ?，
    # 在 stdout 第 0 位匹配到 '======'(test session starts 行) 后零宽成功，
    # 没继续往后找 'N passed' → 实测真实输出解析为 passed=0。
    # 改用 splitlines 逐行扫：必须是首尾都是 === 的行且含 passed/failed/errors/warnings/no tests ran 关键字。
    for line in combined.splitlines():
        s = line.strip()
        if not (s.startswith("=") and s.endswith("=") and len(s) >= 6):
            continue
        body = s.strip("=").strip()
        if not re.search(r"\b(passed|failed|errors?|warnings?|no tests ran)\b", body):
            continue
        pm = re.search(r"(\d+)\s*passed", body)
        fm = re.search(r"(\d+)\s*failed", body)
        em = re.search(r"(\d+)\s*errors?", body)
        wm = re.search(r"(\d+)\s*warnings?", body)
        if pm:
            result["passed"] = int(pm.group(1))
        if fm:
            result["failed"] = int(fm.group(1))
        if em:
            result["errors"] = int(em.group(1))
        if wm:
            result["warnings"] = int(wm.group(1))
        result["summary_line"] = body
        break   # 只取第一个汇总行（pytest 只输出一个）

    # 2. 失败项：FAILED tests/test_xxx.py::test_name - 或 _ test_name _
    # 格式1: FAILED tests/test_foo.py::test_bar - assert 3 == 4
    for fm in re.finditer(r"FAILED\s+(\S+?)::(\w+)\s*-\s*(.+)", combined):
        result["failed_items"].append({
            "name": fm.group(2),
            "file": fm.group(1),
            "line": 0,
            "error": fm.group(3)[:300],
        })
    # 格式2: _____ test_name _____（失败函数标题块）
    if not result["failed_items"]:
        for fm in re.finditer(r"_{5,}\s+(\w+)\s+_{5,}", combined):
            result["failed_items"].append({
                "name": fm.group(1), "file": "", "line": 0, "error": "",
            })

    # 3. 错误项：ERROR tests/test_xxx.py::test_name
    for em in re.finditer(r"ERROR\s+(\S+?)::(\w+)", combined):
        result["failed_items"].append({
            "name": em.group(2), "file": em.group(1), "line": 0, "error": "收集阶段错误",
        })

    # 4. 第一个失败的 traceback 片段（最相关）
    tb_match = re.search(r"(E\s+\w+Error:.+?)(?:={3,}|\n\n)", combined, re.DOTALL)
    if tb_match:
        result["first_error"] = tb_match.group(1)[:500]
    else:
        result["first_error"] = ""

    return result


def run_tests(args: dict) -> str:
    """结构化测试运行工具：调用 pytest 并解析输出，返回结构化结果而非原始 stderr。
    比 run_command('pytest ...') 更适合模型快速判断失败用例与根因。
    参数：
      target: 测试目标（文件/目录），默认 'tests/'
      args_str: 额外 pytest 参数，如 '-v --tb=short'，默认 '-v'
    """
    target = args.get("target", "tests/")
    extra_args = args.get("args_str", "-v --tb=short")
    # 安全：target 越界检查（测试目录须在工作区内）
    try:
        _resolve_workspace_path(target)
    except PermissionError as e:
        return _err(str(e), context_hint="测试目标必须在工作区内")

    # 构造命令：pytest <target> <extra_args>
    # 注意：target 路径形式要避免被白名单当编译产物（pytest 在白名单，无此问题）
    cmd = f"pytest {target} {extra_args}".strip()
    # 复用 run_command 的安全检查
    reject = _check_command_safety(cmd)
    if reject:
        return _err(reject, context_hint="测试命令须以 pytest 开头")
    fixed_cmd = _chcp_utf8_prefix() + _windows_fix_exec_path(cmd)
    try:
        proc = subprocess.run(
            fixed_cmd, shell=True, cwd=WORKSPACE, timeout=CMD_TIMEOUT,
            capture_output=True, env=_build_safe_env_utf8(),
        )
        stdout = _decode_bytes(proc.stdout) or ""
        stderr = _decode_bytes(proc.stderr) or ""
        parsed = _parse_pytest_output(stdout, stderr)
        parsed["exit_code"] = proc.returncode
        parsed["raw_tail"] = stdout[-500:] if stdout else stderr[-500:]
        success = proc.returncode == 0
        result_str = json.dumps(parsed, ensure_ascii=False)
        # 防误判：pytest 未收集到任何测试（passed/failed/errors 全 0 且 failed_items 为空）
        # 此时可能 exit_code=0（被吞）或 exit_code=4（no tests ran），
        # 旧逻辑下要么报"全部通过"要么报"测试失败但 failed_items 为空"，都会误导模型。
        # 统一给显式提示，让模型先排查 collection 问题，不要编造"全部通过"。
        if (parsed["passed"] == 0 and parsed["failed"] == 0
                and parsed["errors"] == 0 and not parsed["failed_items"]):
            hint = ("pytest 未收集到任何测试。请检查：1) target 路径是否正确；"
                    "2) 测试文件命名是否为 test_*.py；"
                    "3) 测试函数命名是否为 test_*；"
                    "4) 测试文件 import 是否失败（conftest.py 是否漏写 sys.path.insert）；"
                    "5) raw_tail 字段含末尾原始输出，可定位 collection error")
            return _ok(result_str,
                       reason=f"未收集到测试（exit_code={proc.returncode}）",
                       context_hint=hint)
        if success:
            return _ok(result_str, reason=f"测试通过：{parsed['passed']} passed",
                       context_hint="全部通过，可以进入下一步或提交")
        else:
            hint = "测试失败，请根据 failed_items 修改源码后重新 run_tests；" \
                   "同一失败连续3次必须换方案（如换算法/重新设计）"
            return _ok(result_str, reason=f"测试失败：{parsed['failed']} failed, "
                                          f"{parsed['errors']} errors", context_hint=hint)
    except subprocess.TimeoutExpired:
        return _err(f"测试超时（>{CMD_TIMEOUT}s）",
                    context_hint="测试卡死或死循环，必须换方案")
    except Exception as e:
        return _err(f"执行异常: {type(e).__name__}: {e}")
