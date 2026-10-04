from __future__ import annotations

import os
import shutil
import sqlite3
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else default


DATA_DIR = env_path("CLEAN_LEDGER_DATA_DIR", ROOT / "data")
FILES_DIR = env_path("CLEAN_LEDGER_FILES_DIR", ROOT / "files")
INVOICE_DIR = env_path("CLEAN_LEDGER_INVOICE_DIR", FILES_DIR / "invoices")
EMAIL_DIR = env_path("CLEAN_LEDGER_EMAIL_DIR", ROOT / "email_outbox")
DB_PATH = env_path("CLEAN_LEDGER_DB", DATA_DIR / "clean_ledger.sqlite3")


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS owners (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    billing_email TEXT,
    phone TEXT,
    abn TEXT,
    billing_address_line_1 TEXT,
    billing_address_line_2 TEXT,
    billing_city TEXT,
    billing_state TEXT,
    billing_postcode TEXT,
    billing_country TEXT NOT NULL DEFAULT 'Australia',
    default_payment_terms_days INTEGER NOT NULL DEFAULT 14,
    default_rate_ex_gst_cents INTEGER NOT NULL DEFAULT 0,
    gst_treatment TEXT NOT NULL DEFAULT 'gst_registered',
    notes TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS properties (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL REFERENCES owners(id),
    address_line_1 TEXT NOT NULL,
    address_line_2 TEXT,
    suburb TEXT,
    state TEXT,
    postcode TEXT,
    nickname TEXT,
    default_description TEXT,
    default_rate_ex_gst_cents INTEGER,
    invoice_number_prefix TEXT,
    next_invoice_number INTEGER,
    invoice_number_padding INTEGER NOT NULL DEFAULT 3,
    notes TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS job_imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    row_count INTEGER NOT NULL DEFAULT 0,
    imported_count INTEGER NOT NULL DEFAULT 0,
    duplicate_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS cleaning_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL REFERENCES owners(id),
    property_id INTEGER NOT NULL REFERENCES properties(id),
    service_date TEXT NOT NULL,
    description TEXT NOT NULL,
    hours TEXT NOT NULL,
    rate_ex_gst_cents INTEGER NOT NULL,
    gst_rate_bps INTEGER NOT NULL DEFAULT 1000,
    amount_ex_gst_cents INTEGER NOT NULL,
    gst_amount_cents INTEGER NOT NULL,
    amount_inc_gst_cents INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'unbilled',
    invoice_id INTEGER REFERENCES invoices(id),
    import_id INTEGER REFERENCES job_imports(id),
    internal_note TEXT,
    duplicate_key TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_cleaning_jobs_duplicate_key
ON cleaning_jobs(duplicate_key);

CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_number TEXT NOT NULL UNIQUE,
    owner_id INTEGER NOT NULL REFERENCES owners(id),
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    issue_date TEXT NOT NULL,
    due_date TEXT NOT NULL,
    subtotal_ex_gst_cents INTEGER NOT NULL DEFAULT 0,
    gst_total_cents INTEGER NOT NULL DEFAULT 0,
    total_inc_gst_cents INTEGER NOT NULL DEFAULT 0,
    amount_paid_cents INTEGER NOT NULL DEFAULT 0,
    amount_outstanding_cents INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'draft',
    pdf_file_id INTEGER REFERENCES files(id),
    sent_at TEXT,
    paid_at TEXT,
    voided_at TEXT,
    void_reason TEXT,
    public_note TEXT,
    internal_note TEXT,
    email_status TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS invoice_line_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL REFERENCES invoices(id),
    cleaning_job_id INTEGER REFERENCES cleaning_jobs(id),
    property_id INTEGER REFERENCES properties(id),
    line_type TEXT NOT NULL DEFAULT 'job',
    service_date TEXT NOT NULL,
    property_label TEXT NOT NULL,
    description TEXT NOT NULL,
    quantity_hours TEXT NOT NULL,
    unit_rate_ex_gst_cents INTEGER NOT NULL,
    gst_rate_bps INTEGER NOT NULL,
    amount_ex_gst_cents INTEGER NOT NULL,
    gst_amount_cents INTEGER NOT NULL,
    amount_inc_gst_cents INTEGER NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bank_imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT,
    source_bank TEXT,
    account_identifier TEXT,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    row_count INTEGER NOT NULL DEFAULT 0,
    imported_count INTEGER NOT NULL DEFAULT 0,
    duplicate_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    created_by TEXT
);

CREATE TABLE IF NOT EXISTS bank_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bank_import_id INTEGER REFERENCES bank_imports(id),
    transaction_date TEXT NOT NULL,
    description TEXT NOT NULL,
    debit_cents INTEGER NOT NULL DEFAULT 0,
    credit_cents INTEGER NOT NULL DEFAULT 0,
    amount_cents INTEGER NOT NULL,
    balance_cents INTEGER,
    account_identifier TEXT,
    unique_hash TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'unreconciled',
    matched_payment_id INTEGER REFERENCES payments(id),
    suggested_invoice_id INTEGER REFERENCES invoices(id),
    match_reason TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bank_transaction_id INTEGER REFERENCES bank_transactions(id),
    received_date TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    payer_description TEXT,
    created_by TEXT NOT NULL DEFAULT 'system',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reversed_at TEXT,
    reversal_reason TEXT
);

CREATE TABLE IF NOT EXISTS payment_allocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    payment_id INTEGER NOT NULL REFERENCES payments(id),
    invoice_id INTEGER NOT NULL REFERENCES invoices(id),
    amount_allocated_cents INTEGER NOT NULL,
    allocation_type TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT 'system',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reversed_at TEXT,
    reversal_reason TEXT
);

CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_type TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    filename TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    checksum TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS email_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER REFERENCES invoices(id),
    event_type TEXT NOT NULL,
    recipient TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    attachment_file_id INTEGER REFERENCES files(id),
    provider_message_id TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    old_value_json TEXT,
    new_value_json TEXT,
    reason TEXT,
    user_id TEXT NOT NULL DEFAULT 'system',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS business_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    business_name TEXT NOT NULL,
    abn TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT,
    address TEXT,
    default_payment_terms_days INTEGER NOT NULL DEFAULT 14,
    default_gst_rate_bps INTEGER NOT NULL DEFAULT 1000,
    bsb TEXT,
    account_number TEXT,
    account_name TEXT,
    payid TEXT,
    invoice_number_prefix TEXT NOT NULL DEFAULT 'INV',
    next_invoice_number INTEGER NOT NULL DEFAULT 1,
    invoice_footer TEXT,
    brand_accent TEXT NOT NULL DEFAULT '#111827',
    reminder_settings_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def ensure_directories() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    INVOICE_DIR.mkdir(parents=True, exist_ok=True)
    EMAIL_DIR.mkdir(parents=True, exist_ok=True)


def backup_database(*, keep: int = 30) -> Path | None:
    if not DB_PATH.exists():
        return None
    backup_dir = DATA_DIR / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"clean_ledger-{date.today().isoformat()}.sqlite3"
    if not backup_path.exists():
        shutil.copy2(DB_PATH, backup_path)
    backups = sorted(
        backup_dir.glob("clean_ledger-*.sqlite3"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for stale in backups[keep:]:
        stale.unlink(missing_ok=True)
    return backup_path


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    ensure_directories()
    conn = sqlite3.connect(path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_database(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    ensure_column(conn, "properties", "invoice_number_prefix", "TEXT")
    ensure_column(conn, "properties", "next_invoice_number", "INTEGER")
    ensure_column(conn, "properties", "invoice_number_padding", "INTEGER NOT NULL DEFAULT 3")
    ensure_column(conn, "invoice_line_items", "property_id", "INTEGER REFERENCES properties(id)")
    ensure_column(conn, "invoice_line_items", "line_type", "TEXT NOT NULL DEFAULT 'job'")
    conn.execute(
        """
        UPDATE invoice_line_items
        SET line_type = 'job'
        WHERE line_type IS NULL OR line_type = ''
        """
    )
    conn.execute(
        """
        UPDATE invoice_line_items
        SET property_id = (
            SELECT property_id
            FROM cleaning_jobs
            WHERE cleaning_jobs.id = invoice_line_items.cleaning_job_id
        )
        WHERE property_id IS NULL
          AND cleaning_job_id IS NOT NULL
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO business_settings (
            id, business_name, abn, email, phone, address, default_payment_terms_days,
            default_gst_rate_bps, bsb, account_number, account_name, payid,
            invoice_number_prefix, next_invoice_number, invoice_footer, brand_accent
        )
        VALUES (
            1, 'Clean Ledger Demo Co', '12 345 678 901', 'accounts@example.com',
            '0400 000 000', '1 Harbour Street, Hobart TAS 7000', 14, 1000,
            '123-456', '12345678', 'Clean Ledger Demo Co', 'accounts@example.com',
            'INV', 1, 'Thank you for your business.', '#111827'
        )
        """
    )
    conn.commit()


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def reset_database(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(
        """
        DROP TABLE IF EXISTS audit_log;
        DROP TABLE IF EXISTS email_events;
        DROP TABLE IF EXISTS files;
        DROP TABLE IF EXISTS payment_allocations;
        DROP TABLE IF EXISTS payments;
        DROP TABLE IF EXISTS bank_transactions;
        DROP TABLE IF EXISTS bank_imports;
        DROP TABLE IF EXISTS invoice_line_items;
        DROP TABLE IF EXISTS invoices;
        DROP TABLE IF EXISTS cleaning_jobs;
        DROP TABLE IF EXISTS job_imports;
        DROP TABLE IF EXISTS properties;
        DROP TABLE IF EXISTS owners;
        DROP TABLE IF EXISTS business_settings;
        """
    )
    conn.execute("PRAGMA foreign_keys = ON")
    init_database(conn)
