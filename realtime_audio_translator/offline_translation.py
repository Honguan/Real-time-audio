import json
import zipfile
from pathlib import Path

import requests

from .models import models_dir


ARGOS_INDEX_URL = "https://raw.githubusercontent.com/argosopentech/argospm-index/main/index.json"
_TRANSLATORS: dict[Path, object] = {}
_TOKENIZERS: dict[Path, object] = {}


def language_code(language: str) -> str:
    return str(language or "").split("-")[0].lower()


def normalize_translation_text(text: str) -> str:
    return " ".join(str(text).replace("▁", " ").split())


def translation_models_dir(config: dict) -> Path:
    return models_dir(config) / "translation"


def _installed_packages(config: dict) -> list[tuple[dict, Path]]:
    packages_dir = translation_models_dir(config) / "packages"
    packages: list[tuple[dict, Path]] = []
    if not packages_dir.exists():
        return packages
    for package_dir in packages_dir.iterdir():
        metadata_path = package_dir / "metadata.json"
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(metadata, dict) and (package_dir / "model").is_dir() and (package_dir / "sentencepiece.model").is_file():
            packages.append((metadata, package_dir))
    return packages


def translation_model_available(config: dict, source_language: str = "", target_language: str = "") -> bool:
    source_code = language_code(source_language)
    target_code = language_code(target_language)
    packages = _installed_packages(config)
    if source_code in ("", "auto"):
        return any(not target_code or metadata.get("to_code") == target_code for metadata, _path in packages)
    return bool(_translation_path(packages, source_code, target_code))


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


def install_translation_models(config: dict) -> int:
    models_path = translation_models_dir(config)
    packages_dir = models_path / "packages"
    packages_dir.mkdir(parents=True, exist_ok=True)
    installed = 0
    for model_file in models_path.glob("*.argosmodel"):
        with zipfile.ZipFile(model_file) as archive:
            metadata_name = next((name for name in archive.namelist() if name.endswith("/metadata.json")), "")
            if not metadata_name:
                continue
            package_name = Path(metadata_name).parent.name
            package_dir = packages_dir / package_name
            if (package_dir / "metadata.json").is_file():
                continue
            archive.extractall(packages_dir)
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


def download_translation_models(config: dict, source_language: str, target_language: str) -> list[Path]:
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
            with requests.get(url, timeout=120, stream=True) as response:
                response.raise_for_status()
                with model_path.open("wb") as handle:
                    for block in response.iter_content(1024 * 1024):
                        if block:
                            handle.write(block)
        downloaded.append(model_path)
    install_translation_models(config)
    return downloaded


def translate_offline(config: dict, text: str, source_language: str, target_language: str) -> str:
    source_code = language_code(source_language)
    target_code = language_code(target_language)
    if source_code in ("", "auto") or not target_code or source_code == target_code:
        return ""
    install_translation_models(config)
    packages = _translation_path(_installed_packages(config), source_code, target_code)
    if not packages:
        return ""
    try:
        import ctranslate2
        import sentencepiece as sentencepiece
    except ImportError:
        return ""
    translated = text
    for metadata, package_path in packages:
        tokenizer = _TOKENIZERS.get(package_path)
        if tokenizer is None:
            tokenizer = sentencepiece.SentencePieceProcessor(model_file=str(package_path / "sentencepiece.model"))
            _TOKENIZERS[package_path] = tokenizer
        translator = _TRANSLATORS.get(package_path)
        if translator is None:
            translator = ctranslate2.Translator(str(package_path / "model"), device="cpu")
            _TRANSLATORS[package_path] = translator
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
