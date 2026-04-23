#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Open a persistent browser profile for manual login and save session state.",
    )
    parser.add_argument(
        "--user-data-dir",
        default="/tmp/awo-chrome-profile",
        help="Persistent Chromium user data directory (reused by headless runs).",
    )
    parser.add_argument(
        "--start-url",
        default="https://www.yelp.com/",
        help="URL to open for manual login/verification.",
    )
    parser.add_argument(
        "--state-output",
        default=".local_sessions/yelp_storage_state.json",
        help="Where to save Playwright storage_state JSON.",
    )
    parser.add_argument(
        "--profile-directory",
        default="Default",
        help="Chromium profile directory name.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    user_data_dir = Path(args.user_data_dir).expanduser().resolve()
    state_output = Path(args.state_output).expanduser().resolve()
    state_output.parent.mkdir(parents=True, exist_ok=True)

    print(f"Using profile dir: {user_data_dir}")
    print(f"Will save storage state to: {state_output}")
    print("Browser opening now. Log in manually, solve any verification, then return here.")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            headless=False,
            args=[f"--profile-directory={args.profile_directory}"],
        )
        page = context.new_page()
        page.goto(args.start_url, wait_until="domcontentloaded")

        input("Press Enter after login is complete and the account appears authenticated...")
        context.storage_state(path=str(state_output))
        context.close()

    print("Saved session state successfully.")


if __name__ == "__main__":
    main()
