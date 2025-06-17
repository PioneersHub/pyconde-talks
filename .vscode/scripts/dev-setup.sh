#!/bin/bash

# Script name: dev-setup.sh
# Description: setup development environment

# Default configuration (can be overridden by environment variables)
DEBUG="${DEBUG:-false}"
VENV_DIR="${VENV_DIR:-.venv}"
VENV_PYTHON="${VENV_DIR}/bin/python"
VENV_TAILWIND="${VENV_DIR}/bin/tailwindcss"
VENV_MAILPIT="${VENV_DIR}/bin/mailpit"
DJANGO_SUPERUSER_EMAIL="${DJANGO_SUPERUSER_EMAIL:-admin@example.com}"
DJANGO_SUPERUSER_PASSWORD="${DJANGO_SUPERUSER_PASSWORD:-PyConDE_2025}"
FAKE_DATA_COUNT="${FAKE_DATA_COUNT:-50}"
RUN_SERVER="${RUN_SERVER:-true}"
DJANGO_PORT="${DJANGO_PORT:-8000}"
SKIP_STEPS="${SKIP_STEPS:-}"
GEN_FAKE_DATA="${GEN_FAKE_DATA:-true}"
PRETALX_SYNC="${PRETALX_SYNC:-false}"
IMPORT_STREAMS="${IMPORT_STREAMS:-false}"

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Output functions
log() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() {
    echo -e "${RED}[ERROR]${NC} $1"
    exit 1
}

# Detect operating system
detect_os() {
    local format="${1:-default}"
    local os=""

    # Use OSTYPE variable for initial detection
    case "$OSTYPE" in
    linux*) os="linux" ;;
    darwin*) os="darwin" ;;
    freebsd*) os="freebsd" ;;
    msys* | mingw*) os="windows" ;;
    cygwin*) os="windows" ;;
    *)
        # Fallback to uname if OSTYPE detection failed
        os=$(uname | tr '[:upper:]' '[:lower:]')
        case "$os" in
        linux*) os="linux" ;;
        darwin*) os="darwin" ;;
        freebsd*) os="freebsd" ;;
        msys* | mingw*) os="windows" ;;
        sunos*) os="solaris" ;;
        *) os="unknown" ;;
        esac
        ;;
    esac

    # Alternative format (like macos)
    if [ "$format" = "alt" ]; then
        case "$os" in
        darwin) os="macos" ;;
        linux) os="linux" ;;
        windows) os="win" ;;
        freebsd) os="fbsd" ;;
        esac
    fi

    echo "$os"
}

# Detect architecture
detect_arch() {
    local format="${1:-default}"
    local arch=$(uname -m)

    # Normalize architecture names based on format
    if [ "$format" = "alt" ]; then
        # Alternative format (like x64)
        case "$arch" in
        x86_64) arch="x64" ;;
        i686 | i386) arch="x86" ;;
        armv6* | armv7*) arch="arm" ;;
        aarch64 | arm64) arch="arm64" ;;
        esac
    else
        # Default format (like amd64)
        case "$arch" in
        x86_64) arch="amd64" ;;
        i686 | i386) arch="386" ;;
        armv6* | armv7*) arch="arm" ;;
        aarch64 | arm64) arch="arm64" ;;
        esac
    fi

    echo "$arch"
}

# Detect platform
detect_platform() {
    # default: "linux-amd64", "darwin-arm64"
    # alt: "linux-x64", "macos-arm64"
    local format="${1:-default}"

    # Get OS and architecture with the same format
    local os=$(detect_os "$format")
    local arch=$(detect_arch "$format")

    # Return "os-arch"
    echo "$os-$arch"
}

# Setup dependencies
setup_dependencies() {
    if [[ "$SKIP_STEPS" == *"deps"* ]]; then
        log "Skipping dependencies setup"
        return
    fi

    log "Setting up dependencies..."

    # Check if uv is already installed
    if command -v uv &>/dev/null; then
        log "uv is already installed."
        log "Current version: $(uv --version)"
    else
        log "uv is not installed. Installing now..."

        # Try with curl
        if command -v curl &>/dev/null; then
            curl -LsSf https://astral.sh/uv/install.sh | sh
        # Try with wget
        elif command -v wget &>/dev/null; then
            wget -qO- https://astral.sh/uv/install.sh | sh
        else
            error "Please install curl or wget."
        fi

        # Verify installation
        if command -v uv &>/dev/null; then
            log "uv has been successfully installed."
            log "Current version: $(uv --version)"
        else
            error "uv installation failed."
        fi
    fi

    # Create virtual environment if needed
    if [ ! -d "$VENV_DIR" ]; then
        log "Creating virtual environment..."
        uv venv "$VENV_DIR"
    fi

    log "Syncing dependencies with uv..."
    uv sync --group dev --group test

    log "Installing pre-commit hooks..."
    $VENV_DIR/bin/pre-commit install

    if [ ! -f "django-vars.env" ]; then
        warn "django-vars.env file not found. Reading settings from environment variables..."
        export DJANGO_READ_VARS_FILE=False
    else
        log "Reading settings from django-vars.env file..."
        export DJANGO_READ_VARS_FILE=True
    fi
}

# Setup TailwindCSS
setup_tailwind() {
    if [[ "$SKIP_STEPS" == *"tailwind"* ]]; then
        log "Skipping TailwindCSS setup"
        return
    fi

    log "Setting up TailwindCSS..."

    # Download TailwindCSS if needed
    if [ ! -f "$VENV_TAILWIND" ]; then
        mkdir -p "$(dirname "$VENV_TAILWIND")"
        if command -v tailwindcss &>/dev/null; then
            TAILWIND_VERSION=$(tailwindcss --help 2>/dev/null | head -n 1 | grep -o "v[0-9]\+\.[0-9]\+\.[0-9]\+")
            log "Found tailwindcss ${TAILWIND_VERSION:-unknown version} in PATH, creating symlink..."
            ln -sf "$(command -v tailwindcss)" "$VENV_TAILWIND"
        else
            log "Downloading TailwindCSS..."

            # Detect platform
            PLATFORM=$(detect_platform alt)
            log "Detected platform: $PLATFORM"

            curl -sL "https://github.com/tailwindlabs/tailwindcss/releases/latest/download/tailwindcss-${PLATFORM}" -o "$VENV_TAILWIND"
            chmod +x "$VENV_TAILWIND"
        fi
    fi

    # Make sure folders exist
    mkdir -p assets/css static/css
}

# Setup mailpit
setup_mailpit() {
    if [[ "$SKIP_STEPS" == *"mailpit"* ]]; then
        log "Skipping Mailpit setup"
        return
    fi

    log "Setting up Mailpit..."

    # Download Mailpit if necessary
    if [ ! -f "$VENV_MAILPIT" ]; then
        mkdir -p "$(dirname "$VENV_MAILPIT")"
        if command -v mailpit &>/dev/null; then
            MAILPIT_VERSION=$(mailpit version)
            log "Found Mailpit ${MAILPIT_VERSION:-unknown version} in PATH, creating symlink..."
            ln -sf "$(command -v mailpit)" "$VENV_MAILPIT"
        else
            log "Downloading Mailpit..."

            # Detect platform
            PLATFORM=$(detect_platform)
            log "Detected platform: $PLATFORM"

            curl -sL "https://github.com/axllent/mailpit/releases/latest/download/mailpit-${PLATFORM}.tar.gz" | tar -xz -C "$VENV_DIR/bin" mailpit
            chmod +x "$VENV_MAILPIT"
        fi
    fi
}

# Initialize Django
initialize_django() {
    if [[ "$SKIP_STEPS" == *"django"* ]]; then
        log "Skipping Django initialization"
        return
    fi

    log "Initializing Django..."

    # Migrations
    log "Running migrations..."
    "$VENV_PYTHON" manage.py makemigrations
    "$VENV_PYTHON" manage.py migrate

    # Create superuser
    log "Creating superuser..."
    export DJANGO_SUPERUSER_EMAIL
    export DJANGO_SUPERUSER_PASSWORD
    "$VENV_PYTHON" manage.py createsuperuser --noinput || warn "Superuser creation failed"

    # Create test users
    if [ "$GEN_FAKE_DATA" = "true" ]; then
        log "Creating test users..."
        "$VENV_PYTHON" manage.py createuser --email=user1@example.com || warn "User1 creation failed"
        "$VENV_PYTHON" manage.py createuser --email=user2@example.com || warn "User2 creation failed"
    fi

    # Generate test data if requested
    if [ "$GEN_FAKE_DATA" = "true" ]; then
        log "Generating fake talks..."
        "$VENV_PYTHON" manage.py generate_fake_talks --count "$FAKE_DATA_COUNT" || warn "Failed to generate fake data"
    fi

    # Sync with Pretalx
    if [ "$PRETALX_SYNC" = "true" ]; then
        log "Syncing with Pretalx..."
        "$VENV_PYTHON" manage.py import_pretalx_talks --verbosity 3 || warn "Failed to import talks from Pretalx"
    fi

    # Sync with Google Sheets
    if [ "$IMPORT_STREAMS" = "true" ]; then
        log "Importing streams from Google Sheets..."
        "$VENV_PYTHON" manage.py import_livestream_urls || warn "Failed to import livestreams from Google Sheets"
    fi
}

# Start services
start_services() {
    log "Starting services..."

    # Only build TailwindCSS if not skipped
    if [[ "$SKIP_STEPS" != *"tailwind"* ]]; then
        if [ "$DEBUG" = "true" ]; then
            # Start TailwindCSS in background
            log "Starting TailwindCSS watcher..."
            "$VENV_TAILWIND" -i ./assets/css/input.css -o ./static/css/tailwind.min.css --watch &
            TAILWIND_PID=$!
            # Clean up tailwind when the script exits
            trap 'kill $TAILWIND_PID 2>/dev/null' EXIT
        else
            log "Building minified TailwindCSS..."
            "$VENV_TAILWIND" -i ./assets/css/input.css -o ./static/css/tailwind.min.css --minify
        fi
    fi

    # Start Mailpit if not skipped
    if [[ "$SKIP_STEPS" != *"mailpit"* ]]; then
        log "Starting Mailpit..."
        "$VENV_MAILPIT" &
        MAILPIT_PID=$!

        # Clean up mailpit when the script exits
        trap 'kill $MAILPIT_PID 2>/dev/null' EXIT
    fi

    # Start Django server if requested
    if [ "$RUN_SERVER" = "true" ]; then
        log "Starting Django development server..."
        "$VENV_PYTHON" manage.py runserver ${DJANGO_PORT}
    else
        log "Setup complete (server not started)"
    fi
}

# Collect static files
collect_static() {
    if [[ "$SKIP_STEPS" == *"collectstatic"* ]]; then
        log "Skipping static files collection"
        return
    fi
    log "Collecting static files..."
    "$VENV_PYTHON" manage.py collectstatic --noinput
}

# Main execution flow
main() {
    log "Starting development environment setup..."
    setup_dependencies
    setup_tailwind
    setup_mailpit
    initialize_django
    start_services
    collect_static
}

# Run the script
main
