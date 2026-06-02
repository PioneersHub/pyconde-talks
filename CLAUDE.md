# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

Django 6 app for PyCon DE / PyData Berlin talks and schedules. Stack: Python 3.14, Tailwind v4,
HTMX, SQLite (dev) / PostgreSQL 18 (prod), `uv` for packages.

## Code style

- Python 3.14: use modern syntax. PEP 695 generics, `match`, structural pattern matching, and PEP
  758 `except` without parentheses (`except ValueError, TypeError:`) are all fine.
- 100-column width for code, comments, and docstrings.
- Write comments and docstrings in plain, simple language. Explain the _why_, not the _what_.
- Never use em-dash (`—`). Use a regular hyphen or rephrase.

## Tests and code quality

- Add or update tests whenever you change behavior. Target: 90%+ coverage.
- `uv run pytest` (full suite) or `uv run pytest -k test_name` (single test).
- Keep functions small and focused. If a function grows complex (deep nesting, many branches, hard
  to test), refactor before adding more to it. Extract helpers, split responsibilities.
- SonarQube has many false positives. Silence with `# NOSONAR` (and a one-line reason) only when the
  finding clearly does not apply. Default is to fix, not silence.
- When you change views, querysets, permissions, middleware, or auth code, consider spawning the
  `security-reviewer` subagent for a second pass (cross-event leaks, auth bypasses, PII in logs).
  Optional, not mandatory.

## Before committing

```bash
uv run ruff check --fix .   # Lint + auto-fix
uv run ruff format .        # Format
zuban check                 # Strict type check (mypy-compatible, with django-stubs)
prek run -a                 # Run all pre-commit hooks (drop-in pre-commit replacement)
```

The full local CI + SonarQube refresh pipeline lives in the `/ci` skill
([.claude/skills/ci.md](.claude/skills/ci.md)). Claude **may** invoke it, but should do so
sparingly:

- Each run takes ~1-2 minutes and uploads to SonarCloud, so treat it as a verification step, not a
  loop.
- Run it at most twice per round of fixes, to confirm the findings actually cleared. Do not re-run
  just because the `mcp__sonarqube__*` tools still show yesterday's data; wait until there is a real
  set of changes to verify.

The `mcp__sonarqube__*` tools query findings from the last upload.

Two MCP servers are configured in `.mcp.json`: `sonarqube` (self-hosted Docker, full toolset) and
`sonarqubeCloud` (cloud-native HTTP, smaller toolset). Default is `sonarqube`. Only switch to
`sonarqubeCloud` if the Docker-based server is unavailable (e.g., Docker not running).

## Git

- Make small, atomic commits. One logical change per commit.
- **Commit as you go.** After finishing a discrete change (a single fix, one refactor, one file's
  worth of related edits), run the pre-commit checks and create the commit before moving to the next
  change. Do not batch many unrelated edits into one large commit at the end of a session.
- Write clear commit messages: short imperative subject, body describing the _why_.

## Things Claude must never do

- **Never push to GitHub** unless the user explicitly asks in this turn. Creating local commits is
  fine; `git push` is not.
- **Never skip hooks** (`--no-verify`, `--no-gpg-sign`). If a hook fails, fix the cause.
- **gpg-agent restart is pre-authorized.** If a commit hangs, times out, or fails with a
  pinentry / gpg-agent error (common after the laptop wakes from sleep), Claude MUST run
  `gpgconf --kill gpg-agent` itself via the Bash tool and retry the commit. Do not stop and wait
  for the user: this command is the standard fix, safe to run at any time, and the commit cannot
  complete until the agent is restarted.
- **Never commit real secrets.** If `django-vars.env` or `docker/.env` shows non-placeholder values
  in a staged diff, stop and flag it.
- **Never silence lint/type/SonarQube findings just to make CI pass.** Fix the underlying issue;
  silence only with a documented reason.
- **Never rewrite shared history** (`git rebase`, `git reset --hard`, force-push) on any branch that
  has been pushed, unless the user asks.

## Environment files

`django-vars.env` (local) and `docker/.env` (Docker Compose) must stay in sync. When you add,
rename, or remove a variable in one, update the other in the same commit.

## Context management

When compacting, preserve: the list of files modified in this session, any failing tests or open
SonarQube findings not yet resolved, and any pending todos. Drop large file dumps and exploratory
search output.

## Further reading

Load these only when relevant; they are not auto-imported.

- Day-to-day commands, setup, CI: [docs/development.md](docs/development.md)
- App layout and integrations: [docs/architecture.md](docs/architecture.md)
- Pretalx importer (modes, detect-and-review, image regen): [docs/pretalx-sync.md](docs/pretalx-sync.md)
- Env var reference: [django-vars.env](django-vars.env)
