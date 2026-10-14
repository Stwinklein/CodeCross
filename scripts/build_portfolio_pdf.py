"""Build the four-page, evidence-labelled CodeCross recruiting handout."""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output" / "pdf" / "CodeCross_项目展示_2026-10-14.pdf"
FONT_CANDIDATES = (
    Path("C:/Windows/Fonts/simhei.ttf"),
    Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
)
PAGE_W, PAGE_H = A4
NAVY = colors.HexColor("#10243A")
INK = colors.HexColor("#18364D")
TEAL = colors.HexColor("#087F80")
MINT = colors.HexColor("#E8F5F2")
PALE = colors.HexColor("#F2F6FA")
GOLD = colors.HexColor("#E4A83C")
MUTED = colors.HexColor("#5F7283")
WHITE = colors.white


def register_fonts() -> None:
    for path in FONT_CANDIDATES:
        if path.exists():
            pdfmetrics.registerFont(TTFont("PortfolioCN", str(path)))
            return
    raise FileNotFoundError("A Chinese TrueType font is required to build the portfolio PDF")


def text(c: canvas.Canvas, x: float, y: float, value: str, size: int = 10,
         color=INK, font: str = "PortfolioCN") -> None:
    c.setFillColor(color)
    c.setFont(font, size)
    c.drawString(x, y, value)


def wrap(value: str, width: float, size: int, font: str = "PortfolioCN") -> list[str]:
    result: list[str] = []
    for paragraph in value.split("\n"):
        if not paragraph:
            result.append("")
            continue
        current = ""
        for character in paragraph:
            candidate = current + character
            if current and pdfmetrics.stringWidth(candidate, font, size) > width:
                result.append(current.rstrip())
                current = character.lstrip()
            else:
                current = candidate
        if current:
            result.append(current.rstrip())
    return result


def para(c: canvas.Canvas, x: float, top: float, value: str, width: float,
         size: int = 10, leading: float = 17, color=INK) -> float:
    y = top
    for line in wrap(value, width, size):
        if line:
            text(c, x, y, line, size, color)
        y -= leading
    return y


def rounded(c: canvas.Canvas, x: float, y: float, w: float, h: float,
            fill=PALE, stroke=None, radius: float = 12) -> None:
    c.setFillColor(fill)
    c.setStrokeColor(stroke or fill)
    c.roundRect(x, y, w, h, radius, fill=1, stroke=int(stroke is not None))


def arrow(c: canvas.Canvas, x1: float, y1: float, x2: float, y2: float,
          color=TEAL, width: float = 2) -> None:
    from math import atan2, cos, pi, sin

    c.setStrokeColor(color)
    c.setFillColor(color)
    c.setLineWidth(width)
    c.line(x1, y1, x2, y2)
    angle = atan2(y2 - y1, x2 - x1)
    length = 8
    c.line(x2, y2, x2 - length * cos(angle - pi / 6), y2 - length * sin(angle - pi / 6))
    c.line(x2, y2, x2 - length * cos(angle + pi / 6), y2 - length * sin(angle + pi / 6))


def footer(c: canvas.Canvas, page: int) -> None:
    c.setStrokeColor(colors.HexColor("#D8E2E8"))
    c.line(40, 42, PAGE_W - 40, 42)
    text(c, 40, 26, "github.com/Stwinklein/CodeCross", 9, MUTED)
    c.linkURL("https://github.com/Stwinklein/CodeCross", (40, 20, 270, 40), relative=0)
    text(c, PAGE_W - 86, 26, f"{page} / 4", 9, MUTED)


def section_header(c: canvas.Canvas, number: str, title: str, subtitle: str) -> None:
    rounded(c, 40, 762, 42, 37, MINT, radius=10)
    text(c, 51, 774, number, 13, TEAL)
    text(c, 96, 779, title, 19, NAVY)
    text(c, 96, 759, subtitle, 9, MUTED)


def metric(c: canvas.Canvas, x: float, y: float, w: float,
           number: str, label: str, sub: str) -> None:
    rounded(c, x, y, w, 96, WHITE, colors.HexColor("#DCE8EA"), 12)
    text(c, x + 16, y + 58, number, 27, TEAL)
    text(c, x + 16, y + 35, label, 10, INK)
    text(c, x + 16, y + 16, sub, 8, MUTED)


def page_one(c: canvas.Canvas) -> None:
    c.setFillColor(NAVY)
    c.rect(0, 565, PAGE_W, PAGE_H - 565, stroke=0, fill=1)
    c.setFillColor(TEAL)
    c.roundRect(40, 760, 210, 25, 9, stroke=0, fill=1)
    text(c, 52, 768, "AI APPLICATION PORTFOLIO", 9, WHITE)
    text(c, 40, 685, "CodeCross", 36, WHITE)
    text(c, 40, 643, "多 Agent PR 代码评审与证据验证", 18, WHITE)
    para(c, 40, 607, "从代码变更理解、独立评审、问题合并，到隔离测试与可审计评测的端到端 AI 应用。",
         485, 10, 17, colors.HexColor("#DDEBF0"))

    metric(c, 40, 465, 161, "2", "默认独立 Reviewer", "逻辑 + 安全/可维护性")
    metric(c, 217, 465, 161, "4", "真实 Docker 集成测试", "2026-10-14 GitHub CI")
    metric(c, 394, 465, 161, "350", "PR 数据记录", "SWE-PRBench 适配")

    text(c, 40, 422, "项目解决什么问题", 15, NAVY)
    para(c, 40, 397,
         "普通 AI 评审容易把猜测当成缺陷，也可能在验证阶段直接执行不可信的生成测试。CodeCross 将评审结论、验证证据和不可测试状态分开，并为生成测试设置默认 Docker 隔离边界。",
         515, 10, 18)
    text(c, 40, 319, "可展示的能力", 15, NAVY)
    for y, title, body in (
        (279, "多 Agent 协作", "LangGraph 条件路由、双 Reviewer、Review Board 去重与筛选"),
        (226, "安全工具执行", "临时副本、断网、非 root、资源限额；不可用时 fail closed"),
        (173, "工程化交付", "双语报告、GitHub PR 评论、CI 与固定任务清单评测"),
    ):
        rounded(c, 40, y - 14, 515, 43, PALE, radius=9)
        text(c, 53, y + 8, title, 10, TEAL)
        text(c, 160, y + 8, body, 9, INK)
    rounded(c, 40, 75, 515, 55, MINT, radius=10)
    para(c, 53, 111,
         "状态说明：已验证工程实现与隔离边界；暂无官方 SWE-PRBench 榜分或与商业 Reviewer 的同协议对照。",
         486, 9, 15, INK)
    footer(c, 1)
    c.showPage()


def node(c: canvas.Canvas, x: float, y: float, w: float, h: float,
         title: str, detail: str, fill=PALE, title_color=NAVY) -> None:
    rounded(c, x, y, w, h, fill, radius=10)
    text(c, x + 12, y + h - 24, title, 11, title_color)
    para(c, x + 12, y + h - 45, detail, w - 24, 8.3, 14, MUTED)


def page_two(c: canvas.Canvas) -> None:
    section_header(c, "01", "系统架构", "从 PR 输入到有证据状态的双语输出")
    node(c, 40, 660, 515, 65, "PR diff + 聚焦上下文", "冻结输入 / 相关源文件 / 变更位置")
    arrow(c, 297, 660, 297, 641)
    node(c, 166, 574, 263, 67, "Planner + Context Retriever", "确定风险区域与审阅重点；获取必要上下文", MINT, TEAL)
    arrow(c, 240, 574, 166, 550)
    arrow(c, 355, 574, 429, 550)
    node(c, 40, 466, 242, 84, "逻辑 Reviewer", "业务逻辑、边界条件、行为回归")
    node(c, 313, 466, 242, 84, "安全 / 可维护性 Reviewer", "安全隐患、约束与长期维护风险")
    arrow(c, 166, 466, 226, 446)
    arrow(c, 429, 466, 369, 446)
    node(c, 166, 365, 263, 82, "Review Board", "结构化 findings、去重、Top-K 与验证状态追踪", MINT, TEAL)

    arrow(c, 242, 365, 150, 335)
    arrow(c, 353, 365, 445, 335)
    node(c, 40, 248, 228, 87, "可选证据验证", "生成测试 -> Docker 执行 -> 证据状态")
    node(c, 327, 248, 228, 87, "风险评估与报告", "高风险二次复核 -> 中英双语报告 / PR 评论")
    arrow(c, 268, 278, 327, 278)

    rounded(c, 40, 86, 515, 137, NAVY, radius=12)
    text(c, 56, 194, "Docker 执行边界", 13, WHITE)
    para(c, 56, 168,
         "仅复制安全源文件到临时工作区；原始仓库、.env、Git 元数据和主机密钥不挂载。容器默认断网、非 root、只读根文件系统，并限制 CPU、内存、进程数、运行时长和输出大小。",
         483, 9, 16, colors.HexColor("#EAF3F5"))
    para(c, 56, 111,
         "Docker / 镜像 / 依赖不可用时返回 not_testable，不会静默回退到宿主机。",
         483, 9, 15, colors.HexColor("#B8ECE4"))
    footer(c, 2)
    c.showPage()


def page_three(c: canvas.Canvas) -> None:
    section_header(c, "02", "技术实现与示例", "可控编排、可解释状态、可复现实验")
    cards = (
        (40, "编排与状态", "LangGraph TypedDict 状态 + 条件边；Reviewer 并行收集，Board 决定是否继续验证或进入评估。"),
        (216, "验证与安全", "生成测试经静态检查后在 Docker 临时副本运行；测试结果区分通过、失败、超时和不可测试。"),
        (392, "评测与溯源", "固定 manifest 分母；保留评论编号、原始输出与缺失/失败原因；导出官方 AgentOutput 结构。"),
    )
    for x, title, body in cards:
        rounded(c, x, 555, 163, 170, PALE, radius=11)
        rounded(c, x + 12, 681, 34, 27, MINT, radius=7)
        text(c, x + 18, 689, "AI", 10, TEAL)
        text(c, x + 12, 648, title, 12, NAVY)
        para(c, x + 12, 620, body, 139, 9, 16)

    text(c, 40, 515, "示例：把评审猜测变成可复核证据", 15, NAVY)
    rounded(c, 40, 407, 515, 85, NAVY, radius=10)
    text(c, 56, 464, "def safe_divide(a, b):", 12, WHITE, "Courier")
    text(c, 56, 441, "    return a / b", 12, WHITE, "Courier")
    text(c, 355, 439, "输入 b = 0", 10, colors.HexColor("#B8ECE4"))
    arrow(c, 297, 406, 297, 380)

    rounded(c, 40, 257, 515, 120, MINT, radius=10)
    text(c, 56, 348, "早期端到端演示中的证据链", 12, TEAL)
    para(c, 56, 321,
         "两个独立 Reviewer 指出零除风险 -> 生成测试观察到 ZeroDivisionError -> 报告将该问题标为高风险并建议阻止合并；另一些未经失败证据支持的类型问题被降权。",
         482, 9.5, 17)
    para(c, 56, 275,
         "这是仓库里的历史示例报告，不是 SWE-PRBench 官方样本；当前 v1.7 已将生成测试改为默认 Docker 隔离执行。",
         482, 8.5, 15, MUTED)

    text(c, 40, 218, "设计重点", 13, NAVY)
    para(c, 40, 191,
         "证据不足时保留“不确定”而非强行下结论；review_only 基准模式与真实测试执行分离，防止把 Reviewer 分数误写成沙箱或证据验证能力。",
         515, 9.5, 17)
    rounded(c, 40, 91, 515, 55, PALE, radius=9)
    para(c, 53, 125,
         "实现索引：codecross/workflow.py · codecross/sandbox.py · codecross/evaluation/ · tests/",
         490, 8.5, 15, MUTED)
    footer(c, 3)
    c.showPage()


def page_four(c: canvas.Canvas) -> None:
    section_header(c, "03", "验证结果与对标边界", "数字附带口径；未完成的实验明确标注")
    metric(c, 40, 620, 161, "198", "单元测试通过", "CI：另有 1 项跳过")
    metric(c, 217, 620, 161, "4", "Docker 测试通过", "CI：真实 Linux 容器")
    metric(c, 394, 620, 161, "350", "唯一 PR task_id", "官方 prs.jsonl 数据")
    text(c, 40, 596, "2026-10-14 GitHub Actions 两项作业均成功；容器测试覆盖隔离、Node、PID 限额与超时。", 8, MUTED)
    c.linkURL("https://github.com/Stwinklein/CodeCross/actions/runs/38572612331", (40, 587, 555, 610), relative=0)

    text(c, 40, 555, "评测协议：为公平比较准备什么", 14, NAVY)
    stages = (
        (40, "固定任务清单", "manifest / 版本"),
        (173, "全量结果记账", "空输出 / 失败 / 缺失"),
        (306, "官方结构导出", "AgentOutput / IDs"),
        (439, "官方评分", "待正式执行"),
    )
    for x, title, detail in stages:
        rounded(c, x, 475, 116, 62, MINT if x < 439 else PALE, radius=8)
        text(c, x + 9, 510, title, 9, TEAL if x < 439 else MUTED)
        text(c, x + 9, 490, detail, 7.5, MUTED)
    for x in (158, 291, 424):
        arrow(c, x, 505, x + 12, 505)

    rounded(c, 40, 306, 515, 138, colors.HexColor("#FFF7EA"), radius=10)
    text(c, 56, 415, "历史 15 PR 探索性诊断 - 非官方榜分", 12, colors.HexColor("#936014"))
    para(c, 56, 386,
         "旧版自定义 Judge 口径：15 PR / 78 条人工评论；最终 75 条模型评论，其中 9 条命中人工评论、7 条被判为捏造；逐 PR 宏平均 DR 15.2%、FPR 7.78%。",
         482, 9, 17)
    para(c, 56, 332,
         "该结果揭示漏报与空输出问题；旧筛选和 Judge 与新版不一致，不能外推为当前性能，更不能与 CodeRabbit 等商业产品直接比较。",
         482, 8.5, 15, MUTED)

    text(c, 40, 274, "下一步：同协议市场对标", 14, NAVY)
    para(c, 40, 247,
         "在同一 PR 集合、同一提交、同一裁判下运行 CodeCross 与可取得结果的其他 Reviewer，同时报告检出、误报、有效运行率、每 PR 评论数、成本和时延。先完成 100 PR 正式对照，再讨论优劣。",
         515, 9.5, 17)
    rounded(c, 40, 87, 515, 89, PALE, radius=10)
    text(c, 53, 150, "证据位置", 10, TEAL)
    para(c, 53, 130,
         "隔离测试：tests/test_sandbox_docker_integration.py\n评测适配：codecross/evaluation/swe_prbench.py\n历史诊断：docs/evaluation-diagnosis-v025.md",
         489, 8.5, 15, MUTED)
    footer(c, 4)
    c.showPage()


def main() -> None:
    register_fonts()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(OUTPUT), pagesize=A4, pageCompression=1)
    c.setTitle("CodeCross - AI 应用能力作品展示")
    c.setAuthor("Stwinklein")
    c.setSubject("Multi-agent PR code review, Docker isolation and auditable evaluation")
    for page in (page_one, page_two, page_three, page_four):
        page(c)
    c.save()
    print(OUTPUT)


if __name__ == "__main__":
    main()
