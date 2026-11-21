"""Obsidian Markdown to EPUB3 CLI converter (pure Python 3 stdlib).

Searches the Obsidian vault (configured via config.json or --vault) for
![[image]] and ![](image) embeds, builds a valid EPUB3 in data/inbox/,
and registers it in data/state.json.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Optional

from epub_builder import EpubBook, EpubChapter, EpubImage, write_epub_file
from state_store import StateStore, sanitize_filename

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = BASE_DIR / "config.json"
DEFAULT_VAULT = Path.cwd()
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}

_WIKI_IMG_RE = re.compile(r"!\[\[([^\]]+)\]\]")
_MD_IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_WIKI_LINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_STRIKE_RE = re.compile(r"~~(.+?)~~")
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_HR_RE = re.compile(r"^\s*(?:[-*_]\s*){3,}$")
_UL_RE = re.compile(r"^\s*[-*+]\s+(.+)$")
_OL_RE = re.compile(r"^\s*\d+[.)]\s+(.+)$")
_FENCE_RE = re.compile(r"^\s*(```+|~~~+)(.*)$")


def _load_defaults() -> tuple[Path, Path]:
    vault = DEFAULT_VAULT
    data_dir = BASE_DIR / "data"
    if DEFAULT_CONFIG_PATH.is_file():
        try:
            cfg = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
            if cfg.get("obsidian_vault"):
                vault = Path(cfg["obsidian_vault"])
            if cfg.get("data_dir"):
                raw = Path(cfg["data_dir"])
                data_dir = raw if raw.is_absolute() else (BASE_DIR / raw)
        except (OSError, json.JSONDecodeError):
            pass
    return vault, data_dir


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Extract simple YAML frontmatter key/value pairs without external deps."""
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    if len(lines) < 3 or lines[0].strip() != "---":
        return {}, text

    end_idx: Optional[int] = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() in {"---", "..."}:
            end_idx = idx
            break
    if end_idx is None:
        return {}, text

    meta: dict[str, str] = {}
    for line in lines[1:end_idx]:
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        key, val = line.split(":", 1)
        key = key.strip().lower()
        val = val.strip().strip('"').strip("'")
        if key and val:
            meta[key] = val

    body = "\n".join(lines[end_idx + 1 :])
    return meta, body


class VaultImageResolver:
    """Resolve Obsidian ![[img]] and Markdown ![](img) paths against a vault."""

    def __init__(self, md_dir: Path, vault_dir: Optional[Path]) -> None:
        self.md_dir = md_dir
        self.vault_dir = vault_dir if (vault_dir and vault_dir.exists()) else None
        self._name_index: Optional[dict[str, Path]] = None
        self._embedded: dict[Path, EpubImage] = {}
        self._counter = 0

    def _build_index(self) -> dict[str, Path]:
        if self._name_index is not None:
            return self._name_index
        index: dict[str, Path] = {}
        if self.vault_dir is not None:
            for p in self.vault_dir.rglob("*"):
                if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
                    index.setdefault(p.name.lower(), p)
        self._name_index = index
        return index

    def resolve_path(self, raw_target: str) -> Optional[Path]:
        target = urllib.parse.unquote(raw_target.strip().strip("<>"))
        if not target or target.startswith(("http://", "https://", "data:")):
            return None
        # Strip optional title after space in Markdown ![](path "title")
        if ' "' in target:
            target = target.split(' "', 1)[0].strip()
        if " '" in target:
            target = target.split(" '", 1)[0].strip()

        rel = Path(target)
        # 1. Relative to the markdown file
        cand1 = (self.md_dir / rel).resolve()
        if cand1.is_file():
            return cand1

        # 2. Relative to vault root
        if self.vault_dir is not None:
            cand2 = (self.vault_dir / rel).resolve()
            if cand2.is_file():
                return cand2

            # 3. Filename lookup across the vault
            index = self._build_index()
            found = index.get(rel.name.lower())
            if found and found.is_file():
                return found

        return None

    def embed_image(self, raw_target: str, alt: str = "") -> str:
        """Resolve and embed an image, returning an XHTML <figure> or placeholder."""
        resolved = self.resolve_path(raw_target)
        if resolved is None:
            label = html.escape(alt or raw_target)
            return f'<div class="missing-image">[Missing image: {label}]</div>'

        if resolved not in self._embedded:
            self._counter += 1
            ext = resolved.suffix.lower() or ".png"
            href = f"images/img_{self._counter:03d}{ext}"
            self._embedded[resolved] = EpubImage(href=href, data=resolved.read_bytes())

        img = self._embedded[resolved]
        esc_alt = html.escape(alt or resolved.stem, quote=True)
        return (
            f'<figure><img src="{html.escape(img.href, quote=True)}" '
            f'alt="{esc_alt}"/></figure>'
        )

    @property
    def images(self) -> list[EpubImage]:
        return list(self._embedded.values())


def _render_inline(text: str, resolver: VaultImageResolver) -> str:
    """Convert inline Markdown & Obsidian syntax into valid XHTML."""
    placeholders: list[str] = []

    def _stash(fragment: str) -> str:
        token = f"\x00PH{len(placeholders)}\x00"
        placeholders.append(fragment)
        return token

    def _wiki_img(m: re.Match[str]) -> str:
        inner = m.group(1)
        if "|" in inner:
            target, hint = inner.split("|", 1)
            alt = "" if re.fullmatch(r"\d+(?:x\d+)?", hint.strip()) else hint.strip()
        else:
            target, alt = inner, ""
        return _stash(resolver.embed_image(target.strip(), alt))

    def _md_img(m: re.Match[str]) -> str:
        alt, target = m.group(1).strip(), m.group(2).strip()
        if target.startswith(("http://", "https://")):
            esc_url = html.escape(target, quote=True)
            esc_alt = html.escape(alt or target, quote=True)
            return _stash(f'<a href="{esc_url}">[Image: {esc_alt}]</a>')
        return _stash(resolver.embed_image(target, alt))

    def _inline_code(m: re.Match[str]) -> str:
        return _stash(f"<code>{html.escape(m.group(1))}</code>")

    def _md_link(m: re.Match[str]) -> str:
        label = html.escape(m.group(1))
        url = html.escape(m.group(2).strip(), quote=True)
        return _stash(f'<a href="{url}">{label}</a>')

    def _wiki_link(m: re.Match[str]) -> str:
        target = m.group(1).strip()
        alias = (m.group(2) or target).strip()
        return _stash(html.escape(alias))

    work = _WIKI_IMG_RE.sub(_wiki_img, text)
    work = _MD_IMG_RE.sub(_md_img, work)
    work = _INLINE_CODE_RE.sub(_inline_code, work)
    work = _MD_LINK_RE.sub(_md_link, work)
    work = _WIKI_LINK_RE.sub(_wiki_link, work)

    work = html.escape(work, quote=False)
    work = _BOLD_RE.sub(
        lambda m: f"<strong>{m.group(1) or m.group(2)}</strong>", work
    )
    work = _STRIKE_RE.sub(r"<del>\1</del>", work)
    work = _ITALIC_RE.sub(r"<em>\1</em>", work)

    for idx, frag in enumerate(placeholders):
        work = work.replace(f"\x00PH{idx}\x00", frag)
    return work


def _is_table_sep(line: str) -> bool:
    stripped = line.strip()
    if "|" not in stripped:
        return False
    cells = [c.strip() for c in stripped.strip("|").split("|")]
    return all(re.fullmatch(r":?-{3,}:?", c) for c in cells if c != "")


def _split_table_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def markdown_to_xhtml(md_body: str, resolver: VaultImageResolver) -> str:
    """Convert Markdown body text into clean XHTML fragments."""
    lines = md_body.splitlines()
    out: list[str] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        if not stripped:
            i += 1
            continue

        # Fenced code block
        fence_m = _FENCE_RE.match(line)
        if fence_m:
            fence = fence_m.group(1)
            code_lines: list[str] = []
            i += 1
            while i < n and not lines[i].strip().startswith(fence):
                code_lines.append(lines[i])
                i += 1
            i += 1
            out.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
            continue

        # Horizontal rule
        if _HR_RE.match(line):
            out.append("<hr/>")
            i += 1
            continue

        # Heading
        head_m = _HEADING_RE.match(line)
        if head_m:
            level = len(head_m.group(1))
            content = _render_inline(head_m.group(2), resolver)
            out.append(f"<h{level}>{content}</h{level}>")
            i += 1
            continue

        # Blockquote
        if stripped.startswith(">"):
            quote_lines: list[str] = []
            while i < n and lines[i].strip().startswith(">"):
                quote_lines.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            inner_html = markdown_to_xhtml("\n".join(quote_lines), resolver)
            out.append(f"<blockquote>\n{inner_html}\n</blockquote>")
            continue

        # Table
        if "|" in line and i + 1 < n and _is_table_sep(lines[i + 1]):
            headers = _split_table_row(line)
            i += 2
            rows: list[list[str]] = []
            while i < n and "|" in lines[i] and lines[i].strip():
                rows.append(_split_table_row(lines[i]))
                i += 1
            th_html = "".join(f"<th>{_render_inline(h, resolver)}</th>" for h in headers)
            body_rows = []
            for row in rows:
                tds = "".join(f"<td>{_render_inline(c, resolver)}</td>" for c in row)
                body_rows.append(f"<tr>{tds}</tr>")
            out.append(
                "<table>\n<thead><tr>"
                + th_html
                + "</tr></thead>\n<tbody>\n"
                + "\n".join(body_rows)
                + "\n</tbody>\n</table>"
            )
            continue

        # Unordered list
        if _UL_RE.match(line):
            items: list[str] = []
            while i < n:
                m = _UL_RE.match(lines[i])
                if not m:
                    break
                items.append(f"  <li>{_render_inline(m.group(1), resolver)}</li>")
                i += 1
            out.append("<ul>\n" + "\n".join(items) + "\n</ul>")
            continue

        # Ordered list
        if _OL_RE.match(line):
            items = []
            while i < n:
                m = _OL_RE.match(lines[i])
                if not m:
                    break
                items.append(f"  <li>{_render_inline(m.group(1), resolver)}</li>")
                i += 1
            out.append("<ol>\n" + "\n".join(items) + "\n</ol>")
            continue

        # Paragraph
        para_lines: list[str] = []
        while i < n:
            cur = lines[i]
            if (
                not cur.strip()
                or _FENCE_RE.match(cur)
                or _HEADING_RE.match(cur)
                or _HR_RE.match(cur)
                or cur.strip().startswith(">")
                or _UL_RE.match(cur)
                or _OL_RE.match(cur)
            ):
                break
            para_lines.append(cur.strip())
            i += 1

        joined = " ".join(para_lines)
        rendered = _render_inline(joined, resolver)
        # Standalone figure doesn't need to be wrapped in <p>
        if rendered.startswith("<figure>") and rendered.endswith("</figure>"):
            out.append(rendered)
        else:
            out.append(f"<p>{rendered}</p>")

    return "\n".join(out)


def convert_md_to_epub(
    md_path: Path,
    output_dir: Path,
    vault_dir: Optional[Path] = None,
    title_override: str = "",
    author_override: str = "",
    store: Optional[StateStore] = None,
) -> Path:
    """Convert a single Markdown file to an EPUB3 file and optionally register it."""
    raw = md_path.read_text(encoding="utf-8-sig")
    meta, body = parse_frontmatter(raw)

    title = title_override.strip() or meta.get("title", "").strip()
    if not title:
        for line in body.splitlines():
            m = re.match(r"^#\s+(.+)$", line.strip())
            if m:
                title = m.group(1).strip()
                break
    if not title:
        title = md_path.stem

    author = author_override.strip() or meta.get("author", "").strip() or "Obsidian"
    source_url = meta.get("source") or meta.get("url") or ""

    resolver = VaultImageResolver(md_path.parent, vault_dir)
    body_xhtml = markdown_to_xhtml(body, resolver)

    meta_parts = [f"<span>Author: {html.escape(author)}</span>"]
    if meta.get("date"):
        meta_parts.append(f"<span>Date: {html.escape(meta['date'])}</span>")
    if source_url:
        esc_u = html.escape(source_url, quote=True)
        meta_parts.append(f'<span>Source: <a href="{esc_u}">{html.escape(source_url)}</a></span>')
    meta_block = '<div class="article-meta">' + " &#183; ".join(meta_parts) + "</div>"

    # Prepend H1 if body does not already start with an H1
    has_leading_h1 = body_xhtml.lstrip().startswith("<h1>")
    header_html = "" if has_leading_h1 else f"<h1>{html.escape(title)}</h1>\n"
    full_body = f"{header_html}{meta_block}\n{body_xhtml}"

    book = EpubBook(
        title=title,
        author=author,
        source_url=source_url,
        chapters=[EpubChapter(title=title, body_xhtml=full_body, filename="chapter1.xhtml")],
        images=resolver.images,
    )

    safe_name = sanitize_filename(md_path.stem, fallback_stem="article")
    out_path = output_dir / safe_name
    write_epub_file(book, out_path)

    if store is not None and output_dir.resolve() == store.inbox_dir.resolve():
        store.register_inbox_file(safe_name, title=title, source_url=source_url)

    return out_path


def main(argv: Optional[list[str]] = None) -> int:
    default_vault, default_data = _load_defaults()

    parser = argparse.ArgumentParser(
        description="Convert Obsidian Markdown file(s) to EPUB3 in sync_server/data/inbox/"
    )
    parser.add_argument("input", type=Path, help="Path to a .md file or directory")
    parser.add_argument("--vault", type=Path, default=default_vault)
    parser.add_argument("--data-dir", type=Path, default=default_data)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--title", type=str, default="")
    parser.add_argument("--author", type=str, default="")
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--no-register", action="store_true")
    args = parser.parse_args(argv)

    inp: Path = args.input
    if not inp.exists():
        print(f"Error: input path does not exist: {inp}", file=sys.stderr)
        return 1

    store: Optional[StateStore] = None if args.no_register else StateStore(args.data_dir)
    output_dir = args.output_dir or (args.data_dir / "inbox")
    output_dir.mkdir(parents=True, exist_ok=True)

    if inp.is_file():
        md_files = [inp]
    else:
        pattern = "**/*.md" if args.recursive else "*.md"
        md_files = sorted(inp.glob(pattern))

    if not md_files:
        print(f"No Markdown files found in {inp}", file=sys.stderr)
        return 1

    for md_file in md_files:
        out = convert_md_to_epub(
            md_file,
            output_dir=output_dir,
            vault_dir=args.vault,
            title_override=args.title if len(md_files) == 1 else "",
            author_override=args.author,
            store=store,
        )
        print(f"Generated: {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
