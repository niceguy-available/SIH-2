#!/bin/sh
set -e
mkdir -p "$DATA_ROOT"
# Seed the curated catalogue into a fresh data volume; keep an edited copy.
[ -f "$DATA_ROOT/footprints.json" ] || cp /app/catalog/footprints.json "$DATA_ROOT/footprints.json"
exec "$@"
