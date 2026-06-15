---
icon: lucide/flask-conical
---

# Testing

The suite uses [pytest](https://docs.pytest.org/) with
[pytest-django](https://pytest-django.readthedocs.io/). Tests live next to the app they cover, in
`<app>/tests/`, and are discovered by the `test_*.py` filename pattern.

## Running the suite

```bash
uv run pytest
```

That single command runs everything with the project defaults. There is no extra setup: the test
settings use a SQLite database, plain `StaticFilesStorage`, and an unsafe throwaway secret key, so
the suite runs the same locally and in CI.

Run a single test, file, or pattern with `-k` or a node ID:

```bash
uv run pytest -k test_rating_average        # match by name substring
uv run pytest talks/tests/test_models.py    # one file
uv run pytest talks/tests/test_models.py::test_talk_str   # one test
```

## Configured defaults

These options come from `[tool.pytest.ini_options]` in
[pyproject.toml](https://github.com/PioneersHub/pyconde-talks/blob/main/pyproject.toml) and apply to
every run:

| Option                                  | Why it is set                                                       |
| --------------------------------------- | ------------------------------------------------------------------- |
| `--random-order`                        | Run tests in a random order each time, to surface ordering coupling |
| `--cov=.`                               | Measure coverage of the whole project                               |
| `--cov-report=term`                     | Print a coverage summary in the terminal                            |
| `--cov-report=lcov:reports/lcov.info`   | Write LCOV output for editors and coverage tools                    |
| `--cov-report=xml:reports/coverage.xml` | Write Cobertura XML, which SonarQube reads                          |
| `--durations=10`                        | List the ten slowest tests, to catch creeping slowness              |
| `-ra`                                   | Show a short reason summary for everything except passes            |
| `--tb=short`                            | Short tracebacks                                                    |
| `--import-mode=importlib`               | Modern import mode, no `__init__.py` juggling                       |
| `--verbose`                             | One line per test                                                   |

`DJANGO_SETTINGS_MODULE` is set to `event_talks.settings`, so pytest-django bootstraps Django for
you.

!!! tip "Reproducing a random-order failure"

    `--random-order` prints the seed it used at the start of the run, for example
    `Using --random-order-seed=123456`. Re-run that exact order with:

    ```bash
    uv run pytest --random-order-seed=123456
    ```

    If a test only fails under a particular order, that is a real bug (shared state between tests), not
    a flake. Fix the leaking state rather than pinning the seed.

## What the suite covers

Models, views, querysets, permissions, forms, templates tags, and the management commands all have
tests. A few integration tests talk to real external services (Pretalx, Google Sheets) and are
skipped by default via a module-level `pytest.mark.skipif` marker unless an opt-in environment
variable such as `RUN_LIVE_IMPORT_TEST` is set. Because their bodies never run in the normal suite,
they are excluded from the coverage measurement (see below) so they do not drag the number down.

## Coverage

Coverage is collected on every run via `pytest-cov`. The target is **90% or higher**; add or update
tests whenever you change behavior.

Reports are written under `reports/`:

- `reports/lcov.info` - LCOV, picked up by editor coverage gutters and other tooling
- `reports/coverage.xml` - Cobertura XML, uploaded to SonarCloud by the CI/SonarQube pipeline

The terminal report (`--cov-report=term`) gives you the per-file summary at the end of a run. For a
browsable HTML report, add the flag yourself:

```bash
uv run pytest --cov-report=html
```

`[tool.coverage.run]` omits files that should not count toward coverage: `manage.py`, the ASGI and
WSGI entry points, `settings.py`, all migrations, and the three live-integration test modules
described above.

## Test dependencies

The `test` dependency group in
[pyproject.toml](https://github.com/PioneersHub/pyconde-talks/blob/main/pyproject.toml) pulls in the
plugins the suite relies on:

- `pytest-django` - Django fixtures (`db`, `client`, `settings`) and test-database management
- `pytest-cov` - coverage integration
- `pytest-random-order` - the random test ordering enabled above
- `pytest-mock` - the `mocker` fixture for patching
- `pytest-httpx2` - mock the `httpx2` client used by the importers; provides the `httpx2_mock`
    fixture and the `httpx2` marker (registered in `[tool.pytest.ini_options]`)
- `hypothesis` - property-based testing, for generating inputs across a wide range
- `inline-snapshot` - snapshot assertions whose expected values are written back into the test
    source

!!! example "Updating inline snapshots"

    When you change output that an `inline-snapshot` test captures, run pytest with the fix flag so the
    new expected values are written into the test files, then review the diff:

    ```bash
    uv run pytest --inline-snapshot=fix
    ```

## In CI

The reusable `checks` workflow runs `uv run pytest` after lint, format, and type checks. It creates
the `reports/` directory first so the coverage outputs have somewhere to land. See
[Code quality](code-quality.md) for the full CI gate.
