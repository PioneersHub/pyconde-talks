---
description: How internationalization (i18n) works and how to translate the UI.
---

# Translations (i18n)

The site is internationalized with Django's standard `gettext` machinery. The interface, flash
messages, form/validation text, the admin, and the transactional emails (login code, e-mail
verification) are all translatable. Talk content imported from Pretalx (titles, abstracts, speaker
names) is data, not UI, and is shown as authored.

## Languages

Offered languages live in `settings.LANGUAGES`:

| Code    | Language            | Catalog                                   |
| ------- | ------------------- | ----------------------------------------- |
| `en`    | English (source)    | none (source strings)                     |
| `pt-br` | Portuguese (Brazil) | `locale/pt_BR/LC_MESSAGES/django.{po,mo}` |

The on-disk locale directory uses Django's locale name (`pt_BR`); the language code used everywhere
else is lowercase and hyphenated (`pt-br`).

## How the active language is chosen

`django.middleware.locale.LocaleMiddleware` resolves the language per request from, in order:

1. the `django_language` cookie (set by the language switcher), then
2. the `Accept-Language` request header, then
3. `settings.LANGUAGE_CODE` (the default).

On top of that, `users.middleware.UserLanguageMiddleware` runs after authentication and, for a
logged-in user who has set `CustomUser.preferred_language`, overrides the choice so it follows the
account across browsers and devices. Transactional emails are rendered in the recipient's
`preferred_language` when set (see `users.adapters.AccountAdapter.send_mail`).

The switcher itself is `templates/partials/language_selector.html` in the nav. It POSTs to the
custom `set_language` view (`users.views.set_language`), which wraps Django's `set_language` to also
persist the choice on the user's profile.

## Marking strings for translation

**Templates** - load the tag library once per file and wrap visible text:

```django
{% load i18n %}
<h1>{% trans "Schedule" %}</h1>
<p>{% blocktrans with name=event.name %}Welcome to {{ name }}{% endblocktrans %}</p>
```

Use `{% blocktrans %}` whenever the string contains a variable. Do not wrap dynamic data (talk
titles, speaker names) or markup attributes that are not user-visible text.

**Python** - use `gettext_lazy` for module/class-level strings (model fields, form fields, admin
labels) and `gettext` for per-request strings (messages in views). Use printf placeholders, never
f-strings, when a translatable string interpolates a value:

```python
from django.utils.translation import gettext as _

messages.success(request, _("Saved %(n)s talks") % {"n": count})
```

Do not translate log messages or developer-only exceptions.

## Workflow

After changing or adding translatable strings, regenerate and compile the catalog (requires GNU
`gettext`; on macOS `brew install gettext`):

```bash
# 1. Extract strings into locale/pt_BR/LC_MESSAGES/django.po (preserves existing translations)
uv run python manage.py makemessages -l pt_BR \
  --ignore=".venv/*" --ignore="staticfiles/*" --ignore="media/*" --ignore="docs/*"

# 2. Edit django.po and fill in the new msgstr entries

# 3. Compile to django.mo (what Django reads at runtime)
uv run python manage.py compilemessages -l pt_BR
```

Both `django.po` (source) and `django.mo` (compiled) are committed. The Docker image copies the
`locale/` directory into the final stage, so the compiled catalog ships with the app. **Re-run
`compilemessages` whenever you edit the `.po`** or the change will not take effect.

## Adding a new language

1. Add it to `settings.LANGUAGES`, e.g. `("de", _("German"))`.
2. `uv run python manage.py makemessages -l de` (locale name, e.g. `de`, `fr`, `pt_BR`).
3. Translate `locale/<locale>/LC_MESSAGES/django.po`.
4. `uv run python manage.py compilemessages -l de`.
5. The switcher and `CustomUser.preferred_language` choices pick it up automatically from
    `settings.LANGUAGES`.
