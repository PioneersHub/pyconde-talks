# PyCon DE & PyData 2025 talks website

This is the repository for the PyCon DE & PyData 2025 talks website.

## Development

To run the project locally, you need to have Python 3.13 installed.
This project uses [uv](https://docs.astral.sh/uv) to manage the dependencies
and the virtual environment. You can still use other tools like `pip` to manage
the dependencies though.

### Virtual environment

Create a virtual environment and install the dependencies:

```bash
uv venv
uv sync
```

Activate the virtual environment:

```bash
source .venv/bin/activate
```

### Django

Assuming you have the virtual environment activated, you can now run the Django
commands to run the development server locally:

```bash
python manage.py makemigrations
python manage.py migrate
python manage.py runserver
```

Other commands are available, like creating a superuser or creating regular users:

```bash
python manage.py createsuperuser --email admin@example.com
python manage.py createuser --email user1@example.com
```

Fill the database with testing data:

```bash
python manage.py generate_fake_talks --count 50
```

How to run locally:
```
RUN_SERVER=true PRETALX_SYNC=false IMPORT_STREAMS=false GEN_FAKE_DATA=true .vscode/scripts/dev-setup.sh
```

How to connect as admin:
* open this [http://127.0.0.1:8000/admin/](http://127.0.0.1:8000/admin/)
* log in with admin:
    * email: `admin@example.com`
    * password: `PyConDE_2025`
* browse to [http://127.0.0.1:8000/](http://127.0.0.1:8000/)


How to connect as user:
* go to [http://127.0.0.1:8000/admin/](http://127.0.0.1:8000/admin/)
* enter `user1@example.com` or `user2@example.com`
* go to [http://localhost:8025/](http://localhost:8025/) to see the emails sent.
* copy the validation code to the form.
