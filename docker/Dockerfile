# syntax=docker/dockerfile:1.9

# Base image: Chainguard's wolfi-base (glibc + busybox + apk-tools + ca-certificates), a minimal,
# CVE-patched, daily-rebuilt base. We build BOTH stages on it instead of python:3.14-slim.
#
# Pinned by digest for reproducible builds and supply-chain integrity. ":latest" is the only free
# Chainguard tier and is rebuilt daily with security fixes; a frozen digest would stop receiving
# those fixes, so the digest is kept fresh by Renovate's dockerfile manager, which expands this ARG,
# re-resolves the digest behind ":latest", and opens a PR whenever it moves. Tag + digest together
# gives both reproducibility and patching.
ARG WOLFI_BASE=cgr.dev/chainguard/wolfi-base:latest@sha256:b78bb982194828b6c9c214230bf34d51944e2102ea8468f01ac21e5f99328efd

# Stage 1: Builder
FROM ${WOLFI_BASE} AS builder

# uv package manager: a prebuilt static binary copied from Astral's official distroless image.
# COPY (not the curl install script) avoids uv's broken glibc detection on Wolfi.
# Pinned by version AND digest for reproducibility.
# Renovate bumps both the version tag and the digest, grouped with the uv-pre-commit hook so the two
# uv pins stay in sync (see renovate.json5).
COPY --from=ghcr.io/astral-sh/uv:0.11.19@sha256:b46b03ddfcfbf8f547af7e9eaefdf8a39c8cebcba7c98858d3162bd28cf536f6 /uv /uvx /usr/local/bin/

# Build dependency: the Python interpreter. Wolfi's Python is dynamically linked against the base
# image's apk-managed OpenSSL, so a fresh base patches it (unlike uv's python-build-standalone,
# which statically bundles its own OpenSSL/sqlite that the base image cannot patch). No git is
# needed: all dependencies resolve from PyPI (see uv.lock).
# libpq is unnecessary: psycopg[binary] ships it.
# No C toolchain (build-base / python-dev) is needed: all deps ship prebuilt wheels and py-ubjson is
# excluded via override-dependencies in pyproject.toml.
RUN apk add --no-cache \
    python-3.14

# Environment. uv is told to use ONLY the system (apk) interpreter and to never download its own
# python-build-standalone build.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON=/usr/bin/python3.14 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON_PREFERENCE=only-system \
    UV_PROJECT_ENVIRONMENT=/venv \
    PATH="/venv/bin:$PATH"

WORKDIR /app

# Copy dependency files first (for better layer caching)
COPY uv.lock pyproject.toml /app/

# Install dependencies
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-build --no-install-project --no-default-groups --group prod

# Install TailwindCSS (ADD fetches over HTTPS and applies --chmod in one layer)
ARG PLATFORM=linux-x64
ADD --chmod=0555 \
    "https://github.com/tailwindlabs/tailwindcss/releases/latest/download/tailwindcss-${PLATFORM}" \
    /usr/local/bin/tailwindcss

# Copy project
COPY . /app/

# Install project and collect static files into STATIC_ROOT.
#
# The staticfiles storage backend MUST match runtime (see docker/.env). With
# ManifestStaticFilesStorage, collectstatic content-hashes every asset and writes the
# staticfiles.json manifest the app reads to resolve {% static %} tags. If the build used
# the plain storage instead, the running app would 500 with "Missing staticfiles manifest
# entry". The default here mirrors prod; override via the build arg for a manifest-less build.
#
# Tailwind must build into the SOURCE static dir BEFORE collectstatic so the generated CSS
# is itself collected, hashed, and recorded in the manifest (base.html loads it via
# {% static 'css/tailwind.min.css' %}).
ENV DJANGO_STATIC_ROOT=/staticfiles
ARG DJANGO_STATICFILES_STORAGE=django.contrib.staticfiles.storage.ManifestStaticFilesStorage
ENV DJANGO_STATICFILES_STORAGE=${DJANGO_STATICFILES_STORAGE}
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-build --no-default-groups --group prod && \
    mkdir -p /app/static/css && \
    /usr/local/bin/tailwindcss -i /app/assets/css/input.css -o /app/static/css/tailwind.min.css --minify && \
    python manage.py collectstatic --noinput

# Stage 2: extract static files
#
# Build with Docker Buildx Bake:
#     `docker buildx bake --allow=fs.read=..` or `COMPOSE_BAKE=true docker compose up --build`
#
# A folder called "staticfiles" will be created in the current directory.
# Transfer it to the server:
#      rsync -ave "ssh" --rsync-path="sudo rsync" /path/to/staticfiles/ server:/var/cache/${APP_NAME}/staticfiles/
#
# Then set the appropriate permissions:
#   - Django does not need any permissions after collecting static files
#   - Nginx (www-data user with UID 33) needs read-only permissions to serve static files
FROM scratch AS staticfiles-stage
ARG DJANGO_STATIC_ROOT=/staticfiles
COPY --from=builder ${DJANGO_STATIC_ROOT}/ /

# Stage 3: Final image
# Production runtime: only what is essential to run the app, on the same minimal wolfi-base.
# The media (DJANGO_MEDIA_ROOT) and logs (DJANGO_LOG_DIR) directories should be mounted as volumes.
# Set the correct ownership on the host (see docker/ensure_permissions.sh):
#   - Django (UID 65532) needs read and write permissions to media files and logs
#   - Nginx (www-data, UID 33) only needs read permissions to serve media files
FROM ${WOLFI_BASE} AS final

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLCONFIGDIR=/tmp/matplotlib \
    HOME=/tmp \
    PATH="/venv/bin:$PATH"

# Runtime packages, kept minimal (no curl, no vim, no compiler):
#   python-3.14  the interpreter (dynamically linked, apk-patchable OpenSSL)
#   tzdata       wolfi-base ships no zoneinfo; Django USE_TZ needs named time zones
#   libstdc++    C++ runtime for the matplotlib/pandas/numpy compiled extensions (manylinux wheels
#                expect it from the system; Debian shipped it implicitly, wolfi-base does not)
#   libffi       C ABI used by cffi (argon2-cffi)
# ca-certificates-bundle (outbound HTTPS: Vimeo API, TLS email, the healthcheck) and busybox
# (/bin/sh for the entrypoint) already ship in wolfi-base, as does the "nonroot" account
# (UID/GID 65532) we run as below, so no user creation is needed.
RUN apk add --no-cache \
    libffi \
    libstdc++ \
    python-3.14 \
    tzdata

WORKDIR /app

# Copy the virtual environment from the builder. Root-owned and read-only to the django user; uv
# itself is NOT copied into the final image.
COPY --from=builder --chown=root:root /venv /venv

# Copy only project files necessary for production (read-only for the django user)
COPY --from=builder --chown=root:root --chmod=0555 /app/manage.py /app/
COPY --from=builder --chown=root:root --chmod=0555 /app/event_talks /app/event_talks/
COPY --from=builder --chown=root:root --chmod=0555 /app/events /app/events/
COPY --from=builder --chown=root:root --chmod=0555 /app/svg /app/svg/
COPY --from=builder --chown=root:root --chmod=0555 /app/talks /app/talks/
COPY --from=builder --chown=root:root --chmod=0555 /app/templates /app/templates/
COPY --from=builder --chown=root:root --chmod=0555 /app/users /app/users/
COPY --from=builder --chown=root:root --chmod=0555 /app/utils /app/utils/

# Staticfiles manifest only (NOT the assets, which nginx serves from its own synced copy).
# ManifestStaticFilesStorage reads staticfiles.json at request time to map each asset to its
# content-hashed name. Without this file the app 500s with "Missing staticfiles manifest entry".
#
# Mode 0555, not 0444: --chmod also applies to the auto-created /staticfiles directory, and a
# directory needs the execute (traverse) bit or the non-root django user can't open the file
# inside it. The execute bit is harmless on the JSON file itself.
COPY --from=builder --chown=root:root --chmod=0555 /staticfiles/staticfiles.json /staticfiles/staticfiles.json
ADD --chmod=0444 \
    "https://github.com/notofonts/notofonts.github.io/raw/refs/heads/main/fonts/NotoSans/full/variable-ttf/NotoSans%5Bwdth,wght%5D.ttf" \
    /usr/share/fonts/truetype/noto/NotoSans.ttf

# Fail the BUILD (not production) if any compiled extension cannot resolve a runtime shared library
# in this minimal image, and confirm the TLS trust store is wired up for outbound HTTPS. Cheap
# insurance for the hardened base; uses a throwaway matplotlib cache and leaves no artifacts.
RUN MPLCONFIGDIR=/tmp/build-cache python -c "\
    import ssl; ssl.create_default_context(); \
    import django, numpy, pandas, matplotlib; matplotlib.use('Agg'); \
    import matplotlib.pyplot, PIL.Image, argon2, psycopg; \
    print('native import + TLS smoke test: OK')" \
    && rm -rf /tmp/build-cache

# Run entrypoint script
COPY --chown=root:root --chmod=0555 ./docker/docker-entrypoint.sh /usr/local/bin/

# Run as wolfi-base's predefined "nonroot" account (UID/GID 65532). Keep 65532 in sync with
# docker/ensure_permissions.sh, which sets ownership of the mounted media/log volumes to that UID.
USER nonroot:nonroot

# Expose the port Daphne will run on
EXPOSE 8000

# Healthcheck via the bundled Python (no curl/wget in the image). Asserts HTTP 200, so a redirect
# or degraded response is not read as healthy; a 4xx/5xx raises (HTTPError subclasses OSError) and
# exits non-zero.
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/ht/?format=json', timeout=4).status == 200 else 1)"]

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "event_talks.asgi:application"]
