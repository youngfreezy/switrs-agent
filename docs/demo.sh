#!/usr/bin/env bash
# Scripted terminal demo of the switrs CLI. Every command really runs against data/switrs.duckdb
# and a live Claude backend; only the typing is simulated so the commands are readable.
# Record it (from the repo root):
#   asciinema rec --window-size 132x42 -c "bash docs/demo.sh" docs/demo.cast
#   agg --idle-time-limit 3 --font-size 16 docs/demo.cast docs/demo.gif
#   ffmpeg -i docs/demo.gif -movflags faststart -pix_fmt yuv420p -vf "scale=trunc(iw/2)*2:trunc(ih/2)*2" docs/demo.mp4
set -u
cd "$(dirname "$0")/.."

say()  { printf '\033[2m# %s\033[0m\n' "$1"; sleep 1; }
show() {                       # type a command out, then run it
  printf '\033[1;32m$\033[0m '
  local s="$1" i
  for ((i = 0; i < ${#s}; i++)); do printf '%s' "${s:i:1}"; sleep 0.02; done
  printf '\n'
  eval "$1"
  printf '\n'
  sleep "${2:-2}"
}

clear
say "switrs: ask questions about California collisions (SWITRS, 2001 to mid-2021) from a terminal"
show "uv run switrs --help" 3

say "The agent's tools also work without a model. How is a category spelled?"
show "uv run switrs values parties vehicle_make --contains porsch --limit 3" 3

say "A guarded query, run directly. Writes are rejected."
show 'uv run switrs sql "SELECT county_location, COUNT(*) AS n FROM collisions GROUP BY 1 ORDER BY 2 DESC LIMIT 3"' 3
show 'uv run switrs sql "DROP TABLE collisions"' 4

clear
say "Ask the agent. Vehicle color is not in the data, so the answer has to say so."
show 'uv run switrs ask "Is there data showing police are biased against red sports cars?"' 8

clear
say "A conversation with a follow-up question (questions piped in here, typed in normal use)"
show "printf 'Which county has the most collisions?\nAnd which of the top five had the highest share of fatal collisions?\n\n' | uv run switrs chat" 6
