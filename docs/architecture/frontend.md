---
icon: lucide/palette
---

# Frontend

The frontend is server-rendered Django templates styled with Tailwind CSS and made interactive with
HTMX. There is no JavaScript build step and no SPA framework: pages are HTML, and small fragments
are swapped in over the wire. The only hand-written JavaScript is the dark-mode toggle.

## Tailwind CSS v4

Styling uses [Tailwind CSS v4](https://tailwindcss.com/) through the standalone binary, so there is
no Node toolchain to install. The dev setup script downloads the binary (or symlinks one already on
`PATH`) into the virtualenv
([`scripts/dev-setup.sh`](https://github.com/PioneersHub/pyconde-talks/blob/main/scripts/dev-setup.sh)),
then compiles the stylesheet:

```bash
# Watch and rebuild on change (DEBUG mode)
tailwindcss -i ./assets/css/input.css -o ./static/css/tailwind.min.css --watch

# One-off minified build (production / collectstatic)
tailwindcss -i ./assets/css/input.css -o ./static/css/tailwind.min.css --minify
```

The source stylesheet is
[`assets/css/input.css`](https://github.com/PioneersHub/pyconde-talks/blob/main/assets/css/input.css).
It uses Tailwind v4's CSS-first configuration:

- `@source` directives tell Tailwind to scan `templates/**.html` and `static/js/**.js` for the
    classes actually used.
- `@theme` defines the brand palette (the PyCon DE / PyData blues, greens, yellow, red, pink),
    typography, spacing, breakpoints, and easing as CSS custom properties.
- `@layer components` defines reusable classes such as `.btn-primary`, `.card`, `.badge-*`,
    `.alert-*`, and the `.app-body` / `.app-nav` / `.app-main` / `.app-footer` layout shell, each
    with light and dark variants.
- `@layer utilities` adds neutral text and border tones (`.text-muted`, `.border-weak`, ...) and the
    talk-state helpers (`.card-bg-current`, `.ring-upcoming`, the schedule-grid overrides).

The compiled output is served as a regular static file, linked from `base.html`.

## HTMX

[HTMX](https://htmx.org/) drives every dynamic interaction. The script is vendored under
[`static/js/htmx.min.js`](https://github.com/PioneersHub/pyconde-talks/blob/main/static/js/htmx.min.js)
and loaded in `base.html`, alongside `idiomorph-ext.min.js` for morph-style swaps. The `django_htmx`
app and its middleware are installed so views can detect HTMX requests.

The shared rule on the server side is simple: a request is from HTMX when it carries the
`HX-Request: true` header. The helper `is_htmx_request()` in
[`talks/utils.py`](https://github.com/PioneersHub/pyconde-talks/blob/main/talks/utils.py)
encapsulates that check, and views branch on it to return either a full page or just the fragment
that changed.

### Common patterns

- **Live dashboard updates.** The home page (`home.html`) has two containers that poll on a timer:
    `hx-get` the `dashboard_stats` and `upcoming_talks` partials with
    `hx-trigger="load, every 300s"` and `hx-swap="innerHTML"`. They render once on load and refresh
    every five minutes without a full reload. The `upcoming_talks` view is additionally
    `cache_page`-d for 30 seconds and varies on the session cookie.
- **Search and filtering.** The talk list (`talks/talk_list.html`) wires the search box and the
    filter selects to `hx-get` the same `talk_list` URL into `#talks-container`, with `hx-trigger`
    debouncing keystrokes (`keyup changed delay:300ms`) and reacting to select changes.
    `hx-push-url="true"` keeps the URL shareable, `hx-include` submits both forms together, and an
    `hx-indicator` spinner shows during the request. The list container itself also polls
    (`every 300s`) so a long-open list stays current. Matched terms are wrapped with the `highlight`
    filter.
- **Ratings.** The rating widget posts a score (and optional comment) and the view returns the
    re-rendered widget. It also uses out-of-band swaps to update the star summary shown next to the
    talk title elsewhere on the page, so a single response updates two places at once.
- **Q&A voting.** The vote button (`questions/vote_button.html`) issues `hx-post` to
    `question_vote`, passing the current `status_filter` via `hx-vals` and the CSRF token via
    `hx-headers`, then swaps the whole `#question-list` (`hx-swap="outerHTML"`) so the re-sorted,
    re-counted list comes back in one round trip.
- **Saving talks.** The save button (`partials/save_button.html`) posts to `toggle_save_talk` and
    replaces just its own `#save-btn-{pk}` container with the toggled state.

!!! note "CSRF with HTMX"

    Interactive `hx-post` controls pass the CSRF token explicitly with
    `hx-headers='{"X-CSRFToken": "{{ csrf_token }}"}'`, since the request does not go through a normal
    form submission.

## Template layout

Templates live under
[`templates/`](https://github.com/PioneersHub/pyconde-talks/tree/main/templates), loaded with
Django's cached loader. The structure:

- `base.html` - the shell every page extends: the `<head>`, the top navigation, the content block,
    the footer (built from the `brand_*` context variables), and the dark-mode script.
- `home.html`, `404.html`, `500.html` - top-level pages.
- `partials/` - cross-app fragments (`event_selector.html`, `alert_error.html`,
    `useful_link_card.html`).
- Per-app directories: `talks/` (with `talks/partials/` and `talks/questions/` for HTMX fragments),
    `users/`, `account/`, `socialaccount/`, and `admin/` overrides.

The HTMX fragments (the `*_fragment.html`, `*_widget.html`, and button partials) are deliberately
small and self-contained so a view can render exactly one of them in response to an interaction.

## SVG icons

Icons are inlined from `.svg` files rather than referenced with `<img src>`, which lets them inherit
color and size from Tailwind classes. The files live in the top-level
[`svg/`](https://github.com/PioneersHub/pyconde-talks/tree/main/svg) directory, and the `svg`
template tag in
[`talks/templatetags/svg_tags.py`](https://github.com/PioneersHub/pyconde-talks/blob/main/talks/templatetags/svg_tags.py)
reads and injects them:

```django
{% load svg_tags %}
{% svg 'calendar' "h-4 w-4 mr-1" %}
```

The tag reads `svg/<name>.svg`, optionally injects the given CSS classes onto the `<svg>` element,
and marks the result safe. Reads are cached with `lru_cache`, and the resolved path is checked to
stay inside the `svg/` directory to guard against path traversal. The event logo is rendered the
same way, using the event's `logo_svg_name`.

## Dark mode

Dark mode is class-based: the `dark` class on `<html>` flips every `dark:` Tailwind variant. The
toggle logic is inline in
[`base.html`](https://github.com/PioneersHub/pyconde-talks/blob/main/templates/base.html):

- A small script in `<head>` runs before first paint, reading the saved preference from
    `localStorage` (or the OS `prefers-color-scheme`) and adding the `dark` class immediately. This
    prevents a flash of the wrong theme.
- The toggle button swaps a sun/moon icon, toggles the class, persists the choice to `localStorage`,
    and adds a short transition class for a smooth fade.
- When no explicit preference is stored, the page follows live OS theme changes.

The stylesheet declares the variant with `@variant dark (&:where(.dark, .dark *))` and includes dark
overrides for elements that use inline Tailwind classes (the schedule grid, prose blocks, and form
inputs).

## Template tags

The custom tags and filters live in
[`talks/templatetags/`](https://github.com/PioneersHub/pyconde-talks/tree/main/talks/templatetags):

| Module             | Provides                                                                                    |
| ------------------ | ------------------------------------------------------------------------------------------- |
| `svg_tags.py`      | `{% svg name css_class %}` - inline an SVG icon (see above).                                |
| `rating_tags.py`   | `{% star_rating average count %}` - render filled, half, and empty stars from the star SVG. |
| `saved_tags.py`    | `{{ talk.pk\|is_in:saved_talk_ids }}` - set-membership filter for the saved-talk highlight. |
| `schedule_tags.py` | `{% schedule_cell grid slot room_id as talk %}` - look up a talk in the schedule grid.      |
| `stat_tags.py`     | `{% stat_card title value %}` - an inclusion tag rendering a dashboard stat card.           |
| `highlight.py`     | `{{ value\|highlight:query }}` - wrap matched search terms in `<mark>`.                     |
| `time_filters.py`  | `{{ seconds\|format_seconds }}` - format a second count as `H:MM:SS` or `MM:SS`.            |
