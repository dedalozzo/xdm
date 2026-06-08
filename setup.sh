#!/bin/bash
# One-time setup for xdm.py.
# Creates a local virtualenv and installs Playwright. Run:  bash setup.sh
set -e
cd "$(dirname "$0")"

echo "Creating virtualenv in .venv ..."
python3 -m venv .venv

echo "Installing Playwright ..."
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install playwright

echo
echo "Done. Next steps:"
echo "  .venv/bin/python xdm.py --login          # log in once"
echo '  .venv/bin/python xdm.py "message" @handle  # send a DM'
