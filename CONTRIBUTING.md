# Contributing

Contributions are welcome! This document outlines how to get involved.

## Reporting issues

- Check the [existing issues](https://github.com/elxecutor/probe/issues) first to avoid duplicates.
- Describe the expected behavior, the actual behavior, and steps to reproduce.
- Include relevant logs (with secrets redacted) and the commit or workflow run where the issue occurred.

## Submitting changes

1. Fork the repository and create a branch from `main`.
2. Make your change. Keep commits focused and use clear commit messages.
3. Verify the code compiles: `python -m py_compile <changed files>`.
4. Open a pull request against `main`.

> [!NOTE]
> All changes go through pull requests — direct commits to `main` are not permitted. Use a `feature/`, `fix/`, or `docs/` branch prefix.

## Project structure

```
engager.py          # CLI + autopilot loop + preflight + digest
engines.py          # Reply / quote / notification / content cycles
llm.py              # Groq helpers: generation, scoring, safety, niche checks
state.py            # Persistent state: dedup logs, daily caps, heartbeats
x_client.py         # X API client (GraphQL + REST)
phoenix_scorer.py   # Phoenix engagement model wrapper
```

## Development setup

```bash
git clone https://github.com/elxecutor/probe.git && cd probe
cp .env.example .env   # add your X cookies and Groq key
pip install -r requirements.txt
```

Use `python engager.py --once --dry-run` to preview actions without posting.

## Coding conventions

- Follow the existing style (type hints on public functions, module-level `log = logging.getLogger(__name__)`).
- Keep functions focused; extract shared logic rather than duplicating.
- Do not commit secrets, `state.json`, logs, or the Phoenix model artifacts — these are all git-ignored.

## Code of conduct

By participating, you are expected to uphold the [Code of Conduct](CODE_OF_CONDUCT.md).
