---
icon: lucide/book-open
---

# Working on the docs

This documentation site is built with [Zensical](https://zensical.org), the successor to Material
for MkDocs from the same authors. The site configuration is in `zensical.toml` at the repository
root, and the page sources are the Markdown files under `docs/`.

## Preview locally

Start a live-reloading preview server. It rebuilds and refreshes the browser as you edit:

```bash
uv run --group docs zensical serve
```

The preview is served at <http://localhost:8000>.

If port 8000 is already in use, bind the server to another port, for example 8001:

```bash
uv run --group docs zensical serve -a localhost:8001
```

This serves the preview at <http://localhost:8001>.

## Build the static site

Build the fully rendered HTML site into `site/`:

```bash
uv run --group docs zensical build
```

The `site/` directory is gitignored; it is a build artifact, not something you commit. You rarely
need to build by hand, since the preview server covers day-to-day authoring and CI builds the
published site.

!!! note "The docs dependency group"

    Zensical lives in the `docs` dependency group in
    [pyproject.toml](https://github.com/PioneersHub/pyconde-talks/blob/main/pyproject.toml), kept
    separate from the app's runtime and test dependencies. `uv run --group docs ...` installs it on
    demand without polluting the main environment.

## Publishing

Publishing is automatic. The
[.github/workflows/docs.yml](https://github.com/PioneersHub/pyconde-talks/blob/main/.github/workflows/docs.yml)
GitHub Actions workflow builds the site and deploys it to GitHub Pages on every push to `main` that
touches the docs sources (`docs/**`, `zensical.toml`, `pyproject.toml`, `uv.lock`, or the workflow
itself). You can also trigger it manually from the Actions tab.

The published site is at <https://pioneershub.github.io/pyconde-talks/>. The workflow builds with
`uv run --only-group docs --locked --no-build zensical build --clean`, then uploads and deploys the
result as a Pages artifact. Deployments are serialized (one at a time, never cancelling a deploy
already in flight).

## Page structure

The left-hand navigation is defined by the `nav` list in `zensical.toml`, not by the folder layout,
so adding a file does not automatically add it to the menu: add a `nav` entry too. A section can
have its own overview page named `index.md`.

Each page may start with YAML frontmatter that sets a [Lucide](https://lucide.dev) icon shown in the
navigation:

```yaml
---
icon: lucide/rocket
---
```

## Authoring syntax

The Material/MkDocs authoring extensions are available. Use them where they make a page clearer.

=== "Admonitions"

    ```markdown
    !!! tip "Optional title"

        Body text, indented four spaces, after a blank line.
    ```

    Types include `note`, `tip`, `warning`, `danger`, `example`, and `info`.

=== "Content tabs"

    ```markdown
    === "First tab"

        Content for the first tab.

    === "Second tab"

        Content for the second tab.
    ```

    Same-named tabs across the page switch together (the `content.tabs.link` feature).

=== "Mermaid"

    Fenced code blocks tagged `mermaid` render as diagrams:

    ````markdown
    ```mermaid
    graph LR
      A[Browser] --> B[Django] --> C[(Postgres)]
    ```
    ````

## Style and formatting

Prose is hard-wrapped at 100 columns. Use plain language and explain the why, not just the what.
Headings are sentence case. Nested list items indent with four spaces.

Markdown formatting is enforced by a pre-commit hook. There are two `mdformat` passes (see
[.pre-commit-config.yaml](https://github.com/PioneersHub/pyconde-talks/blob/main/.pre-commit-config.yaml)):
the docs pass runs `mdformat` with the `mdformat-mkdocs` plugin on everything under `docs/`, so
admonitions, content tabs, and 4-space nested lists are preserved rather than mangled. Run it (along
with every other hook) with:

```bash
prek run -a
```

!!! tip "Linking between pages"

    Link to other docs pages with relative paths to the `.md` file, for example
    `../reference/management-commands.md`. Link to source code with absolute GitHub URLs under
    `https://github.com/PioneersHub/pyconde-talks/blob/main/`. Do not use relative links that escape the
    `docs/` directory; they break on the published site.
