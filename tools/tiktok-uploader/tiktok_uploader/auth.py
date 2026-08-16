"""TikTok OAuth 2.0 (authorization code + PKCE).

Docs: https://developers.tiktok.com/doc/oauth-user-access-token-management
The tokens belong to the account that approves the consent screen — this tool
only ever posts to that account.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
import urllib.parse
from typing import Any

import requests

from .config import Config
from .store import TokenStore

AUTHORIZE_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
REVOKE_URL = "https://open.tiktokapis.com/v2/oauth/revoke/"

# video.publish  -> Direct Post (straight to the profile)
# video.upload   -> send to drafts/inbox instead
# user.info.basic-> creator nickname/avatar for the dashboard
SCOPES = "user.info.basic,video.publish,video.upload"

# Refresh a little early so a long upload never starts on a token about to die.
EXPIRY_SKEW_SECONDS = 120


class AuthError(RuntimeError):
    pass


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


class Auth:
    def __init__(self, cfg: Config, tokens: TokenStore):
        self.cfg = cfg
        self.tokens = tokens
        self._pending: dict[str, str] = {}  # state -> code_verifier

    # ---------- step 1: send the user to TikTok ----------

    def authorize_url(self) -> str:
        if not self.cfg.configured:
            raise AuthError("TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET are not set")
        state = secrets.token_urlsafe(24)
        verifier, challenge = _pkce_pair()
        self._pending[state] = verifier
        params = {
            "client_key": self.cfg.client_key,
            "scope": SCOPES,
            "response_type": "code",
            "redirect_uri": self.cfg.redirect_uri,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return AUTHORIZE_URL + "?" + urllib.parse.urlencode(params)

    # ---------- step 2: TikTok redirects back with ?code= ----------

    def exchange_code(self, code: str, state: str) -> dict[str, Any]:
        verifier = self._pending.pop(state, None)
        if verifier is None:
            raise AuthError("Unknown or replayed OAuth state — start the login again")
        payload = {
            "client_key": self.cfg.client_key,
            "client_secret": self.cfg.client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": self.cfg.redirect_uri,
            "code_verifier": verifier,
        }
        return self._token_request(payload)

    def refresh(self) -> dict[str, Any]:
        data = self.tokens.load()
        if not data or not data.get("refresh_token"):
            raise AuthError("No refresh token stored — connect the account first")
        payload = {
            "client_key": self.cfg.client_key,
            "client_secret": self.cfg.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": data["refresh_token"],
        }
        return self._token_request(payload)

    def _token_request(self, payload: dict[str, str]) -> dict[str, Any]:
        resp = requests.post(
            TOKEN_URL,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        try:
            body = resp.json()
        except ValueError:
            raise AuthError(f"Token endpoint returned non-JSON ({resp.status_code})")

        # TikTok reports OAuth failures as {"error": ..., "error_description": ...}
        if resp.status_code >= 400 or body.get("error"):
            raise AuthError(
                body.get("error_description") or body.get("error") or
                f"Token request failed ({resp.status_code})"
            )

        now = time.time()
        stored = {
            "access_token": body["access_token"],
            "refresh_token": body.get("refresh_token", ""),
            "open_id": body.get("open_id", ""),
            "scope": body.get("scope", ""),
            "expires_at": now + float(body.get("expires_in", 0)),
            "refresh_expires_at": now + float(body.get("refresh_expires_in", 0)),
            "obtained_at": now,
        }
        self.tokens.save(stored)
        return stored

    # ---------- used on every API call ----------

    def access_token(self) -> str:
        data = self.tokens.load()
        if not data:
            raise AuthError("Account is not connected")
        if time.time() >= data.get("expires_at", 0) - EXPIRY_SKEW_SECONDS:
            data = self.refresh()
        return data["access_token"]

    def status(self) -> dict[str, Any]:
        data = self.tokens.load()
        if not data:
            return {"connected": False}
        now = time.time()
        return {
            "connected": True,
            "open_id": data.get("open_id", ""),
            "scope": data.get("scope", ""),
            "expires_in": max(0, int(data.get("expires_at", 0) - now)),
            "refresh_expires_in": max(0, int(data.get("refresh_expires_at", 0) - now)),
            "can_direct_post": "video.publish" in data.get("scope", ""),
        }

    def disconnect(self) -> None:
        data = self.tokens.load()
        if data and data.get("access_token"):
            try:
                requests.post(
                    REVOKE_URL,
                    data={
                        "client_key": self.cfg.client_key,
                        "client_secret": self.cfg.client_secret,
                        "token": data["access_token"],
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    timeout=15,
                )
            except requests.RequestException:
                pass  # local logout still proceeds
        self.tokens.clear()
