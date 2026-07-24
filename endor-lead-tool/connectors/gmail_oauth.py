"""Google Gmail API OAuth2 Taslak (Draft) oluşturucu (v1.4).

Bu modül, burak@endor.agency hesabına doğrudan GSuite taslağı atar.
"""
from __future__ import annotations
import base64
import json
import logging
import os
import time
from email.message import EmailMessage
from pathlib import Path

logger = logging.getLogger("endor.gmail_oauth")

CLIENT_SECRET_FILE = Path("client_secret.json")
TOKENS_FILE = Path("gmail_tokens.json")
REDIRECT_URI = "http://localhost:5000/api/gmail/callback"
GMAIL_SCOPES = "https://www.googleapis.com/auth/gmail.compose"


def is_configured() -> bool:
    """OAuth2 istemci istemi (client_secret.json) mevcut mu?"""
    return CLIENT_SECRET_FILE.exists()


def is_authenticated() -> bool:
    """burak@endor.agency hesabı doğrulanmış mı (gmail_tokens.json var mı)?"""
    return TOKENS_FILE.exists()


def _get_client_credentials() -> tuple[str, str]:
    if not CLIENT_SECRET_FILE.exists():
        raise FileNotFoundError("client_secret.json dosyası bulunamadı.")

    with open(CLIENT_SECRET_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Web veya Installed istemci tipi
    cfg = data.get("web") or data.get("installed")
    if not cfg:
        raise ValueError("client_secret.json içinde 'web' veya 'installed' istemci yapılandırması bulunamadı.")

    return cfg["client_id"], cfg["client_secret"]


def get_auth_url() -> str:
    """Google OAuth2 izin alma URL'sini üretir."""
    client_id, _ = _get_client_credentials()
    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": GMAIL_SCOPES,
        "access_type": "offline",
        "prompt": "consent",
    }
    from urllib.parse import urlencode
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)


def handle_auth_code(code: str) -> dict:
    """Google'dan dönen auth code'u token'lar ile takas eder ve kaydeder."""
    client_id, client_secret = _get_client_credentials()
    import requests
    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    resp.raise_for_status()
    tokens = resp.json()

    now = int(time.time())
    expires_in = tokens.get("expires_in", 3600)

    token_data = {
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token"),
        "expires_at": now + expires_in - 60,
        "created_at": now,
    }

    # Eski refresh token varsa koru
    if not token_data["refresh_token"] and TOKENS_FILE.exists():
        try:
            with open(TOKENS_FILE, "r", encoding="utf-8") as f:
                old = json.load(f)
                token_data["refresh_token"] = old.get("refresh_token")
        except Exception:
            pass

    with open(TOKENS_FILE, "w", encoding="utf-8") as f:
        json.dump(token_data, f, indent=2)

    logger.info("Gmail OAuth2 jetonları başarıyla saklandı.")
    return token_data


def get_valid_access_token() -> str:
    """Geçerli bir access token döndürür. Süresi dolmuşsa refresh eder."""
    if not TOKENS_FILE.exists():
        raise RuntimeError("Gmail OAuth henüz doğrulanmadı. Lütfen önce /api/gmail/auth adresinden giriş yapın.")

    with open(TOKENS_FILE, "r", encoding="utf-8") as f:
        token_data = json.load(f)

    now = int(time.time())
    if token_data.get("expires_at", 0) > now and token_data.get("access_token"):
        return token_data["access_token"]

    # Token yenileme (refresh)
    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        raise RuntimeError("Refresh token bulunamadı. Lütfen /api/gmail/auth ile tekrar izin verin.")

    client_id, client_secret = _get_client_credentials()
    import requests
    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=15,
    )
    resp.raise_for_status()
    refreshed = resp.json()

    token_data["access_token"] = refreshed["access_token"]
    token_data["expires_at"] = now + refreshed.get("expires_in", 3600) - 60

    with open(TOKENS_FILE, "w", encoding="utf-8") as f:
        json.dump(token_data, f, indent=2)

    return token_data["access_token"]


def create_gmail_draft(to_email: str, subject: str, body: str, cc: str = "abdullah@endor.agency") -> dict:
    """burak@endor.agency GSuite hesabına doğrudan Gmail Taslağı atar (Rich HTML formatında)."""
    access_token = get_valid_access_token()

    msg = EmailMessage()
    msg["To"] = to_email
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = subject

    # Plain text versiyonu
    msg.set_content(body)

    # Rich HTML versiyonu (Gmail Zengin Metin modunu tetikler; böylece imzadaki logo, renk ve fontlar bozulmaz)
    paragraphs = body.strip().split("\n\n")
    html_p_list = []
    for p in paragraphs:
        cleaned_p = p.strip().replace("\n", "<br>")
        if cleaned_p:
            html_p_list.append(f'<p style="margin: 0 0 16px 0; font-family: Arial, sans-serif; font-size: 14px; color: #222222; line-height: 1.5;">{cleaned_p}</p>')

    html_body = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: Arial, sans-serif; font-size: 14px; color: #222222; line-height: 1.5;">
{''.join(html_p_list)}
</body>
</html>"""

    msg.add_alternative(html_body, subtype="html")

    raw_bytes = msg.as_bytes()
    raw_b64 = base64.urlsafe_b64encode(raw_bytes).decode("utf-8")

    import requests
    resp = requests.post(
        "https://gmail.googleapis.com/gmail/v1/users/me/drafts",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json={"message": {"raw": raw_b64}},
        timeout=20,
    )
    resp.raise_for_status()
    result = resp.json()
    logger.info("Gmail Taslağı başarıyla oluşturuldu: draft_id=%s, to=%s", result.get("id"), to_email)
    return result
