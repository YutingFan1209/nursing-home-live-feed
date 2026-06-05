"""
Gmail scraper for Google Alert emails.
Reads Google Alert notification emails and extracts article URLs.

Authentication:
- First run: opens browser for OAuth, saves token to gmail_token.json
- Subsequent runs: uses saved token automatically, auto-refreshes
- On AWS: upload gmail_token.json to Secrets Manager
"""

import os
import re
import base64
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']
CREDS_FILE = os.environ.get('GMAIL_CREDENTIALS_FILE', 'gmail_credentials.json')
TOKEN_FILE  = os.environ.get('GMAIL_TOKEN_FILE', 'gmail_token.json')
ALERT_SENDER = 'googlealerts-noreply@google.com'


def _get_gmail_service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDS_FILE):
                raise FileNotFoundError(f"Gmail credentials not found at {CREDS_FILE}")
            flow = InstalledAppFlow.from_client_secrets_file(CREDS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, 'w') as f:
            f.write(creds.to_json())
    return build('gmail', 'v1', credentials=creds)


def fetch_alert_articles(days_back: int = 2) -> list[dict]:
    """Fetch article URLs from recent Google Alert emails."""
    try:
        service = _get_gmail_service()
    except Exception as e:
        logger.error(f"Gmail authentication failed: {e}")
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    query = f"from:{ALERT_SENDER} after:{cutoff.strftime('%Y/%m/%d')}"

    try:
        results = service.users().messages().list(
            userId='me', q=query, maxResults=50
        ).execute()
    except Exception as e:
        logger.error(f"Failed to fetch Gmail messages: {e}")
        return []

    messages = results.get('messages', [])
    logger.info(f"Found {len(messages)} Google Alert emails")

    articles = []
    seen = set()

    for msg in messages:
        try:
            for art in _extract_articles_from_email(service, msg['id']):
                if art['url'] not in seen:
                    seen.add(art['url'])
                    articles.append(art)
        except Exception as e:
            logger.warning(f"Failed to process email {msg['id']}: {e}")

    logger.info(f"Extracted {len(articles)} unique article URLs from alerts")
    return articles


def _extract_articles_from_email(service, message_id: str) -> list[dict]:
    """Extract article URLs and titles from a single alert email."""
    msg = service.users().messages().get(
        userId='me', id=message_id, format='full'
    ).execute()

    headers = msg['payload'].get('headers', [])
    date_str = next((h['value'] for h in headers if h['name'] == 'Date'), None)
    published_at = _parse_email_date(date_str)

    # Try plain text first — most reliable format for Google Alerts
    plain = _get_body(msg, 'text/plain')
    if plain:
        articles = _extract_from_plain(plain, published_at)
        if articles:
            return articles

    # Fall back to HTML
    html = _get_body(msg, 'text/html')
    if html:
        return _extract_from_html(html, published_at)

    return []


def _extract_from_plain(body: str, published_at) -> list[dict]:
    """
    Plain text Google Alert format:
      Article Title
      Source Name
      <https://www.google.com/url?rct=j&sa=t&url=ACTUAL_URL&ct=ga&...>
    """
    articles = []
    lines = [l.strip() for l in body.split('\n')]

    for i, line in enumerate(lines):
        # Match Google redirect URL in angle brackets
        m = re.search(r'<https://www\.google\.com/url\?[^>]*[?&]url=([^&>]+)', line)
        if not m:
            m = re.search(r'https://www\.google\.com/url\?[^\s]*[?&]url=([^&\s<>]+)', line)
        if not m:
            continue

        url = _unquote(m.group(1))
        if not url or 'google.com' in url:
            continue

        # Title is 1-3 lines before the URL line
        title = ""
        for j in range(i - 1, max(i - 4, -1), -1):
            prev = lines[j]
            if prev and not prev.startswith('<http') and 'google.com' not in prev \
               and not prev.startswith('-') and len(prev) > 5:
                title = prev
                break

        articles.append({
            'url': url,
            'title': title or url,
            'published_at': published_at,
            'source_type': 'rss',
        })

    return articles


def _extract_from_html(body: str, published_at) -> list[dict]:
    """HTML Google Alert format with href redirect URLs."""
    articles = []
    pattern = r'href="https://www\.google\.com/url\?[^"]*[?&]url=([^&"]+)[^"]*"[^>]*>([^<]+)</a>'
    for url, title in re.findall(pattern, body):
        url = _unquote(url)
        if not url or 'google.com' in url:
            continue
        articles.append({
            'url': url.strip(),
            'title': title.strip(),
            'published_at': published_at,
            'source_type': 'rss',
        })
    return articles


def _get_body(msg: dict, mime_type: str) -> str:
    """Extract body of given mime type from Gmail message."""
    payload = msg.get('payload', {})

    # Direct body
    if payload.get('mimeType') == mime_type:
        data = payload.get('body', {}).get('data')
        if data:
            return base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')

    # Search parts recursively
    for part in payload.get('parts', []):
        if part.get('mimeType') == mime_type:
            data = part.get('body', {}).get('data')
            if data:
                return base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
        for subpart in part.get('parts', []):
            if subpart.get('mimeType') == mime_type:
                data = subpart.get('body', {}).get('data')
                if data:
                    return base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
    return ""


# Keep for backward compatibility
def _get_email_body(msg: dict) -> Optional[str]:
    return _get_body(msg, 'text/html') or None


def _parse_email_date(date_str: Optional[str]) -> Optional[datetime]:
    if not date_str:
        return None
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(date_str)
    except Exception:
        return datetime.now(timezone.utc)


def _unquote(s: str) -> str:
    from urllib.parse import unquote
    return unquote(s)
