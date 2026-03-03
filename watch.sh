#!/bin/bash
# Watch live agent sessions for a perfid game.
# Usage: ./watch.sh <game-id>
set -euo pipefail

game_id="${1:-}"
if [[ -z "$game_id" ]]; then
  echo "Usage: ./watch.sh <game-id>" >&2
  exit 1
fi

ctr="perfid-${game_id}"
script_dir="$(cd "$(dirname "$0")" && pwd)"

docker exec "$ctr" bash -c 'tail -f /home/player/.claude/projects/-game/*.jsonl' \
  | python3 "$script_dir/watch_session.py"
