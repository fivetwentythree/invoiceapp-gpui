from __future__ import annotations

import argparse
import hmac
import html
import json
import mimetypes
import sys
from datetime import date, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from clean_ledger.database import (
    EMAIL_DIR,
    INVOICE_DIR,
    backup_database,
    connect,
    init_database,
    reset_database,
)
from clean_ledger.excel_import import import_excel_paste_jobs, import_excel_rows_jobs
from clean_ledger.excel_invoice import invoice_excel_workbook_bytes
from clean_ledger.pdf import invoice_pdf_bytes, store_invoice_pdf
from clean_ledger.services import (
    add_manual_invoice_line,
    build_invoice_email_message,
    cents,
    create_draft_invoices,
    create_job,
    delete_job,
    delete_invoice_line,
    delete_invoice,
    dollars,
    export_csv,
    find_property_by_labels,
    find_or_create_owner,
    find_or_create_property,
    get_invoice,
    get_invoice_lines,
    get_settings,
    gst_rate_bps_from_percent,
    group_unbilled_jobs,
    import_bank_csv,
    import_jobs_csv,
    invoice_email_body,
    list_invoices,
    list_jobs,
    list_reconciliation_transactions,
    manual_match_transaction,
    purge_inactive_uninvoiced_properties,
    purge_placeholder_import_properties,
    remove_property,
    save_settings,
    seed_demo,
    send_invoice,
    update_invoice_line,
    update_overdue_statuses,
    update_owner_details,
    update_property_details,
    validate_invoice_for_send,
)


STATIC_DIR = Path(__file__).resolve().parent / "static"
DESKTOP_STATIC_DIR: Path | None = None
DESKTOP_TOKEN: str | None = None
DESKTOP_COOKIE_NAME = "domestica_desktop_token"
DESKTOP_PROTECTED_PREFIXES = ("/api/", "/invoices/", "/email-outbox/", "/export/")
DESKTOP_PROTECTED_PATHS = {"/jobs", "/month-end", "/invoices", "/reconcile", "/settings"}


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def status_pill(status: str) -> str:
    safe = esc(status)
    css = safe.replace("_", "-")
    return f'<span class="pill pill-{css}">{safe.replace("_", " ")}</span>'


def current_month() -> str:
    today = date.today()
    return f"{today.year}-{today.month:02d}"


def previous_month() -> str:
    today = date.today()
    first = date(today.year, today.month, 1)
    prev = first - timedelta(days=1)
    return f"{prev.year}-{prev.month:02d}"


def month_label(value: str | None) -> str:
    month = str(value or "").strip()[:7]
    if not month:
        return "Unscheduled"
    try:
        return date.fromisoformat(f"{month}-01").strftime("%B %Y")
    except ValueError:
        return month


def render_page(
    title: str,
    active: str,
    content: str,
    *,
    flash: str | None = None,
    extra_head: str = "",
) -> bytes:
    nav = [
        ("jobs", "Jobs", "/jobs"),
        ("month-end", "Month End", f"/month-end?month={previous_month()}"),
        ("invoices", "Invoices", "/invoices"),
        ("reconcile", "Reconcile", "/reconcile"),
        ("settings", "Settings", "/settings"),
    ]
    nav_html = "".join(
        f'<a class="{ "active" if key == active else "" }" href="{href}">{label}</a>'
        for key, label, href in nav
    )
    flash_html = f'<div class="toast">{esc(flash)}</div>' if flash else ""
    html_text = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(title)} · Domestica Invoice</title>
  <link rel="stylesheet" href="/static/styles.css">
  {extra_head}
</head>
<body>
  <header class="topbar">
    <a class="brand" href="/jobs"><span class="brand-mark"></span>Domestica Invoice</a>
    <nav>{nav_html}</nav>
  </header>
  <main class="shell">
    {flash_html}
    {content}
  </main>
</body>
</html>"""
    return html_text.encode("utf-8")


def metric(label: str, value: str, tone: str = "") -> str:
    return f'<div class="metric {tone}"><span>{esc(label)}</span><strong>{esc(value)}</strong></div>'


def form_value(form: dict[str, list[str]], key: str, default: str = "") -> str:
    values = form.get(key)
    return values[0] if values else default


def row_dict(row) -> dict:
    return dict(row) if row is not None else {}


def rows_list(rows) -> list[dict]:
    return [dict(row) for row in rows]


def optional_int(value: object) -> int | None:
    text = str(value or "").strip()
    return int(text) if text else None


def is_file_within_directory(file_path: Path, directory: Path) -> bool:
    try:
        file_path.relative_to(directory)
    except ValueError:
        return False
    return file_path.is_file()


def is_desktop_protected_path(path: str) -> bool:
    return path in DESKTOP_PROTECTED_PATHS or any(
        path.startswith(prefix) for prefix in DESKTOP_PROTECTED_PREFIXES
    )


class CleanLedgerHandler(BaseHTTPRequestHandler):
    server_version = "CleanLedger/0.1"

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))

    def conn(self):
        conn = connect()
        init_database(conn)
        return conn

    def redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.end_headers()

    def send_html(self, body: bytes, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_text(self, text: str, content_type: str = "text/plain; charset=utf-8") -> None:
        data = text.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Domestica-Desktop-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def read_form(self) -> dict[str, list[str]]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length).decode("utf-8")
        return parse_qs(body, keep_blank_values=True)

    def desktop_cookie_token(self) -> str:
        cookies = self.headers.get("Cookie", "")
        for cookie in cookies.split(";"):
            name, _, value = cookie.strip().partition("=")
            if name == DESKTOP_COOKIE_NAME:
                return unquote(value)
        return ""

    def desktop_request_tokens(self, query: dict[str, list[str]]) -> list[str]:
        tokens = []
        tokens.extend(query.get("desktop_token", []))
        header_token = self.headers.get("X-Domestica-Desktop-Token", "")
        if header_token:
            tokens.append(header_token)
        cookie_token = self.desktop_cookie_token()
        if cookie_token:
            tokens.append(cookie_token)
        return tokens

    def has_desktop_token(self, query: dict[str, list[str]]) -> bool:
        if not DESKTOP_TOKEN:
            return True
        return any(
            hmac.compare_digest(token, DESKTOP_TOKEN)
            for token in self.desktop_request_tokens(query)
            if token
        )

    def authorize_desktop_request(self, path: str, query: dict[str, list[str]]) -> bool:
        if not DESKTOP_TOKEN or not is_desktop_protected_path(path):
            return True
        if self.has_desktop_token(query):
            return True
        if path.startswith("/api/"):
            self.send_json({"error": "Desktop session token required"}, HTTPStatus.FORBIDDEN)
            return False
        self.send_response(HTTPStatus.FORBIDDEN)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Desktop session token required")
        return False

    def handle_desktop_static(
        self,
        path: str,
        query: dict[str, list[str]],
        *,
        head_only: bool = False,
    ) -> bool:
        if not DESKTOP_STATIC_DIR:
            return False
        static_root = DESKTOP_STATIC_DIR.resolve()
        if path in ("", "/", "/index.html"):
            file_path = static_root / "index.html"
        else:
            relative = unquote(path.lstrip("/"))
            file_path = (static_root / relative).resolve()
        if not is_file_within_directory(file_path, static_root):
            return False
        data = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header(
            "Content-Type",
            mimetypes.guess_type(file_path.name)[0] or "application/octet-stream",
        )
        if DESKTOP_TOKEN and path in ("", "/", "/index.html") and self.has_desktop_token(query):
            self.send_header(
                "Set-Cookie",
                f"{DESKTOP_COOKIE_NAME}={quote(DESKTOP_TOKEN or '')}; Path=/; SameSite=Strict",
            )
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if not head_only:
            self.wfile.write(data)
        return True

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if not self.authorize_desktop_request(path, query):
                return
            if self.handle_desktop_static(path, query):
                return
            if path.startswith("/api/"):
                self.handle_api_get(path, query)
            elif path == "/":
                self.redirect("/jobs")
            elif path.startswith("/static/"):
                self.handle_static(path)
            elif path == "/jobs":
                self.handle_jobs(query)
            elif path == "/month-end":
                self.handle_month_end(query)
            elif path == "/invoices":
                self.handle_invoices(query)
            elif path.startswith("/invoices/") and path.endswith("/excel"):
                self.handle_invoice_excel(path)
            elif path.startswith("/invoices/") and path.endswith("/pdf"):
                self.handle_invoice_pdf(path)
            elif path.startswith("/email-outbox/") and path.endswith(".eml"):
                self.handle_email_outbox(path)
            elif path.startswith("/invoices/"):
                self.handle_invoice_detail(path, query)
            elif path == "/reconcile":
                self.handle_reconcile(query)
            elif path == "/settings":
                self.handle_settings(query)
            elif path.startswith("/export/") and path.endswith(".csv"):
                self.handle_export(path)
            else:
                self.not_found()
        except Exception as exc:
            if path.startswith("/api/"):
                self.api_error(exc)
            else:
                self.error_page(exc)

    def do_HEAD(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if not self.authorize_desktop_request(parsed.path, query):
            return
        if self.handle_desktop_static(parsed.path, query, head_only=True):
            return
        if parsed.path.startswith("/static/"):
            self.handle_static(parsed.path, head_only=True)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if not self.authorize_desktop_request(path, query):
                return
            if path.startswith("/api/"):
                self.handle_api_post(path)
            elif path == "/jobs":
                self.post_job()
            elif path == "/jobs/import":
                self.post_jobs_import()
            elif path == "/jobs/import-excel-paste":
                self.post_excel_paste_import()
            elif path.startswith("/jobs/") and path.endswith("/delete"):
                self.post_delete_job(path)
            elif path == "/month-end/generate":
                self.post_generate_drafts()
            elif path == "/month-end/property-invoice-number":
                self.post_property_invoice_number()
            elif path.startswith("/invoices/") and path.endswith("/send"):
                self.post_send_invoice(path)
            elif path.startswith("/invoices/") and path.endswith("/delete"):
                self.post_delete_invoice(path)
            elif path == "/reconcile/import":
                self.post_bank_import()
            elif path == "/reconcile/match":
                self.post_manual_match()
            elif path == "/settings":
                self.post_settings()
            else:
                self.not_found()
        except Exception as exc:
            if path.startswith("/api/"):
                self.api_error(exc)
            else:
                self.error_page(exc)

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if not self.authorize_desktop_request(path, query):
                return
            if path.startswith("/api/"):
                self.handle_api_delete(path)
            else:
                self.not_found()
        except Exception as exc:
            if path.startswith("/api/"):
                self.api_error(exc)
            else:
                self.error_page(exc)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Domestica-Desktop-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.end_headers()

    def handle_api_get(self, path: str, query: dict[str, list[str]]) -> None:
        with self.conn() as conn:
            if path == "/api/health":
                self.send_json({"ok": True})
                return
            if path == "/api/jobs":
                jobs = list_jobs(
                    conn,
                    month=query.get("month", [None])[0],
                    status=query.get("status", [""])[0] or None,
                )
                self.send_json({"jobs": rows_list(jobs)})
                return
            if path == "/api/owners-properties":
                owners = conn.execute(
                    "SELECT * FROM owners WHERE active = 1 ORDER BY name"
                ).fetchall()
                properties = conn.execute(
                    """
                    SELECT p.*, o.name AS owner_name
                    FROM properties p
                    JOIN owners o ON o.id = p.owner_id
                    WHERE p.active = 1
                    ORDER BY o.name, p.nickname, p.address_line_1
                    """
                ).fetchall()
                self.send_json({"owners": rows_list(owners), "properties": rows_list(properties)})
                return
            if path == "/api/month-end":
                month = query.get("month", [previous_month()])[0]
                groups = []
                for group in group_unbilled_jobs(conn, month):
                    groups.append(
                        {
                            "owner_id": group.owner_id,
                            "owner_name": group.owner_name,
                            "owner_email": group.owner_email,
                            "property_id": group.property_id,
                            "property_label": group.property_label,
                            "job_count": group.job_count,
                            "property_count": group.property_count,
                            "subtotal_ex_gst_cents": group.subtotal_ex_gst_cents,
                            "gst_total_cents": group.gst_total_cents,
                            "total_inc_gst_cents": group.total_inc_gst_cents,
                            "issues": group.issues,
                            "jobs": rows_list(group.jobs),
                        }
                    )
                self.send_json({"month": month, "groups": groups})
                return
            if path == "/api/invoices":
                invoices = list_invoices(
                    conn,
                    status=query.get("status", [""])[0] or None,
                    month=query.get("month", [""])[0] or None,
                )
                self.send_json({"invoices": rows_list(invoices)})
                return
            if path.startswith("/api/invoices/"):
                invoice_id = int(path.removeprefix("/api/invoices/").split("/")[0])
                invoice = get_invoice(conn, invoice_id)
                if not invoice:
                    self.send_json({"error": "Invoice not found"}, HTTPStatus.NOT_FOUND)
                    return
                payments = conn.execute(
                    """
                    SELECT p.*, pa.amount_allocated_cents, pa.allocation_type
                    FROM payment_allocations pa
                    JOIN payments p ON p.id = pa.payment_id
                    WHERE pa.invoice_id = ?
                    ORDER BY p.received_date DESC, p.id DESC
                    """,
                    (invoice_id,),
                ).fetchall()
                audit_rows = conn.execute(
                    """
                    SELECT * FROM audit_log
                    WHERE entity_type = 'invoice' AND entity_id = ?
                    ORDER BY id DESC
                    """,
                    (invoice_id,),
                ).fetchall()
                email_rows = conn.execute(
                    "SELECT * FROM email_events WHERE invoice_id = ? ORDER BY id DESC",
                    (invoice_id,),
                ).fetchall()
                emails = rows_list(email_rows)
                settings = get_settings(conn)
                current_subject = f"Tax Invoice {invoice['invoice_number']} from {settings['business_name']}"
                default_email_body = invoice_email_body(conn, invoice_id)
                for email in emails:
                    email["subject"] = email.get("subject") or current_subject
                    email["body"] = email.get("body") or default_email_body
                    if email.get("provider_message_id"):
                        email["eml_url"] = f"/email-outbox/{quote(str(email['provider_message_id']))}.eml"
                self.send_json(
                    {
                        "invoice": row_dict(invoice),
                        "settings": row_dict(settings),
                        "default_email_subject": current_subject,
                        "default_email_body": default_email_body,
                        "lines": rows_list(get_invoice_lines(conn, invoice_id)),
                        "payments": rows_list(payments),
                        "emails": emails,
                        "audit": rows_list(audit_rows),
                        "send_errors": validate_invoice_for_send(conn, invoice_id),
                    }
                )
                return
            if path == "/api/reconcile":
                invoices = conn.execute(
                    """
                    SELECT i.*, o.name AS owner_name
                    FROM invoices i
                    JOIN owners o ON o.id = i.owner_id
                    WHERE i.status IN ('sent', 'overdue', 'partially_paid')
                      AND i.amount_outstanding_cents > 0
                    ORDER BY i.issue_date, i.id
                    """
                ).fetchall()
                self.send_json(
                    {
                        "transactions": rows_list(list_reconciliation_transactions(conn)),
                        "invoices": rows_list(invoices),
                    }
                )
                return
            if path == "/api/settings":
                self.send_json({"settings": row_dict(get_settings(conn))})
                return
        self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def handle_api_post(self, path: str) -> None:
        payload = self.read_json()
        with self.conn() as conn:
            if path == "/api/owners-properties":
                rate = cents(payload.get("default_rate_ex_gst"))
                property_id_text = str(payload.get("property_id") or "").strip()
                existing_property = None
                if property_id_text:
                    existing_property = conn.execute(
                        "SELECT * FROM properties WHERE id = ?",
                        (int(property_id_text),),
                    ).fetchone()
                    if not existing_property:
                        raise ValueError("Property not found")
                else:
                    property_nickname = str(payload.get("property_nickname") or "")
                    property_address = str(payload.get("property_address") or "")
                    existing_property = find_property_by_labels(
                        conn,
                        property_nickname,
                        property_address,
                    )
                if existing_property:
                    owner_id = int(existing_property["owner_id"])
                    property_id = int(existing_property["id"])
                    update_owner_details(
                        conn,
                        owner_id,
                        name=str(payload.get("owner_name") or ""),
                        billing_email=str(payload.get("owner_email") or ""),
                        phone=str(payload.get("owner_phone") or ""),
                        abn=str(payload.get("owner_abn") or ""),
                        billing_address_line_1=str(payload.get("owner_billing_address") or ""),
                        default_payment_terms_days=optional_int(
                            payload.get("default_payment_terms_days")
                        ),
                        default_rate_ex_gst_cents=rate,
                    )
                    update_property_details(
                        conn,
                        property_id,
                        address_line_1=str(payload.get("property_address") or ""),
                        nickname=str(payload.get("property_nickname") or ""),
                        default_rate_ex_gst_cents=rate or None,
                        default_description=str(payload.get("default_description") or ""),
                        invoice_number_seed=str(payload.get("property_invoice_number") or ""),
                    )
                else:
                    owner_id = find_or_create_owner(
                        conn,
                        str(payload.get("owner_name") or ""),
                        billing_email=str(payload.get("owner_email") or ""),
                        phone=str(payload.get("owner_phone") or ""),
                        abn=str(payload.get("owner_abn") or ""),
                        billing_address_line_1=str(payload.get("owner_billing_address") or ""),
                        default_payment_terms_days=optional_int(
                            payload.get("default_payment_terms_days")
                        ),
                        default_rate_ex_gst_cents=rate,
                    )
                    property_id = find_or_create_property(
                        conn,
                        owner_id,
                        str(payload.get("property_address") or payload.get("property_nickname") or ""),
                        nickname=str(payload.get("property_nickname") or ""),
                        default_rate_ex_gst_cents=rate or None,
                        default_description=str(payload.get("default_description") or ""),
                        invoice_number_seed=str(payload.get("property_invoice_number") or ""),
                    )
                conn.commit()
                self.send_json({"owner_id": owner_id, "property_id": property_id})
                return
            if path == "/api/properties/remove":
                remove_property(conn, int(payload.get("property_id") or 0))
                conn.commit()
                self.send_json({"ok": True})
                return
            if path == "/api/properties/invoice-number":
                invoice_number = str(payload.get("invoice_number") or "").strip()
                if not invoice_number:
                    raise ValueError("Next invoice number is required")
                property_id = int(payload.get("property_id") or 0)
                update_property_details(
                    conn,
                    property_id,
                    invoice_number_seed=invoice_number,
                )
                conn.commit()
                property_row = conn.execute(
                    """
                    SELECT p.*, o.name AS owner_name
                    FROM properties p
                    JOIN owners o ON o.id = p.owner_id
                    WHERE p.id = ?
                    """,
                    (property_id,),
                ).fetchone()
                self.send_json({"property": row_dict(property_row)})
                return
            if path == "/api/jobs":
                rate = cents(payload.get("rate_ex_gst"))
                property_id_text = str(payload.get("property_id") or "").strip()
                if property_id_text:
                    property_row = conn.execute(
                        "SELECT * FROM properties WHERE id = ? AND active = 1",
                        (int(property_id_text),),
                    ).fetchone()
                    if not property_row:
                        raise ValueError("Property not found")
                    property_id = int(property_row["id"])
                    owner_id = int(property_row["owner_id"])
                else:
                    property_nickname = str(payload.get("property_nickname") or "")
                    property_address = str(payload.get("property_address") or "")
                    existing_property = find_property_by_labels(
                        conn,
                        property_nickname,
                        property_address,
                    )
                    if existing_property:
                        property_id = int(existing_property["id"])
                        owner_id = int(existing_property["owner_id"])
                    else:
                        owner_id = int(payload["owner_id"]) if payload.get("owner_id") else find_or_create_owner(
                            conn,
                            str(payload.get("owner_name") or ""),
                            billing_email=str(payload.get("owner_email") or ""),
                            phone=str(payload.get("owner_phone") or ""),
                            abn=str(payload.get("owner_abn") or ""),
                            billing_address_line_1=str(payload.get("owner_billing_address") or ""),
                            default_payment_terms_days=optional_int(
                                payload.get("default_payment_terms_days")
                            ),
                            default_rate_ex_gst_cents=rate,
                        )
                        property_id = find_or_create_property(
                            conn,
                            owner_id,
                            str(payload.get("property_address") or payload.get("property_nickname") or ""),
                            nickname=str(payload.get("property_nickname") or ""),
                            default_rate_ex_gst_cents=rate,
                            default_description=str(payload.get("description") or ""),
                        )
                job_id, status = create_job(
                    conn,
                    owner_id=owner_id,
                    property_id=property_id,
                    service_date=str(payload.get("service_date") or ""),
                    description=str(payload.get("description") or ""),
                    hours=str(payload.get("hours") or ""),
                    rate_ex_gst_cents=rate,
                    internal_note=str(payload.get("internal_note") or ""),
                )
                conn.commit()
                self.send_json({"job_id": job_id, "status": status})
                return
            if path == "/api/import/xlsx-jobs":
                result = import_excel_rows_jobs(
                    conn,
                    list(payload.get("rows") or []),
                    filename=str(payload.get("filename") or "xlsx-upload"),
                )
                self.send_json(result)
                return
            if path == "/api/month-end/generate":
                created = create_draft_invoices(conn, str(payload.get("month") or previous_month()))
                self.send_json({"created": created, "count": len(created)})
                return
            parts = path.strip("/").split("/")
            if len(parts) in (4, 5) and parts[:2] == ["api", "invoices"] and parts[3] == "lines":
                invoice_id = int(parts[2])
                settings = get_settings(conn)
                gst_rate_bps = gst_rate_bps_from_percent(
                    payload.get("gst_rate"),
                    default_bps=int(settings["default_gst_rate_bps"]),
                )
                if len(parts) == 4:
                    line_id = add_manual_invoice_line(
                        conn,
                        invoice_id,
                        service_date=str(payload.get("service_date") or ""),
                        description=str(payload.get("description") or ""),
                        amount_inc_gst_cents=cents(payload.get("amount_inc_gst")),
                        gst_rate_bps=gst_rate_bps,
                    )
                else:
                    line_id = int(parts[4])
                    update_invoice_line(
                        conn,
                        invoice_id,
                        line_id,
                        service_date=str(payload.get("service_date") or ""),
                        description=str(payload.get("description") or ""),
                        amount_inc_gst_cents=cents(payload.get("amount_inc_gst")),
                        hours=str(payload.get("hours") or ""),
                        rate_ex_gst_cents=cents(payload.get("rate_ex_gst")),
                        gst_rate_bps=gst_rate_bps,
                    )
                self.send_json(
                    {
                        "line": row_dict(conn.execute(
                            "SELECT * FROM invoice_line_items WHERE id = ?",
                            (line_id,),
                        ).fetchone()),
                        "invoice": row_dict(get_invoice(conn, invoice_id)),
                    }
                )
                return
            if path.startswith("/api/invoices/") and path.endswith("/send"):
                invoice_id = int(path.removeprefix("/api/invoices/").split("/")[0])
                pdf_file_id = store_invoice_pdf(conn, invoice_id, INVOICE_DIR)
                body = str(payload.get("email_body") or "").strip()
                if not body:
                    if "email_body" in payload:
                        raise ValueError("Email message is required")
                    body = invoice_email_body(conn, invoice_id)
                send_invoice(conn, invoice_id, pdf_file_id=pdf_file_id, email_body=body, email_dir=EMAIL_DIR)
                self.send_json({"status": "sent", "invoice_id": invoice_id})
                return
            if path == "/api/reconcile/import":
                result = import_bank_csv(
                    conn,
                    str(payload.get("csv_text") or ""),
                    filename=str(payload.get("filename") or "bank-upload.csv"),
                )
                self.send_json(result)
                return
            if path == "/api/reconcile/match":
                payment_id = manual_match_transaction(
                    conn,
                    int(payload["transaction_id"]),
                    int(payload["invoice_id"]),
                    cents(payload.get("amount")) if payload.get("amount") else None,
                    str(payload.get("reason") or "Manual match"),
                )
                self.send_json({"payment_id": payment_id})
                return
            if path == "/api/settings":
                save_settings(conn, {key: str(value) for key, value in payload.items()})
                self.send_json({"settings": row_dict(get_settings(conn))})
                return
        self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def handle_api_delete(self, path: str) -> None:
        with self.conn() as conn:
            parts = path.strip("/").split("/")
            if len(parts) == 5 and parts[:2] == ["api", "invoices"] and parts[3] == "lines":
                invoice_id = int(parts[2])
                line_id = int(parts[4])
                delete_invoice_line(conn, invoice_id, line_id)
                self.send_json({"ok": True, "invoice": row_dict(get_invoice(conn, invoice_id))})
                return
            if path.startswith("/api/jobs/"):
                job_id = int(path.removeprefix("/api/jobs/").split("/")[0])
                result = delete_job(conn, job_id)
                self.send_json({"ok": True, **result})
                return
            if path.startswith("/api/invoices/"):
                invoice_id = int(path.removeprefix("/api/invoices/").split("/")[0])
                result = delete_invoice(conn, invoice_id)
                self.send_json({"ok": True, **result})
                return
        self.send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def handle_static(self, path: str, *, head_only: bool = False) -> None:
        relative = unquote(path.removeprefix("/static/"))
        static_root = STATIC_DIR.resolve()
        file_path = (static_root / relative).resolve()
        if not is_file_within_directory(file_path, static_root):
            self.not_found()
            return
        data = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(file_path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if not head_only:
            self.wfile.write(data)

    def handle_jobs(self, query: dict[str, list[str]]) -> None:
        month = query.get("month", [current_month()])[0]
        status = query.get("status", [""])[0] or None
        flash = query.get("flash", [None])[0]
        with self.conn() as conn:
            jobs = list_jobs(conn, month=month, status=status)
            owners = conn.execute("SELECT * FROM owners WHERE active = 1 ORDER BY name").fetchall()
            properties = conn.execute(
                """
                SELECT p.*, o.name AS owner_name
                FROM properties p
                JOIN owners o ON o.id = p.owner_id
                WHERE p.active = 1
                ORDER BY o.name, p.nickname, p.address_line_1
                """
            ).fetchall()
        rows = ""
        for job in jobs:
            delete_action = (
                f"""
  <form class="table-action-form" method="post" action="/jobs/{job['id']}/delete">
    <input type="hidden" name="month" value="{esc(month)}">
    <input type="hidden" name="status" value="{esc(status or '')}">
    <button class="button secondary small" type="submit">Delete</button>
  </form>"""
                if job["status"] in ("unbilled", "draft_invoice")
                else '<span class="muted">Invoiced</span>'
            )
            rows += f"""<tr>
  <td>{esc(job['service_date'])}</td>
  <td>{esc(job['property_nickname'] or job['property_address'])}</td>
  <td>{esc(job['owner_name'])}</td>
  <td>{esc(job['description'])}</td>
  <td class="num">{esc(job['hours'])}</td>
  <td class="num">{dollars(job['rate_ex_gst_cents'])}</td>
  <td class="num">{dollars(job['gst_amount_cents'])}</td>
  <td class="num strong">{dollars(job['amount_inc_gst_cents'])}</td>
  <td>{status_pill(job['status'])}</td>
  <td>{delete_action}</td>
</tr>"""
        if not rows:
            rows = """<tr><td colspan="10" class="empty">
No cleaning jobs yet. Add a job or import your spreadsheet to get started.
</td></tr>"""
        owner_options = "".join(
            f'<option value="{owner["id"]}">{esc(owner["name"])}</option>' for owner in owners
        )
        property_options = "".join(
            f'<option value="{prop["id"]}">{esc(prop["owner_name"])} · {esc(prop["nickname"] or prop["address_line_1"])}</option>'
            for prop in properties
        )
        content = f"""
<section class="page-head">
  <div>
    <p class="eyebrow">Work capture</p>
    <h1>Jobs</h1>
  </div>
  <div class="actions">
    <a class="button secondary" href="/export/jobs.csv">Export</a>
    <a class="button" href="#add-job">Add job</a>
  </div>
</section>

<form class="toolbar" method="get" action="/jobs">
  <label>Month <input type="month" name="month" value="{esc(month)}"></label>
  <label>Status
    <select name="status">
      <option value="">All</option>
      {''.join(f'<option value="{s}" {"selected" if status == s else ""}>{s}</option>' for s in ["unbilled", "draft_invoice", "invoiced", "void"])}
    </select>
  </label>
  <button class="button secondary" type="submit">Filter</button>
</form>

<section class="table-wrap">
  <table>
    <thead><tr><th>Date</th><th>Property</th><th>Owner</th><th>Description</th><th>Hours</th><th>Rate</th><th>GST</th><th>Total</th><th>Status</th><th>Actions</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</section>

<section class="split">
  <form class="panel" id="add-job" method="post" action="/jobs">
    <h2>Add job</h2>
    <div class="grid two">
      <label>Existing owner
        <select name="owner_id"><option value="">Create from owner name</option>{owner_options}</select>
      </label>
      <label>Existing property
        <select name="property_id"><option value="">Create from property details</option>{property_options}</select>
      </label>
      <label>Owner name <input name="owner_name" placeholder="Harbour Property Group"></label>
      <label>Owner email <input name="owner_email" type="email" placeholder="accounts@example.com"></label>
      <label>Property nickname <input name="property_nickname" placeholder="Salamanca 12"></label>
      <label>Property address <input name="property_address" placeholder="12 Salamanca Place, Hobart TAS"></label>
      <label>Service date <input name="service_date" type="date" required></label>
      <label>Hours <input name="hours" inputmode="decimal" required placeholder="2.5"></label>
      <label>Rate ex GST <input name="rate_ex_gst" inputmode="decimal" required placeholder="72.50"></label>
      <label>Description <input name="description" required placeholder="Standard turnover clean"></label>
    </div>
    <label>Internal note <textarea name="internal_note" rows="2"></textarea></label>
    <button class="button" type="submit">Save job</button>
  </form>

  <form class="panel" method="post" action="/jobs/import">
    <h2>Import jobs CSV</h2>
    <p class="muted">Paste CSV with headers: service_date, owner_name, owner_email, property_nickname, property_address, description, hours, rate_ex_gst, notes.</p>
    <textarea name="csv_text" rows="12" spellcheck="false"></textarea>
    <button class="button" type="submit">Import spreadsheet</button>
  </form>
</section>

<section class="panel wide">
  <h2>Import pasted Excel invoice sheet</h2>
  <p class="muted">Paste rows copied from Excel with headers like Date, property, Hr, Rate incl.GST, Income. Rows labelled extras or Total are ignored.</p>
  <form method="post" action="/jobs/import-excel-paste">
    <textarea name="excel_text" rows="10" spellcheck="false" placeholder="Date&#9;property&#9;Hr&#9;Rate incl.GST&#9;Income"></textarea>
    <button class="button" type="submit">Import Excel paste</button>
  </form>
</section>
"""
        self.send_html(render_page("Jobs", "jobs", content, flash=flash))

    def post_job(self) -> None:
        form = self.read_form()
        with self.conn() as conn:
            owner_id_text = form_value(form, "owner_id")
            property_id_text = form_value(form, "property_id")
            rate = cents(form_value(form, "rate_ex_gst"))
            if owner_id_text:
                owner_id = int(owner_id_text)
            else:
                owner_id = find_or_create_owner(
                    conn,
                    form_value(form, "owner_name"),
                    billing_email=form_value(form, "owner_email"),
                    default_rate_ex_gst_cents=rate,
                )
            if property_id_text:
                property_id = int(property_id_text)
            else:
                property_id = find_or_create_property(
                    conn,
                    owner_id,
                    form_value(form, "property_address") or form_value(form, "property_nickname"),
                    nickname=form_value(form, "property_nickname"),
                    default_rate_ex_gst_cents=rate,
                    default_description=form_value(form, "description"),
                )
            _, status = create_job(
                conn,
                owner_id=owner_id,
                property_id=property_id,
                service_date=form_value(form, "service_date"),
                description=form_value(form, "description"),
                hours=form_value(form, "hours"),
                rate_ex_gst_cents=rate,
                internal_note=form_value(form, "internal_note"),
            )
            conn.commit()
        self.redirect(f"/jobs?flash={quote('Job skipped as duplicate' if status == 'duplicate' else 'Job saved')}")

    def post_delete_job(self, path: str) -> None:
        job_id = int(path.strip("/").split("/")[1])
        form = self.read_form()
        month = form_value(form, "month", current_month())
        status = form_value(form, "status")
        with self.conn() as conn:
            delete_job(conn, job_id)
        query = f"month={quote(month)}&flash={quote('Job deleted')}"
        if status:
            query += f"&status={quote(status)}"
        self.redirect(f"/jobs?{query}")

    def post_jobs_import(self) -> None:
        form = self.read_form()
        with self.conn() as conn:
            result = import_jobs_csv(conn, form_value(form, "csv_text"), filename="pasted-jobs.csv")
        message = (
            f"Imported {result['imported']} jobs, skipped {result['duplicates']} duplicates, "
            f"{result['errors']} errors"
        )
        self.redirect(f"/jobs?flash={quote(message)}")

    def post_excel_paste_import(self) -> None:
        form = self.read_form()
        with self.conn() as conn:
            result = import_excel_paste_jobs(
                conn,
                form_value(form, "excel_text"),
                filename="pasted-excel-invoice-sheet",
            )
        message = (
            f"Imported {result['imported']} Excel rows, skipped {result['duplicates']} duplicates, "
            f"ignored extras/total rows, {result['errors']} errors"
        )
        self.redirect(f"/jobs?flash={quote(message)}")

    def handle_month_end(self, query: dict[str, list[str]]) -> None:
        month = query.get("month", [previous_month()])[0]
        flash = query.get("flash", [None])[0]
        with self.conn() as conn:
            groups = group_unbilled_jobs(conn, month)
        ready_rows = []
        review_rows = []
        for group in groups:
            issues_html = (
                f"<br><span class=\"muted\">{esc(', '.join(group.issues))}</span>"
                if group.issues
                else ""
            )
            resolve_html = ""
            if "Missing property invoice number" in group.issues:
                resolve_html = f"""
  <form class="month-end-resolve-form" method="post" action="/month-end/property-invoice-number">
    <input type="hidden" name="month" value="{esc(month)}">
    <input type="hidden" name="property_id" value="{group.property_id}">
    <label>Next invoice number
      <input name="invoice_number" required placeholder="JAP001">
    </label>
    <p class="muted">Stored on this property for future invoices.</p>
    <button class="button small" type="submit">Save number</button>
  </form>"""
            elif group.issues:
                resolve_html = '<span class="muted">Update owner/property details</span>'
            else:
                resolve_html = '<span class="muted">Ready</span>'
            row = f"""<tr>
  <td>{esc(group.owner_name)}</td>
  <td>{esc(group.property_label)}</td>
  <td class="num">{group.job_count}</td>
  <td class="num">{dollars(group.subtotal_ex_gst_cents)}</td>
  <td class="num">{dollars(group.gst_total_cents)}</td>
  <td class="num strong">{dollars(group.total_inc_gst_cents)}</td>
  <td>{status_pill('ready' if not group.issues else 'needs_review')}{issues_html}</td>
  <td>{resolve_html}</td>
</tr>"""
            if group.issues:
                review_rows.append(row)
            else:
                ready_rows.append(row)
        if not ready_rows:
            ready_rows.append('<tr><td colspan="8" class="empty">No ready invoice groups for this month.</td></tr>')
        if not review_rows:
            review_rows.append('<tr><td colspan="8" class="empty">No invoice groups need review.</td></tr>')
        content = f"""
<section class="page-head">
  <div>
    <p class="eyebrow">Monthly billing</p>
    <h1>Month End</h1>
  </div>
  <form method="post" action="/month-end/generate">
    <input type="hidden" name="month" value="{esc(month)}">
    <button class="button" type="submit">Generate draft invoices</button>
  </form>
</section>

<form class="toolbar" method="get" action="/month-end">
  <label>Billing month <input type="month" name="month" value="{esc(month)}"></label>
  <button class="button secondary" type="submit">Review</button>
  <a class="button secondary" href="/export/gst-summary.csv">Export review</a>
</form>

<section class="table-wrap">
  <div class="section-title"><h2>Ready</h2><span>{len([g for g in groups if not g.issues])} groups</span></div>
  <table>
    <thead><tr><th>Owner</th><th>Property</th><th>Jobs</th><th>Subtotal</th><th>GST</th><th>Total</th><th>Status</th><th>Resolve</th></tr></thead>
    <tbody>{''.join(ready_rows)}</tbody>
  </table>
</section>

<section class="table-wrap">
  <div class="section-title"><h2>Needs review</h2><span>{len([g for g in groups if g.issues])} groups</span></div>
  <table>
    <thead><tr><th>Owner</th><th>Property</th><th>Jobs</th><th>Subtotal</th><th>GST</th><th>Total</th><th>Status</th><th>Resolve</th></tr></thead>
    <tbody>{''.join(review_rows)}</tbody>
  </table>
</section>
"""
        self.send_html(render_page("Month End", "month-end", content, flash=flash))

    def post_generate_drafts(self) -> None:
        form = self.read_form()
        month = form_value(form, "month", previous_month())
        with self.conn() as conn:
            created = create_draft_invoices(conn, month)
        self.redirect(f"/invoices?flash={quote(f'Generated {len(created)} draft invoices')}")

    def post_property_invoice_number(self) -> None:
        form = self.read_form()
        month = form_value(form, "month", previous_month())
        invoice_number = form_value(form, "invoice_number").strip()
        if not invoice_number:
            raise ValueError("Next invoice number is required")
        with self.conn() as conn:
            update_property_details(
                conn,
                int(form_value(form, "property_id")),
                invoice_number_seed=invoice_number,
            )
            conn.commit()
        self.redirect(f"/month-end?month={quote(month)}&flash={quote('Property invoice number saved')}")

    def handle_invoices(self, query: dict[str, list[str]]) -> None:
        status = query.get("status", [""])[0] or None
        month = query.get("month", [""])[0] or None
        flash = query.get("flash", [None])[0]
        with self.conn() as conn:
            invoices = list_invoices(conn, status=status, month=month)
        totals = {
            "outstanding": sum(int(inv["amount_outstanding_cents"]) for inv in invoices),
            "overdue": sum(1 for inv in invoices if inv["status"] == "overdue"),
            "draft": sum(1 for inv in invoices if inv["status"] == "draft"),
        }
        row_parts = []
        current_month = None
        for inv in invoices:
            invoice_month = str(inv["period_start"] or inv["issue_date"] or "")[:7]
            if invoice_month != current_month:
                current_month = invoice_month
                row_parts.append(
                    f'<tr class="month-row"><td colspan="10">{esc(month_label(invoice_month))}</td></tr>'
                )
            row_parts.append(
                f"""<tr>
  <td><a href="/invoices/{inv['id']}">{esc(inv['invoice_number'])}</a></td>
  <td>{esc(inv['owner_name'])}</td>
  <td>{esc(inv['period_start'])} to {esc(inv['period_end'])}</td>
  <td>{esc(inv['issue_date'])}</td>
  <td>{esc(inv['due_date'])}</td>
  <td class="num strong">{dollars(inv['total_inc_gst_cents'])}</td>
  <td class="num">{dollars(inv['amount_paid_cents'])}</td>
  <td class="num">{dollars(inv['amount_outstanding_cents'])}</td>
  <td>{status_pill(inv['status'])}</td>
  <td>
    <form class="inline-form" method="post" action="/invoices/{inv['id']}/delete">
      <button class="button secondary" type="submit">Delete</button>
    </form>
  </td>
</tr>"""
            )
        rows = "".join(row_parts)
        if not rows:
            rows = '<tr><td colspan="10" class="empty">No invoices match this filter.</td></tr>'
        filters = ["", "draft", "sent", "overdue", "partially_paid", "paid", "void"]
        all_months_href = f"/invoices?status={quote(status)}" if status else "/invoices"
        content = f"""
<section class="page-head">
  <div>
    <p class="eyebrow">Invoice lifecycle</p>
    <h1>Invoices</h1>
  </div>
  <div class="actions">
    <a class="button secondary" href="/export/invoices.csv">Export</a>
    <a class="button" href="/month-end?month={previous_month()}">Generate monthly invoices</a>
  </div>
</section>

<section class="metrics">
  {metric("Outstanding", dollars(totals["outstanding"]))}
  {metric("Overdue", str(totals["overdue"]), "warn")}
  {metric("Drafts", str(totals["draft"]))}
</section>

<form class="toolbar" method="get" action="/invoices">
  <label>Invoice month
    <input type="month" name="month" value="{esc(month or '')}">
    <span class="muted">Filters by invoice billing period.</span>
  </label>
  <label>Status
    <select name="status">
      {''.join(f'<option value="{s}" {"selected" if (status or "") == s else ""}>{s or "All"}</option>' for s in filters)}
    </select>
  </label>
  <button class="button secondary" type="submit">Filter</button>
  <a class="button secondary" href="{all_months_href}">All months</a>
</form>

<section class="table-wrap">
  <table>
    <thead><tr><th>Invoice</th><th>Owner</th><th>Period</th><th>Issued</th><th>Due</th><th>Total</th><th>Paid</th><th>Outstanding</th><th>Status</th><th>Actions</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</section>
"""
        self.send_html(render_page("Invoices", "invoices", content, flash=flash))

    def handle_invoice_detail(self, path: str, query: dict[str, list[str]]) -> None:
        invoice_id = int(path.strip("/").split("/")[1])
        flash = query.get("flash", [None])[0]
        with self.conn() as conn:
            invoice = get_invoice(conn, invoice_id)
            if not invoice:
                self.not_found()
                return
            lines = get_invoice_lines(conn, invoice_id)
            payments = conn.execute(
                """
                SELECT p.*, pa.amount_allocated_cents, pa.allocation_type
                FROM payment_allocations pa
                JOIN payments p ON p.id = pa.payment_id
                WHERE pa.invoice_id = ?
                ORDER BY p.received_date DESC, p.id DESC
                """,
                (invoice_id,),
            ).fetchall()
            emails = conn.execute(
                "SELECT * FROM email_events WHERE invoice_id = ? ORDER BY id DESC",
                (invoice_id,),
            ).fetchall()
            audit_rows = conn.execute(
                """
                SELECT * FROM audit_log
                WHERE entity_type = 'invoice' AND entity_id = ?
                ORDER BY id DESC
                """,
                (invoice_id,),
            ).fetchall()
            send_errors = validate_invoice_for_send(conn, invoice_id)
        def invoice_line_row(line) -> str:
            is_manual = line["line_type"] == "manual" or not str(line["quantity_hours"] or "").strip()
            hours = "-" if is_manual else esc(line["quantity_hours"])
            rate = "-" if is_manual else dollars(line["unit_rate_ex_gst_cents"])
            return f"""<tr>
  <td>{esc(line['service_date'])}</td>
  <td>{esc(line['property_label'])}</td>
  <td>{esc(line['description'])}</td>
  <td class="num">{hours}</td>
  <td class="num">{rate}</td>
  <td class="num">{dollars(line['gst_amount_cents'])}</td>
  <td class="num strong">{dollars(line['amount_inc_gst_cents'])}</td>
</tr>"""

        line_rows = "".join(invoice_line_row(line) for line in lines)
        payment_rows = "".join(
            f"""<tr><td>{esc(payment['received_date'])}</td><td>{dollars(payment['amount_allocated_cents'])}</td><td>{esc(payment['allocation_type'])}</td><td>{esc(payment['payer_description'])}</td></tr>"""
            for payment in payments
        ) or '<tr><td colspan="4" class="empty">No payments recorded.</td></tr>'
        email_rows = "".join(
            f"""<tr><td>{esc(email['created_at'])}</td><td>{esc(email['recipient'])}</td><td>{esc(email['status'])}</td><td>{esc(email['subject'])}</td><td><a href="/email-outbox/{quote(str(email['provider_message_id']))}.eml">Download .eml</a></td></tr>"""
            for email in emails
        ) or '<tr><td colspan="5" class="empty">No email history.</td></tr>'
        audit_html = "".join(
            f"""<tr><td>{esc(row['created_at'])}</td><td>{esc(row['action'])}</td><td>{esc(row['reason'] or '')}</td></tr>"""
            for row in audit_rows
        ) or '<tr><td colspan="3" class="empty">No audit entries.</td></tr>'
        error_box = (
            '<div class="callout danger"><strong>Cannot send yet</strong><ul>'
            + "".join(f"<li>{esc(error)}</li>" for error in send_errors)
            + "</ul></div>"
            if send_errors and invoice["status"] == "draft"
            else ""
        )
        email_panel = ""
        if invoice["status"] in ("draft", "sent", "overdue", "partially_paid"):
            default_body = invoice_email_body(conn, invoice_id)
            email_panel = f"""
<section class="panel wide">
  <h2>Email message</h2>
  <form method="post" action="/invoices/{invoice_id}/send">
    <label>Message written into the .eml file
      <textarea name="email_body" rows="12" required>{esc(default_body)}</textarea>
    </label>
    <p class="muted">Review this text before sending. The PDF invoice is attached automatically.</p>
    <button class="button" type="submit">{"Send invoice" if invoice["status"] == "draft" else "Resend invoice"}</button>
  </form>
</section>"""
        delete_button = f"""
<form method="post" action="/invoices/{invoice_id}/delete">
  <button class="button secondary" type="submit">Delete invoice</button>
</form>"""
        content = f"""
<section class="page-head">
  <div>
    <p class="eyebrow">Invoice detail</p>
    <h1>{esc(invoice['invoice_number'])}</h1>
  </div>
  <div class="actions">
    <a class="button secondary" href="/invoices/{invoice_id}/pdf" target="_blank">Preview PDF</a>
    <a class="button secondary" href="/invoices/{invoice_id}/excel">Export Excel</a>
    {delete_button}
  </div>
</section>

{error_box}

<section class="metrics">
  {metric("Total", dollars(invoice["total_inc_gst_cents"]))}
  {metric("Paid", dollars(invoice["amount_paid_cents"]), "success")}
  {metric("Outstanding", dollars(invoice["amount_outstanding_cents"]), "warn" if invoice["amount_outstanding_cents"] else "")}
  {metric("Status", invoice["status"].replace("_", " "))}
</section>

<section class="detail-grid">
  <div>
    <h2>Owner</h2>
    <p class="detail-main">{esc(invoice['owner_name'])}</p>
    <p>{esc(invoice['billing_email'])}</p>
  </div>
  <div>
    <h2>Dates</h2>
    <p>Issued {esc(invoice['issue_date'])}</p>
    <p>Due {esc(invoice['due_date'])}</p>
    <p>Period {esc(invoice['period_start'])} to {esc(invoice['period_end'])}</p>
  </div>
</section>

{email_panel}

<section class="table-wrap">
  <div class="section-title"><h2>Line items</h2><span>Snapshot</span></div>
  <table>
    <thead><tr><th>Date</th><th>Property</th><th>Description</th><th>Hours</th><th>Rate</th><th>GST</th><th>Amount</th></tr></thead>
    <tbody>{line_rows}</tbody>
  </table>
</section>

<section class="split">
  <div class="panel">
    <h2>Payment history</h2>
    <table><thead><tr><th>Date</th><th>Allocated</th><th>Type</th><th>Description</th></tr></thead><tbody>{payment_rows}</tbody></table>
  </div>
  <div class="panel">
    <h2>Email history</h2>
    <table><thead><tr><th>Time</th><th>To</th><th>Status</th><th>Subject</th><th>File</th></tr></thead><tbody>{email_rows}</tbody></table>
  </div>
</section>

<section class="table-wrap">
  <div class="section-title"><h2>Audit log</h2><span>Financial actions</span></div>
  <table><thead><tr><th>Time</th><th>Action</th><th>Reason</th></tr></thead><tbody>{audit_html}</tbody></table>
</section>
"""
        self.send_html(render_page(str(invoice["invoice_number"]), "invoices", content, flash=flash))

    def handle_invoice_pdf(self, path: str) -> None:
        invoice_id = int(path.strip("/").split("/")[1])
        with self.conn() as conn:
            data = invoice_pdf_bytes(conn, invoice_id)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def handle_email_outbox(self, path: str) -> None:
        filename = unquote(path.removeprefix("/email-outbox/"))
        if "/" in filename or "\\" in filename or not filename.endswith(".eml"):
            self.not_found()
            return
        provider_message_id = filename.removesuffix(".eml")
        with self.conn() as conn:
            email_event = conn.execute(
                "SELECT * FROM email_events WHERE provider_message_id = ?",
                (provider_message_id,),
            ).fetchone()
            if email_event:
                invoice = get_invoice(conn, int(email_event["invoice_id"]))
                if not invoice:
                    self.not_found()
                    return
                attachment_filename = f"{invoice['invoice_number']}.pdf"
                attachment_data = invoice_pdf_bytes(conn, int(invoice["id"]))
                if email_event["attachment_file_id"]:
                    attachment = conn.execute(
                        "SELECT * FROM files WHERE id = ?",
                        (email_event["attachment_file_id"],),
                    ).fetchone()
                    if attachment:
                        attachment_path = Path(attachment["storage_path"])
                        if attachment_path.exists() and attachment_path.is_file():
                            attachment_filename = attachment["filename"]
                            attachment_data = attachment_path.read_bytes()
                message = build_invoice_email_message(
                    settings=get_settings(conn),
                    invoice=invoice,
                    subject=email_event["subject"],
                    email_body=email_event["body"],
                    attachment_filename=attachment_filename,
                    attachment_data=attachment_data,
                    message_id=provider_message_id,
                )
                data = message.as_bytes()
                download_name = f"{invoice['invoice_number']}.eml"
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "message/rfc822")
                self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
        eml_path = EMAIL_DIR / filename
        if not eml_path.exists() or not eml_path.is_file():
            self.not_found()
            return
        data = eml_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "message/rfc822")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def handle_invoice_excel(self, path: str) -> None:
        invoice_id = int(path.strip("/").split("/")[1])
        with self.conn() as conn:
            invoice = get_invoice(conn, invoice_id)
            if not invoice:
                self.not_found()
                return
            data = invoice_excel_workbook_bytes(conn, invoice_id)
        filename = f"{invoice['invoice_number']}.xlsx"
        self.send_response(HTTPStatus.OK)
        self.send_header(
            "Content-Type",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def post_send_invoice(self, path: str) -> None:
        invoice_id = int(path.strip("/").split("/")[1])
        form = self.read_form()
        with self.conn() as conn:
            pdf_file_id = store_invoice_pdf(conn, invoice_id, INVOICE_DIR)
            body = form_value(form, "email_body") or invoice_email_body(conn, invoice_id)
            send_invoice(conn, invoice_id, pdf_file_id=pdf_file_id, email_body=body, email_dir=EMAIL_DIR)
        self.redirect(f"/invoices/{invoice_id}?flash={quote('Invoice spooled for email and finalized')}")

    def post_delete_invoice(self, path: str) -> None:
        invoice_id = int(path.strip("/").split("/")[1])
        with self.conn() as conn:
            result = delete_invoice(conn, invoice_id)
        message = (
            f"Deleted {result['invoice_number']}, reset numbering, "
            f"and removed {result['jobs_deleted']} source jobs"
        )
        self.redirect(f"/invoices?flash={quote(message)}")

    def handle_reconcile(self, query: dict[str, list[str]]) -> None:
        flash = query.get("flash", [None])[0]
        with self.conn() as conn:
            txs = list_reconciliation_transactions(conn)
            invoices = conn.execute(
                """
                SELECT i.*, o.name AS owner_name
                FROM invoices i
                JOIN owners o ON o.id = i.owner_id
                WHERE i.status IN ('sent', 'overdue', 'partially_paid')
                  AND i.amount_outstanding_cents > 0
                ORDER BY i.issue_date, i.id
                """
            ).fetchall()
        rows = ""
        for tx in txs:
            invoice_hint = (
                f"{tx['invoice_number']} · {tx['owner_name']} · {dollars(tx['amount_outstanding_cents'])}"
                if tx["invoice_number"]
                else ""
            )
            rows += f"""<tr>
  <td>{esc(tx['transaction_date'])}</td>
  <td>{esc(tx['description'])}<br><span class="muted">{esc(tx['match_reason'] or '')}</span></td>
  <td class="num strong">{dollars(tx['amount_cents'])}</td>
  <td>{esc(invoice_hint)}</td>
  <td>{status_pill(tx['status'])}</td>
  <td>{self.match_form(tx, invoices)}</td>
</tr>"""
        if not rows:
            rows = '<tr><td colspan="6" class="empty">No bank transactions imported yet.</td></tr>'
        content = f"""
<section class="page-head">
  <div>
    <p class="eyebrow">Payment matching</p>
    <h1>Reconcile</h1>
  </div>
  <div class="actions">
    <a class="button secondary" href="/export/bank-transactions.csv">Export bank transactions</a>
    <a class="button secondary" href="/export/payments.csv">Export payments</a>
  </div>
</section>

<section class="split">
  <form class="panel" method="post" action="/reconcile/import">
    <h2>Import bank CSV</h2>
    <p class="muted">Paste CSV with headers: date, description, credit, debit, balance, account.</p>
    <textarea name="csv_text" rows="10" spellcheck="false"></textarea>
    <button class="button" type="submit">Import and reconcile</button>
  </form>
  <div class="panel">
    <h2>Rules</h2>
    <p>Exact invoice reference plus credit amount auto-matches. Exact amount with a single owner or property hint is suggested. Ambiguous or debit transactions stay in review.</p>
  </div>
</section>

<section class="table-wrap">
  <table>
    <thead><tr><th>Date</th><th>Description</th><th>Amount</th><th>Suggested invoice</th><th>Status</th><th>Action</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</section>
"""
        self.send_html(render_page("Reconcile", "reconcile", content, flash=flash))

    def match_form(self, tx, invoices) -> str:
        if tx["status"] == "matched":
            return '<span class="muted">Matched</span>'
        options = "".join(
            f'<option value="{inv["id"]}" {"selected" if tx["suggested_invoice_id"] == inv["id"] else ""}>{esc(inv["invoice_number"])} · {esc(inv["owner_name"])} · {dollars(inv["amount_outstanding_cents"])}</option>'
            for inv in invoices
        )
        return f"""
<form class="inline-form" method="post" action="/reconcile/match">
  <input type="hidden" name="transaction_id" value="{tx['id']}">
  <select name="invoice_id">{options}</select>
  <input name="amount" placeholder="{dollars(tx['amount_cents']).replace('$', '')}">
  <input name="reason" placeholder="Reason">
  <button class="button small" type="submit">Approve</button>
</form>"""

    def post_bank_import(self) -> None:
        form = self.read_form()
        with self.conn() as conn:
            result = import_bank_csv(conn, form_value(form, "csv_text"), filename="pasted-bank.csv")
        message = (
            f"Imported {result['imported']} transactions, skipped {result['duplicates']} duplicates, "
            f"{result['errors']} errors"
        )
        self.redirect(f"/reconcile?flash={quote(message)}")

    def post_manual_match(self) -> None:
        form = self.read_form()
        tx_id = int(form_value(form, "transaction_id"))
        invoice_id = int(form_value(form, "invoice_id"))
        amount_text = form_value(form, "amount")
        amount = cents(amount_text) if amount_text else None
        reason = form_value(form, "reason") or "Manual match"
        with self.conn() as conn:
            manual_match_transaction(conn, tx_id, invoice_id, amount, reason)
        self.redirect(f"/reconcile?flash={quote('Payment matched')}")

    def handle_settings(self, query: dict[str, list[str]]) -> None:
        flash = query.get("flash", [None])[0]
        with self.conn() as conn:
            settings = get_settings(conn)
        content = f"""
<section class="page-head">
  <div>
    <p class="eyebrow">Configuration</p>
    <h1>Settings</h1>
  </div>
</section>

<form class="panel wide" method="post" action="/settings">
  <h2>Business details</h2>
  <div class="grid two">
    <label>Business name <input name="business_name" value="{esc(settings['business_name'])}" required></label>
    <label>ABN <input name="abn" value="{esc(settings['abn'])}" required></label>
    <label>Email <input name="email" type="email" value="{esc(settings['email'])}" required></label>
    <label>Phone <input name="phone" value="{esc(settings['phone'])}"></label>
    <label>Payment terms days <input name="default_payment_terms_days" inputmode="numeric" value="{esc(settings['default_payment_terms_days'])}"></label>
    <label>GST rate % <input name="default_gst_rate" inputmode="decimal" value="{int(settings['default_gst_rate_bps']) / 100:.2f}"></label>
    <label>BSB <input name="bsb" value="{esc(settings['bsb'])}"></label>
    <label>Account number <input name="account_number" value="{esc(settings['account_number'])}"></label>
    <label>Account name <input name="account_name" value="{esc(settings['account_name'])}"></label>
    <label>PayID <input name="payid" value="{esc(settings['payid'])}"></label>
    <label>Brand accent <input name="brand_accent" value="{esc(settings['brand_accent'])}"></label>
  </div>
  <label>Address <textarea name="address" rows="2">{esc(settings['address'])}</textarea></label>
  <label>Invoice footer <textarea name="invoice_footer" rows="3">{esc(settings['invoice_footer'])}</textarea></label>
  <button class="button" type="submit">Save settings</button>
</form>

<section class="table-wrap">
  <div class="section-title"><h2>Exports</h2><span>Accountant-ready CSV</span></div>
  <div class="export-list">
    <a href="/export/jobs.csv">Jobs</a>
    <a href="/export/invoices.csv">Invoices</a>
    <a href="/export/payments.csv">Payments</a>
    <a href="/export/bank-transactions.csv">Bank transactions</a>
    <a href="/export/gst-summary.csv">GST summary</a>
  </div>
</section>
"""
        self.send_html(render_page("Settings", "settings", content, flash=flash))

    def post_settings(self) -> None:
        form = self.read_form()
        simple = {key: value[0] for key, value in form.items()}
        with self.conn() as conn:
            save_settings(conn, simple)
        self.redirect(f"/settings?flash={quote('Settings saved')}")

    def handle_export(self, path: str) -> None:
        kind = path.removeprefix("/export/").removesuffix(".csv")
        with self.conn() as conn:
            csv_text = export_csv(conn, kind)
        data = csv_text.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{kind}.csv"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def not_found(self) -> None:
        self.send_html(
            render_page("Not found", "", "<section class='page-head'><h1>Not found</h1></section>"),
            status=HTTPStatus.NOT_FOUND,
        )

    def error_page(self, exc: Exception) -> None:
        content = f"""
<section class="page-head">
  <div>
    <p class="eyebrow">Error</p>
    <h1>Something needs attention</h1>
  </div>
</section>
<div class="callout danger">{esc(exc)}</div>
"""
        self.send_html(render_page("Error", "", content), status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def api_error(self, exc: Exception) -> None:
        status = HTTPStatus.BAD_REQUEST if isinstance(exc, ValueError) else HTTPStatus.INTERNAL_SERVER_ERROR
        self.send_json({"error": str(exc)}, status=status)


def run(
    host: str,
    port: int,
    *,
    desktop_static_dir: Path | None = None,
    desktop_token: str | None = None,
) -> None:
    global DESKTOP_STATIC_DIR, DESKTOP_TOKEN
    DESKTOP_STATIC_DIR = desktop_static_dir.resolve() if desktop_static_dir else None
    DESKTOP_TOKEN = desktop_token
    with connect() as conn:
        init_database(conn)
        purge_placeholder_import_properties(conn)
        purge_inactive_uninvoiced_properties(conn)
        conn.commit()
        update_overdue_statuses(conn)
    if DESKTOP_TOKEN:
        backup_database()
    server = ThreadingHTTPServer((host, port), CleanLedgerHandler)
    actual_host, actual_port = server.server_address[:2]
    if DESKTOP_STATIC_DIR or DESKTOP_TOKEN:
        print(
            json.dumps(
                {
                    "event": "ready",
                    "host": actual_host,
                    "port": actual_port,
                    "url": f"http://{actual_host}:{actual_port}",
                }
            ),
            flush=True,
        )
    else:
        print(f"Domestica Invoice running at http://{actual_host}:{actual_port}", flush=True)
    server.serve_forever()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--reload-demo", action="store_true")
    parser.add_argument("--desktop-static-dir", type=Path)
    parser.add_argument("--desktop-token")
    args = parser.parse_args(argv)
    with connect() as conn:
        if args.reload_demo:
            reset_database(conn)
        init_database(conn)
        if args.seed:
            seed_demo(conn)
    run(
        args.host,
        args.port,
        desktop_static_dir=args.desktop_static_dir,
        desktop_token=args.desktop_token,
    )


if __name__ == "__main__":
    main()
