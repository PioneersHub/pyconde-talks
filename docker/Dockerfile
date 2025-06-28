# syntax=docker/dockerfile:1.9

# Stage 1: Builder
FROM python:3.13-slim AS builder

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

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    libpq-dev \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency files first (for better layer caching)
COPY uv.lock pyproject.toml /app/

# Install dependencies
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev

# Install TailwindCSS
ARG PLATFORM=linux-x64
RUN curl -sL \
    "https://github.com/tailwindlabs/tailwindcss/releases/latest/download/tailwindcss-${PLATFORM}" \
    -o /usr/local/bin/tailwindcss \
    && chmod +x /usr/local/bin/tailwindcss

# Copy project
COPY . /app/

# Generate minified CSS with Tailwind
RUN tailwindcss -i ./assets/css/input.css -o ./static/css/tailwind.min.css --minify

# Install project and collect static files into STATIC_ROOT
ENV DJANGO_STATIC_ROOT=/staticfiles
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev && \
    python manage.py collectstatic --noinput

# Stage 2: extract static files
#
# Build with Docker Buildx Bake:
#     `docker buildx bake --allow=fs.read=..` or `COMPOSE_BAKE=true docker compose up --build`
#
# A folder called "staticfiles" will be created in the current directory.
# Transfer it to the server:
#      rsync -ave "ssh" --rsync-path="sudo rsync" /path/to/staticfiles/ pycon:/var/cache/talks.pycon.de/staticfiles/
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
FROM python:3.13-slim AS final

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install runtime dependencies only
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libpq5 \
    vim \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -r django && useradd --no-log-init -r -g django django -u 10000

WORKDIR /app

# Copy virtual environment from builder
# Note: uv was used to create the virtual environment, but will not be available in the final image
COPY --from=builder /venv /venv
ENV PATH="/venv/bin:$PATH"

# Copy only project files necessary for production
COPY --from=builder --chown=django:django /app/manage.py /app/
COPY --from=builder --chown=django:django /app/pyconde_talks /app/pyconde_talks/
COPY --from=builder --chown=django:django /app/talks /app/talks/
COPY --from=builder --chown=django:django /app/templates /app/templates/
COPY --from=builder --chown=django:django /app/users /app/users/
COPY --from=builder --chown=django:django /app/svg /app/svg/

# Switch to non-root user
USER django:django

# Expose the port Daphne will run on
EXPOSE 8000

# Add healthcheck
HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 \
    CMD curl --fail "http://127.0.0.1:8000/ht/?format=json" || exit 1

# Run entrypoint script
COPY --chown=django:django ./docker/docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "pyconde_talks.asgi:application"]
