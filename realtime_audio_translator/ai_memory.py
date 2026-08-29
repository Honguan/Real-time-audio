import hashlib
import json
import sqlite3
import threading
from contextlib import closing
from pathlib import Path


CACHE_SCHEMA_VERSION = 2
_CACHE_SCHEMA_LOCK = threading.Lock()


def _create_cache(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE translations (
            request_fingerprint TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            source_language TEXT NOT NULL,
            target_language TEXT NOT NULL,
            source_text TEXT NOT NULL,
            translated_text TEXT NOT NULL
        )
        """
    )


def _ensure_cache(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _CACHE_SCHEMA_LOCK:
        with closing(sqlite3.connect(db_path)) as db, db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > CACHE_SCHEMA_VERSION:
                raise RuntimeError(f"翻譯快取版本過新：{version}")
            columns = {row[1] for row in db.execute("PRAGMA table_info(translations)")}
            if not columns:
                _create_cache(db)
            elif "request_fingerprint" not in columns:
                legacy_rows = db.execute(
                    "SELECT provider, source_language, target_language, source_text, translated_text FROM translations"
                ).fetchall()
                db.execute("ALTER TABLE translations RENAME TO translations_v1")
                _create_cache(db)
                db.executemany(
                    """
                    INSERT INTO translations
                        (request_fingerprint, provider, source_language, target_language, source_text, translated_text)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            "legacy:" + hashlib.sha256(json.dumps(row[:4], ensure_ascii=False).encode("utf-8")).hexdigest(),
                            *row,
                        )
                        for row in legacy_rows
                    ],
                )
                db.execute("DROP TABLE translations_v1")
            db.execute(f"PRAGMA user_version = {CACHE_SCHEMA_VERSION}")


def cached_translation(db_path: Path, request_fingerprint: str) -> str | None:
    if not db_path.exists():
        return None
    _ensure_cache(db_path)
    with closing(sqlite3.connect(db_path)) as db:
        row = db.execute(
            "SELECT translated_text FROM translations WHERE request_fingerprint = ?",
            (request_fingerprint,),
        ).fetchone()
    return str(row[0]) if row else None


def cache_translation(db_path: Path, request_fingerprint: str, provider: str, source_language: str, target_language: str, text: str, translated: str) -> None:
    if not text.strip() or not translated.strip():
        return
    _ensure_cache(db_path)
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute(
            """
            INSERT OR REPLACE INTO translations
                (request_fingerprint, provider, source_language, target_language, source_text, translated_text)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (request_fingerprint, provider, source_language, target_language, text.strip(), translated),
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
