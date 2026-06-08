#!/bin/bash
# One-time setup for send_x_dm.py.
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
echo "  .venv/bin/python send_x_dm.py --login          # log in once"
echo '  .venv/bin/python send_x_dm.py "message" @handle  # send a DM'
