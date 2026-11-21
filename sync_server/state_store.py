"""Thread-safe storage and state.json manager for Kindle Article Sync."""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import threading
from pathlib import Path
from typing import Any, Optional

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sanitize_filename(raw_name: str, fallback_stem: str = "article") -> str:
    """Sanitize an EPUB filename and prevent path traversal."""
    name = (raw_name or "").strip()
    # Take only the final path segment across both POSIX and Windows separators
    name = name.replace("\\", "/").split("/")[-1].strip()
    name = _INVALID_FILENAME_CHARS.sub("_", name)
    name = name.strip(" .")
    if not name or name in {".", ".."}:
        name = fallback_stem
    if not name.lower().endswith(".epub"):
        name = f"{name}.epub"
    # Keep filenames bounded to avoid Windows MAX_PATH issues
    if len(name) > 180:
        stem = name[:-5][:175].rstrip(" .")
        name = f"{stem}.epub"
    return name


def is_safe_epub_name(name: str) -> bool:
    """Validate a requested filename from a URL path without modifying it."""
    if not name or name in {".", ".."}:
        return False
    if "/" in name or "\\" in name or "\x00" in name:
        return False
    if ".." in Path(name).parts:
        return False
    return name.lower().endswith(".epub")


def compute_sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compute_sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class StateStore:
    """Manages data/inbox/, data/read/, and data/state.json."""

    def __init__(self, data_dir: Path | str) -> None:
        self.data_dir = Path(data_dir).resolve()
        self.inbox_dir = self.data_dir / "inbox"
        self.read_dir = self.data_dir / "read"
        self.state_path = self.data_dir / "state.json"
        self._lock = threading.RLock()
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        self.read_dir.mkdir(parents=True, exist_ok=True)
        if not self.state_path.exists():
            self._write_state({"articles": {}})

    def _read_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"articles": {}}
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("articles"), dict):
                return data
        except (OSError, json.JSONDecodeError):
            pass
        return {"articles": {}}

    def _write_state(self, state: dict[str, Any]) -> None:
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, self.state_path)

    def _reconcile_unlocked(self, state: dict[str, Any]) -> bool:
        """Discover any .epub files in inbox/ or read/ missing from state.json."""
        articles: dict[str, dict[str, Any]] = state.setdefault("articles", {})
        changed = False

        for folder, status in ((self.inbox_dir, "unread"), (self.read_dir, "read")):
            if not folder.exists():
                continue
            for epub in sorted(folder.glob("*.epub")):
                fn = epub.name
                stat = epub.stat()
                if fn not in articles:
                    created = _dt.datetime.fromtimestamp(
                        stat.st_mtime, tz=_dt.timezone.utc
                    ).strftime("%Y-%m-%dT%H:%M:%SZ")
                    articles[fn] = {
                        "filename": fn,
                        "title": epub.stem,
                        "source_url": "",
                        "created_at": created,
                        "status": status,
                        "read_at": created if status == "read" else None,
                        "size": stat.st_size,
                        "sha256": compute_sha256_file(epub),
                    }
                    changed = True
                else:
                    rec = articles[fn]
                    if rec.get("status") != status:
                        rec["status"] = status
                        if status == "read" and not rec.get("read_at"):
                            rec["read_at"] = utc_now_iso()
                        changed = True
                    if rec.get("size") != stat.st_size or not rec.get("sha256"):
                        rec["size"] = stat.st_size
                        rec["sha256"] = compute_sha256_file(epub)
                        changed = True

        return changed

    def reconcile(self) -> None:
        with self._lock:
            state = self._read_state()
            if self._reconcile_unlocked(state):
                self._write_state(state)

    def save_uploaded_epub(
        self,
        raw_filename: str,
        epub_bytes: bytes,
        title: str = "",
        source_url: str = "",
    ) -> dict[str, Any]:
        """Save uploaded EPUB bytes into inbox/ and record in state.json."""
        filename = sanitize_filename(raw_filename, fallback_stem=title or "article")
        dest = self.inbox_dir / filename
        sha256 = compute_sha256_bytes(epub_bytes)
        now = utc_now_iso()

        with self._lock:
            dest.write_bytes(epub_bytes)
            # If a previous copy existed in read/, remove stale read copy
            read_copy = self.read_dir / filename
            if read_copy.exists():
                read_copy.unlink()

            state = self._read_state()
            articles = state.setdefault("articles", {})
            record = {
                "filename": filename,
                "title": (title or "").strip() or Path(filename).stem,
                "source_url": (source_url or "").strip(),
                "created_at": now,
                "status": "unread",
                "read_at": None,
                "size": len(epub_bytes),
                "sha256": sha256,
            }
            articles[filename] = record
            self._write_state(state)
            return dict(record)

    def register_inbox_file(
        self,
        filename: str,
        title: str = "",
        source_url: str = "",
    ) -> dict[str, Any]:
        """Register an .epub already written to inbox/ in state.json."""
        epub_path = self.inbox_dir / filename
        if not epub_path.is_file():
            raise FileNotFoundError(f"EPUB not found in inbox: {epub_path}")
        stat = epub_path.stat()
        now = utc_now_iso()
        sha256 = compute_sha256_file(epub_path)

        with self._lock:
            state = self._read_state()
            articles = state.setdefault("articles", {})
            record = {
                "filename": filename,
                "title": (title or "").strip() or epub_path.stem,
                "source_url": (source_url or "").strip(),
                "created_at": now,
                "status": "unread",
                "read_at": None,
                "size": stat.st_size,
                "sha256": sha256,
            }
            articles[filename] = record
            self._write_state(state)
            return dict(record)

    def list_articles(self, status: str = "unread") -> list[dict[str, Any]]:
        """Return articles filtered by status ('unread', 'read', or 'all')."""
        with self._lock:
            state = self._read_state()
            if self._reconcile_unlocked(state):
                self._write_state(state)

            articles = state.get("articles", {})
            result: list[dict[str, Any]] = []
            for fn, rec in articles.items():
                rec_status = rec.get("status", "unread")
                if status != "all" and rec_status != status:
                    continue
                folder = self.inbox_dir if rec_status == "unread" else self.read_dir
                file_path = folder / fn
                if not file_path.is_file():
                    continue
                item = {
                    "filename": fn,
                    "title": rec.get("title") or Path(fn).stem,
                    "size": rec.get("size", file_path.stat().st_size),
                    "sha256": rec.get("sha256", ""),
                    "created_at": rec.get("created_at", ""),
                }
                if rec.get("source_url"):
                    item["source_url"] = rec["source_url"]
                if status == "all":
                    item["status"] = rec_status
                    item["read_at"] = rec.get("read_at")
                result.append(item)

            result.sort(key=lambda x: x.get("created_at", ""), reverse=True)
            return result

    def unread_count(self) -> int:
        return len(self.list_articles(status="unread"))

    def get_article_path(self, filename: str) -> Optional[Path]:
        """Locate an EPUB file in inbox/ or read/."""
        if not is_safe_epub_name(filename):
            return None
        inbox_file = self.inbox_dir / filename
        if inbox_file.is_file():
            return inbox_file
        read_file = self.read_dir / filename
        if read_file.is_file():
            return read_file
        return None

    def mark_read(self, filename: str) -> bool:
        """Move <filename> from inbox/ to read/ and update state.json."""
        if not is_safe_epub_name(filename):
            return False
        with self._lock:
            inbox_file = self.inbox_dir / filename
            read_file = self.read_dir / filename
            if inbox_file.is_file():
                shutil.move(str(inbox_file), str(read_file))
            elif not read_file.is_file():
                return False

            state = self._read_state()
            articles = state.setdefault("articles", {})
            now = utc_now_iso()
            if filename in articles:
                articles[filename]["status"] = "read"
                articles[filename]["read_at"] = now
            else:
                stat = read_file.stat()
                articles[filename] = {
                    "filename": filename,
                    "title": read_file.stem,
                    "source_url": "",
                    "created_at": now,
                    "status": "read",
                    "read_at": now,
                    "size": stat.st_size,
                    "sha256": compute_sha256_file(read_file),
                }
            self._write_state(state)
            return True
