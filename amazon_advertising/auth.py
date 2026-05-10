import requests
import time
from dataclasses import dataclass


@dataclass
class AdvCredentials:
    client_id: str
    client_secret: str
    refresh_token: str
    profile_id: str = ""  # Advertising API profile ID


class AdvAuth:
    TOKEN_URL = "https://api.amazon.co.jp/auth/o2/token"
    API_BASE = "https://advertising-api-fe.amazon.com"

    def __init__(self, credentials: AdvCredentials):
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
        headers = {
            "Authorization": f"Bearer {self.get_access_token()}",
            "Amazon-Advertising-API-ClientId": self.credentials.client_id,
            "Content-Type": "application/json",
        }
        if self.credentials.profile_id:
            headers["Amazon-Advertising-API-Scope"] = self.credentials.profile_id
        return headers

    def get_profiles(self) -> list[dict]:
        resp = requests.get(
            f"{self.API_BASE}/v2/profiles",
            headers=self.get_headers(),
        )
        resp.raise_for_status()
        return resp.json()
