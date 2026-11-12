# Rohlík.cz out-of-stock monitoring – Polaris

POS Media Czech Republic · automated daily availability monitoring on Rohlik.cz.

**Dashboard:** https://isigno247.github.io/rohlik-oos-report/

## What it does

Every day at 04:30 UTC (05:30 CET / 06:30 CEST) GitHub Actions:

1. **Measures** all tracked SKUs in 5 Rohlík warehouses (Chrášťany 8801, Praha Liboc 8791,
   Horní Počernice 8799, Brno 8793, Ostrava 8802): stock state, exact units (Rohlík caps at 50),
   regular price and every active promotion with its type (sale, multibuy, long-term low price,
   clearance; Xtra member prices are recorded but not counted as promotions).
2. **Classifies** each SKU × warehouse as *in stock*, *out of stock* or *not listed* against the
   stored footprint in `config/polaris.yml` – never via keyword search.
3. **Self-checks** the result. Strict mode: every warehouse must be verified (stock rows must come
   from the expected warehouse ID). If anything fails, nothing is published and GitHub e-mails the
   repo owner.
4. **Publishes** the client e-mail report (`reports/polaris/latest.html`, English, POS Media
   letterhead) and refreshes the dashboard data (`docs/data/polaris.json`, Czech UI).

## How measurement works

Rohlík's public web endpoints, anonymous session per warehouse – no login, no MCP, no account:

`GET /api/v3/address/autocomplete` → `POST /api/v4/address/autocomplete/confirm` →
`POST /services/frontend-service/delivery-address/check` →
`POST /services/frontend-service/delivery-address/set` (pins the warehouse) →
`GET /api/v1/products/stock` + `GET /api/v1/products/prices`.

Headers: `X-Origin: WEB` and a random `X-Custom-SessionId`. These endpoints are undocumented;
if Rohlík changes them, the self-checks fail loudly instead of reporting wrong data.

## Repository layout

| Path | Purpose |
|---|---|
| `config/polaris.yml` | SKUs (row order, short names), footprint, warehouses, thresholds |
| `campaigns.yml` | Retail media campaigns – the dashboard flags OOS during a running campaign |
| `oos/` | measure → classify → report (`report2.py`) → checks → `run.py` |
| `tools/` | Outlook history parser and dashboard data builder |
| `history/` | Reports from Outlook (25.09.–11.11.2026) that seed the dashboard history |
| `reference/` | The approved e-mail layout the report replicates |
| `docs/` | Dashboard (GitHub Pages) |
| `tests/` | Mock of the Rohlík endpoints used by the CI self-test |

## Run manually

Actions → *Rohlik OOS daily report* → *Run workflow*.

## Add a campaign

Edit `campaigns.yml` (name, SKUs, start, end). The next run shows it on the dashboard.
