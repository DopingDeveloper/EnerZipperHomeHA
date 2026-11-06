#!/bin/sh
set -eu
# Edit this local copy for your installation. Do not commit the credentials file.
cd "$HOME/Workspace/EnerZipperHomeHA"
set -a
. "$HOME/.config/enerzipper/mqtt.env"
set +a
exec "$PWD/.venv/bin/python" enerzipper_bridge.py --config "$PWD/config.json"
