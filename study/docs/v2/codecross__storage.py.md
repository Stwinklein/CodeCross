# `codecross/storage.py` 解析

## 文件作用
SQLite run 历史存储层，用标准库 sqlite3 保存和读取 API run 的完整 JSON 快照。

- 源码行数：134
- 主要依赖：`json`、`os`、`sqlite3`、`threading`、`pathlib`、`typing`

## 函数与类

### `SQLiteRunStore`（类，第 21 行）
- 签名：`class SQLiteRunStore`
- 作用：Small repository object around the standard-library sqlite3 module.
- 方法：
  - `__init__`（第 24 行）：模块内部辅助逻辑，配合相邻函数完成该文件的主流程。
  - `from_environment`（第 30 行）：Build the store from CODECROSS_RUN_DB or the local default path.
  - `_connect`（第 35 行）：Open a short-lived connection.
  - `_ensure_schema`（第 47 行）：Create the V1.5 schema if this is the first run on the machine.
  - `upsert_run`（第 68 行）：Insert or replace one full RunRecord JSON document.
  - `get_run`（第 92 行）：Load a full RunRecord JSON document by ID.
  - `list_runs`（第 104 行）：Return recent RunRecord JSON documents for the history page.
  - `clear`（第 130 行）：Delete all runs, used by tests to keep the suite deterministic.

## 学习提示
建议结合 `schemas.py` 中的对象一起读：这个文件的大多数函数都在接收或返回那些结构化对象。
