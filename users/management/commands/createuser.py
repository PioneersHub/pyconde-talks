"""Management command to create a regular user with email."""

from typing import Any

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandParser

from users.models import CustomUser, InvalidEmailError


class Command(BaseCommand):
    """
    Django command to create a regular user.

    This command creates a non-superuser account with the specified email address.
    The user will not have a password set, following the application's authentication model.
    """

    help = "Create a regular user with the specified email address"

    def add_arguments(self, parser: CommandParser) -> None:
        """
        Add command line arguments.

        Args:
            parser: The command argument parser

        """
        parser.add_argument(
            "--email",
            type=str,
            required=True,
            help="Email address for the new user",
        )

    def handle(self, *args: Any, **options: dict[str, Any]) -> None:
        """
        Handle the command execution.

        Creates a new user with the specified email address.
        The user will be created with is_active=True by default.

        Args:
            *args: Additional positional arguments
            **options: Command options including the email address

        """
        User = get_user_model()
        email = options["email"]

        try:
            user: CustomUser = User.objects.create_user(
                email=email,
                is_active=True,
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"Successfully created user with email: {user.email}",
                ),
            )
        except InvalidEmailError:
            self.stdout.write(
                self.style.ERROR(f"Invalid email format: {email}"),
            )
            raise
        except ValidationError as e:
            self.stdout.write(
                self.style.ERROR(f"Validation error: {e}"),
            )
            raise
