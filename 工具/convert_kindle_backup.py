#!/usr/bin/env python3
"""
Batch-convert DRM-free ebooks/articles from a Kindle backup folder into clean EPUB files
using Calibre's `ebook-convert`, separating out personal highlights (.txt) and ignoring
Kindle system/junk files (.sdr, .phl, .asc, screenshots, user guide, dictionaries).
"""
from __future__ import annotations

import argparse
import concurrent.futures
import re
import shutil
import subprocess
import sys
from pathlib import Path

CONVERT_EXTS = {".azw3", ".azw", ".mobi", ".prc", ".kfx"}
SKIP_FILENAMES = {
    "kindle_users_guide.azw3",
    "b00akjgtaq_b00akjgtaq.azw",  # Kindle built-in dictionary
}
NOTE_FILENAMES = {
    "my clippings.txt",
}
NOTE_KEYWORDS = ("标注笔记", "clippings")


def find_ebook_convert() -> str:
    found = shutil.which("ebook-convert")
    if found:
        return found
    candidates = [
        Path(r"C:\Program Files\Calibre2\ebook-convert.exe"),
        Path(r"C:\Program Files (x86)\Calibre2\ebook-convert.exe"),
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    raise FileNotFoundError("未找到 Calibre 的 ebook-convert，请先安装 Calibre。")


def clean_book_title(filename: str) -> str:
    stem = Path(filename).stem
    # Remove trailing _<32-char Personal Document ID> or _B0XXXXXXXX ASIN
    stem = re.sub(r"_[A-Z0-9]{32}$", "", stem)
    stem = re.sub(r"_B[0-9A-Z]{9}$", "", stem)
    # Remove leftover inner .azw / .azw3 / .mobi extensions in stem
    stem = re.sub(r"\.(azw3?|mobi|prc|kfx)$", "", stem, flags=re.IGNORECASE)
    # Remove Windows invalid filename characters
    stem = re.sub(r'[<>:"/\\|?*]+', "_", stem).strip(" .")
    return stem or "untitled"


def collect_sources(src_root: Path) -> tuple[list[Path], list[Path]]:
    """Collect convertible book files and valuable note files from documents/ folders."""
    books: list[Path] = []
    notes: list[Path] = []
    seen_names: set[str] = set()

    doc_dirs = [
        src_root / "documents",
        src_root / "重置后_刷机前" / "documents",
    ]
    for d in doc_dirs:
        if not d.is_dir():
            continue
        for item in sorted(d.iterdir()):
            if not item.is_file():
                continue
            low_name = item.name.lower()
            ext = item.suffix.lower()
            if low_name in SKIP_FILENAMES:
                continue
            if ext == ".txt":
                if low_name in NOTE_FILENAMES or any(k in low_name for k in NOTE_KEYWORDS):
                    if item.name not in seen_names:
                        notes.append(item)
                        seen_names.add(item.name)
                continue
            if ext in CONVERT_EXTS:
                # Deduplicate identical cleaned titles preferring larger file
                books.append(item)
    return books, notes


def convert_one(converter: str, src: Path, out_dir: Path) -> tuple[str, Path, str]:
    clean_name = clean_book_title(src.name)
    out_path = out_dir / f"{clean_name}.epub"
    # Handle duplicate clean names by appending short suffix
    if out_path.exists():
        out_path = out_dir / f"{clean_name}_{src.suffix.lstrip('.')}.epub"

    cmd = [converter, str(src), str(out_path)]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        if out_path.exists():
            out_path.unlink(missing_ok=True)
        return ("timeout", src, "转换超时 (>180s)")

    if proc.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0:
        return ("ok", src, out_path.name)

    if out_path.exists():
        out_path.unlink(missing_ok=True)

    err_text = (proc.stderr or proc.stdout or "").strip()
    low_err = err_text.lower()
    if "drm" in low_err or "protected" in low_err or "encrypted" in low_err:
        reason = "受亚马逊 DRM 加密保护，无法直接转换"
    elif src.suffix.lower() == ".kfx":
        reason = "亚马逊 KFX 专有格式（通常带 DRM），Calibre 原生不支持直接转换"
    else:
        last_line = err_text.splitlines()[-1] if err_text else f"exit code {proc.returncode}"
        reason = last_line[:160]
    return ("failed", src, reason)


def main() -> int:
    parser = argparse.ArgumentParser(description="批量将 Kindle 备份里的无加密电子书和文章转换为 EPUB")
    parser.add_argument("--src", required=True, help="Kindle 备份目录路径")
    parser.add_argument("--out", required=True, help="输出整理目录路径")
    parser.add_argument("--workers", type=int, default=4, help="并发转换数（默认 4）")
    args = parser.parse_args()

    src_root = Path(args.src).resolve()
    out_root = Path(args.out).resolve()
    epub_dir = out_root / "已转EPUB_待挑选"
    notes_dir = out_root / "个人划线笔记"

    converter = find_ebook_convert()
    epub_dir.mkdir(parents=True, exist_ok=True)
    notes_dir.mkdir(parents=True, exist_ok=True)

    books, notes = collect_sources(src_root)
    print(f"[扫描完成] 发现待转换书/文章 {len(books)} 个，个人笔记 {len(notes)} 个")

    # Copy notes
    for n in notes:
        shutil.copy2(n, notes_dir / n.name)

    ok_list: list[tuple[str, str]] = []
    fail_list: list[tuple[str, str]] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futs = [pool.submit(convert_one, converter, b, epub_dir) for b in books]
        for fut in concurrent.futures.as_completed(futs):
            status, src, info = fut.result()
            if status == "ok":
                ok_list.append((src.name, info))
                print(f"  [OK] {info}")
            else:
                fail_list.append((src.name, info))
                print(f"  [SKIP] {src.name} -> {info}")

    ok_list.sort(key=lambda x: x[1])
    fail_list.sort(key=lambda x: x[0])

    report_path = out_root / "转换清单与说明.md"
    lines = [
        "# Kindle 备份批量转换报告",
        "",
        f"- 源目录：`{src_root}`",
        f"- 成功转为 EPUB：**{len(ok_list)}** 个（位于 `已转EPUB_待挑选/`）",
        f"- 跳过 / 无法直接转换（含 DRM 加密或 KFX）：**{len(fail_list)}** 个",
        f"- 保留的个人划线笔记：**{len(notes)}** 个（位于 `个人划线笔记/`）",
        "",
        "## 使用建议",
        "1. 打开 `已转EPUB_待挑选/`，把你想看的长文章拖进 `sync_server/data/inbox/`（或其子文件夹），把想看的电子书拖进 `sync_server/data/books/`。",
        "2. 不想要的直接删除即可；确认全部挑完后，整个原备份目录 `Kindle备份_2026-11-21/` 连同里面的所有系统干扰文件都可以放心删除。",
        "",
        f"## 1. 成功转换清单（{len(ok_list)} 个）",
        "",
    ]
    for src_name, out_name in ok_list:
        lines.append(f"- `{out_name}` （原文件：`{src_name}`）")

    lines.extend([
        "",
        f"## 2. 跳过 / 无法转换清单（{len(fail_list)} 个）",
        "",
    ])
    for src_name, reason in fail_list:
        lines.append(f"- `{src_name}` — {reason}")

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n[全部完成] 成功 {len(ok_list)} 个，跳过 {len(fail_list)} 个，报告已保存至：{report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
