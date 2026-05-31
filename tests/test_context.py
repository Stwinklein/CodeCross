"""Context Retriever 测试。

V1.2 的关键能力是让 reviewer 看见真实代码上下文，而不是只看 diff。
这里用一个小项目验证 changed file、symbol definition、call site、existing test
都会被检索出来。
"""

from pathlib import Path

from codecross.config import load_project_config
from codecross.context import retrieve_code_context
from codecross.schemas import ReviewPlan


def test_context_retriever_collects_changed_file_symbol_calls_and_tests(tmp_path: Path):
    """Context Retriever 应围绕 changed_symbols 收集最关键的客观上下文。"""

    package = tmp_path / "pkg"
    tests = tmp_path / "tests"
    package.mkdir()
    tests.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "math_utils.py").write_text(
        "def safe_divide(a, b):\n"
        "    if b == 0:\n"
        "        return None\n"
        "    return a / b\n",
        encoding="utf-8",
    )
    (package / "consumer.py").write_text(
        "from pkg.math_utils import safe_divide\n\n"
        "def ratio_or_zero(a, b):\n"
        "    result = safe_divide(a, b)\n"
        "    return result or 0\n",
        encoding="utf-8",
    )
    (tests / "test_math_utils.py").write_text(
        "from pkg.math_utils import safe_divide\n\n"
        "def test_safe_divide_zero():\n"
        "    assert safe_divide(10, 0) is None\n",
        encoding="utf-8",
    )

    config = load_project_config(tmp_path, mock_llm=True)
    plan = ReviewPlan(
        changed_files=["pkg/math_utils.py"],
        changed_symbols=["safe_divide"],
    )

    context = retrieve_code_context(
        diff="diff --git a/pkg/math_utils.py b/pkg/math_utils.py\n",
        project_root=tmp_path,
        review_plan=plan,
        config=config,
    )

    kinds = {snippet.kind for snippet in context.snippets}
    files = {snippet.file_path for snippet in context.snippets}

    assert "changed_file" in kinds
    assert "symbol_definition" in kinds
    assert "call_site" in kinds
    assert "existing_test" in kinds
    assert "pkg/math_utils.py" in files
    assert "pkg/consumer.py" in files
    assert "tests/test_math_utils.py" in files
    assert context.missing_files == []


def test_context_retriever_collects_javascript_symbols_calls_and_tests(tmp_path: Path):
    """JS/Vue projects should get the same core context shape as Python repos."""

    source = tmp_path / "src"
    source.mkdir()
    (source / "auth.js").write_text(
        "export function normalizeAuthKeys(auth) {\n"
        "    return Array.isArray(auth) ? auth : [];\n"
        "}\n",
        encoding="utf-8",
    )
    (source / "consumer.js").write_text(
        "import { normalizeAuthKeys } from './auth.js';\n\n"
        "export function firstToken(config) {\n"
        "    return normalizeAuthKeys(config.authKeys)[0]?.token || '';\n"
        "}\n",
        encoding="utf-8",
    )
    (source / "auth.test.js").write_text(
        "import { normalizeAuthKeys } from './auth.js';\n\n"
        "test('normalizes arrays', () => {\n"
        "    expect(normalizeAuthKeys([{ token: 'abc' }])).toHaveLength(1);\n"
        "});\n",
        encoding="utf-8",
    )

    config = load_project_config(tmp_path, mock_llm=True)
    plan = ReviewPlan(
        changed_files=["src/auth.js"],
        changed_symbols=["normalizeAuthKeys"],
    )

    context = retrieve_code_context(
        diff="diff --git a/src/auth.js b/src/auth.js\n",
        project_root=tmp_path,
        review_plan=plan,
        config=config,
    )

    snippets = {(snippet.kind, snippet.file_path, snippet.symbol) for snippet in context.snippets}

    assert ("changed_file", "src/auth.js", None) in snippets
    assert ("symbol_definition", "src/auth.js", "normalizeAuthKeys") in snippets
    assert ("call_site", "src/consumer.js", "normalizeAuthKeys") in snippets
    assert ("existing_test", "src/auth.test.js", "normalizeAuthKeys") in snippets


def test_context_retriever_includes_changed_hunks_for_large_javascript_files(tmp_path: Path):
    """Large JS files should still expose the changed hunk to reviewers."""

    source = tmp_path / "src"
    source.mkdir()
    filler = [f"// filler {index}" for index in range(250)]
    body = [
        "export async function parseRequest(data, options) {",
        "    const allowModelFallback = options.allowModelFallback || false;",
        "    return { data, allowModelFallback };",
        "}",
    ]
    (source / "parse.js").write_text("\n".join([*filler, *body]), encoding="utf-8")

    config = load_project_config(tmp_path, mock_llm=True)
    plan = ReviewPlan(
        changed_files=["src/parse.js"],
        changed_symbols=["parseRequest"],
    )
    diff = """diff --git a/src/parse.js b/src/parse.js
--- a/src/parse.js
+++ b/src/parse.js
@@ -251,4 +251,4 @@ export async function parseRequest(data, options) {
+    const allowModelFallback = options.allowModelFallback || false;
"""

    context = retrieve_code_context(
        diff=diff,
        project_root=tmp_path,
        review_plan=plan,
        config=config,
    )

    kinds = {snippet.kind for snippet in context.snippets}
    assert "changed_hunk" in kinds
    assert "symbol_definition" in kinds
