import os
import sys
import requests

api_base = os.environ.get("SCANNER_API_BASE", "").rstrip("/")
secret = os.environ.get("SIGNAL_CRON_SECRET", "")

if not api_base or not secret:
    print("Faltan SCANNER_API_BASE o SIGNAL_CRON_SECRET", file=sys.stderr)
    raise SystemExit(2)

response = requests.post(
    api_base + "/api/push/scan",
    headers={"X-Scanner-Cron-Secret": secret},
    timeout=300,
)
print("HTTP", response.status_code)
try:
    print(response.text[:12000])
except Exception:
    pass
response.raise_for_status()
