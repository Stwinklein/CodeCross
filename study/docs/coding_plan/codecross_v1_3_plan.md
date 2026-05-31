# CodeCross V1.3 计划：真实 Web Run 闭环

## 版本目标

V1.3 的目标是把 CodeCross 从“CLI + 模拟前端”推进到“浏览器里可以真实发起一次审查并看到结果”。

当前状态：

- 后端 CLI 已经能跑完整 workflow。
- V1.1 已加强 executor 安全和证据可信度。
- V1.2 已加入 Context Retriever，让 reviewer 能看到本地代码上下文。
- 前端 V1 已有界面，但 Run 执行仍是本地模拟数据。

V1.3 要补齐最关键的产品闭环：

```text
前端新建 Run
  -> POST /api/runs
  -> 后端后台执行 run_workflow
  -> GET /api/runs/{run_id} 轮询状态
  -> 前端展示真实结果
  -> 导出真实 Markdown / JSON
```

## 必须做

### 1. 新增最小 FastAPI 后端

新增文件：

```text
codecross/api.py
```

V1.3 只提供最小 API：

| 方法 | 路径 | 作用 |
|---|---|---|
| `POST` | `/api/runs` | 创建 Run 并后台执行 |
| `GET` | `/api/runs/{run_id}` | 获取 Run 当前状态与快照 |
| `GET` | `/api/runs/{run_id}/export?format=markdown|json` | 导出报告 |
| `GET` | `/api/health` | 健康检查 |

暂时不做 SSE。前端用轮询即可。

### 2. Run 请求与响应 schema

新增 API 层 schema，可以先放在 `codecross/api.py`，后续再拆：

```python
class CreateRunRequest(BaseModel):
    diff: str
    project_root: str = "."
    mock_llm: bool = True
    reviewers: list[str] = []
    runner_config: dict[str, Any] = {}
```

Run 状态：

```python
RunStatus = Literal["pending", "running", "succeeded", "failed"]
```

Run 记录：

```python
class RunRecord(BaseModel):
    run_id: str
    status: RunStatus
    created_at: str
    updated_at: str
    request: CreateRunRequest
    snapshot: dict[str, Any] | None = None
    final_report: str = ""
    error: str = ""
```

### 3. 内存 Run 存储

V1.3 先用内存字典，不引 SQLite：

```python
RUNS: dict[str, RunRecord] = {}
```

原因：

- 先验证 API 契约和前端真实调用。
- 避免在 V1.3 同时处理数据库迁移、并发写入和持久化。
- SQLite 放到 V1.4 或 V1.5 更稳。

注意事项：

- `run_id` 用 UUID 短 ID。
- `snapshot` 使用 `to_jsonable` 转换 workflow state。
- API 返回不要暴露 `config` 里的敏感字段。

### 4. 后台执行 workflow

`POST /api/runs` 创建 Run 后，启动后台任务执行：

```python
run_workflow(diff, project_root=project_root, config=config)
```

状态流转：

```text
pending -> running -> succeeded
pending -> running -> failed
```

实现建议：

- V1.3 用 `threading.Thread` 即可。
- 创建 Run 时立即返回 `run_id`。
- 后台线程更新 `RUNS[run_id]`。
- 捕获异常后写入 `status="failed"` 和 `error`。

### 5. 前端接真实 API

修改：

```text
frontend/src/App.tsx
```

行为：

- 点击“开始审查”时调用 `POST /api/runs`。
- 成功后跳转 `/runs/{run_id}`。
- Run 页面轮询 `GET /api/runs/{run_id}`。
- `pending/running` 时展示执行中布局。
- `succeeded` 时使用真实 `snapshot` 渲染风险评估、Findings、日志/摘要。
- `failed` 时展示失败状态和错误信息。

V1.3 可以保留现有 Pipeline 静态布局：

- 节点实时状态仍然用模拟/阶段推断。
- 真实逐节点事件留给 V1.4 SSE。

### 6. 前端开发代理

修改：

```text
frontend/vite.config.ts
```

增加代理：

```ts
server: {
  port: 5173,
  proxy: {
    "/api": "http://127.0.0.1:8000"
  }
}
```

这样前端可以直接请求 `/api/runs`。

### 7. 导出真实报告

前端“导出 MD / JSON”按钮改为请求：

```text
GET /api/runs/{run_id}/export?format=markdown
GET /api/runs/{run_id}/export?format=json
```

后端行为：

- markdown 返回 `final_report`
- json 返回完整 `snapshot`
- Run 未完成时返回 409 或带明确错误信息

## 测试要求

### 后端测试

新增：

```text
tests/test_api.py
```

覆盖：

- `GET /api/health`
- `POST /api/runs` 能创建 run
- `GET /api/runs/{run_id}` 能查询状态
- mock_llm demo 最终进入 `succeeded`
- export markdown/json 可用
- 不存在的 run 返回 404

### 前端验证

至少跑：

```powershell
cd frontend
npm.cmd run build
```

手动验证：

- 打开 `http://127.0.0.1:5173`
- 点击“开始审查”
- 页面跳到 `/runs/{run_id}`
- 等待状态变 succeeded
- 能看到真实风险结果
- 能导出 MD/JSON

## 暂时不做

V1.3 不做以下内容：

- SSE 实时事件流
- 真实 Abort 终止
- SQLite 持久化
- Runs 历史列表
- 多用户/账号权限
- Docker executor
- 多语言 executor
- SARIF / CI 集成
- 前端逐节点真实进度

这些内容分别放到后续版本：

- V1.4：SSE 事件流 + 真实 Abort
- V1.5：SQLite Run 存储 + Runs 历史列表
- V1.6：Test Author Agent 增强测试生成
- V2.0：Docker executor + CI/SARIF

## 推荐实施顺序

1. 新增 `codecross/api.py`，完成 `/api/health`。
2. 实现 `POST /api/runs`、内存 `RUNS`、后台线程执行。
3. 实现 `GET /api/runs/{run_id}`。
4. 实现 export markdown/json。
5. 补 `tests/test_api.py`。
6. 前端增加 API client 和 Vite proxy。
7. 把“开始审查”改为真实 POST。
8. Run 页面轮询真实状态。
9. succeeded 状态使用真实 `snapshot` 渲染核心结果。
10. 跑后端 pytest 和前端 build。

## 验收标准

V1.3 完成后，应满足：

- 用户可以只通过浏览器发起一次真实 CodeCross 审查。
- 后端实际执行 `run_workflow`，不是前端模拟。
- Run 成功后能看到真实 evaluator risk、board summary、final report。
- 导出 MD/JSON 来自真实 workflow state。
- 所有现有后端测试通过。
- 新增 API 测试通过。
- 前端可以正常构建。

## 设计原则

- 先闭环，再实时。
- 先内存，再持久化。
- 先轮询，再 SSE。
- 先真实结果，再精细进度。
- 不在 V1.3 同时引入数据库、Abort、事件流和历史列表。
