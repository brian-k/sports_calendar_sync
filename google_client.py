from __future__ import annotations

from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def get_calendar_service(credentials_path: str, token_path: str = None) -> Any:
    """Create calendar service using service account credentials."""
    creds = service_account.Credentials.from_service_account_file(
        credentials_path, scopes=SCOPES
    )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)