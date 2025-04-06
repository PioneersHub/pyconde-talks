#!/bin/bash

# Script name: dev-setup.sh
# Description: setup development environment

# Default configuration (can be overridden by environment variables)
VENV_DIR="${VENV_DIR:-.venv}"
VENV_PYTHON="${VENV_DIR}/bin/python"
VENV_TAILWIND="${VENV_DIR}/bin/tailwindcss"
DJANGO_SUPERUSER_EMAIL="${DJANGO_SUPERUSER_EMAIL:-admin@example.com}"
DJANGO_SUPERUSER_PASSWORD="${DJANGO_SUPERUSER_PASSWORD:-PyConDE_2025}"
FAKE_DATA_COUNT="${FAKE_DATA_COUNT:-50}"
RUN_SERVER="${RUN_SERVER:-true}"
SKIP_STEPS="${SKIP_STEPS:-}"

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

# Platform detection
detect_platform() {
    local OS="linux"
    local ARCH="x64"

    # Detect OS
    if [[ "$OSTYPE" == "darwin"* ]]; then
        OS="macos"
    elif [[ "$OSTYPE" == "msys" || "$OSTYPE" == "cygwin" ]]; then
        OS="windows"
    fi

    # Detect architecture
    if [[ "$(uname -m)" == "arm64" || "$(uname -m)" == "aarch64" ]]; then
        ARCH="arm64"
    fi

    echo "${OS}-${ARCH}"
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
    uv sync --all-groups

    log "Installing pre-commit hooks..."
    $VENV_DIR/bin/pre-commit install

    if [ ! -f ".env" ]; then
        log "Creating .env file..."
        cp env.example .env
    else
        log ".env file already exists, skipping creation."
    fi
}

# Setup TailwindCSS
setup_tailwind() {
    if [[ "$SKIP_STEPS" == *"tailwind"* ]]; then
        log "Skipping TailwindCSS setup"
        return
    fi

    log "Setting up TailwindCSS..."

    # Detect platform
    PLATFORM=$(detect_platform)
    log "Detected platform: $PLATFORM"

    # Download TailwindCSS if needed
    if [ ! -f "$VENV_TAILWIND" ]; then
        if command -v tailwindcss &>/dev/null; then
            TAILWIND_VERSION=$(tailwindcss --help 2>/dev/null | head -n 1 | grep -o "v[0-9]\+\.[0-9]\+\.[0-9]\+")
            log "Found tailwindcss ${TAILWIND_VERSION:-unknown version} in PATH, creating symlink..."
            ln -sf "$(command -v tailwindcss)" "$VENV_TAILWIND"
        else
            log "Downloading TailwindCSS..."
            mkdir -p "$(dirname "$VENV_TAILWIND")"
            curl -sL "https://github.com/tailwindlabs/tailwindcss/releases/latest/download/tailwindcss-${PLATFORM}" -o "$VENV_TAILWIND"
            chmod +x "$VENV_TAILWIND"
        fi
    fi

    # Make sure folders exist
    mkdir -p assets/css static/css
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
    log "Creating test users..."
    "$VENV_PYTHON" manage.py createuser --email=user1@example.com || warn "User1 creation failed"
    "$VENV_PYTHON" manage.py createuser --email=user2@example.com || warn "User2 creation failed"

    # Generate test data
    log "Generating fake talks..."
    "$VENV_PYTHON" manage.py generate_fake_talks --count "$FAKE_DATA_COUNT" || warn "Failed to generate fake data"
}

# Start services
start_services() {
    log "Starting services..."

    # Only build TailwindCSS if not skipped
    if [[ "$SKIP_STEPS" != *"tailwind"* ]]; then
        # Start TailwindCSS in background
        # Note: will use --minify in production
        log "Starting TailwindCSS watcher..."
        "$VENV_TAILWIND" -i ./assets/css/input.css -o ./static/css/tailwind.min.css --watch &
        TAILWIND_PID=$!

        # Clean up tailwind when the script exits
        trap 'kill $TAILWIND_PID 2>/dev/null' EXIT
    fi

    # Start Django server if requested
    if [ "$RUN_SERVER" = "true" ]; then
        log "Starting Django development server..."
        "$VENV_PYTHON" manage.py runserver
    else
        log "Setup complete (server not started)"
    fi
}

# Main execution flow
main() {
    log "Starting development environment setup..."
    setup_dependencies
    setup_tailwind
    initialize_django
    start_services
}

# Run the script
main
