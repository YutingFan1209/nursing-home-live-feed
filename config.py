import os
from dataclasses import dataclass, field
from typing import Optional
from dotenv import load_dotenv

load_dotenv()  # reads .env file into os.environ before any field is evaluated


@dataclass
class Config:
    # Database
    database_url: str = field(
        default_factory=lambda: os.environ.get("DATABASE_URL", "")
    )

    # Anthropic
    anthropic_api_key: str = field(
        default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", "")
    )
    claude_model: str = "claude-sonnet-4-5"
    claude_max_tokens: int = 2000

    # SendGrid (alerts)
    sendgrid_api_key: Optional[str] = field(
        default_factory=lambda: os.environ.get("SENDGRID_API_KEY")
    )
    alert_from_email: str = field(
        default_factory=lambda: os.environ.get("ALERT_FROM_EMAIL", "alerts@yourorg.org")
    )
    alert_to_emails: list = field(
        default_factory=lambda: [
            e.strip()
            for e in os.environ.get("ALERT_TO_EMAILS", "").split(",")
            if e.strip()
        ]
    )

    # S3 / GCS (raw article archive)
    archive_bucket: Optional[str] = field(
        default_factory=lambda: os.environ.get("ARCHIVE_BUCKET")
    )
    archive_prefix: str = "articles"

    # CMS
    cms_ownership_dataset: str = "qhpq-qrm6"
    cms_carecompare_dataset: str = "4pq5-n9py"
    cms_api_base: str = "https://data.cms.gov/resource"
    cms_page_size: int = 5000

    # Pipeline tuning
    fuzzy_match_threshold: int = 70
    recheck_interval_days: int = 7
    recheck_max_attempts: int = 12
    dedup_window_days: int = 30
    max_articles_per_run: int = 50        # hard cap per run — override with --max-articles

    # Scraper
    request_timeout: int = 30
    request_delay: float = 1.0            # seconds between requests (be polite)
    max_article_age_days: int = 7         # only process articles from last N days


def get_config() -> Config:
    return Config()
