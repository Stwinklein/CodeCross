# `examples/sample_project/math_utils.py` 解析

## 文件作用
极小示例业务文件，包含故意脆弱的除法函数，用于演示 CodeCross 如何发现零分母风险。

- 源码行数：4

## 函数与类

### `safe_divide`（函数，第 1 行）
- 签名：`def safe_divide(a, b)`
- 作用：故意保留的 demo bug：b 为 0 时会抛 ZeroDivisionError。

## 学习提示
先看文件作用和公开函数，再顺着调用关系回到核心 workflow，会更容易理解它在项目里的位置。
