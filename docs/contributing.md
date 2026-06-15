---
icon: lucide/heart
---

# Contributing

Contributions are welcome: bug fixes, new features, tests, and documentation improvements. This page
explains the conventions the project follows so your changes fit in and pass CI on the first try.

## Set up a development environment

Follow [Getting started](getting-started/index.md). The short version: run `scripts/dev-setup.sh`
(or open the project in a Dev Container) and you get a virtual environment, migrations, test users,
fake data, Mailpit, and a running dev server.

## Code style

- **Python 3.14.** Use modern syntax and features. No need to support older versions.
- **100-column width** for code, comments, and docstrings. Ruff enforces this.
- **Plain, simple language** in comments and docstrings. Explain the _why_, not the _what_.
- **Small, focused functions.** If a function grows complex (deep nesting, many branches, hard to
    test), refactor before adding more to it. Extract helpers, split responsibilities.

Tool configuration lives in
[pyproject.toml](https://github.com/PioneersHub/pyconde-talks/blob/main/pyproject.toml): ruff
(linting and formatting), zuban (strict type checking with django-stubs), djlint (Django templates),
bandit (security), and coverage.

## Tests

Add or update tests whenever you change behavior. The coverage target is **90%+**.

```bash
uv run pytest                  # full suite, random order, coverage report
uv run pytest -k test_name     # a single test
```

Coverage is written to the terminal and to `reports/lcov.info` and `reports/coverage.xml`. The suite
covers models, views, admin, management commands, template tags, validators, and utilities; new code
should follow that pattern.

## Pre-commit hooks

The project uses [prek](https://github.com/j178/prek), a drop-in replacement for pre-commit. Run all
hooks before committing:

```bash
uv run ruff check --fix .   # lint + auto-fix
uv run ruff format .        # format
zuban check                 # strict type check
prek run -a                 # all pre-commit hooks
```

The hooks configured in
[.pre-commit-config.yaml](https://github.com/PioneersHub/pyconde-talks/blob/main/.pre-commit-config.yaml):

| Hook                         | What it does                                                              |
| ---------------------------- | ------------------------------------------------------------------------- |
| `pre-commit-hooks`           | File size, syntax (TOML/YAML), debug statements, line endings, whitespace |
| `uv-lock`                    | Keeps `uv.lock` in sync with `pyproject.toml`                             |
| `ruff-check` / `ruff-format` | Lints (with `--fix`) and formats Python code                              |
| `djlint`                     | Reformats and lints Django templates                                      |
| `mdformat`                   | Formats Markdown (GitHub Flavored Markdown, frontmatter-aware)            |
| `cspell`                     | Spell checks changed files and the commit message                         |
| `bandit`                     | Security scan, configured via `pyproject.toml`                            |
| `pyupgrade`                  | Upgrades syntax to Python 3.14+ idioms                                    |
| `renovate-config-validator`  | Validates `renovate.json5` so dependency updates do not silently break    |
| `zuban`                      | Strict type check (mypy-compatible, with django-stubs)                    |

!!! warning "Do not skip hooks"

    Never commit with `--no-verify`. If a hook fails, fix the cause. Likewise, do not silence lint or
    type findings just to make CI pass; silence only with a documented reason when a finding clearly
    does not apply.

## Commits

- **Small, atomic commits.** One logical change per commit: a single fix, one refactor, one file's
    worth of related edits. Do not batch many unrelated edits into one large commit.
- **Commit as you go.** Run the pre-commit checks and commit each discrete change before moving on
    to the next one.
- **Clear messages.** Short imperative subject ("Fix schedule grid overflow", not "Fixed" or
    "Fixes"), with a body that explains the _why_.

## Pull requests and CI

Opening a pull request triggers the
[`ci` workflow](https://github.com/PioneersHub/pyconde-talks/blob/main/.github/workflows/ci.yml),
which calls the reusable
[`checks` workflow](https://github.com/PioneersHub/pyconde-talks/blob/main/.github/workflows/checks.yml).
Superseded runs on the same PR are cancelled automatically to save minutes.

The quality gate runs, in order:

1. `uv sync --all-groups --locked --no-build` - install dev, test, and prod dependencies from the
    lockfile; every dependency must ship a prebuilt wheel
2. `uv run ruff check` - lint
3. `uv run ruff format --check` - formatting check
4. `uv run zuban check` - strict type check
5. `uv run pytest` - test suite (CI-safe defaults: SQLite, plain static files storage)

Deploys never run from pull requests. The same `checks` workflow is also called by the deploy
pipeline before any image is built, so only green code reaches production. See
[Deployment](deployment/index.md) for how releases work.

!!! tip

    Run `prek run -a` and `uv run pytest` locally before pushing. They cover everything CI checks, so a
    green local run almost always means a green PR.
