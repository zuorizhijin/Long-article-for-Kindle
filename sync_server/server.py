"""Lightweight local HTTP sync server for Kindle KOReader (Scheme A1).

Pure Python 3 stdlib implementation using ThreadingHTTPServer.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import email.parser
import email.policy
import json
import mimetypes
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

from state_store import (
    BookLibrary,
    StateStore,
    is_safe_book_name,
    is_safe_epub_name,
    sanitize_filename,
    sanitize_rel_folder,
)

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = BASE_DIR / "config.json"
STREAM_CHUNK_SIZE = 65536

CONVERT_TO_EPUB_EXTS = {
    ".mobi",
    ".azw3",
    ".azw",
    ".prc",
    ".txt",
    ".html",
    ".htm",
    ".docx",
    ".doc",
    ".rtf",
    ".fb2",
    ".chm",
    ".odt",
}

_CALIBRE_WIN_PATHS = (
    Path(r"C:\Program Files\Calibre2\ebook-convert.exe"),
    Path(r"C:\Program Files (x86)\Calibre2\ebook-convert.exe"),
)


def _find_calibre_convert() -> Optional[str]:
    """Locate Calibre's ebook-convert binary on PATH or standard Windows paths."""
    found = shutil.which("ebook-convert")
    if found:
        return found
    for cand in _CALIBRE_WIN_PATHS:
        if cand.is_file():
            return str(cand)
    return None

_BOOK_MIME_OVERRIDES = {
    ".epub": "application/epub+zip",
    ".pdf": "application/pdf",
    ".mobi": "application/x-mobipocket-ebook",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".htmlz": "application/zip",
    ".fb2": "application/x-fictionbook+xml",
    ".djvu": "image/vnd.djvu",
    ".cbz": "application/vnd.comicbook+zip",
    ".cbr": "application/vnd.comicbook-rar",
    ".cbt": "application/x-tar",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".rtf": "application/rtf",
    ".chm": "application/vnd.ms-htmlhelp",
    ".xps": "application/vnd.ms-xpsdocument",
}


def _guess_book_mime(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in _BOOK_MIME_OVERRIDES:
        return _BOOK_MIME_OVERRIDES[ext]
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def load_config(config_path: Path) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "host": "0.0.0.0",
        "port": 8765,
        "data_dir": "data",
        "books_dir": "data/books",
        "obsidian_vault": "",
        "max_upload_mb": 100,
    }
    if config_path.is_file():
        try:
            loaded = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                defaults.update(loaded)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[warn] Failed to read {config_path}: {exc}", file=sys.stderr)
    return defaults


def _parse_multipart(content_type: str, body: bytes) -> dict[str, Any]:
    """Parse multipart/form-data without using the deprecated cgi module."""
    raw = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + body
    msg = email.parser.BytesParser(policy=email.policy.default).parsebytes(raw)
    if not msg.is_multipart():
        raise ValueError("Invalid multipart payload")

    fields: dict[str, Any] = {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        payload = part.get_payload(decode=True) or b""
        part_filename = part.get_filename()
        if part_filename is not None:
            fields[name] = {"filename": part_filename, "bytes": payload}
        else:
            fields[name] = payload.decode("utf-8", errors="replace")
    return fields


def resolve_picked_inbox_folder(chosen_path: str, inbox_dir: Path) -> Optional[str]:
    """Resolve a picked directory path relative to inbox_dir.

    Returns None if cancelled (empty path), "" for inbox_dir itself,
    a normalized relative POSIX path for a subfolder inside inbox_dir,
    or raises ValueError if outside inbox_dir / invalid segment.
    """
    raw = (chosen_path or "").strip()
    if not raw:
        return None
    inbox_resolved = inbox_dir.resolve()
    chosen_resolved = Path(raw).resolve()
    if chosen_resolved == inbox_resolved:
        return ""
    if not chosen_resolved.is_relative_to(inbox_resolved):
        raise ValueError("请选择文章同步目录（inbox）或其内部的子文件夹")
    rel_posix = chosen_resolved.relative_to(inbox_resolved).as_posix()
    return sanitize_rel_folder(rel_posix)


def _run_folder_picker_dialog(inbox_dir: Path) -> str:
    """Open a topmost native folder picker in an isolated subprocess."""
    inbox_dir.mkdir(parents=True, exist_ok=True)
    script = (
        "import sys, tkinter as tk\n"
        "from tkinter import filedialog\n"
        "root = tk.Tk()\n"
        "root.withdraw()\n"
        "root.attributes('-topmost', True)\n"
        "chosen = filedialog.askdirectory(\n"
        "    parent=root,\n"
        "    initialdir=sys.argv[1],\n"
        "    title='选择或新建文章分类子文件夹（须在 inbox 目录内）',\n"
        "    mustexist=True,\n"
        ")\n"
        "root.destroy()\n"
        "if chosen:\n"
        "    sys.stdout.buffer.write(chosen.encode('utf-8'))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script, str(inbox_dir.resolve())],
        capture_output=True,
        timeout=300,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(err or f"Folder picker exited with code {proc.returncode}")
    return proc.stdout.decode("utf-8", errors="replace").strip()


class KindleSyncHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer carrying shared StateStore and upload limit."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        RequestHandlerClass: type[BaseHTTPRequestHandler],
        store: StateStore,
        max_upload_bytes: int,
        books: Optional[BookLibrary] = None,
    ) -> None:
        self.store = store
        self.books = books if books is not None else BookLibrary(store.data_dir / "books")
        self.max_upload_bytes = max_upload_bytes
        super().__init__(server_address, RequestHandlerClass)


class KindleSyncHandler(BaseHTTPRequestHandler):
    """HTTP request handler implementing the Kindle A1 sync API contract."""

    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        sys.stdout.write(
            "%s - - [%s] %s\n"
            % (self.address_string(), self.log_date_time_string(), format % args)
        )
        sys.stdout.flush()

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, Authorization, X-Requested-With",
        )
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Max-Age", "86400")

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_request_body(self, max_bytes_override: Optional[int] = None) -> Optional[bytes]:
        length_hdr = self.headers.get("Content-Length")
        if length_hdr is None:
            self._send_json(411, {"ok": False, "error": "Content-Length required"})
            return None
        try:
            length = int(length_hdr)
        except ValueError:
            self._send_json(400, {"ok": False, "error": "Invalid Content-Length"})
            return None
        if length < 0:
            self._send_json(400, {"ok": False, "error": "Negative Content-Length"})
            return None
        max_bytes = max_bytes_override if max_bytes_override is not None else self.server.max_upload_bytes  # type: ignore[attr-defined]
        if length > max_bytes:
            self._send_json(413, {"ok": False, "error": "Payload too large"})
            return None
        return self.rfile.read(length)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._send_cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        store: StateStore = self.server.store  # type: ignore[attr-defined]
        books: BookLibrary = self.server.books  # type: ignore[attr-defined]

        if path == "/health":
            self._send_json(
                200,
                {
                    "ok": True,
                    "service": "kindle-article-sync",
                    "unread_count": store.unread_count(),
                    "books_count": books.count_books(),
                    "calibre_available": _find_calibre_convert() is not None,
                },
            )
            return

        if path == "/api/folders":
            self._send_json(200, {"folders": store.list_folders()})
            return

        if path == "/api/books":
            self._send_json(200, {"books": books.list_books()})
            return

        if path.startswith("/api/books/"):
            raw_name = path[len("/api/books/") :]
            if not raw_name:
                self._send_json(404, {"ok": False, "error": "Not found"})
                return
            rel_path = urllib.parse.unquote(raw_name)
            if not is_safe_book_name(rel_path):
                self._send_json(400, {"ok": False, "error": "Invalid book filename"})
                return
            file_path = books.get_book_path(rel_path)
            if file_path is None:
                self._send_json(404, {"ok": False, "error": "Book not found"})
                return

            file_size = file_path.stat().st_size
            base_name = Path(rel_path).name
            ascii_fallback = base_name.encode("ascii", "ignore").decode("ascii") or f"book{file_path.suffix}"
            utf8_quoted = urllib.parse.quote(base_name, safe="")
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", _guess_book_mime(base_name))
            self.send_header("Content-Length", str(file_size))
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{utf8_quoted}',
            )
            self.end_headers()
            try:
                with file_path.open("rb") as f:
                    while True:
                        chunk = f.read(STREAM_CHUNK_SIZE)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        if path == "/api/articles":
            qs = urllib.parse.parse_qs(parsed.query)
            status = (qs.get("status", ["unread"])[0] or "unread").strip().lower()
            if status not in {"unread", "read", "all"}:
                self._send_json(400, {"ok": False, "error": "Invalid status filter"})
                return
            articles = store.list_articles(status=status)
            self._send_json(200, {"articles": articles})
            return

        if path.startswith("/api/articles/"):
            raw_name = path[len("/api/articles/") :]
            if not raw_name:
                self._send_json(404, {"ok": False, "error": "Not found"})
                return
            rel_path = urllib.parse.unquote(raw_name)
            if not is_safe_epub_name(rel_path):
                self._send_json(400, {"ok": False, "error": "Invalid filename"})
                return
            file_path = store.get_article_path(rel_path)
            if file_path is None:
                self._send_json(404, {"ok": False, "error": "Article not found"})
                return

            data = file_path.read_bytes()
            base_name = Path(rel_path).name
            ascii_fallback = base_name.encode("ascii", "ignore").decode("ascii") or "article.epub"
            utf8_quoted = urllib.parse.quote(base_name, safe="")
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/epub+zip")
            self.send_header("Content-Length", str(len(data)))
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{utf8_quoted}',
            )
            self.end_headers()
            self.wfile.write(data)
            return

        self._send_json(404, {"ok": False, "error": "Not found"})

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        store: StateStore = self.server.store  # type: ignore[attr-defined]

        if path == "/api/upload":
            self._handle_upload(store)
            return

        if path == "/api/convert-upload":
            books: BookLibrary = self.server.books  # type: ignore[attr-defined]
            self._handle_convert_upload(store, books)
            return

        if path == "/api/folders/pick":
            length_hdr = self.headers.get("Content-Length")
            if length_hdr and length_hdr.isdigit() and int(length_hdr) > 0:
                self.rfile.read(min(int(length_hdr), 1024 * 1024))
            try:
                chosen = _run_folder_picker_dialog(store.inbox_dir)
                rel_folder = resolve_picked_inbox_folder(chosen, store.inbox_dir)
            except ValueError as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
                return
            except (subprocess.TimeoutExpired, RuntimeError, OSError) as exc:
                self._send_json(500, {"ok": False, "error": f"Folder picker failed: {exc}"})
                return

            if rel_folder is None:
                self._send_json(200, {"ok": True, "cancelled": True})
            else:
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "cancelled": False,
                        "folder": rel_folder,
                        "folders": store.list_folders(),
                    },
                )
            return

        if path.startswith("/api/articles/") and path.endswith("/mark-read"):
            inner = path[len("/api/articles/") : -len("/mark-read")]
            if not inner:
                self._send_json(404, {"ok": False, "error": "Not found"})
                return
            rel_path = urllib.parse.unquote(inner)
            if not is_safe_epub_name(rel_path):
                self._send_json(400, {"ok": False, "error": "Invalid filename"})
                return
            # Drain any optional request body cleanly
            length_hdr = self.headers.get("Content-Length")
            if length_hdr and length_hdr.isdigit() and int(length_hdr) > 0:
                self.rfile.read(min(int(length_hdr), 1024 * 1024))

            if store.mark_read(rel_path):
                self._send_json(200, {"ok": True})
            else:
                self._send_json(404, {"ok": False, "error": "Article not found"})
            return

        self._send_json(404, {"ok": False, "error": "Not found"})

    def _handle_upload(self, store: StateStore) -> None:
        body = self._read_request_body()
        if body is None:
            return

        content_type = (self.headers.get("Content-Type") or "").lower()
        filename = ""
        title = ""
        source_url = ""
        folder = ""
        epub_bytes: Optional[bytes] = None

        try:
            if "multipart/form-data" in content_type:
                fields = _parse_multipart(self.headers.get("Content-Type", ""), body)
                file_field = fields.get("file") or fields.get("epub")
                if isinstance(file_field, dict):
                    epub_bytes = file_field["bytes"]
                    filename = str(fields.get("filename") or file_field.get("filename") or "")
                elif isinstance(fields.get("epub_base64"), str):
                    epub_bytes = base64.b64decode(fields["epub_base64"], validate=True)
                    filename = str(fields.get("filename") or "")
                title = str(fields.get("title") or "")
                source_url = str(fields.get("source_url") or "")
                folder = str(fields.get("folder") or "")
            else:
                payload = json.loads(body.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("JSON body must be an object")
                b64 = payload.get("epub_base64")
                if not isinstance(b64, str) or not b64.strip():
                    raise ValueError("Missing epub_base64")
                if "," in b64 and b64.lstrip().startswith("data:"):
                    b64 = b64.split(",", 1)[1]
                epub_bytes = base64.b64decode(b64.strip(), validate=True)
                filename = str(payload.get("filename") or "")
                title = str(payload.get("title") or "")
                source_url = str(payload.get("source_url") or "")
                folder = str(payload.get("folder") or "")
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error) as exc:
            self._send_json(400, {"ok": False, "error": f"Invalid upload payload: {exc}"})
            return

        if not epub_bytes or not epub_bytes.startswith(b"PK"):
            self._send_json(400, {"ok": False, "error": "Invalid EPUB binary (expected ZIP)"})
            return

        try:
            record = store.save_uploaded_epub(
                raw_filename=filename or (title or "article"),
                epub_bytes=epub_bytes,
                title=title,
                source_url=source_url,
                folder=folder,
            )
        except ValueError as exc:
            self._send_json(400, {"ok": False, "error": f"Invalid folder: {exc}"})
            return
        self._send_json(200, {"ok": True, "filename": record["filename"]})

    def _handle_convert_upload(self, store: StateStore, books: BookLibrary) -> None:
        max_raw = max(self.server.max_upload_bytes, 100 * 1024 * 1024)  # type: ignore[attr-defined]
        body = self._read_request_body(max_bytes_override=int(max_raw * 1.45))
        if body is None:
            return

        try:
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object")
            raw_filename = str(payload.get("filename") or "").strip()
            if not raw_filename:
                raise ValueError("Missing filename")
            b64 = payload.get("content_base64")
            if not isinstance(b64, str) or not b64.strip():
                raise ValueError("Missing content_base64")
            if "," in b64 and b64.lstrip().startswith("data:"):
                b64 = b64.split(",", 1)[1]
            src_bytes = base64.b64decode(b64.strip(), validate=True)
            if not src_bytes:
                raise ValueError("Empty file content")
            if len(src_bytes) > max_raw:
                self._send_json(413, {"ok": False, "error": "文件超过大小限制（最大 100MB）"})
                return

            target = str(payload.get("target") or "inbox").strip().lower()
            if target not in {"inbox", "books"}:
                self._send_json(400, {"ok": False, "error": "target 仅支持 inbox 或 books"})
                return

            safe_folder = sanitize_rel_folder(str(payload.get("folder") or ""))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error) as exc:
            self._send_json(400, {"ok": False, "error": f"无效请求参数: {exc}"})
            return

        # Extract safe basename and validate extension
        base_only = raw_filename.replace("\\", "/").split("/")[-1].strip()
        if not base_only or base_only in {".", ".."} or "\x00" in base_only:
            self._send_json(400, {"ok": False, "error": "无效文件名"})
            return
        ext = Path(base_only).suffix.lower()
        stem = Path(sanitize_filename(f"{Path(base_only).stem}.epub")).stem

        direct_ok = ext == ".epub" or (target == "books" and ext == ".pdf")
        if not direct_ok and ext not in CONVERT_TO_EPUB_EXTS:
            if ext == ".kfx":
                self._send_json(422, {"ok": False, "error": "不支持 .kfx 格式（Amazon 专有/通常带 DRM 加密）"})
            else:
                self._send_json(400, {"ok": False, "error": f"不支持的文件格式: {ext or '(无后缀)'}"})
            return

        converted = False
        out_ext = ext if direct_ok else ".epub"
        out_bytes = src_bytes

        if direct_ok:
            if ext == ".epub" and not src_bytes.startswith(b"PK"):
                self._send_json(400, {"ok": False, "error": "无效的 EPUB 文件（非 ZIP 格式）"})
                return
        else:
            calibre_bin = _find_calibre_convert()
            if not calibre_bin:
                self._send_json(
                    500,
                    {"ok": False, "error": "电脑端未安装 Calibre（未找到 ebook-convert），无法转换该格式"},
                )
                return
            try:
                with tempfile.TemporaryDirectory() as tmpdir:
                    tmp_path = Path(tmpdir)
                    src_file = tmp_path / f"input{ext}"
                    dst_file = tmp_path / "output.epub"
                    src_file.write_bytes(src_bytes)
                    proc = subprocess.run(
                        [calibre_bin, str(src_file), str(dst_file)],
                        capture_output=True,
                        timeout=180,
                    )
                    if proc.returncode != 0 or not dst_file.is_file() or dst_file.stat().st_size == 0:
                        combined = (
                            proc.stderr.decode("utf-8", errors="replace")
                            + "\n"
                            + proc.stdout.decode("utf-8", errors="replace")
                        ).strip()
                        low = combined.lower()
                        if any(k in low for k in ("drm", "encrypt", "protected", "kfx")):
                            msg = "转换失败：文件受 DRM 加密保护或为专有加密格式，无法转换"
                        else:
                            snippet = combined.splitlines()[-1][:200] if combined else f"exit code {proc.returncode}"
                            msg = f"Calibre 转换失败: {snippet}"
                        self._send_json(422, {"ok": False, "error": msg})
                        return
                    out_bytes = dst_file.read_bytes()
                    converted = True
            except subprocess.TimeoutExpired:
                self._send_json(422, {"ok": False, "error": "Calibre 转换超时（超过 180 秒）"})
                return
            except OSError as exc:
                self._send_json(500, {"ok": False, "error": f"转换过程发生系统错误: {exc}"})
                return

        if target == "inbox":
            record = store.save_uploaded_epub(
                raw_filename=f"{stem}.epub",
                epub_bytes=out_bytes,
                title=stem,
                folder=safe_folder,
            )
            rel_path = record["filename"]
        else:
            safe_base = f"{stem}{out_ext}"
            rel_path = f"{safe_folder}/{safe_base}" if safe_folder else safe_base
            dest = (books.books_dir / rel_path).resolve()
            if not dest.is_relative_to(books.books_dir):
                self._send_json(400, {"ok": False, "error": "非法目标路径"})
                return
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp_dest = dest.with_name(f".{dest.name}.tmp")
            tmp_dest.write_bytes(out_bytes)
            os.replace(tmp_dest, dest)

        self._send_json(
            200,
            {
                "ok": True,
                "filename": rel_path,
                "target": target,
                "converted": converted,
                "size": len(out_bytes),
            },
        )


def create_server(
    host: str,
    port: int,
    data_dir: Path,
    max_upload_mb: int = 100,
    books_dir: Optional[Path] = None,
) -> KindleSyncHTTPServer:
    store = StateStore(data_dir)
    store.reconcile()
    books = BookLibrary(books_dir if books_dir is not None else (data_dir / "books"))
    return KindleSyncHTTPServer(
        (host, port),
        KindleSyncHandler,
        store=store,
        max_upload_bytes=max_upload_mb * 1024 * 1024,
        books=books,
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Kindle KOReader Local HTTP Sync Server")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--host", type=str, default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--books-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    host = args.host if args.host is not None else str(cfg["host"])
    port = args.port if args.port is not None else int(cfg["port"])
    if args.data_dir is not None:
        data_dir = args.data_dir
    else:
        raw_data = Path(str(cfg["data_dir"]))
        data_dir = raw_data if raw_data.is_absolute() else (BASE_DIR / raw_data)

    if args.books_dir is not None:
        books_dir = args.books_dir
    elif cfg.get("books_dir"):
        raw_books = Path(str(cfg["books_dir"]))
        books_dir = raw_books if raw_books.is_absolute() else (BASE_DIR / raw_books)
    else:
        books_dir = data_dir / "books"

    max_upload_mb = int(cfg.get("max_upload_mb", 100))
    httpd = create_server(
        host, port, data_dir, max_upload_mb=max_upload_mb, books_dir=books_dir
    )
    bound_host, bound_port = httpd.server_address[:2]
    print(
        f"Kindle Article Sync Server listening on http://{bound_host}:{bound_port} "
        f"(data_dir={data_dir}, books_dir={books_dir})",
        flush=True,
    )

    def _shutdown_handler(signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt()

    signal.signal(signal.SIGTERM, _shutdown_handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...", flush=True)
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
