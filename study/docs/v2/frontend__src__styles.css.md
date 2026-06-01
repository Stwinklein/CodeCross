# `frontend/src/styles.css` 解析

## 文件作用
前端全局样式文件，定义布局、表单、pipeline、finding 列表、抽屉、弹窗和响应式规则。

- 源码行数：1271
- 这个文件没有 JavaScript/Python 函数，主要通过 CSS selector 定义前端视觉行为。

## 样式模块
- ``:root` / 全局元素`：定义颜色、阴影、字体渲染、body 背景等全局基础。
- `.app-shell / .topbar / .workspace`：搭建应用整体框架、顶部栏和主工作区布局。
- `.hero-panel / .form-section / .source-tabs`：服务新建审查页面，包括 diff 输入、表单区域和数据源切换。
- `.reviewer-* / .advanced-grid`：控制 reviewer 配置、高级参数和模型连接编辑器。
- `.run-* / .status-line / .progress-*`：展示运行状态、当前步骤、进度条和操作按钮。
- `.pipeline-* / .pipeline-node`：绘制 workflow pipeline 节点、连接线和状态色。
- `.finding-* / .risk-* / .severity-*`：展示 finding 列表、严重度、风险摘要和证据入口。
- `.log-*`：展示 run 事件日志和状态图标。
- `.drawer-* / .modal-*`：控制详情抽屉和取消确认弹窗。
- `@media 响应式规则`：适配移动端和窄屏布局，避免面板和按钮溢出。

## 代表性选择器
- `:root`
- `box-sizing: border-box; } html`
- `min-width: 320px; background: var(--bg); } body`
- `font: inherit; } button`
- `color: inherit; } .app-shell`
- `min-height: 100vh; } .topbar`
- `width: min(1180px, calc(100vw - 32px)); margin: 22px auto 64px; } .page-surface`
- `margin-top: 0; } h1`
- `margin-bottom: 0; font-size: 26px; line-height: 1.15; } h2`
- `margin-bottom: 12px; font-size: 17px; line-height: 1.2; } h3`
- `margin: 20px 0 8px; font-size: 14px; color: var(--text); } .run-mode, .icon-note`
- `color: var(--muted); font-family: var(--code); font-size: 12px; } .form-section`
- `padding-bottom: 14px; } .section-index`
- `color: var(--faint); font-family: var(--code); font-size: 12px; } .section-body`
- `min-width: 0; } .source-tabs`
- `display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 10px; } .source-tabs button`
- `padding: 0 12px; } .source-tabs button.active`
- `opacity: 0.45; cursor: not-allowed; } .hidden-input`
- `display: none; } .diff-editor`
- `border-color: var(--blue); box-shadow: 0 0 0 3px var(--blue-soft); } .parse-status`
- `min-width: 0; overflow-wrap: anywhere; } .parse-status.ok`
- `color: var(--green); } .parse-status.error`
- `color: var(--red); } .api-error`
- `max-width: min(520px, 100%); margin-top: 0; margin-right: auto; } .path-row`
- `padding: 0 12px; } .path-row button`
- `margin: 7px 0 0; color: var(--muted); font-size: 12px; } .reviewer-list`
- `display: grid; gap: 8px; } .reviewer-row`
- `accent-color: var(--blue); } .reviewer-name, .reviewer-model`
- `color: var(--muted); } .advanced-toggle`
- `padding: 0 10px; } .advanced-grid`
- `display: grid; gap: 6px; color: var(--muted); font-size: 12px; } .reviewer-config-panel`
- `display: grid; gap: 12px; margin-top: 16px; } .reviewer-config-panel h3`
- `margin: 2px 0 0; font-size: 15px; } .reviewer-config-list`
- `display: grid; gap: 10px; } .reviewer-config-card`
- `color: var(--muted); font-size: 12px; } .reviewer-config-grid`
- `min-height: 28px; } .check-option`
- `overflow: hidden; } .history-error`
- `margin: 0 24px 12px; } .history-list`
- `display: grid; gap: 8px; padding: 0 24px 24px; } .history-row`
- `display: grid; gap: 3px; } .history-status`
- `color: var(--muted); } .button.danger`
- `opacity: 0.5; cursor: not-allowed; } .compact-button`
- `min-height: 34px; padding: 0 11px; font-size: 13px; } .abort-button`
- `width: min(1240px, calc(100vw - 32px)); } .run-heading`
- `align-items: center; } .run-actions`
- `display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; } .status-line`
- `color: var(--text); } .status-line span`
- `color: var(--blue); } .tone-orange svg, .log-kind.abort`
- `color: var(--orange); } .tone-green svg, .log-kind.done`
- `color: var(--green); } .tone-red svg, .log-kind.failed`
- `color: var(--red); } .progress-row`
- `padding: 20px 24px; border-bottom: 1px solid var(--line); } .current-step`
- `margin-bottom: 0; color: var(--muted); } .section-header`
- `margin-bottom: 0; } .step-meta`
- `color: var(--red); } .risk-copy`
- `display: grid; gap: 4px; } .risk-copy span`
- `color: var(--text); } .pipeline-scroller`
- `width: 100%; overflow-x: auto; padding-top: 14px; } .pipeline-stage`
- `overflow: hidden; text-overflow: ellipsis; white-space: nowrap; } .pipeline-node.done`
- `outline: 2px solid rgba(91, 141, 239, 0.45); outline-offset: 2px; } .filter-actions`
- 其余 39 个选择器见源码。

## 学习提示
读 CSS 时可以和 `frontend/src/App.tsx` 对照：先找页面结构 class，再回到这里看布局、状态色和响应式规则。
