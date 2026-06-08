# send_x_dm — send X (Twitter) DMs from the command line

Drives your real Chrome with Playwright using a dedicated, persistent login
profile. Log in to X **once**, then send direct messages from the terminal — no
manual browser clicking.

## Setup (one time)

```bash
cd "x-dm"
bash setup.sh            # creates .venv and installs Playwright
.venv/bin/python send_x_dm.py --login   # opens Chrome; log in to X, then press Enter
```

`setup.sh` just does:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip playwright
```

Chrome is driven via `channel="chrome"`, so you do **not** need to download
Chromium. (Fallback if Chrome ever misbehaves:
`.venv/bin/python -m playwright install chromium` and remove `channel="chrome"`.)

## Usage

```bash
# message first, then the profile URL or @handle
.venv/bin/python send_x_dm.py "こんにちは！ 👋" https://x.com/jack
.venv/bin/python send_x_dm.py -m "Hello there" -t @jack

# long / multi-line message from a file or stdin
.venv/bin/python send_x_dm.py -f message.txt -t jack
echo "hi" | .venv/bin/python send_x_dm.py --stdin -t jack -y

# compose but don't actually send (great for a first test)
.venv/bin/python send_x_dm.py "test" @jack --dry-run
```

### Options

| Flag | Meaning |
|------|---------|
| `MESSAGE PROFILE` | positional: message text, then URL/@handle |
| `-m, --message` / `-t, --to` | same as positionals, named |
| `-f, --message-file` / `--stdin` | read message from a file / stdin |
| `--login` | open Chrome to log in once, then exit |
| `--dry-run` | compose but don't click Send |
| `-y, --yes` | skip the confirmation prompt (needed when scripting / piping) |
| `--headless` | no visible window (X may flag this more — headed is the default) |
| `--timeout` | per-step timeout in seconds (default 45) |
| `--profile-dir` | where the saved login lives (default `~/.x-dm-chrome-profile`) |

### Optional: a short alias (fish)

```fish
alias xdm '~/path/to/x-dm/.venv/bin/python ~/path/to/x-dm/send_x_dm.py'
funcsave xdm
# then: xdm "メッセージ" @handle
```

## Good to know / limits

- **You can only DM someone if X lets you** — they accept DMs from anyone, or
  you follow each other. Otherwise there's no "Message" button and the script
  says so.
- It automates **your own** logged-in account for personal, one-at-a-time use.
  Respect X's automation rules and don't bulk-send.
- X occasionally renames its internal `data-testid` hooks. If sending suddenly
  fails, the selectors near the top of `send_x_dm.py`
  (`sendDMFromProfile`, `dmComposerTextInput`, `dmComposerSendButton`) are what
  to update.
- The session can expire (X logout, password change) — just rerun `--login`.
