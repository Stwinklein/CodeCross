"""
基础版react实现
"""
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional
import json
import re

#1message结构
"""
字典dict模拟
用户消息：
{
    "role": "user",
    "content": "帮我计算 12 * 3"
}

AI 普通回答：
{
    "role": "assistant",
    "content": "答案是 36"
}

AI 要求调用工具：
{
    "role": "assistant",
    "content": "",
    "tool_calls": [
        {
            "id": "call_1",
            "name": "calculator",
            "args": {"expression": "12 * 3"}
        }
    ]
}
工具返回结果：
{
    "role": "tool",
    "tool_call_id": "call_1",
    "name": "calculator",
    "content": "36"
}
"""
Message = Dict[str, Any]

#2tool定义
@dataclass
class Tool:
    """工具定义
    langchain的@tool装饰器本质上是
    1记录工具名
    2记录工具描述
    3记录工具参数schema
    4保存真正要执行的python函数
    """
    name: str
    description: str
    function: Callable[[str], str]

