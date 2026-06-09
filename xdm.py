#!/usr/bin/env python3
"""
xdm.py — Send a Direct Message on X (Twitter) from the command line.

It drives a real Chrome browser (via Playwright) using a dedicated, persistent
login profile, so you log in to X *once* and from then on send DMs straight from
the terminal without touching the browser yourself.

Quick start
-----------
    # 1) one-time: open Chrome and log in to X (saves the session)
    python xdm.py --login

    # 2) send a DM — message first, then the profile link/@handle
    python xdm.py "こんにちは！ 👋" https://x.com/jack
    python xdm.py -m "Hello there" -t @jack

    # follow the profile first, then send
    python xdm.py --follow "Thanks!" @jack

    # message from a file or stdin (handy for long / multi-line text)
    python xdm.py -f message.txt -t jack
    echo "hi" | python xdm.py --stdin -t jack -y

    # compose but DON'T actually send, to check it works
    python xdm.py "test" @jack --dry-run

Notes
-----
* You must be logged in (run --login once). The session is stored in the
  profile dir (default: ~/.x-dm-chrome-profile) and reused on every run.
* X only shows a "Message" button when the recipient accepts DMs from you
  (they have open DMs, or you follow each other). If there's no button, there
  is nothing to click and the script will tell you so.
* This automates *your own* logged-in account for personal use. Mind X's
  automation rules and don't blast messages — it's built to send one at a time.
"""

import argparse
import sys
import textwrap
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_PROFILE_DIR = Path.home() / ".x-dm-chrome-profile"

# X is fairly stable on these data-testid hooks.
SEL_DM_BUTTON = '[data-testid="sendDMFromProfile"]'
SEL_DM_INPUT = '[data-testid="dmComposerTextInput"]'
SEL_DM_SEND = '[data-testid="dmComposerSendButton"]'
SEL_LOGGED_IN = (
    '[data-testid="SideNav_AccountSwitcher_Button"], '
    '[data-testid="AppTabBar_Home_Link"]'
)
# Follow / Unfollow buttons embed the profile's numeric id, e.g.
# data-testid="1234567890-follow". Match by suffix and scope to the main
# column so we never touch "Who to follow" suggestions in the sidebar.
SEL_FOLLOW = '[data-testid="primaryColumn"] [data-testid$="-follow"]'
SEL_UNFOLLOW = '[data-testid="primaryColumn"] [data-testid$="-unfollow"]'


def eprint(*a, **k):
    print(*a, file=sys.stderr, **k)


def import_playwright():
    """Import Playwright lazily so --help / arg parsing work without it."""
    try:
        from playwright.sync_api import sync_playwright
        return sync_playwright
    except ModuleNotFoundError:
        eprint(
            "Playwright isn't installed for this Python interpreter.\n"
            "Set it up once:\n"
            "    python3 -m venv .venv\n"
            "    .venv/bin/python -m pip install --upgrade pip playwright\n"
            "then run with  .venv/bin/python xdm.py ..."
        )
        sys.exit(3)


def normalize_target(target: str):
    """Accept a full URL, @handle, or bare handle -> (profile_url, handle)."""
    target = target.strip()
    if target.lower().startswith(("http://", "https://")):
        path = urlparse(target).path
    elif "/" in target and "." in target.split("/", 1)[0]:
        # schemeless URL, e.g. twitter.com/jack or x.com/jack
        path = urlparse("https://" + target).path
    else:
        path = "/" + target.lstrip("@")
    parts = [p for p in path.split("/") if p]
    if not parts:
        raise ValueError(f"Couldn't find a handle in: {target!r}")
    handle = parts[0]
    return f"https://x.com/{handle}", handle


def resolve_message(args):
    if args.stdin:
        msg = sys.stdin.read()
    elif args.message_file:
        msg = Path(args.message_file).read_text(encoding="utf-8")
    elif args.message_opt is not None:
        msg = args.message_opt
    elif args.message_pos is not None:
        msg = args.message_pos
    else:
        return None
    return msg.rstrip("\n")


def launch(pw, profile_dir, headless):
    kw = dict(
        user_data_dir=str(profile_dir),
        channel="chrome",  # use the installed Google Chrome, not bundled Chromium
        headless=headless,
        args=["--disable-blink-features=AutomationControlled"],
    )
    if headless:
        kw["viewport"] = {"width": 1280, "height": 900}
    else:
        kw["no_viewport"] = True
    return pw.chromium.launch_persistent_context(**kw)


def is_logged_in(page, timeout=8000):
    try:
        page.wait_for_selector(SEL_LOGGED_IN, timeout=timeout)
        return True
    except Exception:
        return False


def follow_profile(page, handle, ms):
    """Follow the profile unless already following (then it's a no-op)."""
    from playwright.sync_api import TimeoutError as PWTimeout

    # Wait for either the Follow or the Unfollow (already-following) button.
    try:
        page.wait_for_selector(f"{SEL_FOLLOW}, {SEL_UNFOLLOW}", timeout=ms)
    except PWTimeout:
        raise SystemExit(f"Couldn't find a Follow button on @{handle}'s profile.")

    if page.query_selector(SEL_UNFOLLOW):
        print(f"Already following @{handle}.")
        return

    page.click(SEL_FOLLOW, timeout=ms)
    try:
        page.wait_for_selector(SEL_UNFOLLOW, timeout=ms)
        print(f"✓ Followed @{handle}.")
    except PWTimeout:
        print(f"Clicked Follow on @{handle} (couldn't confirm the new state).")


def cmd_login(profile_dir):
    sync_playwright = import_playwright()
    with sync_playwright() as pw:
        ctx = launch(pw, profile_dir, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto("https://x.com/login", wait_until="domcontentloaded")
        except Exception:
            pass
        print("A Chrome window opened. Log in to X (handle any 2FA there).")
        print("When your home timeline is visible, come back here and press Enter.")
        try:
            input()
        except EOFError:
            pass
        ok = is_logged_in(page, timeout=4000)
        ctx.close()
        if ok:
            print(f"✓ Logged in. Session saved in {profile_dir}")
        else:
            eprint(
                "Couldn't auto-confirm login. If you did log in it's probably fine — "
                "try sending a DM. Otherwise run --login again."
            )


def cmd_send(profile_dir, url, handle, message, headless, timeout_s, dry_run, follow):
    sync_playwright = import_playwright()
    from playwright.sync_api import TimeoutError as PWTimeout

    ms = int(timeout_s * 1000)
    with sync_playwright() as pw:
        ctx = launch(pw, profile_dir, headless=headless)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_default_timeout(ms)
        try:
            page.goto(url, wait_until="domcontentloaded")

            if not is_logged_in(page):
                raise SystemExit(
                    "Not logged in to X. Run:  xdm.py --login"
                )

            # Optionally follow the profile before messaging.
            if follow:
                if dry_run:
                    print(f"DRY RUN — would follow @{handle} first (not following).")
                else:
                    follow_profile(page, handle, ms)

            # Open the DM composer from the profile.
            try:
                page.click(SEL_DM_BUTTON, timeout=ms)
            except PWTimeout:
                raise SystemExit(
                    f"No “Message” button on @{handle}'s profile.\n"
                    "X only shows it when they accept DMs from you (open DMs, or "
                    "you follow each other) — so there's nothing to click."
                )

            # Type the message, preserving newlines and Unicode/emoji.
            page.wait_for_selector(SEL_DM_INPUT, timeout=ms).click()
            for i, line in enumerate(message.split("\n")):
                if i:
                    page.keyboard.press("Shift+Enter")  # newline without sending
                if line:
                    page.keyboard.insert_text(line)  # robust for UTF-8 / emoji

            if dry_run:
                print("DRY RUN — message composed but NOT sent.")
                if not headless:
                    try:
                        input("Check the Chrome window, then press Enter to close…")
                    except EOFError:
                        pass
                return

            page.click(SEL_DM_SEND, timeout=ms)

            # Best-effort confirmation: the input clears once the DM is sent.
            try:
                page.wait_for_function(
                    "(s)=>{const e=document.querySelector(s);"
                    "return !!e && e.innerText.trim()==='';}",
                    SEL_DM_INPUT,
                    timeout=ms,
                )
            except PWTimeout:
                pass

            print(f"✓ Sent to @{handle}.")
        finally:
            ctx.close()


def main():
    ap = argparse.ArgumentParser(
        prog="xdm.py",
        description="Send a Direct Message on X (Twitter) from the command line via Chrome.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """\
            examples:
              xdm.py --login
              xdm.py "こんにちは 👋" https://x.com/jack
              xdm.py -m "hi" -t @jack
              xdm.py --follow "hi" @jack
              xdm.py -f note.txt -t jack --dry-run
            """
        ),
    )
    ap.add_argument("message_pos", nargs="?", metavar="MESSAGE",
                    help="message text (UTF-8)")
    ap.add_argument("to_pos", nargs="?", metavar="PROFILE",
                    help="X profile URL or @handle")
    ap.add_argument("-m", "--message", dest="message_opt", help="message text (UTF-8)")
    ap.add_argument("-t", "--to", dest="to_opt", help="X profile URL or @handle")
    ap.add_argument("-f", "--message-file", help="read the message from this file")
    ap.add_argument("--stdin", action="store_true", help="read the message from stdin")
    ap.add_argument("--login", action="store_true",
                    help="open Chrome to log in once; saves the session and exits")
    ap.add_argument("--follow", action="store_true",
                    help="follow the profile before sending (no-op if already following)")
    ap.add_argument("--headless", action="store_true",
                    help="run with no visible window (can be flagged more by X)")
    ap.add_argument("--dry-run", action="store_true",
                    help="compose the DM but don't click Send")
    ap.add_argument("-y", "--yes", action="store_true",
                    help="skip the confirmation prompt")
    ap.add_argument("--timeout", type=float, default=45.0,
                    help="per-step timeout in seconds (default 45)")
    ap.add_argument("--profile-dir", default=str(DEFAULT_PROFILE_DIR),
                    help=f"Chrome profile dir for the saved login (default {DEFAULT_PROFILE_DIR})")
    args = ap.parse_args()

    profile_dir = Path(args.profile_dir).expanduser()
    profile_dir.mkdir(parents=True, exist_ok=True)

    if args.login:
        cmd_login(profile_dir)
        return

    message = resolve_message(args)
    target = args.to_opt or args.to_pos

    if message is None or not message.strip():
        ap.error("no (non-empty) message — pass it positionally, or with -m / -f / --stdin")
    if not target:
        ap.error("no profile — pass a URL or @handle positionally or with -t")

    url, handle = normalize_target(target)

    # Confirm before this outward, irreversible action (unless -y / --dry-run).
    if not args.yes and not args.dry_run:
        if not sys.stdin.isatty():
            eprint("Refusing to send without confirmation in a non-interactive shell. Add -y to send.")
            sys.exit(2)
        if args.follow:
            print(f"Action:  follow @{handle}, then send the DM below")
        print(f"To:      @{handle}  ({url})")
        print("Message:")
        print(textwrap.indent(message, "    "))
        try:
            ans = input("Send this DM? [y/N] ").strip().lower()
        except EOFError:
            ans = ""
        if ans not in ("y", "yes"):
            print("Aborted.")
            return

    cmd_send(profile_dir, url, handle, message, args.headless, args.timeout,
             args.dry_run, args.follow)


if __name__ == "__main__":
    main()
