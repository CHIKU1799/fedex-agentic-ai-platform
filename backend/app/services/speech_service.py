# app/services/speech_service.py

import os
import tempfile
from openai import OpenAI
from app.core.config import settings

_client = None


def _get_client():
    global _client
    if _client is None:
        api_key = settings.OPENAI_API_KEY
        if not api_key:
            return None
        _client = OpenAI(api_key=api_key)
    return _client


# 🎤 Speech → Text (STT)
def speech_to_text(audio_bytes: bytes) -> str:
    try:
        client = _get_client()
        if client is None:
            return "STT Error: OPENAI_API_KEY not configured."

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_audio:
                temp_audio.write(audio_bytes)
                temp_audio.flush()
                tmp_path = temp_audio.name

            with open(tmp_path, "rb") as audio_file:
                transcript = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file
                )
            return transcript.text
        finally:
            # Never leave user audio on disk after transcription.
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)

    except Exception as e:
        return f"STT Error: {str(e)}"


# 🔊 Text → Speech (TTS)
def text_to_speech(text: str) -> bytes:
    try:
        client = _get_client()
        if client is None:
            return bytes("TTS Error: OPENAI_API_KEY not configured.", "utf-8")

        response = client.audio.speech.create(
            model="tts-1",
            voice="alloy",
            input=text
        )

        return response.read()

    except Exception as e:
        return bytes(f"TTS Error: {str(e)}", "utf-8")