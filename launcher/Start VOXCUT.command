#!/usr/bin/env bash
# macOS double-clickable launcher. Delegates to start.sh next to it.
cd "$(dirname "$0")"
exec ./start.sh
