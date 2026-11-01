# Render Cron setup — production parity

This repository includes `scripts/run_signal_scan.py` as the scheduled-job entry point.
It must be configured manually after the production PR is approved and merged.

## Render service

Create a Cron Job in the same workspace and Frankfurt region as the production API:

- Repository: `gazztattoo/vantage-signal-scanner`
- Branch: `main` (only after PR #5 is approved and merged)
- Runtime: Python
- Build command: `pip install -r requirements.txt`
- Start command: `python scripts/run_signal_scan.py`
- Environment:
  - `SCANNER_API_URL=https://vantage-signal-api-carlos.onrender.com`
  - `SIGNAL_CRON_SECRET`: generate a long random secret and set the exact same value on the API service.
- Schedule: choose only after checking the Twelve Data plan's request quota and agreeing the scan cadence.

## Important operational notes

- Each full scan evaluates up to 9 symbol/timeframe combinations and spaces requests to avoid bursts.
- A scan can take more than one minute. Do not schedule overlapping jobs.
- The API endpoint requires the `X-Scanner-Cron-Secret` header.
- The job records signal history/state and may send real push notifications when configured.
- Never run this script manually against production during validation; use TEST with an explicitly authorized test window.
- The scanner does not place broker orders. It only analyzes market data, records simulated signals, resolves simulated TP/SL outcomes, and sends alerts.
- Monitor Twelve Data quota, Render job duration, Supabase errors and push delivery before increasing frequency.


## Push delivery reliability

- New signal events enqueue one outbox row per currently registered device.
- The unique key `(event_fingerprint, endpoint)` prevents duplicate queue rows for the same event/device.
- The scanner drains due pending rows after each scan cycle.
- Transient failures retry with exponential backoff (5 minutes, doubling to a 6-hour cap), up to 8 attempts.
- HTTP 404/410 marks a delivery dead and removes the expired subscription.
- Successful deliveries are marked sent and are not selected again.
- Delivery is at-least-once: if a push provider accepts a message but the subsequent database acknowledgement fails, a later retry can produce a duplicate. This is an unavoidable uncertain-delivery window without provider-side idempotency.
- Apply migration `202611010003_push_delivery_outbox.sql` before enabling the scanner after approval.
