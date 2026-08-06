from app.services.speech_service import speech_to_text, text_to_speech
from app.agents.planner_agent import handle_user_query
from app.core.logger import get_logger

logger = get_logger()


class VoiceAgent:
    """
    Voice Agent:
    - Converts speech → text
    - Sends text to Planner Agent
    - Converts response → speech
    """

    def __init__(self):
        pass

    async def process_voice(self, audio_bytes: bytes, db, user):
        try:
            # 🎤 Step 1: Speech → Text (OpenAI Whisper)
            text_query = speech_to_text(audio_bytes)
            logger.info(f"Transcribed Text: {text_query}")

            if not text_query or text_query.startswith("STT Error"):
                return {
                    "error": text_query or "Could not understand audio",
                    "text": "",
                    "response": None
                }

            # 🧠 Step 2: Send to Planner Agent (agentic, auth-scoped)
            ai_response = handle_user_query(text_query, db, user)

            # Return JSON-safe response (TTS handled by browser speechSynthesis)
            return {
                "text": text_query,
                "response": ai_response
            }

        except Exception as e:
            logger.error(f"VoiceAgent Error: {str(e)}")
            return {
                "error": str(e)
            }