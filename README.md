# CodeCross

CodeCross is a multi-agent code review workflow. It reads a pull request diff,
retrieves focused code context from the target repository, runs independent
reviewers, records evidence on a review board, and can post a stable summary
comment back to GitHub pull requests.

## Local Setup

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env
```

Fill `.env` with your local model credentials. The real `.env` file is ignored
by git on purpose.

## Run Locally

```bash
python -m codecross.main --diff examples/diffs/safe_divide.diff --project-root .
```

For a wiring-only run without real model calls:

```bash
python -m codecross.ci --diff examples/diffs/safe_divide.diff --project-root . --output-comment comment.md --mock-llm
```

## GitHub CI Secrets

Do not commit API keys. Add these values as GitHub repository secrets or
variables in the repository that runs CodeCross:

- `DEEPSEEK_API_KEY`
- `DEEPSEEK_BASE_URL`
- `DEEPSEEK_MODEL`
- `ANTHROPIC_API_KEY`
- `ANTHROPIC_BASE_URL`
- `ANTHROPIC_MODEL`
- `CODECROSS_MOCK_LLM` as a variable, only for CI wiring tests

Local execution reads `.env`; GitHub Actions reads Secrets and Variables. This
keeps the same runtime behavior without putting tokens in source control.
