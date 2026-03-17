#!/usr/bin/env python3
"""Sanitize asset filenames inside a given assets directory and update references in a Markdown file.

Usage:
    python sanitize_assets.py --md-file "file.md" --assets-dir "dir"

This script will:
- Rename files in assets dir replacing unsafe chars with '_' (keeps extension)
- Update occurrences of filenames in the Markdown file to the new names
- Print a summary of changes
"""
import argparse
import re
from pathlib import Path

SAFE_RE = re.compile(r'[^A-Za-z0-9._-]')


def sanitize_name(name: str) -> str:
    # keep extension
    p = Path(name)
    stem = p.stem
    suf = p.suffix
    new_stem = SAFE_RE.sub('_', stem)
    # collapse multiple underscores
    new_stem = re.sub(r'_+', '_', new_stem).strip('_')
    if not new_stem:
        new_stem = 'asset'
    return new_stem + suf


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--md-file', required=True, help='Path to markdown file to update')
    parser.add_argument('--assets-dir', required=True, help='Assets directory relative to md file or absolute')
    args = parser.parse_args()

    md_path = Path(args.md_file)
    assets_dir = Path(args.assets_dir)
    if not md_path.exists():
        print('Markdown file not found:', md_path)
        return
    if not assets_dir.exists() or not assets_dir.is_dir():
        print('Assets dir not found or not a dir:', assets_dir)
        return

    text = md_path.read_text(encoding='utf-8')

    changes = []
    for f in sorted(assets_dir.iterdir()):
        if f.is_file():
            new_name = sanitize_name(f.name)
            if new_name != f.name:
                new_path = f.with_name(new_name)
                # avoid collisions
                i = 1
                while new_path.exists():
                    new_path = f.with_name(f"{Path(new_name).stem}_{i}{Path(new_name).suffix}")
                    i += 1
                f.rename(new_path)
                print(f'Renamed: {f.name} -> {new_path.name}')
                changes.append((f.name, new_path.name))

    # Update markdown references
    if changes:
        for old, new in changes:
            # replace occurrences of /old with /new (handles URLs and local paths)
            text = text.replace(f"/{old}", f"/{new}")
            text = text.replace(f"({old}", f"({new}")
            # bare filenames as well
            text = text.replace(old, new)
        md_path.write_text(text, encoding='utf-8')
        print('Updated markdown file:', md_path)

    # URL-encode asset paths (to handle spaces and brackets in folder name)
    try:
        from urllib.parse import quote
        text = md_path.read_text(encoding='utf-8')
        folder = assets_dir.name
        for f in sorted(assets_dir.iterdir()):
            if f.is_file():
                raw = f"{folder}/{f.name}"
                enc = quote(raw, safe='/')
                if raw in text:
                    text = text.replace(raw, enc)
                    print(f'Encoded path in markdown: {raw} -> {enc}')
        md_path.write_text(text, encoding='utf-8')
    except Exception as e:
        print('Aviso: não foi possível URL-encode nos paths:', e)

    print('Done')


if __name__ == '__main__':
    main()
