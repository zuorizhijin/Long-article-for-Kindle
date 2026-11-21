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

BOOK_EXTS = {
    ".epub",
    ".pdf",
    ".mobi",
    ".txt",
    ".md",
    ".html",
    ".htmlz",
    ".fb2",
    ".djvu",
    ".cbz",
    ".cbr",
    ".cbt",
    ".docx",
    ".doc",
    ".rtf",
    ".chm",
    ".xps",
}


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


def is_safe_rel_path(rel_path: str, allowed_exts: set[str] | tuple[str, ...]) -> bool:
    """Validate a relative POSIX path (no traversal, hidden/sdr dirs, or bad ext)."""
    if not rel_path or "\x00" in rel_path or "\\" in rel_path:
        return False
    if rel_path.startswith("/") or rel_path.endswith("/"):
        return False
    parts = rel_path.split("/")
    if any(not p or p in {".", ".."} for p in parts):
        return False
    # Reject Windows drive letters like C:foo
    if ":" in parts[0]:
        return False
    for p in parts:
        if p.startswith(".") or p.lower().endswith(".sdr"):
            return False
    return Path(parts[-1]).suffix.lower() in allowed_exts


def sanitize_rel_folder(folder: str) -> str:
    """Normalize and validate an optional relative POSIX subfolder path."""
    raw = (folder or "").strip().replace("\\", "/")
    raw = raw.strip("/")
    if not raw:
        return ""
    parts = [p.strip() for p in raw.split("/") if p.strip()]
    clean_parts: list[str] = []
    for p in parts:
        if p in {".", ".."} or ":" in p or p.startswith(".") or p.lower().endswith(".sdr"):
            raise ValueError(f"Invalid folder segment: {p!r}")
        cleaned = _INVALID_FILENAME_CHARS.sub("_", p).strip(" .")
        if not cleaned or cleaned in {".", ".."} or cleaned.startswith(".") or cleaned.lower().endswith(".sdr"):
            raise ValueError(f"Invalid folder segment: {p!r}")
        clean_parts.append(cleaned)
    return "/".join(clean_parts)


def is_safe_epub_name(name: str) -> bool:
    """Validate a requested EPUB relative POSIX path without modifying it."""
    return is_safe_rel_path(name, {".epub"})


def is_safe_book_name(name: str) -> bool:
    """Validate a requested e-book relative POSIX path (whitelisted ext)."""
    return is_safe_rel_path(name, BOOK_EXTS)


def _iter_rel_files(base_dir: Path, allowed_exts: set[str]) -> list[tuple[str, Path]]:
    """Recursively yield (relative_posix_path, abs_path) skipping hidden/.sdr dirs."""
    if not base_dir.is_dir():
        return []
    results: list[tuple[str, Path]] = []
    for root, dirs, files in os.walk(base_dir):
        dirs[:] = sorted(
            d for d in dirs if not d.startswith(".") and not d.lower().endswith(".sdr")
        )
        root_path = Path(root)
        for fn in sorted(files):
            if fn.startswith("."):
                continue
            full = root_path / fn
            if full.suffix.lower() not in allowed_exts:
                continue
            rel = full.relative_to(base_dir).as_posix()
            results.append((rel, full))
    return results


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
            for fn, epub in _iter_rel_files(folder, {".epub"}):
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
        folder: str = "",
    ) -> dict[str, Any]:
        """Save uploaded EPUB bytes into inbox/[folder/] and record in state.json."""
        safe_folder = sanitize_rel_folder(folder)
        safe_base = sanitize_filename(raw_filename, fallback_stem=title or "article")
        rel_path = f"{safe_folder}/{safe_base}" if safe_folder else safe_base
        dest = self.inbox_dir / rel_path
        sha256 = compute_sha256_bytes(epub_bytes)
        now = utc_now_iso()

        with self._lock:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(epub_bytes)
            # If a previous copy existed in read/, remove stale read copy
            read_copy = self.read_dir / rel_path
            if read_copy.exists():
                read_copy.unlink()

            state = self._read_state()
            articles = state.setdefault("articles", {})
            record = {
                "filename": rel_path,
                "title": (title or "").strip() or Path(safe_base).stem,
                "source_url": (source_url or "").strip(),
                "created_at": now,
                "status": "unread",
                "read_at": None,
                "size": len(epub_bytes),
                "sha256": sha256,
            }
            articles[rel_path] = record
            self._write_state(state)
            return dict(record)

    save_unread = save_uploaded_epub

    def list_folders(self) -> list[str]:
        """Return sorted relative POSIX subfolder paths under inbox/."""
        if not self.inbox_dir.is_dir():
            return []
        folders: list[str] = []
        for root, dirs, _files in os.walk(self.inbox_dir):
            dirs[:] = sorted(
                d for d in dirs if not d.startswith(".") and not d.lower().endswith(".sdr")
            )
            root_path = Path(root)
            for d in dirs:
                rel = (root_path / d).relative_to(self.inbox_dir).as_posix()
                folders.append(rel)
        return sorted(folders)

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
        """Locate an EPUB file in inbox/ or read/ by relative POSIX path."""
        if not is_safe_epub_name(filename):
            return None
        inbox_file = (self.inbox_dir / filename).resolve()
        if inbox_file.is_relative_to(self.inbox_dir) and inbox_file.is_file():
            return inbox_file
        read_file = (self.read_dir / filename).resolve()
        if read_file.is_relative_to(self.read_dir) and read_file.is_file():
            return read_file
        return None

    def mark_read(self, filename: str) -> bool:
        """Move <rel_path> from inbox/ to read/ and update state.json."""
        if not is_safe_epub_name(filename):
            return False
        with self._lock:
            inbox_file = (self.inbox_dir / filename).resolve()
            read_file = (self.read_dir / filename).resolve()
            if not inbox_file.is_relative_to(self.inbox_dir) or not read_file.is_relative_to(self.read_dir):
                return False
            if inbox_file.is_file():
                read_file.parent.mkdir(parents=True, exist_ok=True)
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


class BookLibrary:
    """Read-only scanner and hash cache for the local e-books directory."""

    def __init__(self, books_dir: Path | str) -> None:
        self.books_dir = Path(books_dir).resolve()
        self.books_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._hash_cache: dict[str, tuple[int, int, str]] = {}

    def _iter_book_entries(self) -> list[tuple[str, Path]]:
        return _iter_rel_files(self.books_dir, BOOK_EXTS)

    def count_books(self) -> int:
        return len(self._iter_book_entries())

    def list_books(self) -> list[dict[str, Any]]:
        """Recursively scan books_dir and return metadata sorted by mtime descending."""
        entries = self._iter_book_entries()
        results: list[dict[str, Any]] = []
        seen: set[str] = set()

        for rel_path, entry in entries:
            seen.add(rel_path)
            try:
                stat = entry.stat()
            except OSError:
                continue

            cache_key = (stat.st_size, stat.st_mtime_ns)
            with self._lock:
                cached = self._hash_cache.get(rel_path)
            if cached and cached[0] == cache_key[0] and cached[1] == cache_key[1]:
                sha256 = cached[2]
            else:
                sha256 = compute_sha256_file(entry)
                with self._lock:
                    self._hash_cache[rel_path] = (cache_key[0], cache_key[1], sha256)

            mtime_iso = _dt.datetime.fromtimestamp(
                stat.st_mtime, tz=_dt.timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
            results.append(
                {
                    "filename": rel_path,
                    "ext": entry.suffix.lower(),
                    "size": stat.st_size,
                    "mtime": mtime_iso,
                    "sha256": sha256,
                }
            )

        with self._lock:
            for stale in [k for k in self._hash_cache if k not in seen]:
                del self._hash_cache[stale]

        results.sort(key=lambda x: x.get("mtime", ""), reverse=True)
        return results

    def get_book_path(self, filename: str) -> Optional[Path]:
        """Locate a whitelisted e-book file inside books_dir by relative POSIX path."""
        if not is_safe_book_name(filename):
            return None
        cand = (self.books_dir / filename).resolve()
        if not cand.is_relative_to(self.books_dir) or not cand.is_file():
            return None
        return cand
