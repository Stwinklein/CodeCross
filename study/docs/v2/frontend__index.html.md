# `frontend/index.html` 解析

## 文件作用
Vite 前端的 HTML 宿主页，提供 React 应用挂载点和模块入口。

- 源码行数：12

## 结构说明
- `<head>` 设置字符集、viewport 和页面标题。
- `<div id="root"></div>` 是 React 应用挂载点。
- `<script type="module" src="/src/main.tsx"></script>` 交给 Vite 加载前端入口。

## 函数与类
这个 HTML 文件没有函数或类，职责是提供应用启动所需的 DOM 壳。
