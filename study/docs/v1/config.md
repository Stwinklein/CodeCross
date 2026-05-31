# `codecross/config.py` 解析

## 文件设计说明

`config.py` 负责把项目运行需要的配置集中起来，尤其是模型角色配置。

这样设计的原因是：

- CodeCross 不能把模型写死在代码里。
- V1 需要从 `.env` 中读取 DeepSeek 和 Anthropic 两个渠道，配置成两个独立 reviewer。
- workflow 只关心“有哪些 reviewer 被启用”，不关心 API key 和 base url 怎么读。
- 后续要换模型、接更多 reviewer、加 YAML/TOML 配置时，可以优先改配置层。

简单说：

```text
config.py 决定：这个角色用哪个模型、怎么运行验证 loop。
```

## 常量

### `PROJECT_ROOT`

```python
PROJECT_ROOT = Path(__file__).resolve().parents[1]
```

含义：从 `codecross/config.py` 向上两层找到项目根目录。

项目根目录里有 `.env`。

## 数据结构

### `ModelSettings`

一个模型角色的配置。

字段：

- `role`：系统角色，例如 `planner`、`deepseek_reviewer`。
- `provider`：模型 provider，例如 `deepseek`、`anthropic`。
- `model`：模型名。
- `name`：报告里显示的名字。
- `focus`：这个模型角色的关注点。
- `temperature`
- `max_tokens`
- `timeout_seconds`
- `retry_count`
- `enabled`
- `api_key_env`
- `base_url_env`

它不直接保存密钥，只保存环境变量名。

#### `display_name`

```python
return self.name or self.role
```

报告优先显示 `name`，如果没有 name，就用 role。

#### `api_key`

从环境变量读取 API key。

这样可以避免把密钥打印到对象里，也避免源码持有密钥。

#### `base_url`

从环境变量读取 base url。

这对中转站很重要，因为你现在 `.env` 里配置了：

```text
DEEPSEEK_BASE_URL
ANTHROPIC_BASE_URL
```

### `RunnerConfig`

验证循环和 executor 的运行配置。

字段：

- `max_verification_rounds`
- `max_tests_per_round`
- `test_timeout_seconds`
- `allow_network`
- `allow_write_outside_project`
- `fail_open`
- `blocked_commands`

这里体现 V1 的安全边界：

```text
默认不允许网络
默认不允许写项目外文件
默认拦截危险命令/片段
```

### `ProjectConfig`

整个项目工作流的总配置。

字段：

- `language`
- `test_framework`
- `package_manager`
- `source_roots`
- `test_roots`
- `entry_points`
- `allowed_commands`
- `model_config`
- `reviewers`
- `runner_config`
- `mock_llm`

#### `enabled_reviewers`

返回启用的 reviewer 列表。

workflow 用它动态创建 reviewer 节点：

```python
for index, reviewer in enumerate(config.enabled_reviewers()):
```

## 函数详解

### `_load_env(project_root: Path)`

作用：

1. 加载项目根目录 `.env`。
2. 兼容 DeepSeek 的环境变量名。

代码逻辑：

```python
load_dotenv(project_root / ".env")
if os.getenv("DEEPSEEK_BASE_URL") and not os.getenv("DEEPSEEK_API_BASE"):
    os.environ["DEEPSEEK_API_BASE"] = os.environ["DEEPSEEK_BASE_URL"]
```

原因：

- 你的 `.env` 里用的是 `DEEPSEEK_BASE_URL`。
- 某些 LangChain/DeepSeek 实现可能读取 `DEEPSEEK_API_BASE`。
- 这里做一次映射，避免用户改 `.env`。

### `load_project_config(project_root=None, mock_llm=False)`

这是配置层最重要的函数。

它做几件事：

1. 确定项目根目录。
2. 加载 `.env`。
3. 读取模型名：

```python
DEEPSEEK_MODEL
ANTHROPIC_MODEL
```

4. 创建两个 reviewer：

```text
logic_reviewer
  provider: deepseek
  focus: logic, boundary conditions, runtime exceptions, maintainability

security_maintainability_reviewer
  provider: anthropic
  focus: security, API design, failure modes, missing tests
```

5. 创建其他模型角色：

- planner
- test_strategy_agent
- test_case_agent
- test_result_analyzer
- coverage_gap_agent
- targeted_reviewer
- evaluator
- high_risk_reviewer

6. 返回 `ProjectConfig`。

## 为什么 reviewer 是 list，而其他角色是 dict

`reviewers` 是 list，因为 reviewer 数量可能扩展：

```text
2 个 reviewer
3 个 reviewer
本地规则 reviewer
安全 reviewer
A2A reviewer
```

workflow 会按列表动态 fan-out。

其他角色是固定职责，例如只有一个 planner、一个 evaluator，所以放在 `model_config` dict 里按 role 查询。

## mock_llm 的作用

`mock_llm=True` 时，Agent 层不会真实调用模型，而是走确定性 mock。

用途：

- 单测稳定。
- 本地快速调试 workflow。
- 不消耗模型额度。
- 不受网络影响。

真实链路调试时不要加 `--mock-llm`。

## 这个文件的关键思想

`config.py` 把“角色”和“模型渠道”解耦。

Agent 不应该写死：

```python
deepseek-chat
claude-opus
```

而应该从配置拿：

```python
settings.provider
settings.model
settings.api_key
settings.base_url
```

