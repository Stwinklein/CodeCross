# `codecross/llm.py` 解析

## 文件设计说明

`llm.py` 是 CodeCross 的模型适配层，也可以理解成“模型网关”。

它存在的原因不是为了炫技，而是多 Agent 系统一定会遇到这些问题：

- 不同 provider 参数名不一样。
- 不同 provider 返回内容格式不一样。
- 中转站对 structured output、tool calling、JSON schema 支持程度不一样。
- 模型经常不按要求输出纯 JSON。
- 每个 Agent 都要结构化输出，不能到处重复写解析逻辑。

所以 `llm.py` 统一处理：

```text
创建模型
规范化消息
调用模型
提取 JSON
Pydantic 校验
repair retry
统一异常
```

## 核心类型

### `ModelCallError`

统一模型调用错误。

它把底层的：

- JSONDecodeError
- Pydantic ValidationError
- provider error
- 响应为空

都收敛成一个异常类型，方便 Agent 层处理。

### `MessageInput`

```python
MessageInput = BaseMessage | tuple[str, str] | dict[str, str]
```

表示 Agent 层可以传三种消息：

1. LangChain 原生 `BaseMessage`
2. 简单 tuple：

```python
("system", "...")
("user", "...")
```

3. dict：

```python
{"role": "user", "content": "..."}
```

`llm.py` 会统一转成 LangChain message。

## 函数详解

### `json_schema_instruction(schema)`

把 Pydantic schema 转成 prompt 里的 JSON 输出约束。

作用：

```text
告诉模型应该返回什么结构
```

注意：这是 V1 的 fallback 策略。它比 provider-native structured output 弱，但兼容性更好。

原因：

- 你用了 `BASE_URL`，可能经过中转站。
- 中转站对 `with_structured_output`、`response_format`、tool calling 支持可能不完整。
- prompt schema + 本地校验是最通用的跨模型方案。

### `create_chat_model(settings)`

根据 `ModelSettings` 创建 LangChain chat model。

主要处理：

- API key 是否存在
- temperature
- timeout
- base_url
- max_tokens
- provider 参数差异

例如 Anthropic 参数名不同：

```python
if settings.provider == "anthropic":
    kwargs["max_tokens_to_sample"] = settings.max_tokens
else:
    kwargs["max_tokens"] = settings.max_tokens
```

最后调用：

```python
init_chat_model(
    model=settings.model,
    model_provider=settings.provider,
    **kwargs,
)
```

### `content_to_text(content)`

把模型返回的 `message.content` 转成字符串。

为什么需要：

不同 provider 返回可能不同：

- 普通字符串
- list 分段内容
- dict 结构

Agent 层不应该关心这些差异，所以在这里统一。

### `_message_from_input(message)`

把内部轻量消息格式转成 LangChain 消息对象。

例如：

```python
("system", "你是 Planner")
```

转成：

```python
SystemMessage(content="你是 Planner")
```

```python
("user", "请审查这个 diff")
```

转成：

```python
HumanMessage(content="请审查这个 diff")
```

它的意义不是简单包装，而是保留 role 语义，并把内部格式适配到 LangChain 标准接口。

### `normalize_messages(messages)`

批量转换消息。

Agent 层传入 messages 后，最终都会进入这里。

### `call_model(settings, messages)`

最基础的模型文本调用。

流程：

```text
create_chat_model
normalize_messages
model.invoke
content_to_text
```

返回纯文本。

### `_strip_code_fences(text)`

去掉模型输出里的 Markdown 代码块。

模型经常返回：

```markdown
```json
{...}
```
```

这个函数会把外层 fence 去掉。

### `_extract_json_text(text)`

从模型输出中提取 JSON 文本。

解决的问题：

模型可能会输出：

```text
下面是结果：
{
  "risk_level": "high"
}
```

直接 `json.loads()` 会失败，因为前面有说明文字。

这个函数会尽量截取第一个 `{...}` 或 `[...]`。

注意：

这是 V1 轻量容错，不是完美 parser。后续可以用更严格的 JSON 提取策略。

### `parse_json_model(schema, text)`

先提取 JSON，再做 Pydantic 校验：

```python
data = json.loads(_extract_json_text(text))
return schema.model_validate(data)
```

这个函数是信任边界：

```text
模型说自己输出了 JSON 不算，Pydantic 校验通过才算。
```

### `call_model_json(settings, messages, schema)`

这是最重要的函数。

它负责：

1. 调模型。
2. 解析 JSON。
3. Pydantic 校验。
4. 失败时追加 repair message 再试。
5. 仍失败则抛 `ModelCallError`。

流程：

```text
call_model
  -> parse_json_model
      -> json.loads
      -> schema.model_validate
失败：
  -> 把错误和 schema 发回模型
  -> 要求模型修复 JSON
仍失败：
  -> ModelCallError
```

调试真实链路时，最应该打断点在这里。

观察变量：

```python
settings.role
settings.provider
settings.model
schema.__name__
messages
```

你可以看到当前是在调：

- planner
- reviewer
- test_case_agent
- targeted_reviewer
- high_risk_reviewer

### `summarize_messages(messages)`

调试辅助函数，把消息序列渲染成文本。

## 为什么不直接用 LangChain structured output

LangChain 确实有 structured output 能力，理论上更强。

V1 没有优先使用它，是为了兼容中转站和多 provider。

很多中转站对这些能力支持不完整：

- `response_format`
- `json_schema`
- tool calling
- function calling
- `with_structured_output`

所以 V1 采用：

```text
prompt schema + 本地 JSON 提取 + Pydantic 校验 + repair retry
```

后续更理想的设计是：

```text
优先用 provider-native structured output
失败后 fallback 到 prompt schema
```

## 这个文件的关键思想

`llm.py` 把“怎么稳定和模型说话”从 Agent 业务逻辑里抽出来。

分层关系：

```text
config.py  决定用哪个模型
llm.py     决定怎么调用模型并拿到结构化结果
agents.py  决定每个 Agent 要问什么
```

