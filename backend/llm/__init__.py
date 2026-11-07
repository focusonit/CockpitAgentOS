"""LLM 抽象层。"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

from backend.llm.base import ChatModel, LLMError, LLMReply, LLMToolCall

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def model_from_env(model: str | None = None) -> ChatModel:
    """读取 .env 和环境变量，构造 DeepSeek 模型；没有 key 时抛 LLMError。"""
    load_dotenv(ENV_FILE)
    from backend.llm.deepseek import DeepSeekModel

    return DeepSeekModel.from_env(model)


__all__ = ["ChatModel", "LLMError", "LLMReply", "LLMToolCall", "model_from_env"]
