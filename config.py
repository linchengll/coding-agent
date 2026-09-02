# config.py
"""配置加载：定位 .env、读取环境变量、构造 OpenAI 客户端、加载系统提示词。
所有模块通过此文件获取配置常量与 client。"""
import os
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
