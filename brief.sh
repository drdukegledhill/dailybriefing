#!/usr/bin/env bash
# Run the briefing tool:  ./brief.sh run | sources | printer   (see README)
cd "$(dirname "$0")" || exit 1
# On a Mac, WeasyPrint finds Homebrew's Pango here; harmless elsewhere.
[ -d /opt/homebrew/lib ] && export DYLD_FALLBACK_LIBRARY_PATH="/opt/homebrew/lib${DYLD_FALLBACK_LIBRARY_PATH:+:$DYLD_FALLBACK_LIBRARY_PATH}"
exec .venv/bin/python -m digest "$@"
