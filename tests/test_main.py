"""主 CLI 的参数安全契约测试。"""

import pytest

from codecross.main import parse_args, parse_test_command_override


def test_cli_accepts_explicit_test_command_override():
    """带空格的参数应保持为一个 argv 元素，而不是交给 shell 再解析。"""

    args = parse_args(["--test-command", 'gradle test --tests "Demo Test"'])

    assert parse_test_command_override(args.test_command) == [
        "gradle",
        "test",
        "--tests",
        "Demo Test",
    ]


@pytest.mark.parametrize("operator", ["&&", "||", "|", ">", ";"])
def test_cli_rejects_shell_control_operators(operator: str):
    """覆盖命令只能选择测试工具，不能借机获得 shell 管道能力。"""

    with pytest.raises(ValueError, match="不允许 shell"):
        parse_test_command_override(f"npm test {operator} whoami")

