from __future__ import annotations

"""LangChain 模型调用适配层。

其他模块不要直接 import 具体 provider SDK，而是通过这里的 call_model /
call_model_json 统一调用。这样以后换 OpenAI、Qwen、本地模型或 A2A remote agent
时，只需要扩展这一层，业务流程不用跟着改。
"""

import json
from typing import Any, Iterable, Sequence

from langchain.chat_models import init_chat_model
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError

from .config import ModelSettings


class ModelCallError(RuntimeError):
    """模型调用或结构化解析失败时抛出的统一异常。"""

    pass


# Agent 代码里可以用 LangChain message，也可以用轻量 tuple/dict 表示消息。
MessageInput = BaseMessage | tuple[str, str] | dict[str, str]


# 这些模块名前缀代表“外部模型/网络/SDK 层”的异常。
# 这里不直接 catch 所有 Exception，是为了避免把我们自己的代码 bug 也包装成
# “模型失败”。比如字段名写错、对象类型不对，应该暴露堆栈，而不是悄悄进入 fallback。
RECOVERABLE_MODEL_EXCEPTION_MODULES = (
    "httpx",
    "httpcore",
    "anthropic",
    "openai",
    "langchain",
    "langchain_core",
)


def json_schema_instruction(schema: type[BaseModel]) -> str:
    """生成给 LLM 的 JSON schema 约束提示。"""

    return (
        "Return exactly one valid JSON object. Do not wrap it in Markdown. "
        "It must validate against this JSON Schema:\n"
        f"{json.dumps(schema.model_json_schema(), ensure_ascii=False, indent=2)}"
    )


def _provider_kwargs(settings: ModelSettings) -> dict[str, Any]:
    """把内部 ModelSettings 转成 LangChain provider 参数。

    V1.1 把 provider 差异集中在这里，原因有两个：
    1. 不同 provider 的参数名可能不同，例如 Anthropic 当前走 `max_tokens`；
    2. 业务层不应该知道这些 SDK 细节，否则以后换 provider 会散落很多 if/else。
    """

    kwargs: dict[str, Any] = {
        "temperature": settings.temperature,
        "timeout": settings.timeout_seconds,
        "max_tokens": settings.max_tokens,
    }
    if settings.api_key:
        kwargs["api_key"] = settings.api_key
    if settings.base_url:
        kwargs["base_url"] = settings.base_url
    return kwargs


def _is_recoverable_model_exception(exc: Exception) -> bool:
    """判断异常是否属于模型调用链路的可恢复失败。

    这里用 module 前缀而不是 import 一堆 provider 异常类，是为了保持适配层轻量：
    项目可能只安装其中一个 provider，强行 import 其它 SDK 反而会制造依赖问题。
    """

    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True
    module = type(exc).__module__
    return module.startswith(RECOVERABLE_MODEL_EXCEPTION_MODULES)


def create_chat_model(settings: ModelSettings):
    """按 ModelSettings 创建 LangChain chat model。

    这里集中处理 api_key/base_url/max_tokens 等 provider 差异。
    """

    if settings.api_key_env and not settings.api_key:
        raise ModelCallError(
            f"Missing environment variable {settings.api_key_env} for {settings.display_name}."
        )

    return init_chat_model(
        model=settings.model,
        model_provider=settings.provider,
        **_provider_kwargs(settings),
    )


def content_to_text(content: Any) -> str:
    """把不同 provider 返回的 message.content 统一转成纯文本。"""

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if text:
                    parts.append(str(text))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content)


def _message_from_input(message: MessageInput) -> BaseMessage:
    """把 tuple/dict 形式的轻量消息转换成 LangChain BaseMessage。"""

    if isinstance(message, BaseMessage):
        return message
    if isinstance(message, tuple):
        role, content = message
    else:
        role, content = message["role"], message["content"]

    if role in {"system", "developer"}:
        return SystemMessage(content=content)
    if role in {"human", "user"}:
        return HumanMessage(content=content)
    return HumanMessage(content=content)


def normalize_messages(messages: Sequence[MessageInput]) -> list[BaseMessage]:
    """批量规范化消息，供 invoke 调用。"""

    return [_message_from_input(message) for message in messages]


def call_model(settings: ModelSettings, messages: Sequence[MessageInput]) -> str:
    """调用模型并返回文本。"""

    try:
        model = create_chat_model(settings)
        response = model.invoke(normalize_messages(messages))
        return content_to_text(response.content)
    except ModelCallError:
        raise
    except Exception as exc:
        if _is_recoverable_model_exception(exc):
            raise ModelCallError(
                f"{settings.display_name} model call failed: {type(exc).__name__}: {exc}"
            ) from exc
        raise


def _strip_code_fences(text: str) -> str:
    """去掉 LLM 常见的 ```json 代码块包装。"""

    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return cleaned


def _extract_json_text(text: str) -> str:
    """从模型输出中截取第一个 JSON object/array。"""

    cleaned = _strip_code_fences(text)
    if not cleaned:
        raise ValueError("empty response")

    starts = [idx for idx in (cleaned.find("{"), cleaned.find("[")) if idx >= 0]
    if not starts:
        raise ValueError("response did not contain JSON")
    start = min(starts)
    opener = cleaned[start]
    closer = "}" if opener == "{" else "]"
    end = cleaned.rfind(closer)
    if end < start:
        raise ValueError("response JSON was not closed")
    return cleaned[start : end + 1]


def parse_json_model(schema: type[BaseModel], text: str) -> BaseModel:
    """把模型文本解析并校验为指定 Pydantic schema。"""

    data = json.loads(_extract_json_text(text))
    return schema.model_validate(data)


def call_model_json(
    settings: ModelSettings,
    messages: Sequence[MessageInput],
    schema: type[BaseModel],
) -> BaseModel:
    """调用模型并要求它返回可校验 JSON。

    V1 只做一次简单 repair retry：如果 JSON 解析或 schema 校验失败，
    再把错误和 schema 发回模型，请它只修复输出格式。
    """

    attempts = max(settings.retry_count + 1, 1)
    last_error: Exception | None = None
    augmented_messages = list(messages)

    for attempt in range(attempts):
        try:
            text = call_model(settings, augmented_messages)
            return parse_json_model(schema, text)
        except (json.JSONDecodeError, ValidationError, ValueError, ModelCallError) as exc:
            last_error = exc
            if attempt >= attempts - 1 or isinstance(exc, ModelCallError):
                break
            augmented_messages = [
                *messages,
                (
                    "user",
                    "The previous response was invalid JSON or did not match the schema. "
                    "Repair it and return JSON only.\n"
                    f"Schema:\n{json.dumps(schema.model_json_schema(), ensure_ascii=False)}\n"
                    f"Error:\n{exc}",
                ),
            ]

    raise ModelCallError(f"{settings.display_name} failed to return valid JSON: {last_error}")


def summarize_messages(messages: Iterable[MessageInput]) -> str:
    """调试辅助：把消息序列渲染成人类可读文本。"""

    normalized = normalize_messages(list(messages))
    return "\n\n".join(f"{message.type}: {message.content}" for message in normalized)
