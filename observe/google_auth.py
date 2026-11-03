"""Auth headers for Google Maps Platform APIs: service account (OAuth) or API key."""

from __future__ import annotations

from pathlib import Path

OAUTH_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


class GoogleAuthError(Exception):
    pass


class GoogleAuth:
    def __init__(self, service_account_file: Path | None, api_key: str):
        self._api_key = api_key
        self._creds = None
        self._project_id = None
        if service_account_file is not None:
            from google.oauth2 import service_account

            try:
                self._creds = service_account.Credentials.from_service_account_file(
                    str(service_account_file), scopes=OAUTH_SCOPES
                )
            except (ValueError, OSError) as e:  # json errors are ValueError subclasses
                raise GoogleAuthError(f"service account file {service_account_file} is unusable: {e}") from e
            self._project_id = self._creds.project_id
        elif not api_key:
            raise GoogleAuthError("no Google credentials: set GOOGLE_SERVICE_ACCOUNT_FILE or GOOGLE_MAPS_API_KEY")

    def headers(self) -> dict[str, str]:
        if self._creds is None:
            return {"X-Goog-Api-Key": self._api_key}
        if not self._creds.valid:
            import google.auth.exceptions
            import google.auth.transport.requests

            try:
                self._creds.refresh(google.auth.transport.requests.Request())
            except google.auth.exceptions.GoogleAuthError as e:
                raise GoogleAuthError(f"could not get an OAuth token from the service account: {e}") from e
        h = {"Authorization": f"Bearer {self._creds.token}"}
        if self._project_id:
            # Bills and checks API enablement against the service account's project.
            h["X-Goog-User-Project"] = self._project_id
        return h
