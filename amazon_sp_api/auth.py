import requests
import time
from dataclasses import dataclass


@dataclass
class SPAPICredentials:
    client_id: str
    client_secret: str
    refresh_token: str
    marketplace_id: str = "A1VC38T7YXB528"  # Amazon.co.jp


class SPAPIAuth:
    TOKEN_URL = "https://api.amazon.com/auth/o2/token"

    def __init__(self, credentials: SPAPICredentials):
        self.credentials = credentials
        self._access_token: str | None = None
        self._token_expires_at: float = 0

    def get_access_token(self) -> str:
        if self._access_token and time.time() < self._token_expires_at - 60:
            return self._access_token

        response = requests.post(
            self.TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": self.credentials.refresh_token,
                "client_id": self.credentials.client_id,
                "client_secret": self.credentials.client_secret,
            },
        )
        response.raise_for_status()
        data = response.json()

        self._access_token = data["access_token"]
        self._token_expires_at = time.time() + data.get("expires_in", 3600)
        return self._access_token

    def get_headers(self) -> dict:
        return {
            "x-amz-access-token": self.get_access_token(),
            "Content-Type": "application/json",
        }
