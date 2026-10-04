# Domestica Invoice

Domestica Invoice is a lean, local-first app for monthly cleaning invoices and bank reconciliation.

It uses:

- Python 3.13
- UV for the virtual environment and command execution
- SQLite for the source-of-truth database
- Standard-library HTTP server, CSV parsing, and tests

All monetary values are stored as integer cents. GST defaults to 10%.

## Run

```bash
uv run python -m clean_ledger.web --seed --reload-demo
uv run python -m clean_ledger.web
npm run dev
```

Open the React app at http://127.0.0.1:5173.

The Python backend API runs at http://127.0.0.1:8000.

The development email sender writes `.eml` files to `email_outbox/`. Invoice PDFs are stored in
`files/invoices/`.

## Desktop App

Domestica Invoice can also run as a local-first Electron desktop app for macOS and Windows.
The desktop app starts the Python backend on `127.0.0.1` with a per-launch token and stores
SQLite data, invoices, and generated email files in the operating system app-data folder.

```bash
npm run desktop:dev
npm run desktop:pack
npm run desktop:build:mac
npm run desktop:build:win
```

The desktop backend sidecar is built with UV and PyInstaller during the package scripts.

## Test

```bash
uv run python -m unittest discover -s tests
```

## MVP Scope Implemented

- Owner and property records
- Manual cleaning job entry
- `.xlsx` job import for sheets shaped like `May invoices.xlsx`
- CSV job import fallback
- Month-end grouped draft invoice generation
- Invoice detail, PDF preview, send/finalize flow
- Excel invoice export shaped like `680 SB invoice.xlsx`
- Immutable sent invoice PDF snapshots
- Bank CSV import with deterministic transaction hashes
- Exact invoice-reference auto-reconciliation
- Suggested and exception match states
- Manual payment allocation
- Overdue invoice updates
- Audit log
- CSV exports

## Excel Imports

The React UI imports `.xlsx` workbooks directly. The first sheet should have headers:

```text
Date | property | Hr | Rate incl.GST | Income
```

Rows with `property` values like `extras`, `extra`, `Total`, or blank are ignored.

## CSV Imports

Jobs CSV headers:

```text
service_date,owner_name,owner_email,property_nickname,property_address,description,hours,rate_ex_gst,notes
```

Bank CSV headers:

```text
date,description,credit,debit,balance,account
```

Commonwealth Bank exports without a header row are also supported when shaped as:

```text
date,amount,description,balance
```
