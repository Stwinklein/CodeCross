# CodeCross v0.25：15 PR 离线诊断

本页只复算已有的本地 JSON，不重新调用模型或 Judge。来源为被 `.gitignore` 排除的 `.codecross/swe-prbench-core-v025-development.json` 与 `...-raw.json`。历史文件使用旧版 CodeCross 自定义 Judge/筛选口径，**不是 SWE-PRBench 官方榜分**；新版适配器已改为优先使用官方 `substantive_comment_ids`，后续分数必须重新生成，不能与本页直接比较。

| 项目 | 历史观察 |
| --- | ---: |
| PR / human comments | 15 / 78 |
| raw / final 模型评论 | 104 / 75 |
| final 命中的人工评论 / FABRICATED 评论 | 9 / 7 |
| 无 final 评论的 PR | 2 / 15 |
| 没命中人工评论的 PR | 8 / 15 |
| 逐 PR 宏平均 DR / FPR | 0.152088 / 0.077778 |

| PR | 人工评论 | raw → final 评论 | raw → final 命中 | final FABRICATED |
| --- | ---: | ---: | ---: | ---: |
| autogluon__5586 | 2 | 9 → 6 | 1 → 1 | 0 |
| chia-blockchain__20099 | 2 | 6 → 6 | 0 → 0 | 6 |
| dask__12221 | 3 | 8 → 6 | 0 → 0 | 0 |
| deepchem__4601 | 1 | 10 → 6 | 0 → 0 | 0 |
| kale__593 | 5 | 9 → 6 | 4 → 3 | 0 |
| kedro__5334 | 7 | 0 → 0 | 0 → 0 | 0 |
| nessie__11676 | 4 | 7 → 6 | 0 → 1 | 0 |
| openbao__1906 | 1 | 9 → 6 | 0 → 0 | 0 |
| pip__13482 | 4 | 8 → 6 | 0 → 0 | 0 |
| pipecat__3598 | 3 | 0 → 0 | 0 → 0 | 0 |
| prowler__9606 | 7 | 3 → 3 | 0 → 0 | 0 |
| qutebrowser__8845 | 4 | 9 → 6 | 1 → 1 | 0 |
| spyder__24990 | 26 | 11 → 6 | 0 → 1 | 0 |
| stylelint__8953 | 7 | 7 → 6 | 1 → 1 | 0 |
| zod__5672 | 2 | 8 → 6 | 1 → 1 | 1 |

优先排查三处：`kedro` 和 `pipecat` 为什么完全无输出；`chia-blockchain` 为什么 6 条评论均被旧 Judge 判为 FABRICATED；`kale` 的 Top-K/去重是否误删可命中的问题。raw 与 final 分别重新调用 Judge，出现 `0 → 1` 不表示后处理创造了新问题，而可能是 Judge 波动；必须用同一批 finding ID 和人工复核确认因果。

下一次正式报告应使用冻结的 manifest、官方数据筛选与官方评测工具，逐 PR 保留评论 ID、阶段 lineage、缺失任务、空输出和运行失败。未运行 100 PR 或官方 Judge 前，不发布与市场产品的数值比较。
