import hashlib
import importlib.metadata
import json
import os
import tempfile
import threading
import time
import zipfile
from pathlib import Path, PurePosixPath

import requests

from .archive_install import atomic_replace_tree, safe_extract_zip, verify_install_manifest, write_install_manifest
from .models import models_dir


ARGOS_INDEX_URL = "https://raw.githubusercontent.com/argosopentech/argospm-index/main/index.json"
_VERIFIED_PACKAGES: dict[Path, tuple[tuple[str, int, int], ...]] = {}
_MODEL_LOCK = threading.RLock()


def language_code(language: str) -> str:
    return str(language or "").split("-")[0].lower()


def normalize_translation_text(text: str) -> str:
    return " ".join(str(text).replace("▁", " ").split())


def translation_models_dir(config: dict) -> Path:
    return models_dir(config) / "translation"


def _verified_package(package_dir: Path) -> bool:
    if not verify_install_manifest(package_dir):
        _VERIFIED_PACKAGES.pop(package_dir, None)
        return False
    try:
        signature = tuple(
            sorted(
                (path.relative_to(package_dir).as_posix(), path.stat().st_size, path.stat().st_mtime_ns)
                for path in package_dir.rglob("*")
                if path.is_file()
            )
        )
    except OSError:
        _VERIFIED_PACKAGES.pop(package_dir, None)
        return False
    previous_signature = _VERIFIED_PACKAGES.get(package_dir)
    if previous_signature == signature:
        return True
    if not verify_install_manifest(package_dir, verify_hashes=True):
        _VERIFIED_PACKAGES.pop(package_dir, None)
        return False
    _VERIFIED_PACKAGES[package_dir] = signature
    return True


def _installed_packages(config: dict) -> list[tuple[dict, Path]]:
    packages_dir = translation_models_dir(config) / "packages"
    packages: list[tuple[dict, Path]] = []
    if not packages_dir.exists():
        return packages
    for package_dir in packages_dir.iterdir():
        if not _verified_package(package_dir):
            continue
        metadata_path = package_dir / "metadata.json"
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(metadata, dict) and (package_dir / "model").is_dir() and (package_dir / "sentencepiece.model").is_file():
            packages.append((metadata, package_dir))
    return packages


def translation_model_available(config: dict, source_language: str = "", target_language: str = "") -> bool:
    return OfflineTranslationRegistry(config, install=False).available(source_language, target_language)


def _translation_path(packages: list[tuple[dict, Path]], source_code: str, target_code: str) -> list[tuple[dict, Path]]:
    if not source_code or not target_code or source_code == target_code:
        return []
    pending = [(source_code, [])]
    visited = {source_code}
    while pending:
        current, path = pending.pop(0)
        for metadata, package_path in packages:
            if metadata.get("from_code") != current:
                continue
            next_code = str(metadata.get("to_code") or "")
            next_path = path + [(metadata, package_path)]
            if next_code == target_code:
                return next_path
            if next_code and next_code not in visited:
                visited.add(next_code)
                pending.append((next_code, next_path))
    return []


class OfflineTranslationRegistry:
    def __init__(self, config: dict, install: bool = True):
        self.config = config
        self.packages: list[tuple[dict, Path]] = []
        self.translators: dict[Path, object] = {}
        self.tokenizers: dict[Path, object] = {}
        self.reload_seconds = 0.0
        self.model_load_seconds = 0.0
        self.model_bytes = 0
        self.revision = ""
        self.reload(install=install)

    def reload(self, install: bool = True) -> None:
        started = time.monotonic()
        with _MODEL_LOCK:
            if install:
                install_translation_models(self.config)
            packages = _installed_packages(self.config)
            files = [path for _metadata, package in packages for path in package.rglob("*") if path.is_file()]
            model_bytes = sum(path.stat().st_size for path in files)
            revision_parts = [hashlib.sha256(path.read_bytes()).hexdigest() for path in files if path.name == "install_manifest.json"]
            try:
                argos_version = importlib.metadata.version("argostranslate")
            except importlib.metadata.PackageNotFoundError:
                argos_version = ""
            try:
                import argostranslate.settings as argos_settings

                revision_parts.extend(
                    hashlib.sha256(metadata.read_bytes()).hexdigest()
                    for directory in argos_settings.package_dirs
                    for metadata in Path(directory).glob("*/metadata.json")
                )
            except Exception:
                pass
            revision = hashlib.sha256("\n".join(sorted([argos_version, *revision_parts])).encode()).hexdigest()
            self.packages = packages
            self.translators.clear()
            self.tokenizers.clear()
            self.model_bytes = model_bytes
            self.revision = revision
            self.reload_seconds = time.monotonic() - started

    def available(self, source_language: str = "", target_language: str = "") -> bool:
        source_code = language_code(source_language)
        target_code = language_code(target_language)
        if source_code in ("", "auto"):
            return any(not target_code or metadata.get("to_code") == target_code for metadata, _path in self.packages)
        return bool(_translation_path(self.packages, source_code, target_code))

    def stats(self) -> dict[str, float | int]:
        return {
            "reload_seconds": self.reload_seconds,
            "model_load_seconds": self.model_load_seconds,
            "model_bytes": self.model_bytes,
            "loaded_models": len(self.translators),
        }

    def translate(self, text: str, source_language: str, target_language: str) -> str:
        source_code = language_code(source_language)
        target_code = language_code(target_language)
        if source_code in ("", "auto") or not target_code or source_code == target_code:
            return ""
        with _MODEL_LOCK:
            packages = _translation_path(self.packages, source_code, target_code)
            if not packages:
                return ""
            try:
                import ctranslate2
                import sentencepiece
            except ImportError:
                return ""
            translated = text
            for metadata, package_path in packages:
                tokenizer = self.tokenizers.get(package_path)
                translator = self.translators.get(package_path)
                if tokenizer is None or translator is None:
                    started = time.monotonic()
                    tokenizer = sentencepiece.SentencePieceProcessor(model_file=str(package_path / "sentencepiece.model"))
                    translator = ctranslate2.Translator(str(package_path / "model"), device="cpu")
                    self.tokenizers[package_path] = tokenizer
                    self.translators[package_path] = translator
                    self.model_load_seconds = time.monotonic() - started
                target_prefix = str(metadata.get("target_prefix") or "")
                results = translator.translate_batch(
                    [tokenizer.encode(translated, out_type=str)],
                    target_prefix=[[target_prefix]] if target_prefix else None,
                    replace_unknowns=True,
                    beam_size=4,
                    num_hypotheses=1,
                    length_penalty=0.2,
                    return_scores=True,
                )
                translated = normalize_translation_text(tokenizer.decode(results[0].hypotheses[0]))
                if target_prefix and translated.startswith(target_prefix):
                    translated = translated[len(target_prefix):]
                translated = translated.lstrip()
            return translated


def install_translation_models(config: dict) -> int:
    with _MODEL_LOCK:
        models_path = translation_models_dir(config)
        packages_dir = models_path / "packages"
        packages_dir.mkdir(parents=True, exist_ok=True)
        installed = 0
        for model_file in models_path.glob("*.argosmodel"):
            with zipfile.ZipFile(model_file) as archive:
                metadata_names = [PurePosixPath(name.replace("\\", "/")) for name in archive.namelist() if name.replace("\\", "/").endswith("/metadata.json")]
            if len(metadata_names) == 1 and len(metadata_names[0].parts) == 2:
                existing = packages_dir / metadata_names[0].parts[0]
                if _verified_package(existing):
                    continue
            with tempfile.TemporaryDirectory(prefix=".argos-install-", dir=models_path) as temp:
                staging = Path(temp) / "packages"
                safe_extract_zip(model_file, staging)
                metadata_paths = list(staging.glob("*/metadata.json"))
                if len(metadata_paths) != 1:
                    raise RuntimeError(f"Argos 模型必須包含一個頂層 metadata.json：{model_file.name}")
                package_dir = metadata_paths[0].parent
                if any(path.relative_to(staging).parts[0] != package_dir.name for path in staging.rglob("*")):
                    raise RuntimeError(f"Argos 模型包含多個頂層目錄：{model_file.name}")
                try:
                    metadata = json.loads(metadata_paths[0].read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise RuntimeError(f"Argos 模型 metadata 無效：{model_file.name}") from exc
                if not isinstance(metadata, dict) or not metadata.get("from_code") or not metadata.get("to_code"):
                    raise RuntimeError(f"Argos 模型 metadata 缺少語言代碼：{model_file.name}")
                if not (package_dir / "model").is_dir() or not (package_dir / "sentencepiece.model").is_file():
                    raise RuntimeError(f"Argos 模型內容不完整：{model_file.name}")
                write_install_manifest(package_dir)
                if not verify_install_manifest(package_dir, verify_hashes=True):
                    raise RuntimeError(f"Argos 模型 manifest 驗證失敗：{model_file.name}")
                target = packages_dir / package_dir.name
                if _verified_package(target):
                    continue
                atomic_replace_tree(package_dir, target)
                _VERIFIED_PACKAGES.pop(target, None)
                installed += 1
        return installed


def translation_model_pairs(source_language: str, target_language: str) -> tuple[tuple[str, str], ...]:
    source_code = language_code(source_language)
    target_code = language_code(target_language)
    if source_code in ("", "auto") or target_code in ("", "auto") or source_code == target_code:
        return ()
    if source_code != "en" and target_code != "en":
        return ((source_code, "en"), ("en", target_code), (target_code, "en"), ("en", source_code))
    return ((source_code, target_code), (target_code, source_code))


def download_translation_models(
    config: dict,
    source_language: str,
    target_language: str,
    registry: OfflineTranslationRegistry | None = None,
) -> list[Path]:
    pairs = translation_model_pairs(source_language, target_language)
    if not pairs:
        raise ValueError("請先選擇固定的來源語言與目標語言")
    index = requests.get(ARGOS_INDEX_URL, timeout=30)
    index.raise_for_status()
    available = index.json()
    if not isinstance(available, list):
        raise RuntimeError("Argos 模型索引格式錯誤")
    models_path = translation_models_dir(config)
    models_path.mkdir(parents=True, exist_ok=True)
    downloaded: list[Path] = []
    for source_code, target_code in pairs:
        package = next(
            (
                item
                for item in available
                if item.get("from_code") == source_code
                and item.get("to_code") == target_code
                and item.get("links")
            ),
            None,
        )
        if not package:
            raise RuntimeError(f"找不到 {source_code} 到 {target_code} 的離線翻譯模型")
        url = str(package["links"][0])
        model_path = models_path / Path(url).name
        if not model_path.exists():
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(prefix=f".{model_path.name}-", suffix=".tmp", dir=models_path, delete=False) as handle:
                    temporary = Path(handle.name)
                    with requests.get(url, timeout=120, stream=True) as response:
                        response.raise_for_status()
                        for block in response.iter_content(1024 * 1024):
                            if block:
                                handle.write(block)
                os.replace(temporary, model_path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        downloaded.append(model_path)
    if registry is None:
        install_translation_models(config)
    else:
        registry.reload()
    return downloaded


def translate_offline(
    config: dict,
    text: str,
    source_language: str,
    target_language: str,
    registry: OfflineTranslationRegistry | None = None,
) -> str:
    return (registry or OfflineTranslationRegistry(config)).translate(text, source_language, target_language)
