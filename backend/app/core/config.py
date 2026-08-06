import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the project root (one level above backend/)
env_path = Path(__file__).resolve().parents[3] / ".env"
load_dotenv(dotenv_path=env_path)


def _split_csv(value: str) -> list:
    return [item.strip() for item in value.split(",") if item.strip()]


class Settings:
    PROJECT_NAME: str = "FedEx AI Backend"

    # ── AI ─────────────────────────────────────────────────────────
    # Anthropic powers the agentic planner. Haiku 4.5 is the most
    # cost-effective Claude model — chosen deliberately to keep spend low.
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    ANTHROPIC_MODEL: str = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5")
    ANTHROPIC_MAX_TOKENS: int = int(os.getenv("ANTHROPIC_MAX_TOKENS", "1024"))
    # OpenAI key is only used for Whisper STT (voice); optional.
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    # ── Database ───────────────────────────────────────────────────
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./fedex.db")

    # ── Secure by Design ───────────────────────────────────────────
    # CORS: explicit allow-list, never "*" alongside credentials.
    ALLOWED_ORIGINS: list = _split_csv(
        os.getenv("ALLOWED_ORIGINS", "http://localhost:4200,http://127.0.0.1:4200")
    )

    # Auth: HS256 JWT signing secret. MUST be overridden via env in any
    # non-local environment. We fail closed (see validation below).
    JWT_SECRET: str = os.getenv("JWT_SECRET", "")
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_TTL_SECONDS: int = int(os.getenv("ACCESS_TOKEN_TTL_SECONDS", "3600"))

    # When True, mutating endpoints require a valid bearer token.
    # Defaults to True — security is on by default, not opt-in.
    REQUIRE_AUTH: bool = os.getenv("REQUIRE_AUTH", "true").lower() == "true"

    # Basic abuse protection (requests per window, per client IP).
    RATE_LIMIT_REQUESTS: int = int(os.getenv("RATE_LIMIT_REQUESTS", "60"))
    RATE_LIMIT_WINDOW_SECONDS: int = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))

    ENV: str = os.getenv("ENV", "development")


settings = Settings()

# Fail-closed secret handling: in production a real signing secret is
# mandatory. Locally we derive an ephemeral one so dev still works, but
# tokens do not survive a restart (by design).
if not settings.JWT_SECRET:
    if settings.ENV == "production":
        raise RuntimeError(
            "JWT_SECRET must be set in production. Refusing to start with a "
            "default/empty signing key."
        )
    import secrets as _secrets

    settings.JWT_SECRET = _secrets.token_urlsafe(48)
