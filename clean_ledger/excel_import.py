from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from clean_ledger.services import (
    GST_BPS,
    cents,
    create_job,
    normalize,
    resolve_import_owner_property,
)


IGNORED_PROPERTY_LABELS = {"", "extras", "extra", "total", "totals"}


@dataclass
class PastedInvoiceRow:
    service_date: str
    property_label: str
    hours: str
    rate_inc_gst_cents: int
    income_cents: int


def excel_serial_to_date(value: str | int | float | Decimal) -> str:
    serial = int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return (date(1899, 12, 30) + timedelta(days=serial)).isoformat()


def parse_sheet_date(value: str) -> str:
    raw = value.strip()
    if not raw:
        raise ValueError("Date is required")
    try:
        Decimal(raw)
    except Exception:
        pass
    else:
        return excel_serial_to_date(raw)
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return date.fromisoformat(raw).isoformat()


def rate_ex_gst_from_inc_gst(rate_inc_gst_cents: int, gst_bps: int = GST_BPS) -> int:
    return int(
        (
            Decimal(rate_inc_gst_cents)
            * Decimal(10000)
            / Decimal(10000 + gst_bps)
        ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )


def property_title(label: str) -> str:
    parts = []
    for part in label.strip().split():
        parts.append(part.upper() if len(part) <= 2 else part.capitalize())
    return " ".join(parts)


def _dialect(text: str) -> csv.Dialect:
    if "\t" in text:
        class TabDialect(csv.excel):
            delimiter = "\t"

        return TabDialect
    try:
        return csv.Sniffer().sniff(text, delimiters=",\t;")
    except csv.Error:
        return csv.excel


def _header_key(header: str) -> str:
    return normalize(header).replace(" ", "_")


def parse_excel_paste(text: str) -> tuple[list[PastedInvoiceRow], list[dict[str, str]]]:
    cleaned = text.strip()
    if not cleaned:
        return [], []
    reader = csv.DictReader(io.StringIO(cleaned), dialect=_dialect(cleaned))
    field_map = {_header_key(field or ""): field for field in reader.fieldnames or []}

    def field(*names: str) -> str:
        for name in names:
            if name in field_map:
                return field_map[name]
        raise ValueError(f"Missing required column: {names[0].replace('_', ' ')}")

    date_col = field("date")
    property_col = field("property")
    hours_col = field("hr", "hours")
    rate_col = field("rate_incl_gst", "rate_incl", "rate")
    income_col = field("income", "amount")

    parsed: list[PastedInvoiceRow] = []
    errors: list[dict[str, str]] = []
    for row_number, row in enumerate(reader, start=2):
        property_label = (row.get(property_col) or "").strip()
        normalized_property = normalize(property_label)
        if normalized_property in IGNORED_PROPERTY_LABELS:
            continue
        try:
            parsed.append(
                PastedInvoiceRow(
                    service_date=parse_sheet_date(row.get(date_col, "")),
                    property_label=property_label,
                    hours=str(Decimal(str(row.get(hours_col, "")).strip())),
                    rate_inc_gst_cents=cents(row.get(rate_col)),
                    income_cents=cents(row.get(income_col)),
                )
            )
        except Exception as exc:
            errors.append(
                {
                    "row_number": str(row_number),
                    "property": property_label,
                    "error": str(exc),
                }
            )
    return parsed, errors


def import_excel_paste_jobs(conn, text: str, filename: str = "excel-paste") -> dict[str, object]:
    rows, parse_errors = parse_excel_paste(text)
    imported = duplicates = 0
    errors = list(parse_errors)
    for row in rows:
        try:
            title = property_title(row.property_label)
            rate_ex = rate_ex_gst_from_inc_gst(row.rate_inc_gst_cents)
            owner_id, property_id = resolve_import_owner_property(
                conn,
                property_label=row.property_label,
                property_address=title,
                property_nickname=title,
                default_rate_ex_gst_cents=rate_ex,
                default_description="Airbnb Cleaning",
                create_missing=False,
            )
            _, status = create_job(
                conn,
                owner_id=owner_id,
                property_id=property_id,
                service_date=row.service_date,
                description="Airbnb Cleaning",
                hours=row.hours,
                rate_ex_gst_cents=rate_ex_gst_from_inc_gst(row.rate_inc_gst_cents),
                internal_note=f"Imported from {filename}; property label: {row.property_label}; income inc GST: {row.income_cents}",
            )
            if status == "duplicate":
                duplicates += 1
            else:
                imported += 1
        except Exception as exc:
            errors.append(
                {
                    "row_number": "",
                    "property": row.property_label,
                    "error": str(exc),
                }
            )
    conn.commit()
    return {
        "row_count": len(rows) + len(parse_errors),
        "imported": imported,
        "duplicates": duplicates,
        "errors": len(errors),
        "error_rows": errors,
    }


def import_excel_rows_jobs(conn, raw_rows: list[dict[str, object]], filename: str = "xlsx-upload") -> dict[str, object]:
    output = io.StringIO()
    writer = csv.writer(output, dialect=csv.excel_tab)
    writer.writerow(["Date", "property", "Hr", "Rate incl.GST", "Income"])
    for row in raw_rows:
        writer.writerow(
            [
                row.get("Date") or row.get("date") or "",
                row.get("property") or row.get("Property") or "",
                row.get("Hr") or row.get("hr") or row.get("hours") or "",
                row.get("Rate incl.GST")
                or row.get("Rate incl GST")
                or row.get("rate_incl_gst")
                or row.get("rate")
                or "",
                row.get("Income") or row.get("income") or row.get("amount") or "",
            ]
        )
    return import_excel_paste_jobs(conn, output.getvalue(), filename=filename)
