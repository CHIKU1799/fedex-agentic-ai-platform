from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.config import settings
from app.core.middleware import RateLimitMiddleware, SecurityHeadersMiddleware
from app.db.database import Base, engine

Base.metadata.create_all(bind=engine)

app = FastAPI(title="FedEx Agentic AI Backend")

# ── Secure by Design ───────────────────────────────────────────────
# CORS is an explicit allow-list (never "*" with credentials, which is
# both a spec violation and an over-broad grant). Methods/headers are
# scoped to what the SPA actually uses.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware)

app.include_router(router)


@app.get("/health")
def health():
    from app.core.config import settings
    # Capability flags let the UI pick the right mode upfront (e.g. skip the
    # Whisper voice path entirely when no OpenAI key is configured).
    return {
        "status": "ok",
        "stt_whisper": bool(settings.OPENAI_API_KEY),
        "llm": bool(settings.ANTHROPIC_API_KEY),
    }
