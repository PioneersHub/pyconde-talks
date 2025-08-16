# Conference Talks Website

This repository provides a reusable Django app to publish talks, schedules, and Q&A for different
events (e.g., PyConDE, PyData Berlin).

## Event configuration

All configuration variables are listed in the `django-vars.env` file. If you set
`DJANGO_READ_VARS_FILE=true`, the values will be read from that file otherwise you'll need to export
them as environment variables.

## Development

The most straightforward way to run the project is to open it in a dev container or run the
`dev-setup.sh` script locally:

```
RUN_SERVER=true PRETALX_SYNC=false IMPORT_STREAMS=false GEN_FAKE_DATA=true .vscode/scripts/dev-setup.sh
```

It will download [uv] and create a virtual environment (`.venv`) with [tailwindCSS], [Mailpit],
[Python] and all other dependencies required for development and testing.

It will also run migrations, create test users (`user1@example.com`, `user2@example.com` and
`admin@example.com`), generate fake data and start the server in debug mode (on port
[8000](http://127.0.0.1:8000/)) and a Mailpit instance to test emails (on port
[8025](http://127.0.0.1:8025/)).

## Authentication

All users can login via email, including admins:

- go to [http://127.0.0.1:8000](http://127.0.0.1:8000)
- enter `user1@example.com`, `user2@example.com` or `admin@example.com`
- go to [http://localhost:8025/](http://localhost:8025/) to see the emails sent
- copy the validation code to the form

Admins can also login with password in the admin interface:

- open http://127.0.0.1:8000/admin/
- login with admin:
  - email: `admin@example.com`
  - password: `admin`
- browse to http://127.0.0.1:8000/

## Testing

The project uses [pytest] for testing. To run the tests, run:

```
uv run pytest
```

## Deployment

This repository includes example files for a deployment using [Docker], [PostgreSQL], [Nginx], and
[Mailgun]. Adapt them to your needs.

Example:

```bash
sudo mkdir -p ${MEDIA_DIR}
sudo mkdir -p ${STATIC_DIR}
sudo mkdir -p ${LOGS_DIR}

cd docker
docker buildx bake --allow=fs.read=..

mv staticfiles/* ${STATIC_DIR}/
sudo ./ensure_permissions.sh
docker compose up -d

sudo vi /etc/nginx/sites-available/${APP_DOMAIN}
sudo ln -s /etc/nginx/sites-available/${APP_DOMAIN} /etc/nginx/sites-enabled/${APP_DOMAIN}
sudo certbot --nginx -d ${APP_DOMAIN}
sudo systemctl reload nginx
```

[Docker]: https://www.docker.com/
[Mailgun]: https://www.mailgun.com/
[Mailpit]: https://mailpit.axllent.org
[Nginx]: https://nginx.org/
[PostgreSQL]: https://www.postgresql.org/
[Python]: https://www.python.org/
[pytest]: https://docs.pytest.org/en/stable/
[tailwindCSS]: https://tailwindcss.com/
[uv]: https://docs.astral.sh/uv
