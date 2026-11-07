"""端侧规则共用的文本归一化。"""

from __future__ import annotations

import re

_PUNCT = re.compile(r"[\s，。！？、,.!?~～…“”\"']+")
# 句首的客气话、语气词，不影响意图
_PREFIX = re.compile(r"^(那就|那|请你|请|麻烦你|麻烦|帮我|给我|你帮我|你给我|豆包|嘿豆包|小豆|小安|嘿小安)+")
_SUFFIX = re.compile(r"(一下吧|一下|吧|啊|呀|哈|呗|好吗|可以吗|谢谢)+$")


def normalize(text: str) -> str:
    """去掉标点空白；只用于规则匹配，不改用户原话。"""
    return _PUNCT.sub("", text)


def core(text: str) -> str:
    """再去掉句首客气话和句尾语气词，快速通道按整句匹配时用。"""
    return _SUFFIX.sub("", _PREFIX.sub("", normalize(text)))
