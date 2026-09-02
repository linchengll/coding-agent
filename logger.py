# logger.py
"""
观察者 + 适配器模式日志层。
Subject：AgentEventEmitter —— agent 主循环持有一个全局实例 emitter。
Observer：LogObserver 抽象基类，具体观察者按事件类型决定如何打印。

"""
from abc import ABC, abstractmethod
from datetime import datetime
import sys


def _safe_print(s: str) -> None:
    """打印一行，兼容 GBK 默认编码的 Windows 终端。
    UTF-8 终端：print 直接成功，▸ ✓ ✗ ◆ 等符号正确渲染。
    GBK 终端：print 对非 GBK 字符抛 UnicodeEncodeError，降级写 UTF-8 字节
    （终端按 GBK 解码会显示乱码，但不抛异常、不污染既有中文输出）。"""
    try:
        print(s)
    except UnicodeEncodeError:
        try:
            sys.stdout.buffer.write((s + "\n").encode("utf-8", "replace"))
            sys.stdout.flush()
        except Exception:
            pass


class AgentEventEmitter:
    """Subject：事件发射器。agent 主循环持有一个全局实例。"""

    def __init__(self):
        self._observers = []

    def attach(self, obs):
        self._observers.append(obs)

    def detach(self, obs):
        self._observers.remove(obs)

    def emit(self, event_type: str, payload: dict):
        for obs in self._observers:
            try:
                obs.on_event(event_type, payload)
            except Exception as e:
                print(f"[logger] observer 异常: {e}")


class LogObserver(ABC):
    """Observer 抽象基类。"""

    @abstractmethod
    def on_event(self, event_type: str, payload: dict):
        pass


class StageBannerObserver(LogObserver):
    """阶段横幅：stage_change 事件打印 ▌[HH:MM:SS] 阶段 N/M desc"""

    def on_event(self, event_type, payload):
        if event_type != "stage_change":
            return
        ts = datetime.now().strftime("%H:%M:%S")
        s = payload["current_step"]
        t = payload["total_steps"]
        d = payload["desc"]
        _safe_print(f"▌[{ts}] 阶段 {s}/{t} {d}")


class CompactToolObserver(LogObserver):
    """精简工具调用：tool_call/tool_result/loop_end 单行打印，
    替换原 ┌─ 调用工具 / └─ 结果 框。"""

    def on_event(self, event_type, payload):
        if event_type == "tool_call":
            _safe_print(f"  ▸ {payload['name']}({payload['args_preview']})")
        elif event_type == "tool_result":
            mark = "✓" if payload["success"] else "✗"
            _safe_print(f"    {mark} {payload['reason_preview']}")
        elif event_type == "loop_end":
            _safe_print(f"  ◆ 完成（第 {payload['turn']} 轮）")


# 全局单例：agent.py 直接 from logger import emitter 使用
emitter = AgentEventEmitter()
emitter.attach(StageBannerObserver())
emitter.attach(CompactToolObserver())
