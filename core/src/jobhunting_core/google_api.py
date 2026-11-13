"""The agent's own Google account, read from a local Bitwarden `bw serve`; secrets never enter arguments or environment."""
from __future__ import annotations

import json
import os
import urllib.request

import google_auth_httplib2
import httplib2
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# The agent's OAuth client; client IDs are not secret.
AGENT_GOOGLE_CLIENT_ID = os.environ.get("JOBHUNTING_GOOGLE_CLIENT_ID", "")


def google_secrets() -> tuple[str, str]:
    """(client secret, refresh token) of the agent's Google account."""
    with urllib.request.urlopen("http://127.0.0.1:8087/object/item/agent-google", timeout=5) as response:
        fields = {field["name"]: field["value"] for field in json.load(response)["data"]["fields"]}
    return fields["AGENT_GOOGLE_CLIENT_SECRET"], fields["AGENT_GOOGLE_REFRESH_TOKEN"]


def build_service():
    """A Sheets client; httplib2 is not thread-safe, so each thread builds its own."""
    client_secret, refresh_token = google_secrets()
    credentials = Credentials(
        token=None, refresh_token=refresh_token, token_uri="https://oauth2.googleapis.com/token",
        client_id=AGENT_GOOGLE_CLIENT_ID, client_secret=client_secret,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    http = google_auth_httplib2.AuthorizedHttp(credentials, http=httplib2.Http(timeout=30))
    return build("sheets", "v4", http=http, cache_discovery=False)
