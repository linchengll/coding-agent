# coding-agent
构建编程智能体
任务需求：
我需要设计并实现一个编程智能体（coding agent）：它通过与大语言模型交互，能自主
地读写文件、执行命令，完成你交给它的编程任务——类似一个简化的 Claude Code、Codex、
OpenCode、DeepSeek Harness 等。功能繁简不限，可以很简单，也可以做得完善。不允许在现成 agent 产品上封装界面，也不得使用任何 agent 框架 / SDK（LangChain、LlamaIndex、OpenAI Agents SDK、ClaudeAgent SDK、AutoGen、CrewAI 等）。允许使用模型厂商的 API 客户端库、OpenAI 兼容网关及模型原生的 tool calling 接口，但不得依赖 API 服务端托管的代码执行或文件工具（如 CodeInterpreter、Files API）；重要逻辑需自行编写，包括但不限于：对话历史与上下文管理、工具的定义与本地执行、模型输出的解析、循环终止条件、错误处理。API key 等凭据一律通过环境变量获取

工作流程：
## 阶段 1：需求 & 架构设计（先定清楚智能体能干什么）

1. 明确目标：你的编程 Agent 具体任务？
   - 选项 A：需求→自动生成新项目（从零写完整项目）
   - 选项 B：读取现有仓库，自动修复 Bug（SWE 智能体）
   - 选项 C：给代码加新功能、批量重构
   - 选项 D：自主编写、运行测试、迭代调试直到成功
2. 设定边界：禁止操作什么文件，最大迭代次数（防止无限循环）
3. 编写**系统提示词（System Prompt）**：定义 Agent 角色、工作步骤、输出 JSON 格式、出错处理规则


## 阶段 2：最小原型开发（先做最简可用版本，不要一步到位全功能）

1. 选择模型 API、安装 LangGraph 等框架，完成基础 LLM 调用
2. 实现单轮 ReAct 循环：思考→决定调用工具→执行工具→拿到结果再思考
3. 先只开放 1 个简单工具，比如读取单个文件，跑通一轮闭环

> 
> 此时就已经是最简单的编程智能体

## 阶段 3：增加完整编程工具链

依次接入：

1. 文件读写工具
2. Shell 沙盒环境（⚠️**一定要做沙盒隔离，禁止 Agent 无限制执行高危系统命令**）
3. Git 工具：提交代码、查看 diff
4. RAG 代码知识库：上传你的项目源码，让智能体能看懂旧代码
5. 记忆模块：短期对话记忆 + 长期项目记忆

## 阶段 4：核心自主工作流（编程 Agent 标准闭环）

标准自主开发循环：

1. 用户提交需求
2. Agent 规划任务、拆解步骤
3. 扫描现有代码库、检索相关源码
4. 修改 / 新建代码文件
5. 运行单元测试、脚本
6. 判断结果：成功→结束；失败→分析报错，自动修复代码，回到第 4 步，循环迭代
7. 完成，输出结果给用户审阅

## 阶段 5：迭代优化、评测、部署上线

1. 限制最大循环次数，增加终止条件，避免死循环
2. 评测：用 SWE‑bench、自己的测试用例检验 Agent 能否正确解决编程任务
3. 增加人工介入点：关键修改先等待你的审批，再写入磁盘
4. 使用命令行的形式封装，部署运行在本地


大体思路：
总体架构预览
用户输入需求
      │
      ▼
┌─────────────────────────────┐
│  Agent 主循环 agent.py        │
│  · 对话历史管理               │
│  · 终止条件判断               │
│  · 历史长度压缩               │
└─────────────────────────────┘
      │ messages + tools
      ▼
┌─────────────────────────────┐
│  模型 API（DeepSeek 兼容）    │
└─────────────────────────────┘
      │ assistant message / tool_calls
      ▼
┌─────────────────────────────┐
│  工具执行器 tools.py          │
│  · 路径锁定 / 沙盒           │
│  · read_file / write_file    │
│  · run_command / run_tests   │
│  · git 系列工具              │
│  · grep / RAG 代码检索        │
│  · 记忆读写                 │
└─────────────────────────────┘
阶段 1：需求 & 架构设计
1.1 明确目标
你列出的四个选项可以组合：
选项 A：从零生成新项目
选项 B：读取现有仓库修复 Bug
选项 C：加功能、批量重构
选项 D：编写→运行测试→迭代调试直到成功
选择「D + A + B」组合：核心能力是「自主开发循环」，既能从空目录开始写项目，也能在已有仓库中修 Bug 或加功能。原因：
四种任务本质上共用同一套「读文件 → 写文件 → 执行命令 → 看测试结果 → 迭代」闭环。
后续阶段 3、4 的工具链全部围绕这个闭环设计，不需要按任务类型拆分版本。
评测时可以用 SWE-bench 的 Bug 修复任务，也可以用自己的「生成新项目」任务，覆盖面广。
具体任务定义：
纯文本
纯文本
输入：用户自然语言描述
输出：代码变更 + 测试结果 + 总结
核心循环：
  理解需求 → 探索代码 → 修改/创建文件 → 运行验证 → 失败则修复 → 成功则输出
1.2 设定边界
这些边界会在代码中用「配置常量」控制，也会写进 System Prompt 约束模型。
边界项
建议值
说明
工作区根目录
WORKSPACE 环境变量指定
所有读写和执行命令必须发生在该目录下
禁止写入路径
.git/、node_modules/、__pycache__/
防止损坏元数据或第三方依赖
禁止命令
rm -rf /、sudo、mkfs、dd、shutdown、reboot、curl x \| sh
黑名单 + 正则匹配
单命令超时
30 秒
防止死循环或卡死
最大工具调用次数
30 次
防止无限循环
输出截断
工具输出超过 3000 字符则截断
防止上下文爆炸
单文件读取上限
一次最多 200 行
防止大文件淹没模型
1.3 编写 System Prompt
这是 Agent 的「大脑说明书」。我给你一个种子版本，后续你可以根据实际表现反复迭代。
纯文本
纯文本
你是一名资深软件工程师，运行在受控沙盒中，工作目录是 /workspace。
用户会给你一个编程任务。你的目标是完成它并输出结构化结果。

工作流程：
1. 分析需求，给出不超过 5 步的简短计划。
2. 探索现状：使用 list_dir / read_file / grep 浏览相关代码。
3. 实施修改：使用 write_file / edit_file 创建或修改文件。
4. 验证：运行 run_command 执行测试或脚本。
5. 如果失败，阅读报错，分析根因，修改后重新验证。
   同一个错误连续出现 3 次时，必须换一种方案，不要原地打转。
6. 全部完成且测试通过后，输出最终 JSON。

工具使用规范：
- 修改文件之前，必须先 read_file 确认上下文。
- 一次工具调用只做一件事，保持动作清晰。
- 命令输出如果很长，模型收到的已经是截断版本，请不要臆测缺失内容。

安全规则：
- 绝不访问工作区以外的路径。
- 绝不使用 rm -rf、sudo、mkfs、dd 等危险命令。
- 不要猜测文件内容，用工具读取。

最终输出格式（严格 JSON）：
{
  "status": "success" | "failed" | "blocked",
  "summary": "任务总结",
  "changed_files": ["src/main.py", "tests/test_main.py"],
  "test_results": {"passed": 12, "failed": 0, "error": null},
  "unfinished": "未完成事项说明"
}

提示：System Prompt 的完善不是一次性的。每当你发现 Agent 行为不稳定，优先修改提示词，再考虑改代码。

阶段 2：最小原型开发（完整代码）

先明确我们手头已有的文件结构：


project/
├── agent.py               # Agent 主循环 + LLM 调用
├── tools.py               # 工具实现（路径安全、执行逻辑）
├── system_prompt.txt      # 阶段1写好的系统提示词
├── workspace/             # Agent 的工作区（测试用）
│   └── hello.py
└── .env                   # API Key 等配置
2.1 工具实现 tools.py

这里最核心的一个点：路径安全。Agent 只能在工作区内读写文件，绝不能越界。

python
# tools.py
import os
import re

WORKSPACE = os.environ.get("WORKSPACE", os.path.join(os.getcwd(), "workspace"))
os.makedirs(WORKSPACE, exist_ok=True)

# 输出截断上限
MAX_OUTPUT_CHARS = 3000

def _resolve_workspace_path(relative_path: str) -> str:
    """将相对路径解析为工作区内的绝对路径，并阻止越界"""
    # 去掉路径中的 .. 段，防止目录穿越：os.path.realpath 会解析所有符号链接和 ..
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

这个工具已经具备阶段1要求的「工作区隔离」能力。后续所有文件工具都会共用 _resolve_workspace_path，这是安全基石。

2.2 Agent 主循环 agent.py

主循环是整个 Agent 的心脏。它的职责：

维护消息历史
调用模型
解析返回，判断是否要调工具
执行工具，把结果追加到历史
判断终止条件，决定继续还是结束
python
# agent.py
import os
import json
import time
import openai

# ─────────── 配置区 ───────────
API_KEY = os.environ["DEEPSEEK_API_KEY"]
BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
MAX_TOOL_CALLS = int(os.environ.get("MAX_TOOL_CALLS", "30"))
MAX_TOTAL_TURNS = int(os.environ.get("MAX_TOTAL_TURNS", "50"))
RETRY_ATTEMPTS = 3

client = openai.OpenAI(api_key=API_KEY, base_url=BASE_URL)

# 读取系统提示词
with open("system_prompt.txt", encoding="utf-8") as f:
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
        
        # 2. 如果拒绝回答（不返回tool_calls），把纯文本回答加入历史并结束
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
2.3 试运行

准备一个测试工作区：

bash
mkdir -p workspace
cat > workspace/hello.py << 'EOF'
def greet(name):
    return f"Hello, {name}!"

def add(a, b):
    return a + b

if __name__ == "__main__":
    print(greet("World"))
EOF

cat > system_prompt.txt << 'EOF'
你是一名资深软件工程师，运行在受控沙盒中，工作目录是 /workspace。
用户会给你一个编程任务。你的目标是完成它并输出结构化结果。

工作流程：
1. 分析需求，给出简短计划。
2. 使用工具探索现状。
3. 实施修改。
4. 验证结果。
5. 完成后输出最终 JSON。

最终输出格式（严格 JSON）：
{
  "status": "success",
  "summary": "任务总结",
  "changed_files": [],
  "test_results": null,
  "unfinished": ""
}
EOF

然后运行：

bash
export DEEPSEEK_API_KEY="你的key"
python agent.py "读取 workspace/hello.py 的内容，并告诉我这个文件实现了哪些函数"

预期行为：

[agent] 收到需求: 读取 workspace/hello.py ...
[agent] 模型已给出最终回答，循环结束（第 2 轮）

模型应该在第一轮选择调用 read_file，工具返回文件内容，第二轮模型根据文件内容回答用户问题。

