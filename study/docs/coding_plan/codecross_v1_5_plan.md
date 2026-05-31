# CodeCross V1.5 计划：SQLite 持久化与 Run 历史

## 版本目标

V1.4 已经有真实 API、事件流和协作式终止，但 Run 仍主要保存在进程内存里。只要后端重启，浏览器里的 `/runs/{run_id}` 就无法恢复。V1.5 的目标是补上最关键的产品连续性：

1. Run 结束后能持久保存在 SQLite。
2. 后端重启或内存清空后，仍能通过 `run_id` 读取历史 Run。
3. 前端有一个最小历史列表，能从历史进入某次 Run。

## 核心改动

### 1. 新增 SQLite RunStore

新增文件：

```text
codecross/storage.py
```

职责：

- 初始化 SQLite schema。
- 保存完整 RunRecord JSON。
- 按 `run_id` 读取完整记录。
- 分页列出 Run summary。
- 测试中可以清空表。

V1.5 先用“完整 JSON 快照 + 少量索引字段”的方式，避免过早拆表：

```text
runs
- run_id TEXT PRIMARY KEY
- status TEXT
- created_at TEXT
- updated_at TEXT
- finished_at TEXT
- record_json TEXT
```

原因：

- 当前 RunRecord 结构还在快速迭代，JSON 持久化更抗变更。
- 历史列表只需要少量索引字段。
- V1.6 还会新增 Test Author Agent，过早规范化表结构会增加迁移成本。

### 2. API 接入持久化

`codecross/api.py`：

- 创建 Run 后立即写入 SQLite。
- 每次状态、事件、快照更新后 upsert。
- `_get_run_or_404` 先查内存，内存没有时从 SQLite 恢复。
- 新增：

```text
GET /api/runs?limit=20&status=succeeded
```

返回轻量 summary，不返回完整 diff 和 snapshot，避免历史列表太重。

### 3. 前端 Run 历史页

`frontend/src/App.tsx`：

- Header 的 Runs 按钮打开历史列表。
- 历史列表请求 `GET /api/runs?limit=20`。
- 每条显示：状态、risk、finding_count、项目路径、更新时间。
- 点击历史项进入 `/runs/{run_id}`，复用现有 Run 页面读取完整记录。

### 4. 测试计划

后端：

- 完成一次 Run 后，清空内存缓存，再通过 API 读取同一个 `run_id`，应从 SQLite 恢复。
- `GET /api/runs` 能列出刚完成的 Run。
- 测试 fixture 清理 SQLite 表，避免历史数据污染测试。

前端：

```text
npm.cmd run build
```

确保新增历史页类型、状态跳转和 API 类型正常。

## 不做内容

- 不做登录/用户隔离。
- 不做删除 Run。
- 不做复杂搜索。
- 不做数据库迁移框架。
- 不把所有 workflow 中间对象拆成多张表。

这些能力等产品形态稳定后再设计。

## 完成标准

- 后端重启或内存清空后，已保存 Run 仍可读取。
- `/api/runs` 返回历史 summary。
- 前端可以打开 Runs 历史页，并进入历史 Run。
- Python 测试和前端构建均通过。
