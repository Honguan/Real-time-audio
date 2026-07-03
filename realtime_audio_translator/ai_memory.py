import json
import sqlite3
from pathlib import Path


def _ensure_cache(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS translations (
                provider TEXT NOT NULL,
                source_language TEXT NOT NULL,
                target_language TEXT NOT NULL,
                source_text TEXT NOT NULL,
                translated_text TEXT NOT NULL,
                PRIMARY KEY (provider, source_language, target_language, source_text)
            )
            """
        )


def cached_translation(db_path: Path, provider: str, source_language: str, target_language: str, text: str) -> str | None:
    if not db_path.exists():
        return None
    with sqlite3.connect(db_path) as db:
        row = db.execute(
            """
            SELECT translated_text
            FROM translations
            WHERE provider = ? AND source_language = ? AND target_language = ? AND source_text = ?
            """,
            (provider, source_language, target_language, text.strip()),
        ).fetchone()
    return str(row[0]) if row else None


def cache_translation(db_path: Path, provider: str, source_language: str, target_language: str, text: str, translated: str) -> None:
    if not text.strip() or not translated.strip():
        return
    _ensure_cache(db_path)
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            INSERT OR REPLACE INTO translations
                (provider, source_language, target_language, source_text, translated_text)
            VALUES (?, ?, ?, ?, ?)
            """,
            (provider, source_language, target_language, text.strip(), translated),
        )


def add_glossary_term(glossary_path: Path, source: str, target: str) -> None:
    source = source.strip()
    target = target.strip()
    if not source or not target:
        return
    glossary_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        glossary = json.loads(glossary_path.read_text(encoding="utf-8")) if glossary_path.exists() else {}
    except Exception:
        glossary = {}
    if not isinstance(glossary, dict):
        glossary = {}
    glossary[source] = target
    glossary_path.write_text(json.dumps(glossary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
