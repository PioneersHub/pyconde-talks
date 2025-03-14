"""
The module provides utility functions to interact with the PyConDE ticketing API.

Link to the API documentation: https://val.pycon.de/docs
"""

from http import HTTPStatus
from typing import Any
from urllib.parse import urljoin

import requests


API_URL = "https://val.pycon.de"


def _post(endpoint: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    url = urljoin(API_URL, endpoint)
    headers = {"Content-Type": "application/json"}
    response: requests.Response = requests.post(url, json=payload, headers=headers, timeout=5)
    return response.status_code, response.json()


def _validate_name(ticket_id: str, name: str) -> dict[str, Any]:
    payload = {"ticket_id": ticket_id, "name": name}
    status_code, response = _post("/tickets/validate_name/", payload)
    if status_code == HTTPStatus.UNPROCESSABLE_ENTITY:
        raise requests.exceptions.HTTPError(response["detail"])

    return response


def _validate_email(email: str) -> dict[str, Any]:
    payload = {"email": email}
    status_code, response = _post("/tickets/validate_email/", payload)
    if status_code == HTTPStatus.UNPROCESSABLE_ENTITY:
        raise requests.exceptions.HTTPError(response["detail"])

    return response


def is_valid_name(ticket_id: str, name: str) -> bool:
    """
    Check if the name is valid for the given ticket_id.
    """
    response = _validate_name(ticket_id, name)
    return response["is_attendee"]


def is_valid_email(email: str) -> bool:
    """
    Check if the email is valid.
    """
    response = _validate_email(email)
    return response["valid"]
