"""Configuration for Mandi Lens. All values overridable by environment variables."""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# --- data.gov.in ---
# Get a free key: https://data.gov.in -> My Account -> Generate API Key
DATA_GOV_API_KEY = os.environ.get("DATA_GOV_API_KEY", "")
BASE_URL = os.environ.get("DATA_GOV_BASE_URL", "https://api.data.gov.in/resource")

# Resource IDs for the "Current Daily Price of Various Commodities from
# Various Markets" dataset. Different mirrors of the dataset have different IDs,
# so we try each in order until one returns data.
RESOURCE_IDS = [
    os.environ.get("DATA_GOV_RESOURCE_ID", ""),
    "9ef84268-d588-465a-a308-a864a43d0070",
    "35985678-0d79-46b4-9ed6-6f13308a1d24",
]
RESOURCE_IDS = [r for r in RESOURCE_IDS if r]

# --- storage ---
DB_PATH = os.environ.get("MANDI_DB", os.path.join(BASE_DIR, "data", "mandi.db"))
FIXTURE_DIR = os.path.join(BASE_DIR, "data", "fixtures")

# --- behaviour ---
PAGE_SIZE = 100            # records per upstream request
REQUEST_TIMEOUT = 30
MAX_PAGES = 400            # hard safety stop
POLITE_DELAY = 0.35        # seconds between upstream calls
STALE_AFTER_HOURS = 30     # a dataset older than this is flagged stale in the UI
REQUIRE_LIVE = os.environ.get("MANDI_REQUIRE_LIVE", "0") == "1"
