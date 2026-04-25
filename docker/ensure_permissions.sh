#!/bin/bash
set -euo pipefail

# Run as root in the host
if [[ "$(id -u)" -ne 0 ]]; then
    echo "This script must be run as root"
    exit 1
fi

# Base domain/app folder. Override via APP_DOMAIN env var.
APP_DOMAIN="${APP_DOMAIN:-talks}"

# Directories (can be overridden via env)
MEDIA_DIR="${MEDIA_DIR:-/var/opt/${APP_DOMAIN}/media/}"
LOGS_DIR="${LOGS_DIR:-/var/log/${APP_DOMAIN}/}"
STATIC_DIR="${STATIC_DIR:-/var/cache/${APP_DOMAIN}/staticfiles/}"

# Resolve default Nginx UID/GID from the nginx or www-data user
_default_nginx_uid=33
_default_nginx_gid=33
for _u in nginx www-data; do
    if id "$_u" >/dev/null 2>&1; then
        _default_nginx_uid="$(id -u "$_u")"
        _default_nginx_gid="$(id -g "$_u")"
        break
    fi
done

# UIDs/GIDs (can be overridden via env)
NGINX_UID="${NGINX_UID:-$_default_nginx_uid}"
NGINX_GID="${NGINX_GID:-$_default_nginx_gid}"
DJANGO_UID="${DJANGO_UID:-10000}"
DJANGO_GID="${DJANGO_GID:-999}"

# Static files: Nginx needs read only
mkdir -p "${STATIC_DIR}" &&
    chown -R "${NGINX_UID}:${NGINX_GID}" "${STATIC_DIR}" &&
    chmod -R 400 "${STATIC_DIR}" &&
    find "${STATIC_DIR}" -type d -print0 | xargs -0 chmod 500

# Media: Django needs read and write. Nginx needs read only
mkdir -p "${MEDIA_DIR}/talk_images/${DEFAULT_EVENT}" &&
    chown -R "${DJANGO_UID}:${NGINX_GID}" "${MEDIA_DIR}" &&
    chmod -R 640 "${MEDIA_DIR}" &&
    find "${MEDIA_DIR}" -type d -print0 | xargs -0 chmod 2750

# Logs: Django needs read and write
mkdir -p "${LOGS_DIR}" &&
    chown -R "${DJANGO_UID}:${DJANGO_GID}" "${LOGS_DIR}" &&
    chmod -R 600 "${LOGS_DIR}" &&
    find "${LOGS_DIR}" -type d -print0 | xargs -0 chmod 700
