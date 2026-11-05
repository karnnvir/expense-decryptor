"""Download statement PDFs from Gmail into statements/<bank folder>.

    .venv/bin/python fetch_gmail.py --since 2026/08/01

Read-only Gmail access. The first run opens a browser for you to approve it; the approval is kept in
config/gmail_token.json (git-ignored). Needs an OAuth client file at config/gmail_credentials.json:
see README.md, "Gmail fetch".
"""
import argparse
import base64
import json
import re
from datetime import datetime
from pathlib import Path

import yaml
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CREDENTIALS = Path("config/gmail_credentials.json")
TOKEN = Path("config/gmail_token.json")
FETCHED = Path("config/gmail_fetched.json")  # message ids already downloaded


def gmail():
    creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES) if TOKEN.exists() else None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError:  # Google drops a "Testing" app's approval after 7 days: ask again
                creds = None
        if not creds or not creds.valid:
            print("Approve read-only Gmail access in the browser window that just opened.", flush=True)
            creds = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS), SCOPES).run_local_server(port=0)
        TOKEN.write_text(creds.to_json())
        TOKEN.chmod(0o600)
    return build("gmail", "v1", credentials=creds)


def pdf_parts(payload):
    """Yield (filename, attachmentId or inline data) for every PDF in a message, however deeply nested."""
    for part in payload.get("parts", []) or []:
        yield from pdf_parts(part)
    name = payload.get("filename") or ""
    if name.lower().endswith(".pdf"):
        body = payload.get("body", {})
        yield name, body.get("attachmentId"), body.get("data")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True, help="only emails on or after this date, YYYY/MM/DD")
    ap.add_argument("--statements", default="statements")
    args = ap.parse_args()

    sources = yaml.safe_load(Path("config/gmail_sources.yaml").read_text())["sources"]
    fetched = set(json.loads(FETCHED.read_text())) if FETCHED.exists() else set()
    service = gmail().users()
    saved = 0

    for src in sources:
        query = f"from:{src['from']} has:attachment filename:pdf after:{args.since} {src.get('query', '')}"
        messages, token = [], None
        while True:
            resp = service.messages().list(userId="me", q=query, pageToken=token).execute()
            messages += resp.get("messages", [])
            token = resp.get("nextPageToken")
            if not token:
                break

        folder = Path(args.statements, src["folder"])
        folder.mkdir(parents=True, exist_ok=True)
        for ref in messages:
            if ref["id"] in fetched:
                continue
            msg = service.messages().get(userId="me", id=ref["id"]).execute()
            sent = datetime.fromtimestamp(int(msg["internalDate"]) / 1000)
            for name, attachment_id, inline in pdf_parts(msg["payload"]):
                if src.get("filename") and not re.search(src["filename"], name):
                    continue  # e.g. another customer's statement sent by the same bank
                data = inline or service.messages().attachments().get(
                    userId="me", messageId=ref["id"], id=attachment_id).execute()["data"]
                # Some banks reuse one file name every month, so prefix the email's date.
                target = folder / f"{sent:%Y-%m-%d}_{name}"
                if not target.exists():
                    target.write_bytes(base64.urlsafe_b64decode(data))
                    print(f"saved  {target}")
                    saved += 1
            fetched.add(ref["id"])

    FETCHED.write_text(json.dumps(sorted(fetched)))
    print(f"{saved} new PDF(s) saved")


if __name__ == "__main__":
    main()
