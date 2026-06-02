# syntax=docker/dockerfile:1.9

# Stage 1: Builder
FROM python:3.14-slim AS builder

# Copy uv package manager
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/venv \
    PATH="/venv/bin:$PATH"

# Install build dependencies. libpq is no longer needed: psycopg[binary] ships the
# Postgres client library inside its wheel, so neither libpq-dev (build) nor libpq5
# (runtime, removed below) needs to be installed by apt.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    git \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

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
# This stage is used for production and contains only what is essential to run the application.
# The media (DJANGO_MEDIA_ROOT) and logs (DJANGO_LOG_DIR) directories should be mounted as volumes
# Make sure to set the correct permissions for them:
#   - Django (UID 10000) needs read and write permissions to media files
#   - Nginx (www-data, UID 33) only needs read permissions to serve media files
FROM python:3.14-slim AS final

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLCONFIGDIR=/tmp/matplotlib

# Install runtime dependencies only and create non-root user.
# (libpq5 dropped: psycopg[binary] ships its own libpq inside the wheel.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    vim \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r django \
    && useradd --no-log-init -r -g django django -u 10000

WORKDIR /app

# Copy virtual environment from builder
# Note: uv was used to create the virtual environment, but will not be available in the final image
COPY --from=builder /venv /venv
ENV PATH="/venv/bin:$PATH"

# Copy only project files necessary for production (read-only for the django user)
COPY --from=builder --chown=root:root --chmod=0555 /app/manage.py /app/
COPY --from=builder --chown=root:root --chmod=0555 /app/event_talks /app/event_talks/
COPY --from=builder --chown=root:root --chmod=0555 /app/events /app/events/
COPY --from=builder --chown=root:root --chmod=0555 /app/svg /app/svg/
COPY --from=builder --chown=root:root --chmod=0555 /app/talks /app/talks/
COPY --from=builder --chown=root:root --chmod=0555 /app/templates /app/templates/
COPY --from=builder --chown=root:root --chmod=0555 /app/users /app/users/
COPY --from=builder --chown=root:root --chmod=0555 /app/utils /app/utils/

# Install font (ADD fetches over HTTPS and applies --chmod in one layer)
ADD --chmod=0444 \
    "https://github.com/notofonts/notofonts.github.io/raw/refs/heads/main/fonts/NotoSans/full/variable-ttf/NotoSans%5Bwdth,wght%5D.ttf" \
    /usr/share/fonts/truetype/noto/NotoSans.ttf

# Switch to non-root user
USER django:django

# Expose the port Daphne will run on
EXPOSE 8000

# Add healthcheck
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl --fail "http://127.0.0.1:8000/ht/?format=json" || exit 1

# Run entrypoint script
COPY --chown=root:root --chmod=0555 ./docker/docker-entrypoint.sh /usr/local/bin/

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "event_talks.asgi:application"]
