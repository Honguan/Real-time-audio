import base64
import hashlib
import html
import json
import os
import random
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter

from .ai_memory import cache_translation, cached_translation
from .offline_translation import OfflineTranslationRegistry, translate_offline
from .tts import speak_windows_sapi


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
OPENAI_SPEECH_URL = "https://api.openai.com/v1/audio/speech"
GOOGLE_TRANSLATE_URL = "https://translation.googleapis.com/v3/projects/{project}:translateText"
GOOGLE_TTS_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"
_ARGOS_INFERENCE_LOCK = threading.Lock()
_GOOGLE_CREDENTIALS_LOCK = threading.Lock()
_GOOGLE_CREDENTIALS = {}
HTTP_ATTEMPTS = 3
HTTP_BUDGET_SECONDS = 8.0
HTTP_CONNECT_TIMEOUT_SECONDS = 2.0
HTTP_BACKOFF_SECONDS = 0.25
HTTP_BACKOFF_JITTER_SECONDS = 0.10
HTTP_MAX_BACKOFF_SECONDS = 1.0
TRANSLATION_MEMORY_CACHE_SIZE = 256
TRANSLATION_REQUEST_VERSION = 2


class ProviderRequestError(RuntimeError):
    def __init__(self, kind: str, message: str, status_code: int | None = None, attempts: int = 1):
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code
        self.attempts = attempts


@dataclass
class HttpClient:
    session: requests.Session = field(default_factory=requests.Session)
    cancel_event: threading.Event | None = None

    def __post_init__(self) -> None:
        adapter = HTTPAdapter(pool_connections=4, pool_maxsize=4, max_retries=0)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def post(self, url: str, **kwargs):
        started = time.monotonic()
        for attempt in range(1, HTTP_ATTEMPTS + 1):
            self._raise_if_cancelled(attempt)
            remaining = HTTP_BUDGET_SECONDS - (time.monotonic() - started)
            if remaining <= 0:
                raise ProviderRequestError("timeout", "雲端服務超過 8 秒延遲上限", attempts=attempt - 1)
            connect_timeout = min(HTTP_CONNECT_TIMEOUT_SECONDS, remaining / 3)
            kwargs["timeout"] = (connect_timeout, remaining - connect_timeout)
            try:
                response = self.session.post(url, **kwargs)
            except (requests.ConnectTimeout, requests.ReadTimeout, requests.ConnectionError) as exc:
                if attempt == HTTP_ATTEMPTS:
                    kind = "connect_timeout" if isinstance(exc, requests.ConnectTimeout) else "read_timeout" if isinstance(exc, requests.ReadTimeout) else "network"
                    raise ProviderRequestError(kind, f"雲端服務網路錯誤：{exc}", attempts=attempt) from exc
                self._backoff(attempt, started)
                continue
            except requests.RequestException as exc:
                raise ProviderRequestError("network", f"雲端服務網路錯誤：{exc}", attempts=attempt) from exc
            self._raise_if_cancelled(attempt)
            if time.monotonic() - started >= HTTP_BUDGET_SECONDS:
                raise ProviderRequestError("timeout", "雲端服務超過 8 秒延遲上限", attempts=attempt)
            status_code = int(getattr(response, "status_code", 200))
            if status_code < 400:
                return response
            if (status_code == 429 or 500 <= status_code < 600) and attempt < HTTP_ATTEMPTS:
                self._backoff(attempt, started, response)
                continue
            kind = "rate_limit" if status_code == 429 else "server" if status_code >= 500 else "auth" if status_code in (401, 403) else "http"
            raise ProviderRequestError(kind, f"雲端服務回應 HTTP {status_code}", status_code, attempt)
        raise AssertionError("unreachable")

    def _backoff(self, attempt: int, started: float, response=None) -> None:
        retry_after = getattr(response, "headers", {}).get("Retry-After") if response is not None else None
        try:
            delay = max(0.0, float(retry_after))
        except (TypeError, ValueError):
            delay = HTTP_BACKOFF_SECONDS * (2 ** (attempt - 1)) + random.uniform(0, HTTP_BACKOFF_JITTER_SECONDS)
        remaining = HTTP_BUDGET_SECONDS - (time.monotonic() - started)
        delay = min(delay, HTTP_MAX_BACKOFF_SECONDS, max(0.0, remaining))
        if self.cancel_event is not None:
            if self.cancel_event.wait(delay):
                self._raise_if_cancelled(attempt)
        else:
            time.sleep(delay)

    def _raise_if_cancelled(self, attempts: int) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise ProviderRequestError("cancelled", "雲端服務請求已取消", attempts=attempts)


@dataclass(frozen=True)
class TranslationResult:
    text: str
    confidence: float


def build_openai_translation_request(text: str, target_language: str, source_language: str, model: str = "gpt-4.1-mini", context: list[tuple[str, str]] | None = None, style: str = "plain", glossary: dict | None = None) -> dict:
    context_text = ""
    if context:
        context_text = "Recent context:\n" + "\n".join(f"{source} -> {target}" for source, target in context[-4:]) + "\n\n"
    style_text = f"Style: {style}.\n" if style and style != "plain" else ""
    glossary_text = ""
    if glossary:
        glossary_text = "Use these glossary translations first:\n" + "\n".join(f"{source} -> {target}" for source, target in glossary.items()) + "\n\n"
    prompt = f"{context_text}{style_text}{glossary_text}Translate from {source_language} to {target_language}. Return only the translation:\n{text}"
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


class _GoogleAuthResponse:
    def __init__(self, response):
        self.status = response.status_code
        self.headers = response.headers
        self.data = response.content


class _GoogleAuthRequest:
    def __init__(self, http: HttpClient):
        self.http = http

    def __call__(self, url, method="GET", body=None, headers=None, **kwargs):
        if method.upper() != "POST":
            raise ProviderRequestError("auth", f"Google 驗證使用未支援的 HTTP 方法：{method}")
        return _GoogleAuthResponse(self.http.post(url, data=body, headers=headers))


def google_access_token(service_account_json: str, http: HttpClient | None = None) -> str:
    if not service_account_json:
        raise RuntimeError("未設定 Google 服務帳戶 JSON，請先選擇 JSON 檔或改用本機服務")
    if not Path(service_account_json).exists():
        raise RuntimeError(f"找不到 Google 服務帳戶 JSON：{service_account_json}")

    from google.oauth2 import service_account

    path = Path(service_account_json).resolve()
    cache_key = (str(path), path.stat().st_mtime_ns)
    scopes = ["https://www.googleapis.com/auth/cloud-platform"]
    with _GOOGLE_CREDENTIALS_LOCK:
        credentials = _GOOGLE_CREDENTIALS.get(cache_key)
        try:
            if credentials is None:
                credentials = service_account.Credentials.from_service_account_file(path, scopes=scopes)
                _GOOGLE_CREDENTIALS.clear()
                _GOOGLE_CREDENTIALS[cache_key] = credentials
            expiry = getattr(credentials, "expiry", None)
            if expiry is not None and expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if not getattr(credentials, "token", None) or expiry is not None and expiry <= datetime.now(timezone.utc) + timedelta(minutes=5):
                credentials.refresh(_GoogleAuthRequest(http or HttpClient()))
            return credentials.token
        except ProviderRequestError:
            raise
        except Exception as exc:
            raise ProviderRequestError("auth", f"Google 驗證失敗：{exc}") from exc


@dataclass
class Translator:
    config: dict
    offline_registry: OfflineTranslationRegistry | None = None
    cache: OrderedDict[str, str] = field(default_factory=OrderedDict)
    context: list[tuple[str, str]] = field(default_factory=list)
    http: HttpClient = field(default_factory=HttpClient)

    def translate(self, text: str, source_language: str, target_language: str) -> TranslationResult:
        if not text.strip():
            return TranslationResult("", 0.0)
        provider = self.config.get("provider", "google")
        glossary = self._glossary()
        request_fingerprint = self._request_fingerprint(text, source_language, target_language, glossary)
        memory_cached = self._memory_cached(request_fingerprint)
        if memory_cached is not None and not self._unverified_local_passthrough(provider, text, memory_cached):
            self._remember_context(text, memory_cached)
            return TranslationResult(self._apply_glossary(memory_cached, glossary), 1.0)
        db_path = Path(self.config.get("translation_cache_path", ""))
        persistent_cache_enabled = self.config.get("translation_cache_enabled", True)
        if persistent_cache_enabled and db_path:
            cached = cached_translation(db_path, request_fingerprint)
            if cached is not None and not self._unverified_local_passthrough(provider, text, cached):
                self._remember_cached(request_fingerprint, cached)
                self._remember_context(text, cached)
                return TranslationResult(self._apply_glossary(cached, glossary), 1.0)
        if provider == "local":
            translated = self._local_translate(text, source_language, target_language)
            confidence = 0.8 if self.config.get("local_translate_url", "").strip() or translated != text else 0.3
        elif provider == "openai":
            translated = self._openai_translate(text, source_language, target_language, glossary)
            confidence = 0.8
        else:
            translated = self._google_translate(text, source_language, target_language)
            confidence = 0.8
        if not translated.strip():
            confidence = 0.0
        self._remember_cached(request_fingerprint, translated)
        self._remember_context(text, translated)
        if persistent_cache_enabled and db_path and not (provider == "local" and not self.config.get("local_translate_url", "").strip() and translated == text):
            cache_translation(db_path, request_fingerprint, provider, source_language, target_language, text, translated)
        return TranslationResult(self._apply_glossary(translated, glossary), confidence)

    def _request_fingerprint(self, text: str, source_language: str, target_language: str, glossary: dict | None = None) -> str:
        provider = self.config.get("provider", "google")
        request = {
            "version": TRANSLATION_REQUEST_VERSION,
            "provider": provider,
            "backend": self._backend_identity(provider),
            "source_language": source_language,
            "target_language": target_language,
            "source_text": text.strip(),
            "style": self.config.get("translation_style", "plain"),
            "glossary": glossary if glossary is not None else self._glossary(),
            "context": self.context if provider == "openai" else [],
        }
        document = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(document.encode("utf-8")).hexdigest()

    def _backend_identity(self, provider: str) -> dict:
        revision = str(self.config.get("translation_backend_revision", "1"))
        if provider == "openai":
            return {"model": self.config.get("openai_model", ""), "revision": revision}
        if provider == "google":
            return {"api": "translate-v3", "revision": revision}
        url = self.config.get("local_translate_url", "").strip()
        return {
            "url": url,
            "models": "" if url else self._offline_registry().revision,
            "revision": revision,
        }

    def _offline_registry(self) -> OfflineTranslationRegistry:
        if self.offline_registry is None:
            self.offline_registry = OfflineTranslationRegistry(self.config)
        return self.offline_registry

    def _memory_cached(self, request_fingerprint: str) -> str | None:
        translated = self.cache.pop(request_fingerprint, None)
        if translated is not None:
            self.cache[request_fingerprint] = translated
        return translated

    def _remember_cached(self, request_fingerprint: str, translated: str) -> None:
        self.cache.pop(request_fingerprint, None)
        self.cache[request_fingerprint] = translated
        if len(self.cache) > TRANSLATION_MEMORY_CACHE_SIZE:
            self.cache.popitem(last=False)

    def _unverified_local_passthrough(self, provider: str, text: str, translated: str) -> bool:
        return provider == "local" and not self.config.get("local_translate_url", "").strip() and translated.strip() == text.strip()

    def _remember_context(self, text: str, translated: str) -> None:
        if text.strip() and translated.strip():
            self.context = (self.context + [(text.strip(), translated.strip())])[-4:]

    def _apply_glossary(self, text: str, glossary: dict | None = None) -> str:
        glossary = self._glossary() if glossary is None else glossary
        if not glossary:
            return text
        for source, target in sorted(glossary.items(), key=lambda item: len(str(item[0])), reverse=True):
            source = str(source)
            if not source:
                continue
            text = text.replace(source, str(target))
        return text

    def _glossary(self) -> dict:
        path = self.config.get("glossary_path", "").strip()
        if not path or not Path(path).exists():
            return {}
        try:
            with Path(path).open("r", encoding="utf-8") as handle:
                glossary = json.load(handle)
        except Exception:
            return {}
        return glossary if isinstance(glossary, dict) else {}

    def _local_translate(self, text: str, source_language: str, target_language: str) -> str:
        url = self.config.get("local_translate_url", "").strip()
        if not url:
            translated = translate_offline(self.config, text, source_language, target_language, self._offline_registry()) or self._argos_translate(text, source_language, target_language)
            if translated:
                return translated
            raise RuntimeError(f"找不到 {source_language}→{target_language} 的本機翻譯模型；請下載離線翻譯模型或設定本機翻譯 URL")
        response = self.http.post(
            url,
            json={"q": text, "source": source_language, "target": target_language, "format": "text"},
        )
        response.raise_for_status()
        return html.unescape(response.json().get("translatedText", ""))

    def _argos_translate(self, text: str, source_language: str, target_language: str) -> str:
        if source_language == "auto":
            return ""
        with _ARGOS_INFERENCE_LOCK:
            try:
                import argostranslate.translate as argos_translate
            except Exception:
                return ""
            source_code = source_language.split("-")[0]
            target_code = target_language.split("-")[0]
            languages = argos_translate.get_installed_languages()
            source = next((language for language in languages if language.code == source_code), None)
            target = next((language for language in languages if language.code == target_code), None)
            if not source or not target:
                return ""
            return source.get_translation(target).translate(text)

    def _openai_translate(self, text: str, source_language: str, target_language: str, glossary: dict | None = None) -> str:
        request = build_openai_translation_request(text, target_language, source_language, self.config["openai_model"], self.context, self.config.get("translation_style", "plain"), glossary)
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise RuntimeError("未設定 OPENAI_API_KEY，請先設定環境變數或改用本機翻譯服務")
        request["headers"]["Authorization"] = f"Bearer {api_key}"
        response = self.http.post(request["url"], headers=request["headers"], json=request["json"])
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
        token = google_access_token(self.config.get("google_service_account_json", ""), self.http)
        request = build_google_translate_request(text, target_language, source_language, project_id)
        response = self.http.post(
            request["url"],
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=request["json"],
        )
        response.raise_for_status()
        translations = response.json().get("translations", [])
        return html.unescape(translations[0].get("translatedText", "")) if translations else ""


@dataclass
class TextToSpeech:
    config: dict
    http: HttpClient = field(default_factory=HttpClient)

    def speak_local(self, text: str, device_name: str, cancel_event=None) -> dict[str, float]:
        args = (
            text,
            device_name,
            int(self.config.get("tts_rate", 0)),
            int(self.config.get("tts_volume", 100)),
            self.config.get("tts_voice_name", ""),
        )
        return speak_windows_sapi(*args) if cancel_event is None else speak_windows_sapi(*args, cancel_event)

    def synthesize_google_linear16(self, text: str, language_code: str) -> bytes:
        token = google_access_token(self.config.get("google_service_account_json", ""), self.http)
        payload = {
            "input": {"text": text},
            "voice": {"languageCode": language_code},
            "audioConfig": {"audioEncoding": "LINEAR16", "sampleRateHertz": 24000},
        }
        if self.config.get("google_tts_voice", "").strip():
            payload["voice"]["name"] = self.config["google_tts_voice"].strip()
        response = self.http.post(
            GOOGLE_TTS_URL,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
        )
        response.raise_for_status()
        return base64.b64decode(response.json()["audioContent"])

    def synthesize_openai_mp3(self, text: str) -> bytes:
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise RuntimeError("未設定 OPENAI_API_KEY，請先設定環境變數或改用本機 TTS")
        response = self.http.post(
            OPENAI_SPEECH_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": self.config["openai_tts_model"],
                "voice": self.config["openai_tts_voice"],
                "input": text,
            },
        )
        response.raise_for_status()
        return response.content

    def synthesize_openai_linear16(self, text: str) -> bytes:
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise RuntimeError("未設定 OPENAI_API_KEY，請先設定環境變數或改用本機 TTS")
        response = self.http.post(
            OPENAI_SPEECH_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": self.config["openai_tts_model"],
                "voice": self.config["openai_tts_voice"],
                "input": text,
                "response_format": "pcm",
            },
        )
        response.raise_for_status()
        return response.content
