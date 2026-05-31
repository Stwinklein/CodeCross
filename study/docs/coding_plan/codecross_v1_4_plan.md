# CodeCross V1.4 计划：实时事件流与可请求终止

## 版本目标

V1.3 已经完成“浏览器创建 Run -> 后端执行 workflow -> 前端轮询结果 -> 导出报告”的主闭环。V1.4 的目标是补上两个最影响产品手感和安全边界的能力：

1. 前端不再只看粗粒度轮询，而是能通过事件流看到 Run 生命周期变化。
2. 用户可以对正在运行的 Run 发出终止请求，后端会保存这个请求并在安全检查点停止或标记结果。

本版本仍然不引入数据库，所有状态继续保存在内存中；持久化和历史列表留给 V1.5。

## 核心改动

### 1. 后端 Run Event 模型

在 `codecross/api.py` 增加 `RunEvent`：

| 字段 | 含义 |
| --- | --- |
| `event_id` | 单调递增事件 ID，方便 SSE 客户端断线后判断顺序 |
| `run_id` | 所属 Run |
| `type` | `created/running/node/succeeded/failed/abort_requested/aborted` |
| `message` | 给前端展示的人类可读信息 |
| `node` | 可选 workflow 节点名 |
| `created_at` | UTC 时间 |
| `payload` | 小块结构化补充信息 |

RunRecord 新增：

- `events: list[RunEvent]`
- `cancel_requested: bool`

### 2. SSE 事件接口

新增：

```text
GET /api/runs/{run_id}/events
```

V1.4 使用 `text/event-stream`，每条消息格式：

```text
id: 3
event: node
data: {"run_id":"...","type":"node","message":"Executor finished"}
```

实现策略：

- 每 0.4 秒读取一次内存事件列表。
- 只发送尚未发给客户端的新事件。
- Run 进入 `succeeded/failed/aborted` 后，发送完最后事件再关闭连接。
- 仍保留 V1.3 的 `GET /api/runs/{run_id}` 轮询作为兜底。

### 3. 终止请求接口

新增：

```text
POST /api/runs/{run_id}/abort
```

行为：

- `pending/running`：设置 `cancel_requested=true`，追加 `abort_requested` 事件。
- `succeeded/failed/aborted`：返回当前 Run，不做破坏性修改。

限制：

- V1.4 不做进程级强杀。
- 因为当前 workflow 是同步执行，终止是“协作式”的：能在开始前、节点事件检查点、结束写回前生效。
- V1.6 引入更强 executor/agent 后，再考虑更细粒度中断。

### 4. Workflow 节点事件

为了让事件流不是只有开始/结束，给 `run_workflow` 增加两个可选参数：

```python
on_event: Callable[[str, dict[str, Any]], None] | None
should_cancel: Callable[[], bool] | None
```

最小实现：

- `on_event("workflow_started", {...})`
- 通过 LangGraph `stream(..., stream_mode="values")` 或安全 fallback，在节点级别追加进度事件。
- 每个事件检查 `should_cancel()`；如果已请求终止，抛出内部取消异常并由 API 层标记为 `aborted`。

### 5. 前端接入 EventSource

`frontend/src/App.tsx`：

- Run 页面优先连接 `/api/runs/{run_id}/events`。
- 收到 `node/succeeded/failed/aborted` 事件后更新日志、进度、状态。
- EventSource 出错时退回 V1.3 轮询，避免浏览器兼容问题导致页面不可用。
- 终止按钮调用 `POST /api/runs/{run_id}/abort`，不再只做本地模拟。

## 测试计划

### 后端

新增/更新 `tests/test_api.py`：

- `POST /api/runs/{run_id}/abort` 能设置 `cancel_requested` 并写入事件。
- 终止不存在的 Run 返回 404。
- 正常 Run 完成后事件列表包含 `created/running/succeeded`。
- 已完成 Run 再 abort 不会改成 aborted。

### 前端

继续运行：

```text
npm.cmd run build
```

验证 TypeScript 能通过，说明 EventSource、Abort API 和 fallback 轮询的类型都正确。

## 不做内容

- 不引 SQLite。
- 不做 Run 历史列表。
- 不做多进程队列。
- 不强杀 Python 线程或子进程。
- 不改变 executor 的沙箱策略。

这些内容留到 V1.5/V1.6。

## 完成标准

- 后端提供 `/events` 和 `/abort`。
- 前端终止按钮改为真实 API 调用。
- 正常 Run 能产生事件列表并最终成功。
- 请求终止的 Run 不会被悄悄当作成功合并结果。
- Python 测试和前端构建都通过。
