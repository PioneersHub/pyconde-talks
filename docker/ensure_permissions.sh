#!/bin/bash

# Run as root in the host
if [ "$(id -u)" -ne 0 ]; then
    echo "This script must be run as root"
    exit 1
fi

MEDIA_DIR=/var/opt/talks.pycon.de/media/
LOGS_DIR=/var/log/talks.pycon.de/
STATIC_DIR=/var/cache/talks.pycon.de/staticfiles/
NGINX_UID=33
DJANGO_UID=10000

# Static files: Nginx needs read only
mkdir -p ${STATIC_DIR} &&
    chown -R ${NGINX_UID}:${NGINX_UID} ${STATIC_DIR} &&
    chmod -R 400 ${STATIC_DIR} &&
    find ${STATIC_DIR} -type d -print0 | xargs -0 chmod 500

# Media: Django needs read and write. Nginx needs read only
mkdir -p ${MEDIA_DIR} &&
    chown -R ${DJANGO_UID}:${NGINX_UID} ${MEDIA_DIR} &&
    chmod -R 640 ${MEDIA_DIR} &&
    find ${MEDIA_DIR} -type d -print0 | xargs -0 chmod 750 &&
    chmod -R g+s ${MEDIA_DIR}

# Logs: Django needs read and write
mkdir -p ${LOGS_DIR} &&
    chown -R ${DJANGO_UID}:${DJANGO_UID} ${LOGS_DIR} &&
    chmod -R 600 ${LOGS_DIR} &&
    find ${LOGS_DIR} -type d -print0 | xargs -0 chmod 700
