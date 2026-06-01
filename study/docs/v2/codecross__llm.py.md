# `codecross/llm.py` 解析

## 文件作用
LLM 适配层，统一模型创建、消息规范化、可恢复错误包装、JSON 提取和 Pydantic schema 校验。

- 源码行数：245
- 主要依赖：`json`、`typing`、`langchain`、`langchain_core`、`pydantic`、`config`

## 函数与类

### `ModelCallError`（类，第 20 行）
- 签名：`class ModelCallError(RuntimeError)`
- 作用：模型调用或结构化解析失败时抛出的统一异常。

### `json_schema_instruction`（函数，第 43 行）
- 签名：`def json_schema_instruction(schema: type[BaseModel]) -> str`
- 作用：生成给 LLM 的 JSON schema 约束提示。

### `_provider_kwargs`（函数，第 53 行）
- 签名：`def _provider_kwargs(settings: ModelSettings) -> dict[str, Any]`
- 作用：把内部 ModelSettings 转成 LangChain provider 参数。

### `_is_recoverable_model_exception`（函数，第 73 行）
- 签名：`def _is_recoverable_model_exception(exc: Exception) -> bool`
- 作用：判断异常是否属于模型调用链路的可恢复失败。

### `create_chat_model`（函数，第 86 行）
- 签名：`def create_chat_model(settings: ModelSettings)`
- 作用：按 ModelSettings 创建 LangChain chat model。

### `content_to_text`（函数，第 104 行）
- 签名：`def content_to_text(content: Any) -> str`
- 作用：把不同 provider 返回的 message.content 统一转成纯文本。

### `_message_from_input`（函数，第 124 行）
- 签名：`def _message_from_input(message: MessageInput) -> BaseMessage`
- 作用：把 tuple/dict 形式的轻量消息转换成 LangChain BaseMessage。

### `normalize_messages`（函数，第 141 行）
- 签名：`def normalize_messages(messages: Sequence[MessageInput]) -> list[BaseMessage]`
- 作用：批量规范化消息，供 invoke 调用。

### `call_model`（函数，第 147 行）
- 签名：`def call_model(settings: ModelSettings, messages: Sequence[MessageInput]) -> str`
- 作用：调用模型并返回文本。

### `_strip_code_fences`（函数，第 164 行）
- 签名：`def _strip_code_fences(text: str) -> str`
- 作用：去掉 LLM 常见的 ```json 代码块包装。

### `_extract_json_text`（函数，第 178 行）
- 签名：`def _extract_json_text(text: str) -> str`
- 作用：从模型输出中截取第一个 JSON object/array。

### `parse_json_model`（函数，第 197 行）
- 签名：`def parse_json_model(schema: type[BaseModel], text: str) -> BaseModel`
- 作用：把模型文本解析并校验为指定 Pydantic schema。

### `call_model_json`（函数，第 204 行）
- 签名：`def call_model_json(settings: ModelSettings, messages: Sequence[MessageInput], schema: type[BaseModel]) -> BaseModel`
- 作用：调用模型并要求它返回可校验 JSON。

### `summarize_messages`（函数，第 241 行）
- 签名：`def summarize_messages(messages: Iterable[MessageInput]) -> str`
- 作用：调试辅助：把消息序列渲染成人类可读文本。

## 学习提示
建议结合 `schemas.py` 中的对象一起读：这个文件的大多数函数都在接收或返回那些结构化对象。
