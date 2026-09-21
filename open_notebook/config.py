import os

# ROOT DATA FOLDER
DATA_FOLDER = "./data"

# LANGGRAPH CHECKPOINT FILE
sqlite_folder = f"{DATA_FOLDER}/sqlite-db"
os.makedirs(sqlite_folder, exist_ok=True)
LANGGRAPH_CHECKPOINT_FILE = f"{sqlite_folder}/checkpoints.sqlite"

# NOTE (PN02D-B3H): the durable one-shot live-authorization consumption ledger is SECURITY STATE
# and intentionally lives OUTSIDE this app ./data folder (resolved per-user under
# ~/.open-notebook/security/ by open_notebook/integrations/graphrag/eval/authledgerpn02d.py), so
# the documented `tar data/ surreal_data/` backup/restore cannot roll consumed grants backward.
# It has NO config/env override on the production runtime path.

# UPLOADS FOLDER
UPLOADS_FOLDER = f"{DATA_FOLDER}/uploads"
os.makedirs(UPLOADS_FOLDER, exist_ok=True)

# PODCASTS FOLDER
# Matches the root that build_episode_output_dir() (commands/podcast_commands.py)
# creates episode directories under when called with DATA_FOLDER in production.
PODCASTS_FOLDER = f"{DATA_FOLDER}/podcasts"
os.makedirs(PODCASTS_FOLDER, exist_ok=True)

# TIKTOKEN CACHE FOLDER
# Reads TIKTOKEN_CACHE_DIR from the environment so Docker can redirect the cache
# to a path outside /data/ (which is typically volume-mounted and would hide the
# pre-baked encoding baked into the image at build time).
TIKTOKEN_CACHE_DIR = os.environ.get("TIKTOKEN_CACHE_DIR", "").strip() or f"{DATA_FOLDER}/tiktoken-cache"
os.makedirs(TIKTOKEN_CACHE_DIR, exist_ok=True)
