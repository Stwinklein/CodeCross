"""Context Retriever 测试。

V1.2 的关键能力是让 reviewer 看见真实代码上下文，而不是只看 diff。
这里用一个小项目验证 changed file、symbol definition、call site、existing test
都会被检索出来。
"""

from pathlib import Path

from codecross.config import load_project_config
from codecross.context import retrieve_code_context
from codecross.schemas import ReviewPlan
import codecross.context as context_module


def test_context_retriever_ignores_codecross_benchmark_cache(tmp_path: Path):
    """目标项目下的 CodeCross 缓存不能反向污染 Reviewer 上下文。"""

    (tmp_path / "app.py").write_text("def changed():\n    return 1\n", encoding="utf-8")
    cached = tmp_path / ".codecross" / "benchmark_repositories"
    cached.mkdir(parents=True)
    (cached / "foreign.py").write_text(
        "def consumer():\n    return changed()\n", encoding="utf-8"
    )
    config = load_project_config(
        tmp_path, mock_llm=True, diff="diff --git a/app.py b/app.py\n"
    )
    context = retrieve_code_context(
        diff="diff --git a/app.py b/app.py\n",
        project_root=tmp_path,
        review_plan=ReviewPlan(changed_files=["app.py"], changed_symbols=["changed"]),
        config=config,
    )

    assert all(".codecross" not in item.file_path for item in context.snippets)


def test_context_budget_prioritizes_changed_code_over_large_manifest(monkeypatch, tmp_path: Path):
    """字符预算不足时必须保留变更代码，优先丢弃低优先级 manifest。"""

    monkeypatch.setattr(context_module, "MAX_CONTEXT_CHARS", 120)
    (tmp_path / "package.json").write_text("x" * 500, encoding="utf-8")
    source = tmp_path / "src"; source.mkdir()
    (source / "small.ts").write_text("export function changed() { return 1; }\n", encoding="utf-8")
    diff = "diff --git a/src/small.ts b/src/small.ts\n"
    config = load_project_config(tmp_path, mock_llm=True, diff=diff)
    result = retrieve_code_context(
        diff=diff, project_root=tmp_path,
        review_plan=ReviewPlan(changed_files=["src/small.ts"], changed_symbols=["changed"]),
        config=config,
    )

    assert any(item.kind == "changed_file" for item in result.snippets)
    assert not any(item.kind == "project_manifest" for item in result.snippets)
    assert any("Context budget kept" in note for note in result.summary_notes)


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


def test_context_retriever_collects_go_calls_tests_and_manifest(tmp_path: Path):
    """Go 项目应获得定义、调用点、测试和 go.mod，而不是只看到 diff。"""

    (tmp_path / "go.mod").write_text("module example.com/demo\n\ngo 1.23\n", encoding="utf-8")
    source = tmp_path / "service"
    source.mkdir()
    (source / "handler.go").write_text(
        "package service\n\nfunc HandleRequest(id string) error {\n    return nil\n}\n",
        encoding="utf-8",
    )
    (source / "router.go").write_text(
        "package service\n\nfunc route() error { return HandleRequest(\"x\") }\n",
        encoding="utf-8",
    )
    (source / "handler_test.go").write_text(
        "package service\n\nfunc TestHandle(t *testing.T) { _ = HandleRequest(\"x\") }\n",
        encoding="utf-8",
    )
    config = load_project_config(
        tmp_path,
        mock_llm=True,
        diff="diff --git a/service/handler.go b/service/handler.go\n",
    )
    plan = ReviewPlan(
        changed_files=["service/handler.go"], changed_symbols=["HandleRequest"]
    )

    context = retrieve_code_context(
        diff="diff --git a/service/handler.go b/service/handler.go\n",
        project_root=tmp_path,
        review_plan=plan,
        config=config,
    )

    snippets = {(item.kind, item.file_path) for item in context.snippets}
    assert ("project_manifest", "go.mod") in snippets
    assert ("symbol_definition", "service/handler.go") in snippets
    assert ("call_site", "service/router.go") in snippets
    assert ("existing_test", "service/handler_test.go") in snippets


def test_context_retriever_collects_ruby_definition_call_and_spec(tmp_path: Path):
    (tmp_path / "Gemfile").write_text("source 'https://rubygems.org'\n", encoding="utf-8")
    app = tmp_path / "app"; spec = tmp_path / "spec"
    app.mkdir(); spec.mkdir()
    (app / "service.rb").write_text(
        "class Service\n  def process(value)\n    value.to_s\n  end\nend\n", encoding="utf-8"
    )
    (app / "caller.rb").write_text("Service.new.process input\n", encoding="utf-8")
    (spec / "service_spec.rb").write_text("expect(Service.new.process(nil)).to eq('')\n", encoding="utf-8")
    diff = "diff --git a/app/service.rb b/app/service.rb\n"
    config = load_project_config(tmp_path, mock_llm=True, diff=diff)
    context = retrieve_code_context(
        diff=diff, project_root=tmp_path,
        review_plan=ReviewPlan(changed_files=["app/service.rb"], changed_symbols=["process"]),
        config=config,
    )

    snippets = {(item.kind, item.file_path) for item in context.snippets}
    assert ("project_manifest", "Gemfile") in snippets
    assert ("symbol_definition", "app/service.rb") in snippets
    assert ("call_site", "app/caller.rb") in snippets
    assert ("existing_test", "spec/service_spec.rb") in snippets
