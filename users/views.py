"""Views for user authentication."""

from allauth.account.adapter import get_adapter
from allauth.account.forms import LoginForm
from allauth.account.views import RequestLoginCodeView
from django.contrib.auth import get_user_model
from django.http import HttpResponse


class CustomRequestLoginCodeView(RequestLoginCodeView):
    """
    Custom view that overrides the default login code request process.

    This view checks if the email is authorized (whitelist, superuser or via API) before proceeding.
    """

    def form_valid(self, form: LoginForm) -> HttpResponse:
        """
        Check if the email is authorized before proceeding.

        If authorized but user doesn't exist, create the user.
        """
        email = form.cleaned_data["email"].lower()
        adapter = get_adapter(self.request)

        # Check if the email is authorized
        if not adapter.is_email_authorized(email):
            form.add_error("email", "This email is not authorized for access.")
            return self.form_invalid(form)

        # If the email is authorized, create user if needed
        UserModel = get_user_model()  # noqa: N806
        if not UserModel.objects.filter(email=email).exists():
            try:
                user = UserModel.objects.create_user(email=email, is_active=True)
            except Exception as e:
                form.add_error("email", f"Error creating user: {e!s}")
                return self.form_invalid(form)

        # Proceed with standard login code process
        return super().form_valid(form)
