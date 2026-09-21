#!/bin/zsh
# Start the TrustLadder demo on http://127.0.0.1:8765 using the demo's own venv.
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || { uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements-dev.txt; }
[ -f data/trustladder.db ] || .venv/bin/python -m trustladder.seed
exec .venv/bin/streamlit run app.py --server.port 8765 --server.address 127.0.0.1 --server.headless true
