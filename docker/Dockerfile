# syntax=docker/dockerfile:1.9

# Stage 1: Builder
FROM cgr.dev/chainguard/wolfi-base:latest AS builder

# Copy uv package manager. Pinned (not :latest) for reproducible builds and so Dependabot's
# docker ecosystem can track and bump it.
COPY --from=ghcr.io/astral-sh/uv:0.11.18 /uv /usr/local/bin/uv

# Python version - controllable from docker-bake.hcl or compose build args.
ARG PYTHON_VERSION=3.14

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_PROJECT_ENVIRONMENT=/venv \
    PATH="/venv/bin:$PATH"

# Install build dependencies and Python via uv (python-build-standalone).
# Using uv instead of the base image's package manager gives explicit version control
# independent of the image tag.
RUN apk add --no-cache build-base git && \
    uv python install ${PYTHON_VERSION} --default

WORKDIR /app

# Copy dependency files first (for better layer caching)
COPY uv.lock pyproject.toml /app/

# Install dependencies
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-default-groups --group prod

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
    uv sync --locked --no-default-groups --group prod && \
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
COPY --from=builder ${DJANGO_STATIC_ROOT} ${DJANGO_STATIC_ROOT}

# Stage 3: Final image
# Chainguard wolfi-base: glibc + busybox + apk-tools, nothing else.
FROM cgr.dev/chainguard/wolfi-base:latest AS final

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLCONFIGDIR=/tmp/matplotlib \
    PATH="/venv/bin:$PATH"

# Create non-root user (busybox adduser/addgroup; no shadow package needed).
# UID 65532 follows the Chainguard nonroot convention.
#   chown -R 65532:65532 /var/opt/<target>/media /var/log/<target>
RUN addgroup -S django && adduser -S -G django -u 65532 -D -H django

WORKDIR /app

# Copy Python installation from builder. python-build-standalone is self-contained;
# it only needs glibc, which wolfi-base provides.
COPY --from=builder /opt/python /opt/python

# Copy virtual environment from builder.
# uv created it; uv itself is NOT copied to the final image.
COPY --from=builder /venv /venv

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
# directory needs the execute (traverse) bit or the non-root django user can't open
# the file inside it. The execute bit is harmless on the JSON file itself.
COPY --from=builder --chown=root:root --chmod=0555 /staticfiles/staticfiles.json /staticfiles/staticfiles.json
ADD --chmod=0444 \
    "https://github.com/notofonts/notofonts.github.io/raw/refs/heads/main/fonts/NotoSans/full/variable-ttf/NotoSans%5Bwdth,wght%5D.ttf" \
    /usr/share/fonts/truetype/noto/NotoSans.ttf

# Switch to non-root user
USER django:django

# Expose the port Daphne will run on
EXPOSE 8000

# Healthcheck via Python (no curl in the image).
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ht/?format=json')"

# Run entrypoint script
COPY --chown=root:root --chmod=0555 ./docker/docker-entrypoint.sh /usr/local/bin/

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "event_talks.asgi:application"]
