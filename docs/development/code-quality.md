---
icon: lucide/badge-check
---

# Code quality

The project enforces a consistent style and a strict type discipline. Most checks run as pre-commit
hooks and again in CI, so the fastest way to stay green is to let the hooks fix things as you
commit.

## The quick loop

Before committing, run these from the repository root:

```bash
uv run ruff check --fix .   # Lint + auto-fix
uv run ruff format .        # Format
zuban check                 # Strict type check (mypy-compatible, with django-stubs)
prek run -a                 # Run all pre-commit hooks (drop-in pre-commit replacement)
```

`prek run -a` is the catch-all: it runs every configured hook against every file. The individual
commands above are useful when you want to fix one category quickly without waiting on the full set.

## Ruff (lint and format)

[Ruff](https://docs.astral.sh/ruff/) is both the linter and the formatter. Configuration is in
`[tool.ruff]` in
[pyproject.toml](https://github.com/PioneersHub/pyconde-talks/blob/main/pyproject.toml):

- 100-column line length, 4-space indent, target Python 3.14.
- The lint rule set is `select = ["ALL"]`: every Ruff rule is on, with a short, documented ignore
    list (for example `D203`, `D212`, `ANN401`, and `COM812`, the last because it conflicts with the
    formatter).
- Migrations are excluded from linting.
- Per-file ignores relax the rules that do not make sense in tests (asserts, hardcoded passwords,
    private-member access, many fixture arguments) and in `__init__.py` files (missing module
    docstring).
- The formatter uses double quotes, space indentation, and LF line endings. Import sorting is
    handled by Ruff's isort rules (combined as-imports, two blank lines after imports).

```bash
uv run ruff check --fix .   # report and auto-fix lint findings
uv run ruff format .        # reformat
uv run ruff format --check . # check formatting without writing (what CI runs)
```

!!! note "Auto-fix is safe by default"

    `[tool.ruff]` sets `fix = true` and `unsafe-fixes = false`, so `--fix` only applies changes Ruff
    considers safe. If a finding needs an unsafe fix, Ruff reports it but leaves the code alone for you
    to handle.

## Zuban (type checking)

[Zuban](https://github.com/zubanls/zuban) is the type checker. It is mypy-compatible and runs in
strict mode with `django-stubs` so Django models, querysets, and settings are typed. `[tool.zuban]`
enables `strict`, `disallow_untyped_defs`, and `warn_unreachable`; the matching `[tool.mypy]` and
`[tool.django-stubs]` sections point the Django plugin at `event_talks.settings`.

```bash
zuban check
```

Every function needs type annotations. If the checker cannot follow a type, fix the annotation
rather than reaching for `# type: ignore`.

## Other hooks

The full set lives in
[.pre-commit-config.yaml](https://github.com/PioneersHub/pyconde-talks/blob/main/.pre-commit-config.yaml).
Beyond Ruff and Zuban it runs:

- **djLint** - lints and reformats Django templates. Profile `django`, 100-column width, 2-space
    indent (`[tool.djlint]` in `pyproject.toml`).
- **bandit** - security linter for the Python code, configured by `[tool.bandit]` (tests and the
    fake-data generator are excluded; asserts are allowed in tests).
- **cspell** - spell-checks changed files and the commit message. Add real project terms to the
    cspell dictionary rather than rephrasing around them.
- **mdformat** - formats Markdown. Two passes: the plain GitHub-flavored pass for top-level docs,
    and a separate `mdformat-mkdocs` pass for everything under `docs/` so admonitions, content tabs,
    and 4-space nested lists survive. See [Documentation](documentation.md).
- **uv-lock** - keeps `uv.lock` in sync with `pyproject.toml`.
- **pyupgrade** - rewrites code to Python 3.14+ idioms.
- **renovate-config-validator** - validates `renovate.json5` so a typo cannot silently break
    dependency updates.
- The standard pre-commit hygiene hooks: trailing whitespace, end-of-file newline, large-file guard,
    TOML/YAML syntax, and a debug-statement check.

!!! warning "Never skip hooks"

    Do not commit with `--no-verify`, and do not silence a lint, type, or SonarQube finding just to make
    a check pass. Fix the underlying issue. The only acceptable silencing is a documented, one-line
    reason for a finding that genuinely does not apply.

## SonarQube

Code quality is also tracked on [SonarCloud](https://sonarcloud.io/). The scanner configuration is
in
[sonar-project.properties](https://github.com/PioneersHub/pyconde-talks/blob/main/sonar-project.properties):
the organization, project key, host URL, and token come from environment variables, and the file
declares which paths are sources versus tests, what to exclude, and where to read the coverage,
Ruff, and bandit reports from (`reports/coverage.xml`, `reports/ruff_report.json`,
`reports/bandit_report.json`).

A handful of rules are deliberately ignored with documented reasons, for example the
hardcoded-password rule in test files and the Docker tag-plus-digest rule (both the tag and the
digest are pinned on purpose).

The local pipeline that builds those reports and runs the scanner is wrapped in the `/ci` skill.
Treat it as a verification step, not a loop: each run uploads to SonarCloud, so run it only when
there is a real change to confirm.

## CI gate

On every pull request, GitHub Actions runs the same checks you run locally. `ci.yml` is triggered by
`pull_request` and simply calls the reusable `checks.yml` workflow. `deploy.yml` calls the same
workflow before it builds or deploys any image, so nothing ships that has not passed the gate.

`checks.yml` runs one `quality` job on `ubuntu-latest`:

1. Install `uv`, set up the Python version from `pyproject.toml`.
2. `uv sync --all-groups --locked --no-build` - install every dependency group from the locked
    versions, requiring prebuilt wheels.
3. `uv run ruff check --output-format=github .` - lint, with annotations inline on the PR.
4. `uv run ruff format --check .` - fail if anything is unformatted.
5. `uv run zuban check` - strict type check.
6. `uv run pytest` - the full test suite (see [Testing](testing.md)).

Because the workflow installs from `uv.lock`, keep the lockfile committed and up to date (the
`uv-lock` pre-commit hook does this for you).
