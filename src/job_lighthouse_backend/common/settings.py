"""Service settings, read from env vars only."""

import os
from dataclasses import dataclass

DEFAULT_JWT_TTL_SECONDS = 7 * 24 * 60 * 60
DEFAULT_OPENAI_MODEL = "gpt-5-mini"


class SettingsError(RuntimeError):
    pass


def cors_allowed_origins_from_env() -> list[str]:
    """Origins from ``CORS_ALLOWED_ORIGINS`` (comma-separated). Unset: none.

    A trailing ``/`` is dropped: the browser's ``Origin`` header never has one.
    """
    raw = os.environ.get("CORS_ALLOWED_ORIGINS", "")
    return [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]


@dataclass(frozen=True)
class Settings:
    database_url: str
    # Shared by both services: one issues tokens, both validate them locally.
    jwt_secret: str
    jwt_ttl_seconds: int = DEFAULT_JWT_TTL_SECONDS
    # Only the Companies Service needs it (POST /auth/google).
    google_client_id: str | None = None
    # Only the Job Runner needs it (match scoring). Unset: jobs stay unscored.
    openai_api_key: str | None = None
    openai_model: str = DEFAULT_OPENAI_MODEL
    # Transactional email (Resend). Unset: no email is sent.
    resend_api_key: str | None = None
    # Sender, e.g. "Job Lighthouse <digest@example.com>". Required with the key.
    email_from: str | None = None
    # Job Runner tick loop. Off in tests, so TestClient doesn't start runs.
    scheduler_enabled: bool = True

    @classmethod
    def from_env(cls) -> "Settings":
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            raise SettingsError("DATABASE_URL is not set.")
        jwt_secret = os.environ.get("JWT_SECRET")
        if not jwt_secret:
            raise SettingsError("JWT_SECRET is not set.")
        ttl = os.environ.get("JWT_TTL_SECONDS")
        try:
            jwt_ttl_seconds = int(ttl) if ttl else DEFAULT_JWT_TTL_SECONDS
        except ValueError:
            raise SettingsError("JWT_TTL_SECONDS must be an integer.") from None
        if jwt_ttl_seconds <= 0:
            raise SettingsError("JWT_TTL_SECONDS must be positive.")
        resend_api_key = os.environ.get("RESEND_API_KEY") or None
        email_from = os.environ.get("EMAIL_FROM") or None
        if resend_api_key and not email_from:
            raise SettingsError("EMAIL_FROM is required when RESEND_API_KEY is set.")
        scheduler = os.environ.get("SCHEDULER_ENABLED", "").strip().lower()
        if scheduler not in ("", "true", "false"):
            raise SettingsError("SCHEDULER_ENABLED must be true or false.")
        return cls(
            database_url=database_url,
            jwt_secret=jwt_secret,
            jwt_ttl_seconds=jwt_ttl_seconds,
            google_client_id=os.environ.get("GOOGLE_CLIENT_ID") or None,
            openai_api_key=os.environ.get("OPENAI_API_KEY") or None,
            openai_model=os.environ.get("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL,
            resend_api_key=resend_api_key,
            email_from=email_from,
            scheduler_enabled=scheduler != "false",
        )
