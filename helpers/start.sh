#!/bin/bash

# Sync dependencies with uv
uv sync

# Python interpreter path
VENV_PYTHON=".venv/bin/python"

# Django initialization
$VENV_PYTHON manage.py makemigrations && \
$VENV_PYTHON manage.py migrate && \

# Create superuser
export DJANGO_SUPERUSER_EMAIL=admin@example.com
export DJANGO_SUPERUSER_PASSWORD=PyConDE_2025
$VENV_PYTHON manage.py createsuperuser --noinput && \

# Create regular users
$VENV_PYTHON manage.py createuser --email=user1@example.com && \
$VENV_PYTHON manage.py createuser --email=user2@example.com && \


# Fill the database with testing data
$VENV_PYTHON manage.py generate_fake_talks --count 50 && \

# Run server
$VENV_PYTHON manage.py runserver