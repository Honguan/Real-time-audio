import base64
import html
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import requests

from .tts import speak_windows_sapi


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
OPENAI_SPEECH_URL = "https://api.openai.com/v1/audio/speech"
GOOGLE_TRANSLATE_URL = "https://translation.googleapis.com/v3/projects/{project}:translateText"
GOOGLE_TTS_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"


def build_openai_translation_request(text: str, target_language: str, source_language: str, model: str = "gpt-4.1-mini") -> dict:
    prompt = f"Translate from {source_language} to {target_language}. Return only the translation:\n{text}"
    return {
        "url": OPENAI_RESPONSES_URL,
        "headers": {"Authorization": "Bearer ${OPENAI_API_KEY}", "Content-Type": "application/json"},
        "json": {"model": model, "input": prompt},
    }


def build_google_translate_request(text: str, target_language: str, source_language: str, project_id: str) -> dict:
    payload = {
        "contents": [text],
        "mimeType": "text/plain",
        "targetLanguageCode": target_language,
    }
    if source_language != "auto":
        payload["sourceLanguageCode"] = source_language
    return {
        "url": GOOGLE_TRANSLATE_URL.format(project=project_id),
        "json": payload,
    }


def google_access_token(service_account_json: str) -> str:
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
    credentials = service_account.Credentials.from_service_account_file(service_account_json, scopes=scopes)
    credentials.refresh(Request())
    return credentials.token


@dataclass
class Translator:
    config: dict
    cache: dict[tuple[str, str, str, str], str] = field(default_factory=dict)

    def translate(self, text: str, source_language: str, target_language: str) -> str:
        if not text.strip():
            return ""
        provider = self.config.get("provider", "google")
        cache_key = (provider, source_language, target_language, text.strip())
        if cache_key in self.cache:
            return self._apply_glossary(self.cache[cache_key])
        if provider == "local":
            translated = self._local_translate(text, source_language, target_language)
        elif provider == "openai":
            translated = self._openai_translate(text, source_language, target_language)
        else:
            translated = self._google_translate(text, source_language, target_language)
        self.cache[cache_key] = translated
        return self._apply_glossary(translated)

    def _apply_glossary(self, text: str) -> str:
        path = self.config.get("glossary_path", "").strip()
        if not path or not Path(path).exists():
            return text
        try:
            with Path(path).open("r", encoding="utf-8") as handle:
                glossary = json.load(handle)
        except Exception:
            return text
        if not isinstance(glossary, dict):
            return text
        for source, target in sorted(glossary.items(), key=lambda item: len(str(item[0])), reverse=True):
            source = str(source)
            if not source:
                continue
            text = text.replace(source, str(target))
        return text

    def _local_translate(self, text: str, source_language: str, target_language: str) -> str:
        url = self.config.get("local_translate_url", "").strip()
        if not url:
            return text
        response = requests.post(
            url,
            json={"q": text, "source": source_language, "target": target_language, "format": "text"},
            timeout=30,
        )
        response.raise_for_status()
        return html.unescape(response.json().get("translatedText", ""))

    def _openai_translate(self, text: str, source_language: str, target_language: str) -> str:
        request = build_openai_translation_request(text, target_language, source_language, self.config["openai_model"])
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        request["headers"]["Authorization"] = f"Bearer {api_key}"
        response = requests.post(request["url"], headers=request["headers"], json=request["json"], timeout=30)
        response.raise_for_status()
        data = response.json()
        if "output_text" in data:
            return data["output_text"].strip()
        texts = []
        for item in data.get("output", []):
            for content in item.get("content", []):
                if content.get("type") in ("output_text", "text"):
                    texts.append(content.get("text", ""))
        return "\n".join(texts).strip()

    def _google_translate(self, text: str, source_language: str, target_language: str) -> str:
        project_id = self.config.get("google_project_id", "")
        token = google_access_token(self.config.get("google_service_account_json", ""))
        request = build_google_translate_request(text, target_language, source_language, project_id)
        response = requests.post(
            request["url"],
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=request["json"],
            timeout=30,
        )
        response.raise_for_status()
        translations = response.json().get("translations", [])
        return html.unescape(translations[0].get("translatedText", "")) if translations else ""


@dataclass
class TextToSpeech:
    config: dict

    def speak_local(self, text: str, device_name: str) -> None:
        speak_windows_sapi(
            text,
            device_name,
            int(self.config.get("tts_rate", 0)),
            int(self.config.get("tts_volume", 100)),
            self.config.get("tts_voice_name", ""),
        )

    def synthesize_google_linear16(self, text: str, language_code: str) -> bytes:
        token = google_access_token(self.config.get("google_service_account_json", ""))
        payload = {
            "input": {"text": text},
            "voice": {"languageCode": language_code},
            "audioConfig": {"audioEncoding": "LINEAR16", "sampleRateHertz": 24000},
        }
        if self.config.get("google_tts_voice", "").strip():
            payload["voice"]["name"] = self.config["google_tts_voice"].strip()
        response = requests.post(
            GOOGLE_TTS_URL,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        return base64.b64decode(response.json()["audioContent"])

    def synthesize_openai_mp3(self, text: str) -> bytes:
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        response = requests.post(
            OPENAI_SPEECH_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": self.config["openai_tts_model"],
                "voice": self.config["openai_tts_voice"],
                "input": text,
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.content

    def synthesize_openai_linear16(self, text: str) -> bytes:
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        response = requests.post(
            OPENAI_SPEECH_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": self.config["openai_tts_model"],
                "voice": self.config["openai_tts_voice"],
                "input": text,
                "response_format": "pcm",
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.content
