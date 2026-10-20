#!/bin/zsh
# Start the TrustLadder demo on http://127.0.0.1:8765 using the demo's own venv.
#
# The hosted models (vision reader, evidence agent, reason writer) need a Google AI Studio
# key. The launcher reads it from ~/.trustladder/gemini.key, a private file OUTSIDE the
# repository, so the presenter never types it and it can never be committed. With the file
# present the models are switched on; without it the demo runs on its written fallbacks
# and every screen says so.
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || { uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements-dev.txt; }
[ -f data/trustladder.db ] || .venv/bin/python -m trustladder.seed
KEY_FILE="$HOME/.trustladder/gemini.key"
if [ -z "$GEMINI_API_KEY" ] && [ -s "$KEY_FILE" ]; then
  export GEMINI_API_KEY="$(tr -d '[:space:]' < "$KEY_FILE")"
fi
# TRUSTLADDER_MODELS: "on" (default) reads pictures AND narrates held claims with the agent and
# the reason writer; "reader" spends the key on reading pictures only. On a FREE-tier key
# (20 requests a day per model) a held claim's narration takes several requests, so use
# "reader" unless the key's project is on pay-as-you-go:   TRUSTLADDER_MODELS=reader ./run_demo.sh
if [ -n "$GEMINI_API_KEY" ]; then
  export TRUSTLADDER_MODELS="${TRUSTLADDER_MODELS:-on}"
  echo "Hosted models: ${TRUSTLADDER_MODELS} (key from ${KEY_FILE}); free tier = 20 requests a day per model"
else
  echo "Hosted models: off (no key at ${KEY_FILE}); written fallbacks in use"
fi
echo "Open http://127.0.0.1:8765 and sign in (password TrustLadder@2026)."
exec .venv/bin/streamlit run app.py --server.port 8765 --server.address 127.0.0.1 --server.headless true
