"""Small CLI utility to install Playwright browsers.

Usage:
  python scripts/install_playwright.py --browsers chromium,firefox
  python scripts/install_playwright.py            # installs all

The script attempts to run the `playwright` CLI. If it's not available,
prints a helpful error and exits non-zero.
"""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys

VALID = {"chromium", "firefox", "webkit", "all"}


def parse_browsers(value: str) -> list[str]:
    # Accept comma/space separated values or single token
    if not value:
        return ["all"]
    parts = [p.strip().lower() for p in value.replace(";", ",").split(",") if p.strip()]
    if not parts:
        return ["all"]
    for p in parts:
        if p not in VALID:
            raise argparse.ArgumentTypeError(f"invalid browser: {p!r} - valid: {', '.join(sorted(VALID))}")
    # If any 'all' requested, treat as single all
    if "all" in parts:
        return ["all"]
    return parts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="install_playwright",
        description="Instala navegadores Playwright (chromium, firefox, webkit ou all)",
    )
    parser.add_argument(
        "--browsers",
        "-b",
        type=parse_browsers,
        default=["all"],
        help="Quais navegadores instalar. Ex: chromium,firefox ou all (padrão: all)",
    )

    args = parser.parse_args(argv)
    browsers: list[str] = args.browsers

    if browsers == ["all"]:
        cmd = ["playwright", "install"]
        nice = "all browsers"
    else:
        cmd = ["playwright", "install"] + browsers
        nice = ", ".join(browsers)

    print(f"Starting Playwright browser install ({nice})...")

    try:
        # Use subprocess.run to surface stdout/stderr
        completed = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        print(
            "Error: 'playwright' command not found.\n"
            "Install Playwright first (e.g. `pip install playwright`) and/or ensure the 'playwright' CLI is on PATH.",
            file=sys.stderr,
        )
        return 2
    except Exception as exc:
        print(f"Unexpected error running command: {exc!r}", file=sys.stderr)
        return 3

    # Print captured output for visibility
    if completed.stdout:
        print(completed.stdout)
    if completed.stderr:
        print(completed.stderr, file=sys.stderr)

    if completed.returncode == 0:
        print("Playwright browsers installed successfully.")
        return 0
    else:
        print(f"Playwright install failed with exit code {completed.returncode}.", file=sys.stderr)
        return completed.returncode or 1


if __name__ == "__main__":
    raise SystemExit(main())
