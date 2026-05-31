# CodeCross V1 源码学习文档

这个目录按 `codecross/` 正式实现逐文件拆解。建议阅读顺序：

1. `schemas.md`：先看系统里的数据对象长什么样。
2. `config.md`：再看模型角色和运行配置怎么组织。
3. `llm.md`：理解多模型调用为什么需要适配层。
4. `agents.md`：看每个 Agent 怎么组 prompt、拿结构化输出。
5. `board.md`：理解 CodeCross 最核心的证据黑板。
6. `executor.md`：看测试如何被真实执行。
7. `workflow.md`：最后看 LangGraph 怎么把所有模块串起来。
8. `main.md`：看 CLI 怎么启动整个工作流。
9. `__init__.md`：了解包级导出。

## V1 总体心智模型

```text
main.py
  读取 diff / config
  -> workflow.py
       编译 LangGraph
       -> agents.py
            Planner / Reviewers / Test Agents / Evaluator
            -> llm.py
                 统一模型调用和 JSON schema 校验
       -> executor.py
            真实运行 pytest
       -> board.py
            管理 evidence / verification status / loop 停止条件
       -> schemas.py
            所有结构化对象的契约
```

CodeCross V1 的关键不是“让多个 Agent 聊天”，而是：

```text
多个 Reviewer 独立提出风险假设
Board 用规则管理证据状态
Test Runner 真实执行测试产生证据
Evaluator 基于 Final Board 做风险裁决
```

