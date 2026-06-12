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
import os
import re
import sys
import textwrap
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_PROFILE_DIR = Path.home() / ".x-dm-chrome-profile"

# X is fairly stable on these data-testid hooks.
SEL_DM_BUTTON = '[data-testid="sendDMFromProfile"]'
# X's redesigned "Chat" UI uses a real <textarea> plus a send button (an
# up-arrow that appears once the box has text). The old Draft.js contenteditable
# (dmComposerTextInput) and its send button (dmComposerSendButton) differ, so
# match both forms to keep working on either UI.
SEL_DM_INPUT = (
    '[data-testid="dm-composer-textarea"], '
    '[data-testid="dmComposerTextInput"]'
)
SEL_DM_SEND = (
    '[data-testid="dm-composer-send-button"], '
    '[data-testid="dmComposerSendButton"]'
)
# Encrypted-chat ("Chat") passcode/PIN lock screen: a row of numeric <input>
# boxes inside this container.
SEL_PIN_CONTAINER = '[data-testid="pin-code-input-container"]'
SEL_LOGGED_IN = (
    '[data-testid="SideNav_AccountSwitcher_Button"], '
    '[data-testid="AppTabBar_Home_Link"]'
)
# Follow / Unfollow buttons embed the profile's numeric id, e.g.
# data-testid="1234567890-follow". Match by suffix and scope to the main
# column so we never touch "Who to follow" suggestions in the sidebar.
SEL_FOLLOW = '[data-testid="primaryColumn"] [data-testid$="-follow"]'
SEL_UNFOLLOW = '[data-testid="primaryColumn"] [data-testid$="-unfollow"]'
# A profile we've blocked shows an "Unblock" button in place of Follow/Message
# (same numeric-id suffix pattern).
SEL_UNBLOCK = '[data-testid="primaryColumn"] [data-testid$="-unblock"]'
# A suspended / nonexistent account renders an empty-state header instead of a
# profile ("Account suspended", "This account doesn't exist", …) with no action
# buttons at all — so there's no Message button. Detect it and say why.
SEL_EMPTY_STATE = '[data-testid="empty_state_header_text"]'
# Toasts / alert banners / modal sheets — where X explains itself when a chat
# can't be opened (e.g. recipient accepts DMs from verified senders only) or a
# send fails. Read-only; never clicked.
SEL_NOTICE = (
    '[data-testid="toast"], [role="alert"], [role="alertdialog"], '
    '[data-testid="confirmationSheetDialog"], [role="dialog"]'
)


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


def page_notices(page):
    """Visible toast/banner/dialog texts — X's own words for what's wrong."""
    try:
        texts = page.eval_on_selector_all(
            SEL_NOTICE,
            "els => els.map(e => (e.innerText || '').trim().replace(/\\s+/g, ' '))"
            ".filter(t => t && t.length < 300)",
        )
    except Exception:
        return ""
    seen = []
    for t in texts:
        if t not in seen:
            seen.append(t)
    return " | ".join(seen)


def enter_chat_passcode(page, passcode, ms):
    """Type X's encrypted-chat passcode into the PIN lock screen (a row of
    numeric boxes) and wait for it to clear."""
    from playwright.sync_api import TimeoutError as PWTimeout

    boxes = page.query_selector_all(f"{SEL_PIN_CONTAINER} input")
    target = boxes[0] if boxes else page.query_selector(SEL_PIN_CONTAINER)
    if target is None:
        raise SystemExit(
            "Chat passcode screen detected but its input vanished — X may have "
            "changed the lock screen. Not sent."
        )
    target.click()
    page.keyboard.type(passcode, delay=80)   # OTP boxes auto-advance per digit
    page.keyboard.press("Enter")             # usually auto-submits; nudge anyway
    try:
        page.wait_for_selector(SEL_PIN_CONTAINER, state="detached", timeout=ms)
        print("✓ Unlocked encrypted chat.")
    except PWTimeout:
        raise SystemExit(
            "Entered the chat passcode but the lock screen didn't clear — "
            "is the passcode correct?"
        )


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


def cmd_send(profile_dir, url, handle, message, headless, timeout_s, dry_run,
             follow, passcode, settle):
    sync_playwright = import_playwright()
    from playwright.sync_api import Error as PWError
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

            # Fast-fail if we've blocked them: a blocked profile shows "Unblock"
            # instead of Follow/Message, so the Message button never appears and
            # we'd otherwise wait out the whole timeout. The header's action
            # button renders right away, so wait only until one shows, then check.
            try:
                page.wait_for_selector(
                    f"{SEL_DM_BUTTON}, {SEL_FOLLOW}, {SEL_UNFOLLOW}, {SEL_UNBLOCK}, "
                    f"{SEL_EMPTY_STATE}",
                    timeout=ms,
                )
            except PWTimeout:
                pass
            # Suspended or nonexistent account: no profile, hence no Message button.
            empty = page.query_selector(SEL_EMPTY_STATE)
            if empty:
                reason = " ".join((empty.inner_text() or "").split()) or "account unavailable"
                raise SystemExit(f"@{handle}: {reason} — nothing to message. Skipping.")
            if page.query_selector(SEL_UNBLOCK):
                raise SystemExit(f"@{handle} is blocked — skipping.")

            # Optionally follow the profile before messaging.
            if follow:
                if dry_run:
                    print(f"DRY RUN — would follow @{handle} first (not following).")
                else:
                    follow_profile(page, handle, ms)

            # Open the DM composer from the profile. The header already rendered
            # during the block check above, so a present Message button clicks at
            # once; cap the wait low so profiles that don't accept our DMs (closed
            # DMs, or verified-senders-only) fail in seconds, not the full timeout.
            try:
                page.click(SEL_DM_BUTTON, timeout=min(ms, 10000))
            except PWTimeout:
                notes = page_notices(page)
                raise SystemExit(
                    f"Can't DM @{handle}: no usable Message button on their "
                    "profile — they don't accept DMs from you (DMs closed, or "
                    "verified-senders-only, which would need Premium). Skipping."
                    + (f"\nX says: {notes}" if notes else "")
                )

            # After opening the chat, X may show the encrypted-chat passcode
            # lock screen before the composer. Wait for whichever shows first.
            try:
                page.wait_for_selector(
                    f"{SEL_DM_INPUT}, {SEL_PIN_CONTAINER}", timeout=ms
                )
            except PWTimeout:
                # No composer ever showed. If X put up a notice (the verified-
                # senders-only upsell, "you can't message this account", …),
                # report X's own words instead of a generic failure.
                notes = page_notices(page)
                if re.search(r"verif|premium|can.?t .{0,20}messag|not accept",
                              notes, re.I):
                    raise SystemExit(
                        f"Can't DM @{handle} — X says: {notes}\n"
                        "(Most likely they accept DMs from verified senders "
                        "only and this account isn't verified.) Skipping."
                    )
                raise SystemExit(
                    f"Couldn't find the message box for @{handle}."
                    + (f" X says: {notes}" if notes else
                       " X may be throttling, or have changed the composer again.")
                )

            if page.query_selector(SEL_PIN_CONTAINER):
                if not passcode:
                    raise SystemExit(
                        "X is asking for the encrypted-chat passcode. Re-run "
                        "with --passcode <code> or set XDM_CHAT_PASSCODE."
                    )
                enter_chat_passcode(page, passcode, ms)

            # Type the message, preserving newlines and Unicode/emoji.
            try:
                composer = page.wait_for_selector(SEL_DM_INPUT, timeout=ms)
            except PWTimeout:
                notes = page_notices(page)
                raise SystemExit(
                    f"Couldn't find the message box for @{handle} (after any "
                    "passcode)."
                    + (f" X says: {notes}" if notes else
                       " X may be throttling or have changed the composer.")
                )
            composer.click()
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

            # Confirm a send by a genuinely NEW bubble that STILL EXISTS after a
            # settle. Neither weaker signal is proof: the textarea clears
            # optimistically the instant you click send, and an optimistic bubble
            # can flash up then vanish if the send never commits (e.g. the browser
            # closing mid-send). We snapshot existing bubble ids so late-loading
            # history isn't mistaken for our message either.
            new_bubble = (
                "(ids)=>[...document.querySelectorAll("
                "'[data-testid^=\"message-\"]')]"
                ".some(e=>!ids.includes(e.getAttribute('data-testid')))"
            )
            # A new bubble whose text actually contains the message we typed —
            # guards against counting an incoming message or late-loading
            # history as our own send.
            bubble_with_text = (
                "(args)=>{const [ids,needle]=args;"
                "const norm=s=>(s||'').replace(/\\s+/g,' ').trim();"
                "return [...document.querySelectorAll("
                "'[data-testid^=\"message-\"]')]"
                ".some(e=>!ids.includes(e.getAttribute('data-testid'))"
                "&&norm(e.innerText).includes(norm(needle)));}"
            )
            # Short status snippets X attaches to a bubble whose send failed.
            # A failed bubble PERSISTS in the thread, so persistence alone is
            # not proof of delivery — these markers veto it.
            fail_markers = (
                "()=>{const root=document.querySelector('main')||document.body;"
                "return [...root.querySelectorAll('span,div')]"
                ".map(e=>(e.innerText||'').trim())"
                ".filter(t=>t&&t.length<80&&"
                "/\\b(failed to send|not delivered|couldn.t send|message failed)"
                "\\b/i.test(t));}"
            )
            before_ids = [
                el.get_attribute("data-testid")
                for el in page.query_selector_all('[data-testid^="message-"]')
            ]
            marks_before = page.evaluate(fail_markers)

            # The server's verdict on the send call — the one signal an
            # optimistic UI can't fake. Captured from the network while the
            # DOM does its thing.
            send_responses = []

            def _track_send(resp):
                try:
                    if resp.request.method == "POST" and re.search(
                        r"/dm/new|/dm/conversation|dm_new|SendMessage"
                        r"|MessageCreate|/xchat|/chat/",
                        resp.url, re.I,
                    ):
                        send_responses.append((resp.status, resp.url))
                except Exception:
                    pass

            page.on("response", _track_send)

            # Click the send button — the up-arrow that appears once the box has
            # text. Fall back to Enter if it's not present (e.g. the old UI).
            try:
                page.click(SEL_DM_SEND, timeout=min(ms, 10000))
            except PWTimeout:
                page.keyboard.press("Enter")

            # A new bubble must appear...
            try:
                page.wait_for_function(
                    new_bubble, arg=before_ids, timeout=min(ms, 15000)
                )
            except PWTimeout:
                notes = page_notices(page)
                raise SystemExit(
                    f"Send to @{handle} didn't go through — no message appeared "
                    "in the thread." + (f" X says: {notes}" if notes else "")
                    + " NOT marking as sent."
                )

            # ...and survive the send committing. Closing the browser too soon
            # can abort the in-flight send, so the message posts optimistically
            # then never lands — keep the window open `settle` seconds, then
            # confirm the bubble is still there before we tear down.
            page.wait_for_timeout(int(settle * 1000))
            if not page.evaluate(new_bubble, before_ids):
                raise SystemExit(
                    f"Send to @{handle} didn't stick — the message bubble "
                    "vanished, so it wasn't delivered. NOT marking as sent."
                )

            # Server said no -> the persisting bubble is a stuck optimistic one.
            rejected = [s for s, _u in send_responses if s >= 400]
            if rejected:
                raise SystemExit(
                    f"Send to @{handle} FAILED — X rejected the send "
                    f"(HTTP {rejected[-1]}). NOT marking as sent."
                )

            # Bubble flagged "Failed to send" / "Not delivered" -> not sent.
            new_marks = [
                m for m in page.evaluate(fail_markers) if m not in marks_before
            ]
            if new_marks:
                raise SystemExit(
                    f"Send to @{handle} FAILED — the thread shows "
                    f"{new_marks[0]!r}. NOT marking as sent."
                )

            # Positive evidence required for exit 0: the server accepted the
            # send call, or the new bubble verifiably contains our text.
            ok_http = any(200 <= s < 300 for s, _u in send_responses)
            text_ok = page.evaluate(bubble_with_text, [before_ids, message])
            if not ok_http and not text_ok:
                raise SystemExit(
                    f"Send to @{handle} UNCONFIRMED — a new bubble appeared but "
                    "doesn't contain your message, and no send API call was "
                    "observed. NOT marking as sent."
                )

            print(f"✓ Sent to @{handle}."
                  + (" (server confirmed)" if ok_http else ""))
        except PWError as e:
            # Any X surface we don't manage yet (upsell dialogs, overlays,
            # redesigns) lands here: one clean line + X's own notice, not a
            # traceback, and a nonzero exit.
            first = (str(e) or repr(e)).splitlines()[0]
            notes = page_notices(page)
            raise SystemExit(
                f"Couldn't send to @{handle}: {first}"
                + (f"\nX says: {notes}" if notes else "")
            )
        finally:
            try:
                ctx.close()
            except PWError:
                pass


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
    ap.add_argument("--passcode", default=os.environ.get("XDM_CHAT_PASSCODE"),
                    help="encrypted-chat passcode for X's DM lock screen "
                         "(or set XDM_CHAT_PASSCODE)")
    ap.add_argument("--headless", action="store_true",
                    help="run with no visible window (can be flagged more by X)")
    ap.add_argument("--dry-run", action="store_true",
                    help="compose the DM but don't click Send")
    ap.add_argument("-y", "--yes", action="store_true",
                    help="skip the confirmation prompt")
    ap.add_argument("--timeout", type=float, default=45.0,
                    help="per-step timeout in seconds (default 45)")
    ap.add_argument("--settle", type=float, default=5.0,
                    help="seconds to keep the browser open after a send so it "
                         "fully commits before closing (default 5; try 10 if a "
                         "message ever posts but doesn't land)")
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

    passcode = (args.passcode or "").strip() or None
    cmd_send(profile_dir, url, handle, message, args.headless, args.timeout,
             args.dry_run, args.follow, passcode, args.settle)


if __name__ == "__main__":
    main()
