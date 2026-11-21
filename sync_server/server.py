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
import os
import signal
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

from state_store import StateStore, is_safe_epub_name

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = BASE_DIR / "config.json"


def load_config(config_path: Path) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "host": "0.0.0.0",
        "port": 8765,
        "data_dir": "data",
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
    ) -> None:
        self.store = store
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

    def _read_request_body(self) -> Optional[bytes]:
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
        max_bytes = self.server.max_upload_bytes  # type: ignore[attr-defined]
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

        if path == "/health":
            self._send_json(
                200,
                {
                    "ok": True,
                    "service": "kindle-article-sync",
                    "unread_count": store.unread_count(),
                },
            )
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
            if "/" in raw_name or not raw_name:
                self._send_json(404, {"ok": False, "error": "Not found"})
                return
            filename = urllib.parse.unquote(raw_name)
            if not is_safe_epub_name(filename):
                self._send_json(400, {"ok": False, "error": "Invalid filename"})
                return
            file_path = store.get_article_path(filename)
            if file_path is None:
                self._send_json(404, {"ok": False, "error": "Article not found"})
                return

            data = file_path.read_bytes()
            ascii_fallback = filename.encode("ascii", "ignore").decode("ascii") or "article.epub"
            utf8_quoted = urllib.parse.quote(filename, safe="")
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

        if path.startswith("/api/articles/") and path.endswith("/mark-read"):
            inner = path[len("/api/articles/") : -len("/mark-read")]
            if "/" in inner or not inner:
                self._send_json(404, {"ok": False, "error": "Not found"})
                return
            filename = urllib.parse.unquote(inner)
            if not is_safe_epub_name(filename):
                self._send_json(400, {"ok": False, "error": "Invalid filename"})
                return
            # Drain any optional request body cleanly
            length_hdr = self.headers.get("Content-Length")
            if length_hdr and length_hdr.isdigit() and int(length_hdr) > 0:
                self.rfile.read(min(int(length_hdr), 1024 * 1024))

            if store.mark_read(filename):
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
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error) as exc:
            self._send_json(400, {"ok": False, "error": f"Invalid upload payload: {exc}"})
            return

        if not epub_bytes or not epub_bytes.startswith(b"PK"):
            self._send_json(400, {"ok": False, "error": "Invalid EPUB binary (expected ZIP)"})
            return

        record = store.save_uploaded_epub(
            raw_filename=filename or (title or "article"),
            epub_bytes=epub_bytes,
            title=title,
            source_url=source_url,
        )
        self._send_json(200, {"ok": True, "filename": record["filename"]})


def create_server(
    host: str,
    port: int,
    data_dir: Path,
    max_upload_mb: int = 100,
) -> KindleSyncHTTPServer:
    store = StateStore(data_dir)
    store.reconcile()
    return KindleSyncHTTPServer(
        (host, port),
        KindleSyncHandler,
        store=store,
        max_upload_bytes=max_upload_mb * 1024 * 1024,
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Kindle KOReader Local HTTP Sync Server")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--host", type=str, default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--data-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    host = args.host if args.host is not None else str(cfg["host"])
    port = args.port if args.port is not None else int(cfg["port"])
    if args.data_dir is not None:
        data_dir = args.data_dir
    else:
        raw_data = Path(str(cfg["data_dir"]))
        data_dir = raw_data if raw_data.is_absolute() else (BASE_DIR / raw_data)

    max_upload_mb = int(cfg.get("max_upload_mb", 100))
    httpd = create_server(host, port, data_dir, max_upload_mb=max_upload_mb)
    bound_host, bound_port = httpd.server_address[:2]
    print(
        f"Kindle Article Sync Server listening on http://{bound_host}:{bound_port} "
        f"(data_dir={data_dir})",
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
