#!/usr/bin/env bash
# Netlify build for the frontend. The backend runs elsewhere (see NETLIFY.md);
# REACT_APP_BACKEND_URL must be set in Netlify's environment variables.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -z "${REACT_APP_BACKEND_URL:-}" ]; then
  echo "REACT_APP_BACKEND_URL is not set. Add it in Netlify: Site configuration > Environment variables." >&2
  exit 1
fi
# @emergentbase/* are Emergent editor overlays served from a private host; drop them here only.
node -e "const fs=require('fs');const p=JSON.parse(fs.readFileSync('package.json'));for(const k of ['dependencies','devDependencies'])for(const n of Object.keys(p[k]||{}))if(n.startsWith('@emergentbase/'))delete p[k][n];fs.writeFileSync('package.json',JSON.stringify(p,null,2))"
npx --yes yarn@1.22.22 install --ignore-engines --network-timeout 600000
# CI=true (set by Netlify) would turn lint warnings into build failures.
CI=false GENERATE_SOURCEMAP=false DISABLE_ESLINT_PLUGIN=true npx craco build
