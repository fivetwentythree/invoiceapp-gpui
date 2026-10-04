from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from difflib import SequenceMatcher
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from pathlib import Path
from typing import Any


GST_BPS = 1000
LINE_TYPE_JOB = "job"
LINE_TYPE_MANUAL = "manual"


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def cents(value: str | int | float | Decimal | None) -> int:
    if value in (None, ""):
        return 0
    try:
        amount = Decimal(str(value).replace("$", "").replace(",", "").strip())
    except (InvalidOperation, AttributeError):
        raise ValueError(f"Invalid money value: {value!r}") from None
    return int((amount * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def dollars(cents_value: int | None) -> str:
    cents_value = int(cents_value or 0)
    sign = "-" if cents_value < 0 else ""
    cents_abs = abs(cents_value)
    return f"{sign}${cents_abs // 100:,}.{cents_abs % 100:02d}"


def update_open_invoice_due_dates_for_owner(conn, owner_id: int, terms_days: int) -> int:
    invoices = conn.execute(
        """
        SELECT id, issue_date, due_date
        FROM invoices
        WHERE owner_id = ?
          AND status IN ('draft', 'sent', 'overdue', 'partially_paid')
          AND amount_outstanding_cents > 0
        """,
        (owner_id,),
    ).fetchall()
    updated = 0
    for invoice in invoices:
        due_date = (date.fromisoformat(invoice["issue_date"]) + timedelta(days=terms_days)).isoformat()
        if due_date == invoice["due_date"]:
            continue
        conn.execute(
            """
            UPDATE invoices
            SET due_date = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (due_date, invoice["id"]),
        )
        audit(
            conn,
            "invoice",
            int(invoice["id"]),
            "invoice_due_date_changed",
            old={"due_date": invoice["due_date"]},
            new={"due_date": due_date, "payment_terms_days": terms_days},
        )
        updated += 1
    return updated


def decimal_hours(value: str | int | float | Decimal) -> Decimal:
    try:
        hours = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError):
        raise ValueError(f"Invalid hours value: {value!r}") from None
    if hours <= 0:
        raise ValueError("Hours must be positive")
    return hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calculate_totals(hours: str | Decimal, rate_ex_gst_cents: int, gst_rate_bps: int = GST_BPS) -> dict[str, int]:
    hours_decimal = decimal_hours(hours)
    amount_ex = int(
        (hours_decimal * Decimal(rate_ex_gst_cents)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
    gst = int(
        (Decimal(amount_ex) * Decimal(gst_rate_bps) / Decimal(10000)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )
    return {
        "amount_ex_gst_cents": amount_ex,
        "gst_amount_cents": gst,
        "amount_inc_gst_cents": amount_ex + gst,
    }


def gst_rate_bps_from_percent(value: str | int | float | Decimal | None, *, default_bps: int = GST_BPS) -> int:
    if value in (None, ""):
        return int(default_bps)
    try:
        rate = Decimal(str(value).replace("%", "").strip())
    except (InvalidOperation, AttributeError):
        raise ValueError(f"Invalid GST rate: {value!r}") from None
    bps = int((rate * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if bps < 0:
        raise ValueError("GST rate cannot be negative")
    return bps


def calculate_inclusive_totals(amount_inc_gst_cents: int, gst_rate_bps: int = GST_BPS) -> dict[str, int]:
    amount_inc = int(amount_inc_gst_cents)
    if amount_inc <= 0:
        raise ValueError("Amount must be positive")
    if int(gst_rate_bps) < 0:
        raise ValueError("GST rate cannot be negative")
    amount_ex = int(
        (
            Decimal(amount_inc)
            * Decimal(10000)
            / Decimal(10000 + int(gst_rate_bps))
        ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )
    gst = amount_inc - amount_ex
    return {
        "amount_ex_gst_cents": amount_ex,
        "gst_amount_cents": gst,
        "amount_inc_gst_cents": amount_inc,
    }


def month_bounds(month: str) -> tuple[str, str]:
    start = date.fromisoformat(f"{month}-01")
    if start.month == 12:
        next_month = date(start.year + 1, 1, 1)
    else:
        next_month = date(start.year, start.month + 1, 1)
    end = next_month - timedelta(days=1)
    return start.isoformat(), end.isoformat()


def normalize(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def normalized_tokens(text: str | None) -> set[str]:
    stop_words = {
        "a",
        "an",
        "and",
        "au",
        "australia",
        "bank",
        "banking",
        "cba",
        "commonwealth",
        "commbank",
        "account",
        "credit",
        "deposit",
        "direct",
        "fast",
        "from",
        "internet",
        "invoice",
        "netbank",
        "osko",
        "pay",
        "payment",
        "pty",
        "reference",
        "ref",
        "thank",
        "the",
        "to",
        "transfer",
        "value",
        "you",
        "mr",
        "mrs",
        "ms",
        "miss",
        "dr",
    }
    return {
        token
        for token in normalize(text).split()
        if len(token) >= 3 and token not in stop_words
    }


def token_similarity(left: str | None, right: str | None) -> float:
    left_norm = normalize(left)
    right_norm = normalize(right)
    if not left_norm or not right_norm:
        return 0.0
    left_tokens = normalized_tokens(left_norm)
    right_tokens = normalized_tokens(right_norm)
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = len(left_tokens & right_tokens) / max(len(right_tokens), 1)
    fuzzy_overlap = sum(
        1
        for right_token in right_tokens
        if any(tokens_are_similar(left_token, right_token) for left_token in left_tokens)
    ) / max(len(right_tokens), 1)
    sequence = SequenceMatcher(None, left_norm, right_norm).ratio()
    return max(overlap, fuzzy_overlap, sequence)


def tokens_are_similar(left: str, right: str) -> bool:
    if left == right:
        return True
    if left.isdigit() or right.isdigit():
        return False
    if min(len(left), len(right)) >= 4 and (left.startswith(right) or right.startswith(left)):
        return True
    return SequenceMatcher(None, left, right).ratio() >= 0.88


def duplicate_job_key(
    service_date: str, property_id: int, description: str, hours: str, rate_ex_gst_cents: int
) -> str:
    raw = "|".join(
        [
            service_date,
            str(property_id),
            normalize(description),
            str(decimal_hours(hours)),
            str(rate_ex_gst_cents),
        ]
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def transaction_hash(
    account_identifier: str | None,
    transaction_date: str,
    amount_cents: int,
    description: str,
    balance_cents: int | None,
) -> str:
    raw = "|".join(
        [
            normalize(account_identifier),
            transaction_date,
            str(amount_cents),
            normalize(description),
            "" if balance_cents is None else str(balance_cents),
        ]
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def parse_invoice_number_seed(value: str | None) -> tuple[str, int, int] | None:
    text = (value or "").strip()
    if not text:
        return None
    match = re.match(r"^(.*?)(\d+)$", text)
    if not match:
        raise ValueError("Invoice number must end with digits, e.g. STSB070")
    prefix, number_text = match.groups()
    return prefix, int(number_text), len(number_text)


def format_invoice_number(prefix: str | None, number: int | None, padding: int | None) -> str:
    number = int(number or 0)
    padding = int(padding or 0)
    return f"{prefix or ''}{number:0{padding}d}"


def next_available_invoice_number(
    conn,
    *,
    prefix: str,
    number: int,
    padding: int,
    exclude_invoice_id: int | None = None,
) -> tuple[str, int]:
    while True:
        invoice_number = format_invoice_number(prefix, number, padding)
        params: list[Any] = [invoice_number]
        where = "invoice_number = ?"
        if exclude_invoice_id is not None:
            where += " AND id != ?"
            params.append(exclude_invoice_id)
        exists = conn.execute(f"SELECT 1 FROM invoices WHERE {where}", params).fetchone()
        if not exists:
            return invoice_number, number
        number += 1


def audit(
    conn,
    entity_type: str,
    entity_id: int,
    action: str,
    *,
    old: Any = None,
    new: Any = None,
    reason: str | None = None,
    user_id: str = "system",
) -> None:
    conn.execute(
        """
        INSERT INTO audit_log (
            entity_type, entity_id, action, old_value_json, new_value_json, reason, user_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entity_type,
            entity_id,
            action,
            json.dumps(old, default=str) if old is not None else None,
            json.dumps(new, default=str) if new is not None else None,
            reason,
            user_id,
        ),
    )


def get_settings(conn) -> Any:
    return conn.execute("SELECT * FROM business_settings WHERE id = 1").fetchone()


def save_settings(conn, form: dict[str, str]) -> None:
    before = dict(get_settings(conn))
    conn.execute(
        """
        UPDATE business_settings
        SET business_name = ?, abn = ?, email = ?, phone = ?, address = ?,
            default_payment_terms_days = ?, default_gst_rate_bps = ?, bsb = ?,
            account_number = ?, account_name = ?, payid = ?, invoice_number_prefix = ?,
            invoice_footer = ?, brand_accent = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = 1
        """,
        (
            form.get("business_name", "").strip(),
            form.get("abn", "").strip(),
            form.get("email", "").strip(),
            form.get("phone", "").strip(),
            form.get("address", "").strip(),
            int(form.get("default_payment_terms_days") or 14),
            int(Decimal(form.get("default_gst_rate", "10")) * 100),
            form.get("bsb", "").strip(),
            form.get("account_number", "").strip(),
            form.get("account_name", "").strip(),
            form.get("payid", "").strip(),
            form.get("invoice_number_prefix", before["invoice_number_prefix"]).strip()
            or before["invoice_number_prefix"],
            form.get("invoice_footer", "").strip(),
            form.get("brand_accent", before["brand_accent"]).strip() or before["brand_accent"],
        ),
    )
    audit(conn, "business_settings", 1, "settings_changed", old=before, new=dict(get_settings(conn)))
    conn.commit()


def find_or_create_owner(
    conn,
    name: str,
    *,
    billing_email: str | None = None,
    phone: str | None = None,
    abn: str | None = None,
    billing_address_line_1: str | None = None,
    default_payment_terms_days: int | None = None,
    default_rate_ex_gst_cents: int = 0,
) -> int:
    name = name.strip()
    if not name:
        raise ValueError("Owner name is required")
    existing = conn.execute(
        "SELECT id FROM owners WHERE lower(name) = lower(?)",
        (name,),
    ).fetchone()
    if existing:
        before = conn.execute("SELECT * FROM owners WHERE id = ?", (existing["id"],)).fetchone()
        conn.execute(
            """
            UPDATE owners
            SET billing_email = COALESCE(NULLIF(?, ''), billing_email),
                phone = COALESCE(NULLIF(?, ''), phone),
                abn = COALESCE(NULLIF(?, ''), abn),
                billing_address_line_1 = COALESCE(NULLIF(?, ''), billing_address_line_1),
                default_payment_terms_days = COALESCE(?, default_payment_terms_days),
                default_rate_ex_gst_cents = CASE
                    WHEN ? > 0 THEN ?
                    ELSE default_rate_ex_gst_cents
                END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                (billing_email or "").strip(),
                (phone or "").strip(),
                (abn or "").strip(),
                (billing_address_line_1 or "").strip(),
                default_payment_terms_days,
                default_rate_ex_gst_cents,
                default_rate_ex_gst_cents,
                existing["id"],
            ),
        )
        if (
            default_payment_terms_days is not None
            and int(default_payment_terms_days) != int(before["default_payment_terms_days"] or 0)
        ):
            update_open_invoice_due_dates_for_owner(
                conn,
                int(existing["id"]),
                int(default_payment_terms_days),
            )
        return int(existing["id"])
    cur = conn.execute(
        """
        INSERT INTO owners (
            name, billing_email, phone, abn, billing_address_line_1,
            default_payment_terms_days, default_rate_ex_gst_cents
        )
        VALUES (?, ?, ?, ?, ?, COALESCE(?, 14), ?)
        """,
        (
            name,
            (billing_email or "").strip(),
            (phone or "").strip(),
            (abn or "").strip(),
            (billing_address_line_1 or "").strip(),
            default_payment_terms_days,
            default_rate_ex_gst_cents,
        ),
    )
    owner_id = int(cur.lastrowid)
    audit(conn, "owner", owner_id, "owner_created", new={"name": name})
    return owner_id


def find_property_by_labels(conn, *property_labels: str):
    labels = {normalize(property_label) for property_label in property_labels}
    labels.discard("")
    if not labels:
        return None
    properties = conn.execute(
        """
        SELECT p.*, o.id AS owner_id
        FROM properties p
        JOIN owners o ON o.id = p.owner_id
        WHERE p.active = 1
        ORDER BY p.id
        """
    ).fetchall()
    for prop in properties:
        candidates = property_match_candidates(prop)
        if any(normalize(candidate) in labels for candidate in candidates):
            return prop

    alias_matches = {}
    for prop in properties:
        candidates = property_match_candidates(prop)
        if any(
            is_safe_property_alias(label, normalize(candidate))
            for label in labels
            for candidate in candidates
        ):
            alias_matches[int(prop["id"])] = prop
    if len(alias_matches) == 1:
        return next(iter(alias_matches.values()))
    return None


def find_property_by_label(conn, property_label: str):
    return find_property_by_labels(conn, property_label)


def property_match_candidates(prop) -> list[str]:
    return list(
        dict.fromkeys(
            [
                prop["nickname"],
                prop["address_line_1"],
                (prop["nickname"] or "").replace(".", ""),
                (prop["address_line_1"] or "").replace(".", ""),
            ]
        )
    )


def is_safe_property_alias(label: str, candidate: str) -> bool:
    if not label or not candidate or label == candidate:
        return False
    if len(candidate) >= 3 and label.startswith(f"{candidate} "):
        return True
    if len(label) >= 3 and candidate.startswith(f"{label} "):
        return True
    return False


def resolve_import_owner_property(
    conn,
    *,
    property_label: str,
    owner_name: str | None = None,
    owner_email: str | None = None,
    property_address: str | None = None,
    property_nickname: str | None = None,
    default_rate_ex_gst_cents: int = 0,
    default_description: str | None = None,
    create_missing: bool = True,
) -> tuple[int, int]:
    label = (property_label or "").strip()
    address = (property_address or "").strip()
    nickname = (property_nickname or "").strip()
    existing_property = find_property_by_labels(conn, label, address, nickname)
    if existing_property:
        return int(existing_property["owner_id"]), int(existing_property["id"])

    property_name = address or nickname or label
    if not property_name:
        raise ValueError("Property is required")
    if not create_missing:
        raise ValueError(f"Unknown property '{property_name}'. Add it in Owners before importing.")
    owner = (owner_name or "").strip()
    if not owner:
        raise ValueError(f"Owner name is required to create property '{property_name}'")
    owner_id = find_or_create_owner(
        conn,
        owner,
        billing_email=(owner_email or "").strip(),
        default_rate_ex_gst_cents=default_rate_ex_gst_cents,
    )
    property_id = find_or_create_property(
        conn,
        owner_id,
        property_name,
        nickname=nickname or property_name,
        default_rate_ex_gst_cents=default_rate_ex_gst_cents,
        default_description=(default_description or "").strip(),
    )
    return owner_id, property_id


def update_owner_details(
    conn,
    owner_id: int,
    *,
    name: str | None = None,
    billing_email: str | None = None,
    phone: str | None = None,
    abn: str | None = None,
    billing_address_line_1: str | None = None,
    default_payment_terms_days: int | None = None,
    default_rate_ex_gst_cents: int = 0,
) -> None:
    before = conn.execute("SELECT * FROM owners WHERE id = ?", (owner_id,)).fetchone()
    if not before:
        raise ValueError("Owner not found")
    conn.execute(
        """
        UPDATE owners
        SET name = COALESCE(NULLIF(?, ''), name),
            billing_email = COALESCE(NULLIF(?, ''), billing_email),
            phone = COALESCE(NULLIF(?, ''), phone),
            abn = COALESCE(NULLIF(?, ''), abn),
            billing_address_line_1 = COALESCE(NULLIF(?, ''), billing_address_line_1),
            default_payment_terms_days = COALESCE(?, default_payment_terms_days),
            default_rate_ex_gst_cents = CASE
                WHEN ? > 0 THEN ?
                ELSE default_rate_ex_gst_cents
            END,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            (name or "").strip(),
            (billing_email or "").strip(),
            (phone or "").strip(),
            (abn or "").strip(),
            (billing_address_line_1 or "").strip(),
            default_payment_terms_days,
            default_rate_ex_gst_cents,
            default_rate_ex_gst_cents,
            owner_id,
        ),
    )
    after = conn.execute("SELECT * FROM owners WHERE id = ?", (owner_id,)).fetchone()
    if (
        default_payment_terms_days is not None
        and int(default_payment_terms_days) != int(before["default_payment_terms_days"] or 0)
    ):
        update_open_invoice_due_dates_for_owner(conn, owner_id, int(default_payment_terms_days))
    audit(conn, "owner", owner_id, "owner_updated", old=dict(before), new=dict(after))


def find_or_create_property(
    conn,
    owner_id: int,
    address_line_1: str,
    *,
    nickname: str | None = None,
    default_rate_ex_gst_cents: int | None = None,
    default_description: str | None = None,
    invoice_number_seed: str | None = None,
) -> int:
    address = address_line_1.strip()
    nickname = (nickname or "").strip()
    if not (address or nickname):
        raise ValueError("Property is required")
    invoice_seed = parse_invoice_number_seed(invoice_number_seed)
    existing = conn.execute(
        """
        SELECT id FROM properties
        WHERE owner_id = ?
          AND active = 1
          AND lower(address_line_1) = lower(?)
          AND lower(COALESCE(nickname, '')) = lower(COALESCE(?, ''))
        """,
        (owner_id, address, nickname),
    ).fetchone()
    if existing:
        if invoice_seed:
            conn.execute(
                """
                UPDATE properties
                SET invoice_number_prefix = ?,
                    next_invoice_number = ?,
                    invoice_number_padding = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (*invoice_seed, existing["id"]),
            )
        return int(existing["id"])
    cur = conn.execute(
        """
        INSERT INTO properties (
            owner_id, address_line_1, nickname, default_rate_ex_gst_cents, default_description,
            invoice_number_prefix, next_invoice_number, invoice_number_padding
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, COALESCE(?, 3))
        """,
        (
            owner_id,
            address,
            nickname,
            default_rate_ex_gst_cents,
            default_description,
            invoice_seed[0] if invoice_seed else None,
            invoice_seed[1] if invoice_seed else None,
            invoice_seed[2] if invoice_seed else None,
        ),
    )
    property_id = int(cur.lastrowid)
    audit(conn, "property", property_id, "property_created", new={"address": address})
    return property_id


def update_property_details(
    conn,
    property_id: int,
    *,
    address_line_1: str | None = None,
    nickname: str | None = None,
    default_rate_ex_gst_cents: int | None = None,
    default_description: str | None = None,
    invoice_number_seed: str | None = None,
) -> None:
    before = conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
    if not before:
        raise ValueError("Property not found")
    invoice_seed = parse_invoice_number_seed(invoice_number_seed)
    conn.execute(
        """
        UPDATE properties
        SET address_line_1 = COALESCE(NULLIF(?, ''), address_line_1),
            nickname = COALESCE(NULLIF(?, ''), nickname),
            default_rate_ex_gst_cents = COALESCE(?, default_rate_ex_gst_cents),
            default_description = COALESCE(NULLIF(?, ''), default_description),
            invoice_number_prefix = CASE WHEN ? THEN ? ELSE invoice_number_prefix END,
            next_invoice_number = CASE WHEN ? THEN ? ELSE next_invoice_number END,
            invoice_number_padding = CASE WHEN ? THEN ? ELSE invoice_number_padding END,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            (address_line_1 or "").strip(),
            (nickname or "").strip(),
            default_rate_ex_gst_cents,
            (default_description or "").strip(),
            1 if invoice_seed else 0,
            invoice_seed[0] if invoice_seed else None,
            1 if invoice_seed else 0,
            invoice_seed[1] if invoice_seed else None,
            1 if invoice_seed else 0,
            invoice_seed[2] if invoice_seed else None,
            property_id,
        ),
    )
    after = conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
    if invoice_seed:
        renumber_property_invoices(
            conn,
            property_id,
            prefix=invoice_seed[0],
            number=invoice_seed[1],
            padding=invoice_seed[2],
        )
        after = conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
    audit(conn, "property", property_id, "property_updated", old=dict(before), new=dict(after))


def remove_property(conn, property_id: int) -> None:
    before = conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
    if not before:
        raise ValueError("Property not found")
    open_jobs = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM cleaning_jobs
        WHERE property_id = ?
          AND invoice_id IS NULL
          AND status IN ('unbilled', 'draft_invoice')
        """,
        (property_id,),
    ).fetchone()
    if int(open_jobs["count"] or 0):
        raise ValueError(
            "This property has unbilled jobs. Edit the property details instead of removing it."
        )
    conn.execute(
        """
        UPDATE properties
        SET active = 0,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (property_id,),
    )
    after = conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
    audit(conn, "property", property_id, "property_removed", old=dict(before), new=dict(after))


def purge_inactive_uninvoiced_properties(conn) -> dict[str, int]:
    properties = conn.execute(
        """
        SELECT p.*
        FROM properties p
        WHERE p.active = 0
          AND NOT EXISTS (
              SELECT 1
              FROM cleaning_jobs j
              JOIN invoice_line_items li ON li.cleaning_job_id = j.id
              WHERE j.property_id = p.id
          )
        ORDER BY p.id
        """
    ).fetchall()
    purged_jobs = 0
    purged_properties = 0
    for prop in properties:
        jobs = conn.execute(
            "SELECT id FROM cleaning_jobs WHERE property_id = ? AND invoice_id IS NULL",
            (prop["id"],),
        ).fetchall()
        purged_jobs += len(jobs)
        conn.execute(
            "DELETE FROM cleaning_jobs WHERE property_id = ? AND invoice_id IS NULL",
            (prop["id"],),
        )
        conn.execute("DELETE FROM properties WHERE id = ?", (prop["id"],))
        audit(
            conn,
            "property",
            int(prop["id"]),
            "property_purged",
            old=dict(prop),
            new={"deleted_uninvoiced_jobs": len(jobs)},
        )
        purged_properties += 1

    owner_rows = conn.execute(
        """
        SELECT o.*
        FROM owners o
        WHERE o.active = 1
          AND NOT EXISTS (SELECT 1 FROM properties p WHERE p.owner_id = o.id AND p.active = 1)
          AND NOT EXISTS (SELECT 1 FROM cleaning_jobs j WHERE j.owner_id = o.id)
          AND NOT EXISTS (SELECT 1 FROM invoices i WHERE i.owner_id = o.id)
        ORDER BY o.id
        """
    ).fetchall()
    for owner in owner_rows:
        conn.execute(
            """
            UPDATE owners
            SET active = 0,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (owner["id"],),
        )
        after = conn.execute("SELECT * FROM owners WHERE id = ?", (owner["id"],)).fetchone()
        audit(conn, "owner", int(owner["id"]), "owner_deactivated", old=dict(owner), new=dict(after))

    return {
        "properties": purged_properties,
        "jobs": purged_jobs,
        "owners_deactivated": len(owner_rows),
    }


def is_workbook_import_note(value: str | None) -> bool:
    note = (value or "").strip().lower()
    return note.startswith("imported from ") and "property label:" in note


def purge_placeholder_import_properties(conn) -> dict[str, int]:
    properties = conn.execute(
        """
        SELECT p.*, o.name AS owner_name, o.billing_email
        FROM properties p
        JOIN owners o ON o.id = p.owner_id
        WHERE NOT EXISTS (
              SELECT 1
              FROM invoice_line_items li
              WHERE li.property_id = p.id
          )
          AND NOT EXISTS (
              SELECT 1
              FROM invoices i
              WHERE i.owner_id = o.id
          )
        ORDER BY p.id
        """
    ).fetchall()
    purged_jobs = 0
    purged_properties = 0
    owners_deleted = 0
    for prop in properties:
        property_label = prop["nickname"] or prop["address_line_1"]
        if normalize(prop["owner_name"]) != normalize(f"{property_label} Owner"):
            continue
        if (prop["billing_email"] or "").strip():
            continue

        jobs = conn.execute(
            "SELECT * FROM cleaning_jobs WHERE property_id = ?",
            (prop["id"],),
        ).fetchall()
        if not jobs:
            continue
        if any(job["invoice_id"] is not None or job["status"] != "unbilled" for job in jobs):
            continue
        if any(not is_workbook_import_note(job["internal_note"]) for job in jobs):
            continue

        conn.execute("DELETE FROM cleaning_jobs WHERE property_id = ?", (prop["id"],))
        conn.execute("DELETE FROM properties WHERE id = ?", (prop["id"],))
        audit(
            conn,
            "property",
            int(prop["id"]),
            "placeholder_property_purged",
            old=dict(prop),
            new={"deleted_imported_jobs": len(jobs)},
        )
        purged_jobs += len(jobs)
        purged_properties += 1

        owner = conn.execute("SELECT * FROM owners WHERE id = ?", (prop["owner_id"],)).fetchone()
        if not owner:
            continue
        has_references = conn.execute(
            """
            SELECT
                EXISTS(SELECT 1 FROM properties WHERE owner_id = ?) AS has_properties,
                EXISTS(SELECT 1 FROM cleaning_jobs WHERE owner_id = ?) AS has_jobs,
                EXISTS(SELECT 1 FROM invoices WHERE owner_id = ?) AS has_invoices
            """,
            (prop["owner_id"], prop["owner_id"], prop["owner_id"]),
        ).fetchone()
        if any(int(has_references[key] or 0) for key in has_references.keys()):
            continue
        audit(
            conn,
            "owner",
            int(owner["id"]),
            "placeholder_owner_purged",
            old=dict(owner),
            new={"reason": "placeholder workbook import owner"},
        )
        conn.execute("DELETE FROM owners WHERE id = ?", (owner["id"],))
        owners_deleted += 1

    return {
        "properties": purged_properties,
        "jobs": purged_jobs,
        "owners_deleted": owners_deleted,
    }


def create_job(
    conn,
    *,
    owner_id: int,
    property_id: int,
    service_date: str,
    description: str,
    hours: str,
    rate_ex_gst_cents: int,
    gst_rate_bps: int = GST_BPS,
    internal_note: str | None = None,
    import_id: int | None = None,
) -> tuple[int | None, str]:
    if not service_date:
        raise ValueError("Service date is required")
    date.fromisoformat(service_date)
    description = description.strip()
    if not description:
        raise ValueError("Description is required")
    if rate_ex_gst_cents <= 0:
        raise ValueError("Rate must be positive")
    hours_decimal = decimal_hours(hours)
    totals = calculate_totals(hours_decimal, rate_ex_gst_cents, gst_rate_bps)
    duplicate_key = duplicate_job_key(
        service_date, property_id, description, str(hours_decimal), rate_ex_gst_cents
    )
    try:
        cur = conn.execute(
            """
            INSERT INTO cleaning_jobs (
                owner_id, property_id, service_date, description, hours, rate_ex_gst_cents,
                gst_rate_bps, amount_ex_gst_cents, gst_amount_cents, amount_inc_gst_cents,
                status, invoice_id, import_id, internal_note, duplicate_key
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'unbilled', NULL, ?, ?, ?)
            """,
            (
                owner_id,
                property_id,
                service_date,
                description,
                str(hours_decimal),
                rate_ex_gst_cents,
                gst_rate_bps,
                totals["amount_ex_gst_cents"],
                totals["gst_amount_cents"],
                totals["amount_inc_gst_cents"],
                import_id,
                internal_note,
                duplicate_key,
            ),
        )
    except Exception as exc:
        if "UNIQUE constraint failed: cleaning_jobs.duplicate_key" in str(exc):
            return None, "duplicate"
        raise
    job_id = int(cur.lastrowid)
    audit(conn, "cleaning_job", job_id, "job_created", new={"description": description})
    return job_id, "created"


def import_jobs_csv(conn, csv_text: str, filename: str = "jobs.csv") -> dict[str, Any]:
    rows = list(csv.DictReader(io.StringIO(csv_text.strip())))
    cur = conn.execute(
        "INSERT INTO job_imports (filename, row_count) VALUES (?, ?)",
        (filename, len(rows)),
    )
    import_id = int(cur.lastrowid)
    imported = duplicates = errors = 0
    error_rows: list[dict[str, str]] = []
    for index, row in enumerate(rows, start=2):
        try:
            rate = cents(row.get("rate_ex_gst"))
            property_label = (
                row.get("property_nickname", "").strip()
                or row.get("property_address", "").strip()
            )
            owner_id, property_id = resolve_import_owner_property(
                conn,
                property_label=property_label,
                owner_name=row.get("owner_name", "").strip(),
                owner_email=row.get("owner_email", "").strip(),
                property_address=row.get("property_address", "").strip(),
                property_nickname=row.get("property_nickname", "").strip(),
                default_rate_ex_gst_cents=rate,
                default_description=row.get("description", "").strip(),
            )
            _, status = create_job(
                conn,
                owner_id=owner_id,
                property_id=property_id,
                service_date=row.get("service_date", "").strip(),
                description=row.get("description", "").strip(),
                hours=row.get("hours", "").strip(),
                rate_ex_gst_cents=rate,
                internal_note=row.get("notes", "").strip(),
                import_id=import_id,
            )
            if status == "duplicate":
                duplicates += 1
            else:
                imported += 1
        except Exception as exc:
            errors += 1
            row["row_number"] = str(index)
            row["error"] = str(exc)
            error_rows.append(row)
    conn.execute(
        """
        UPDATE job_imports
        SET imported_count = ?, duplicate_count = ?, error_count = ?
        WHERE id = ?
        """,
        (imported, duplicates, errors, import_id),
    )
    audit(
        conn,
        "job_import",
        import_id,
        "job_import_completed",
        new={"imported": imported, "duplicates": duplicates, "errors": errors},
    )
    conn.commit()
    return {
        "import_id": import_id,
        "row_count": len(rows),
        "imported": imported,
        "duplicates": duplicates,
        "errors": errors,
        "error_rows": error_rows,
    }


def list_jobs(conn, *, month: str | None = None, status: str | None = None) -> list[Any]:
    filters = []
    params: list[Any] = []
    if month:
        start, end = month_bounds(month)
        filters.append("j.service_date BETWEEN ? AND ?")
        params.extend([start, end])
    if status:
        filters.append("j.status = ?")
        params.append(status)
    where = "WHERE " + " AND ".join(filters) if filters else ""
    return conn.execute(
        f"""
        SELECT j.*, o.name AS owner_name, p.nickname AS property_nickname,
               p.address_line_1 AS property_address
        FROM cleaning_jobs j
        JOIN owners o ON o.id = j.owner_id
        JOIN properties p ON p.id = j.property_id
        {where}
        ORDER BY j.service_date DESC, j.id DESC
        """,
        params,
    ).fetchall()


def delete_job(
    conn,
    job_id: int,
    *,
    reason: str = "Job deleted by user",
) -> dict[str, Any]:
    job = conn.execute("SELECT * FROM cleaning_jobs WHERE id = ?", (job_id,)).fetchone()
    if not job:
        raise ValueError("Job not found")

    invoice_id = job["invoice_id"]
    if invoice_id:
        invoice = get_invoice(conn, int(invoice_id))
        if not invoice:
            raise ValueError("Linked invoice not found")
        if invoice["status"] != "draft":
            raise ValueError("Only jobs on draft invoices can be deleted here")
        line = conn.execute(
            """
            SELECT *
            FROM invoice_line_items
            WHERE invoice_id = ?
              AND cleaning_job_id = ?
            ORDER BY id
            LIMIT 1
            """,
            (invoice_id, job_id),
        ).fetchone()
        if not line:
            raise ValueError("Linked invoice line not found")
        delete_job_invoice_line(conn, int(invoice_id), int(line["id"]))
        return {
            "job_id": job_id,
            "invoice_id": int(invoice_id),
            "invoice_number": invoice["invoice_number"],
            "status": "deleted_from_draft_invoice",
        }

    conn.execute("DELETE FROM cleaning_jobs WHERE id = ? AND invoice_id IS NULL", (job_id,))
    audit(
        conn,
        "cleaning_job",
        job_id,
        "job_deleted",
        old=dict(job),
        reason=reason,
        user_id="admin",
    )
    return {
        "job_id": job_id,
        "invoice_id": None,
        "invoice_number": None,
        "status": "deleted",
    }


@dataclass
class InvoiceGroup:
    owner_id: int
    owner_name: str
    owner_email: str | None
    property_id: int
    property_label: str
    job_count: int
    property_count: int
    subtotal_ex_gst_cents: int
    gst_total_cents: int
    total_inc_gst_cents: int
    issues: list[str]
    jobs: list[Any]


def group_unbilled_jobs(conn, billing_month: str) -> list[InvoiceGroup]:
    start, end = month_bounds(billing_month)
    jobs = conn.execute(
        """
        SELECT j.*, o.name AS owner_name, o.billing_email, p.nickname AS property_nickname,
               p.address_line_1 AS property_address, p.active AS property_active,
               p.next_invoice_number,
               o.active AS owner_active
        FROM cleaning_jobs j
        JOIN owners o ON o.id = j.owner_id
        JOIN properties p ON p.id = j.property_id
        WHERE j.status = 'unbilled'
          AND j.service_date BETWEEN ? AND ?
        ORDER BY o.name, j.service_date, j.id
        """,
        (start, end),
    ).fetchall()
    grouped: dict[tuple[int, int], list[Any]] = defaultdict(list)
    for job in jobs:
        grouped[(int(job["owner_id"]), int(job["property_id"]))].append(job)
    invoice_groups: list[InvoiceGroup] = []
    for (owner_id, property_id), owner_jobs in grouped.items():
        issues = []
        first = owner_jobs[0]
        if not first["owner_active"]:
            issues.append("Owner inactive")
        for job in owner_jobs:
            if not job["property_active"]:
                issues.append("Property inactive")
            if Decimal(job["hours"]) <= 0:
                issues.append("Zero hours")
            if int(job["rate_ex_gst_cents"]) <= 0:
                issues.append("Missing rate")
            if int(job["amount_inc_gst_cents"]) < 0:
                issues.append("Negative amount")
            if not job["next_invoice_number"]:
                issues.append("Missing property invoice number")
        properties = {job["property_id"] for job in owner_jobs}
        invoice_groups.append(
            InvoiceGroup(
                owner_id=owner_id,
                owner_name=first["owner_name"],
                owner_email=first["billing_email"],
                property_id=property_id,
                property_label=first["property_nickname"] or first["property_address"],
                job_count=len(owner_jobs),
                property_count=len(properties),
                subtotal_ex_gst_cents=sum(int(job["amount_ex_gst_cents"]) for job in owner_jobs),
                gst_total_cents=sum(int(job["gst_amount_cents"]) for job in owner_jobs),
                total_inc_gst_cents=sum(int(job["amount_inc_gst_cents"]) for job in owner_jobs),
                issues=sorted(set(issues)),
                jobs=owner_jobs,
            )
        )
    return invoice_groups


def next_invoice_number(conn, issue_date: str) -> str:
    settings = get_settings(conn)
    year = date.fromisoformat(issue_date).year
    number = int(settings["next_invoice_number"])
    invoice_number = f"{settings['invoice_number_prefix']}-{year}-{number:04d}"
    conn.execute(
        "UPDATE business_settings SET next_invoice_number = next_invoice_number + 1 WHERE id = 1"
    )
    return invoice_number


def next_property_invoice_number(conn, property_id: int, issue_date: str) -> str:
    prop = conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
    if not prop or not prop["next_invoice_number"]:
        raise ValueError("Property invoice number is required")

    prefix = prop["invoice_number_prefix"] or ""
    padding = int(prop["invoice_number_padding"] or 0)
    number = int(prop["next_invoice_number"])
    invoice_number, number = next_available_invoice_number(
        conn,
        prefix=prefix,
        number=number,
        padding=padding,
    )
    conn.execute(
        """
        UPDATE properties
        SET next_invoice_number = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (number + 1, property_id),
    )
    return invoice_number


def renumber_property_invoices(
    conn,
    property_id: int,
    *,
    prefix: str,
    number: int,
    padding: int,
) -> None:
    invoices = conn.execute(
        """
        SELECT DISTINCT i.id
        FROM invoices i
        JOIN invoice_line_items li ON li.invoice_id = i.id
        LEFT JOIN cleaning_jobs j ON j.id = li.cleaning_job_id
        WHERE COALESCE(li.property_id, j.property_id) = ?
        ORDER BY i.period_start, i.id
        """,
        (property_id,),
    ).fetchall()
    for invoice in invoices:
        invoice_number, number = next_available_invoice_number(
            conn,
            prefix=prefix,
            number=number,
            padding=padding,
            exclude_invoice_id=int(invoice["id"]),
        )
        before = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice["id"],)).fetchone()
        conn.execute(
            """
            UPDATE invoices
            SET invoice_number = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (invoice_number, invoice["id"]),
        )
        after = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice["id"],)).fetchone()
        audit(conn, "invoice", int(invoice["id"]), "invoice_number_changed", old=dict(before), new=dict(after))
        number += 1
    conn.execute(
        """
        UPDATE properties
        SET next_invoice_number = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (number, property_id),
    )


def create_draft_invoices(conn, billing_month: str, owner_ids: list[int] | None = None) -> list[int]:
    start, end = month_bounds(billing_month)
    settings = get_settings(conn)
    issue = end
    created: list[int] = []
    requested = set(owner_ids or [])
    for group in group_unbilled_jobs(conn, billing_month):
        if requested and group.owner_id not in requested:
            continue
        if group.issues:
            continue
        owner = conn.execute("SELECT * FROM owners WHERE id = ?", (group.owner_id,)).fetchone()
        terms = int(owner["default_payment_terms_days"] or settings["default_payment_terms_days"])
        due = (date.fromisoformat(issue) + timedelta(days=terms)).isoformat()
        invoice_number = next_property_invoice_number(conn, group.property_id, issue)
        cur = conn.execute(
            """
            INSERT INTO invoices (
                invoice_number, owner_id, period_start, period_end, issue_date, due_date,
                subtotal_ex_gst_cents, gst_total_cents, total_inc_gst_cents,
                amount_paid_cents, amount_outstanding_cents, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'draft')
            """,
            (
                invoice_number,
                group.owner_id,
                start,
                end,
                issue,
                due,
                group.subtotal_ex_gst_cents,
                group.gst_total_cents,
                group.total_inc_gst_cents,
                group.total_inc_gst_cents,
            ),
        )
        invoice_id = int(cur.lastrowid)
        for sort_order, job in enumerate(group.jobs, start=1):
            property_label = job["property_nickname"] or job["property_address"]
            conn.execute(
                """
                INSERT INTO invoice_line_items (
                    invoice_id, cleaning_job_id, property_id, line_type, service_date, property_label, description,
                    quantity_hours, unit_rate_ex_gst_cents, gst_rate_bps, amount_ex_gst_cents,
                    gst_amount_cents, amount_inc_gst_cents, sort_order
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    invoice_id,
                    job["id"],
                    group.property_id,
                    LINE_TYPE_JOB,
                    job["service_date"],
                    property_label,
                    job["description"],
                    job["hours"],
                    job["rate_ex_gst_cents"],
                    job["gst_rate_bps"],
                    job["amount_ex_gst_cents"],
                    job["gst_amount_cents"],
                    job["amount_inc_gst_cents"],
                    sort_order,
                ),
            )
            conn.execute(
                """
                UPDATE cleaning_jobs
                SET status = 'draft_invoice', invoice_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (invoice_id, job["id"]),
            )
        audit(
            conn,
            "invoice",
            invoice_id,
            "invoice_created",
            new={"invoice_number": invoice_number, "job_count": group.job_count},
        )
        created.append(invoice_id)
    conn.commit()
    return created


def get_invoice(conn, invoice_id: int) -> Any:
    return conn.execute(
        """
        SELECT i.*, o.name AS owner_name, o.billing_email, o.billing_address_line_1,
               o.billing_city, o.billing_state, o.billing_postcode, o.abn AS owner_abn
        FROM invoices i
        JOIN owners o ON o.id = i.owner_id
        WHERE i.id = ?
        """,
        (invoice_id,),
    ).fetchone()


def get_invoice_lines(conn, invoice_id: int) -> list[Any]:
    return conn.execute(
        """
        SELECT * FROM invoice_line_items
        WHERE invoice_id = ?
        ORDER BY sort_order, service_date, id
        """,
        (invoice_id,),
    ).fetchall()


def invoice_line_count(conn, invoice_id: int) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) AS count FROM invoice_line_items WHERE invoice_id = ?",
            (invoice_id,),
        ).fetchone()["count"]
    )


def manual_line_property(conn, invoice_id: int) -> tuple[int, str]:
    property_ids = invoice_property_ids(conn, invoice_id)
    if len(property_ids) != 1:
        raise ValueError("Manual lines require an invoice linked to exactly one property")
    prop = conn.execute("SELECT * FROM properties WHERE id = ?", (property_ids[0],)).fetchone()
    if not prop:
        raise ValueError("Property not found")
    return int(prop["id"]), prop["nickname"] or prop["address_line_1"]


def assert_invoice_is_draft(conn, invoice_id: int):
    invoice = get_invoice(conn, invoice_id)
    if not invoice:
        raise ValueError("Invoice not found")
    if invoice["status"] != "draft":
        raise ValueError("Only draft invoices can be edited")
    return invoice


def normalize_manual_line_date(value: str | None, fallback: str) -> str:
    service_date = str(value or fallback or "").strip()
    if not service_date:
        raise ValueError("Service date is required")
    date.fromisoformat(service_date)
    return service_date


def normalize_manual_line_description(value: str | None) -> str:
    description = str(value or "").strip()
    if not description:
        raise ValueError("Description is required")
    return description


def next_invoice_line_sort_order(conn, invoice_id: int) -> int:
    row = conn.execute(
        """
        SELECT COALESCE(MAX(sort_order), 0) + 1 AS next_sort_order
        FROM invoice_line_items
        WHERE invoice_id = ?
        """,
        (invoice_id,),
    ).fetchone()
    return int(row["next_sort_order"])


def refresh_invoice_totals(conn, invoice_id: int) -> None:
    totals = conn.execute(
        """
        SELECT
            COALESCE(SUM(amount_ex_gst_cents), 0) AS subtotal_ex_gst_cents,
            COALESCE(SUM(gst_amount_cents), 0) AS gst_total_cents,
            COALESCE(SUM(amount_inc_gst_cents), 0) AS total_inc_gst_cents
        FROM invoice_line_items
        WHERE invoice_id = ?
        """,
        (invoice_id,),
    ).fetchone()
    conn.execute(
        """
        UPDATE invoices
        SET subtotal_ex_gst_cents = ?,
            gst_total_cents = ?,
            total_inc_gst_cents = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            int(totals["subtotal_ex_gst_cents"]),
            int(totals["gst_total_cents"]),
            int(totals["total_inc_gst_cents"]),
            invoice_id,
        ),
    )
    refresh_invoice_payment_status(conn, invoice_id)


def get_invoice_line(conn, invoice_id: int, line_id: int):
    return conn.execute(
        """
        SELECT *
        FROM invoice_line_items
        WHERE invoice_id = ?
          AND id = ?
        """,
        (invoice_id, line_id),
    ).fetchone()


def assert_manual_invoice_line(conn, invoice_id: int, line_id: int):
    line = get_invoice_line(conn, invoice_id, line_id)
    if not line:
        raise ValueError("Invoice line not found")
    if line["line_type"] != LINE_TYPE_MANUAL or line["cleaning_job_id"] is not None:
        raise ValueError("Only manual invoice lines can be edited")
    return line


def assert_job_invoice_line(conn, invoice_id: int, line_id: int):
    line = get_invoice_line(conn, invoice_id, line_id)
    if not line:
        raise ValueError("Invoice line not found")
    if line["line_type"] != LINE_TYPE_JOB or line["cleaning_job_id"] is None:
        raise ValueError("Only job invoice lines can be edited as jobs")
    return line


def add_manual_invoice_line(
    conn,
    invoice_id: int,
    *,
    service_date: str | None = None,
    description: str,
    amount_inc_gst_cents: int,
    gst_rate_bps: int | None = None,
) -> int:
    invoice = assert_invoice_is_draft(conn, invoice_id)
    property_id, property_label = manual_line_property(conn, invoice_id)
    service_date = normalize_manual_line_date(service_date, invoice["period_end"] or invoice["issue_date"])
    description = normalize_manual_line_description(description)
    if gst_rate_bps is None:
        gst_rate_bps = int(get_settings(conn)["default_gst_rate_bps"])
    totals = calculate_inclusive_totals(amount_inc_gst_cents, gst_rate_bps)
    cur = conn.execute(
        """
        INSERT INTO invoice_line_items (
            invoice_id, cleaning_job_id, property_id, line_type, service_date, property_label,
            description, quantity_hours, unit_rate_ex_gst_cents, gst_rate_bps,
            amount_ex_gst_cents, gst_amount_cents, amount_inc_gst_cents, sort_order
        )
        VALUES (?, NULL, ?, ?, ?, ?, ?, '', 0, ?, ?, ?, ?, ?)
        """,
        (
            invoice_id,
            property_id,
            LINE_TYPE_MANUAL,
            service_date,
            property_label,
            description,
            int(gst_rate_bps),
            totals["amount_ex_gst_cents"],
            totals["gst_amount_cents"],
            totals["amount_inc_gst_cents"],
            next_invoice_line_sort_order(conn, invoice_id),
        ),
    )
    line_id = int(cur.lastrowid)
    refresh_invoice_totals(conn, invoice_id)
    audit(
        conn,
        "invoice",
        invoice_id,
        "manual_line_added",
        new={"line_id": line_id, "description": description, **totals},
    )
    return line_id


def update_manual_invoice_line(
    conn,
    invoice_id: int,
    line_id: int,
    *,
    service_date: str | None,
    description: str,
    amount_inc_gst_cents: int,
    gst_rate_bps: int,
) -> None:
    assert_invoice_is_draft(conn, invoice_id)
    line = assert_manual_invoice_line(conn, invoice_id, line_id)
    service_date = normalize_manual_line_date(service_date, line["service_date"])
    description = normalize_manual_line_description(description)
    totals = calculate_inclusive_totals(amount_inc_gst_cents, gst_rate_bps)
    before = dict(line)
    conn.execute(
        """
        UPDATE invoice_line_items
        SET service_date = ?,
            description = ?,
            gst_rate_bps = ?,
            amount_ex_gst_cents = ?,
            gst_amount_cents = ?,
            amount_inc_gst_cents = ?
        WHERE invoice_id = ?
          AND id = ?
        """,
        (
            service_date,
            description,
            int(gst_rate_bps),
            totals["amount_ex_gst_cents"],
            totals["gst_amount_cents"],
            totals["amount_inc_gst_cents"],
            invoice_id,
            line_id,
        ),
    )
    refresh_invoice_totals(conn, invoice_id)
    after = dict(get_invoice_line(conn, invoice_id, line_id))
    audit(conn, "invoice", invoice_id, "manual_line_updated", old=before, new=after)


def delete_manual_invoice_line(conn, invoice_id: int, line_id: int) -> None:
    assert_invoice_is_draft(conn, invoice_id)
    line = assert_manual_invoice_line(conn, invoice_id, line_id)
    if invoice_line_count(conn, invoice_id) <= 1:
        raise ValueError("Invoice must keep at least one line item")
    conn.execute(
        "DELETE FROM invoice_line_items WHERE invoice_id = ? AND id = ?",
        (invoice_id, line_id),
    )
    refresh_invoice_totals(conn, invoice_id)
    audit(conn, "invoice", invoice_id, "manual_line_deleted", old=dict(line))


def update_job_invoice_line(
    conn,
    invoice_id: int,
    line_id: int,
    *,
    service_date: str | None,
    description: str,
    hours: str,
    rate_ex_gst_cents: int,
    gst_rate_bps: int,
) -> None:
    assert_invoice_is_draft(conn, invoice_id)
    line = assert_job_invoice_line(conn, invoice_id, line_id)
    job_id = int(line["cleaning_job_id"])
    job = conn.execute(
        "SELECT * FROM cleaning_jobs WHERE id = ? AND invoice_id = ?",
        (job_id, invoice_id),
    ).fetchone()
    if not job:
        raise ValueError("Linked cleaning job not found")
    if rate_ex_gst_cents <= 0:
        raise ValueError("Rate must be positive")
    service_date = normalize_manual_line_date(service_date, line["service_date"])
    description = normalize_manual_line_description(description)
    hours_decimal = decimal_hours(hours)
    if int(gst_rate_bps) < 0:
        raise ValueError("GST rate cannot be negative")
    totals = calculate_totals(hours_decimal, rate_ex_gst_cents, gst_rate_bps)
    property_id = int(line["property_id"] or job["property_id"])
    duplicate_key = duplicate_job_key(
        service_date,
        property_id,
        description,
        str(hours_decimal),
        rate_ex_gst_cents,
    )
    duplicate = conn.execute(
        """
        SELECT id
        FROM cleaning_jobs
        WHERE duplicate_key = ?
          AND id != ?
        LIMIT 1
        """,
        (duplicate_key, job_id),
    ).fetchone()
    if duplicate:
        raise ValueError("A matching cleaning job already exists")

    before_line = dict(line)
    before_job = dict(job)
    conn.execute(
        """
        UPDATE cleaning_jobs
        SET service_date = ?,
            description = ?,
            hours = ?,
            rate_ex_gst_cents = ?,
            gst_rate_bps = ?,
            amount_ex_gst_cents = ?,
            gst_amount_cents = ?,
            amount_inc_gst_cents = ?,
            duplicate_key = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
          AND invoice_id = ?
        """,
        (
            service_date,
            description,
            str(hours_decimal),
            int(rate_ex_gst_cents),
            int(gst_rate_bps),
            totals["amount_ex_gst_cents"],
            totals["gst_amount_cents"],
            totals["amount_inc_gst_cents"],
            duplicate_key,
            job_id,
            invoice_id,
        ),
    )
    conn.execute(
        """
        UPDATE invoice_line_items
        SET property_id = ?,
            service_date = ?,
            description = ?,
            quantity_hours = ?,
            unit_rate_ex_gst_cents = ?,
            gst_rate_bps = ?,
            amount_ex_gst_cents = ?,
            gst_amount_cents = ?,
            amount_inc_gst_cents = ?
        WHERE invoice_id = ?
          AND id = ?
        """,
        (
            property_id,
            service_date,
            description,
            str(hours_decimal),
            int(rate_ex_gst_cents),
            int(gst_rate_bps),
            totals["amount_ex_gst_cents"],
            totals["gst_amount_cents"],
            totals["amount_inc_gst_cents"],
            invoice_id,
            line_id,
        ),
    )
    refresh_invoice_totals(conn, invoice_id)
    after_line = dict(get_invoice_line(conn, invoice_id, line_id))
    after_job = dict(conn.execute("SELECT * FROM cleaning_jobs WHERE id = ?", (job_id,)).fetchone())
    audit(conn, "invoice", invoice_id, "job_line_updated", old=before_line, new=after_line)
    audit(conn, "cleaning_job", job_id, "job_updated_from_invoice", old=before_job, new=after_job)


def update_invoice_line(
    conn,
    invoice_id: int,
    line_id: int,
    *,
    service_date: str | None,
    description: str,
    amount_inc_gst_cents: int | None = None,
    hours: str | None = None,
    rate_ex_gst_cents: int | None = None,
    gst_rate_bps: int,
) -> None:
    line = get_invoice_line(conn, invoice_id, line_id)
    if not line:
        raise ValueError("Invoice line not found")
    if line["line_type"] == LINE_TYPE_MANUAL or line["cleaning_job_id"] is None:
        update_manual_invoice_line(
            conn,
            invoice_id,
            line_id,
            service_date=service_date,
            description=description,
            amount_inc_gst_cents=int(amount_inc_gst_cents or 0),
            gst_rate_bps=gst_rate_bps,
        )
        return
    update_job_invoice_line(
        conn,
        invoice_id,
        line_id,
        service_date=service_date,
        description=description,
        hours=str(hours or ""),
        rate_ex_gst_cents=int(rate_ex_gst_cents or 0),
        gst_rate_bps=gst_rate_bps,
    )


def delete_job_invoice_line(conn, invoice_id: int, line_id: int) -> None:
    assert_invoice_is_draft(conn, invoice_id)
    line = assert_job_invoice_line(conn, invoice_id, line_id)
    if invoice_line_count(conn, invoice_id) <= 1:
        raise ValueError("Invoice must keep at least one line item")
    job_id = int(line["cleaning_job_id"])
    job = conn.execute(
        "SELECT * FROM cleaning_jobs WHERE id = ? AND invoice_id = ?",
        (job_id, invoice_id),
    ).fetchone()
    if not job:
        raise ValueError("Linked cleaning job not found")
    conn.execute(
        "DELETE FROM invoice_line_items WHERE invoice_id = ? AND id = ?",
        (invoice_id, line_id),
    )
    conn.execute("DELETE FROM cleaning_jobs WHERE id = ? AND invoice_id = ?", (job_id, invoice_id))
    refresh_invoice_totals(conn, invoice_id)
    audit(conn, "invoice", invoice_id, "job_line_deleted", old=dict(line))
    audit(conn, "cleaning_job", job_id, "job_deleted_from_invoice", old=dict(job))


def delete_invoice_line(conn, invoice_id: int, line_id: int) -> None:
    line = get_invoice_line(conn, invoice_id, line_id)
    if not line:
        raise ValueError("Invoice line not found")
    if line["line_type"] == LINE_TYPE_MANUAL or line["cleaning_job_id"] is None:
        delete_manual_invoice_line(conn, invoice_id, line_id)
        return
    delete_job_invoice_line(conn, invoice_id, line_id)


def list_invoices(conn, *, status: str | None = None, month: str | None = None) -> list[Any]:
    update_overdue_statuses(conn)
    where_parts = []
    params: list[Any] = []
    if status:
        where_parts.append("i.status = ?")
        params.append(status)
    if month:
        start, end = month_bounds(month)
        where_parts.append("i.period_start BETWEEN ? AND ?")
        params.extend([start, end])
    where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
    return conn.execute(
        f"""
        SELECT i.*, o.name AS owner_name
        FROM invoices i
        JOIN owners o ON o.id = i.owner_id
        {where}
        ORDER BY i.issue_date DESC, i.id DESC
        """,
        params,
    ).fetchall()


def invoice_property_ids(conn, invoice_id: int) -> list[int]:
    return [
        int(row["property_id"])
        for row in conn.execute(
            """
            SELECT DISTINCT COALESCE(li.property_id, j.property_id) AS property_id
            FROM invoice_line_items li
            LEFT JOIN cleaning_jobs j ON j.id = li.cleaning_job_id
            WHERE li.invoice_id = ?
              AND COALESCE(li.property_id, j.property_id) IS NOT NULL
            ORDER BY property_id
            """,
            (invoice_id,),
        ).fetchall()
    ]


def invoice_has_payment_history(conn, invoice_id: int) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM payment_allocations WHERE invoice_id = ? LIMIT 1",
            (invoice_id,),
        ).fetchone()
    )


def invoice_has_email_history(conn, invoice_id: int) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM email_events WHERE invoice_id = ? LIMIT 1",
            (invoice_id,),
        ).fetchone()
    )


def property_invoice_sequence_rows(conn, property_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT DISTINCT i.id, i.invoice_number, i.period_start, i.issue_date
        FROM invoices i
        JOIN invoice_line_items li ON li.invoice_id = i.id
        LEFT JOIN cleaning_jobs j ON j.id = li.cleaning_job_id
        WHERE COALESCE(li.property_id, j.property_id) = ?
        ORDER BY i.period_start, i.issue_date, i.id
        """,
        (property_id,),
    ).fetchall()
    sequence: list[dict[str, Any]] = []
    for row in rows:
        prefix, number, padding = parse_invoice_number_seed(row["invoice_number"]) or ("", 0, 0)
        sequence.append(
            {
                "id": int(row["id"]),
                "invoice_number": row["invoice_number"],
                "prefix": prefix,
                "number": number,
                "padding": padding,
                "period_start": row["period_start"],
                "issue_date": row["issue_date"],
            }
        )
    return sequence


def assert_invoice_delete_allowed(conn, invoice_id: int, shifted_invoice_ids: list[int]) -> None:
    blocked: list[str] = []
    affected = [invoice_id, *shifted_invoice_ids]
    for affected_id in affected:
        invoice = get_invoice(conn, affected_id)
        if not invoice:
            continue
        if invoice_has_payment_history(conn, affected_id):
            blocked.append(f"{invoice['invoice_number']} has payment history")
    if blocked:
        raise ValueError(
            "Cannot delete because payment history exists: "
            + "; ".join(blocked)
        )


def remove_invoice_database_records(
    conn,
    invoice_id: int,
    *,
    clear_audit: bool,
) -> dict[str, int]:
    email_file_ids = [
        int(row["attachment_file_id"])
        for row in conn.execute(
            "SELECT attachment_file_id FROM email_events WHERE invoice_id = ? AND attachment_file_id IS NOT NULL",
            (invoice_id,),
        ).fetchall()
    ]
    invoice = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    file_ids = set(email_file_ids)
    if invoice and invoice["pdf_file_id"]:
        file_ids.add(int(invoice["pdf_file_id"]))

    job_count = conn.execute(
        "SELECT COUNT(*) AS count FROM cleaning_jobs WHERE invoice_id = ?",
        (invoice_id,),
    ).fetchone()["count"]
    line_count = conn.execute(
        "SELECT COUNT(*) AS count FROM invoice_line_items WHERE invoice_id = ?",
        (invoice_id,),
    ).fetchone()["count"]
    email_count = conn.execute(
        "SELECT COUNT(*) AS count FROM email_events WHERE invoice_id = ?",
        (invoice_id,),
    ).fetchone()["count"]
    suggested_count = conn.execute(
        "SELECT COUNT(*) AS count FROM bank_transactions WHERE suggested_invoice_id = ?",
        (invoice_id,),
    ).fetchone()["count"]

    conn.execute(
        """
        UPDATE bank_transactions
        SET status = CASE
                WHEN matched_payment_id IS NULL THEN 'unreconciled'
                ELSE status
            END,
            suggested_invoice_id = NULL,
            match_reason = CASE
                WHEN matched_payment_id IS NULL THEN NULL
                ELSE match_reason
            END
        WHERE suggested_invoice_id = ?
        """,
        (invoice_id,),
    )
    conn.execute("DELETE FROM email_events WHERE invoice_id = ?", (invoice_id,))
    conn.execute("DELETE FROM invoice_line_items WHERE invoice_id = ?", (invoice_id,))
    conn.execute("DELETE FROM cleaning_jobs WHERE invoice_id = ?", (invoice_id,))
    conn.execute("DELETE FROM invoices WHERE id = ?", (invoice_id,))
    if clear_audit:
        conn.execute(
            "DELETE FROM audit_log WHERE entity_type = 'invoice' AND entity_id = ?",
            (invoice_id,),
        )

    removed_file_count = 0
    for file_id in file_ids:
        still_referenced = conn.execute(
            """
            SELECT 1
            FROM files f
            WHERE f.id = ?
              AND (
                EXISTS (SELECT 1 FROM invoices WHERE pdf_file_id = f.id)
                OR EXISTS (SELECT 1 FROM email_events WHERE attachment_file_id = f.id)
              )
            """,
            (file_id,),
        ).fetchone()
        if still_referenced:
            continue
        file_row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        if file_row:
            try:
                storage_path = Path(file_row["storage_path"])
                if storage_path.exists() and storage_path.is_file():
                    storage_path.unlink()
            except OSError:
                pass
        conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
        removed_file_count += 1

    return {
        "jobs_deleted": int(job_count),
        "line_items_deleted": int(line_count),
        "email_events_deleted": int(email_count),
        "bank_suggestions_cleared": int(suggested_count),
        "files_deleted": removed_file_count,
    }


def delete_file_records(conn, file_ids: set[int]) -> int:
    removed_file_count = 0
    for file_id in file_ids:
        still_referenced = conn.execute(
            """
            SELECT 1
            FROM files f
            WHERE f.id = ?
              AND (
                EXISTS (SELECT 1 FROM invoices WHERE pdf_file_id = f.id)
                OR EXISTS (SELECT 1 FROM email_events WHERE attachment_file_id = f.id)
              )
            """,
            (file_id,),
        ).fetchone()
        if still_referenced:
            continue
        file_row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        if file_row:
            try:
                storage_path = Path(file_row["storage_path"])
                if storage_path.exists() and storage_path.is_file():
                    storage_path.unlink()
            except OSError:
                pass
        conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
        removed_file_count += 1
    return removed_file_count


def clear_invoice_delivery_artifacts(conn, invoice_id: int) -> int:
    invoice = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    if not invoice:
        return 0
    file_ids = {
        int(row["attachment_file_id"])
        for row in conn.execute(
            "SELECT attachment_file_id FROM email_events WHERE invoice_id = ? AND attachment_file_id IS NOT NULL",
            (invoice_id,),
        ).fetchall()
    }
    if invoice["pdf_file_id"]:
        file_ids.add(int(invoice["pdf_file_id"]))
    conn.execute("DELETE FROM email_events WHERE invoice_id = ?", (invoice_id,))
    conn.execute(
        """
        UPDATE invoices
        SET pdf_file_id = NULL,
            sent_at = NULL,
            email_status = NULL,
            status = CASE
                WHEN status IN ('sent', 'overdue') THEN 'draft'
                ELSE status
            END,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (invoice_id,),
    )
    conn.execute(
        """
        UPDATE cleaning_jobs
        SET status = CASE
                WHEN status = 'invoiced' THEN 'draft_invoice'
                ELSE status
            END,
            updated_at = CURRENT_TIMESTAMP
        WHERE invoice_id = ?
        """,
        (invoice_id,),
    )
    return delete_file_records(conn, file_ids)


def delete_invoice(
    conn,
    invoice_id: int,
    *,
    reason: str = "Invoice deleted by user",
) -> dict[str, Any]:
    invoice = get_invoice(conn, invoice_id)
    if not invoice:
        raise ValueError("Invoice not found")
    property_ids = invoice_property_ids(conn, invoice_id)
    if len(property_ids) != 1:
        raise ValueError("Number reset is only available for invoices linked to exactly one property")

    deleted_prefix, deleted_number, deleted_padding = parse_invoice_number_seed(invoice["invoice_number"]) or ("", 0, 0)
    if deleted_number <= 0:
        raise ValueError("Invoice number must end with digits to reset numbering")

    property_id = property_ids[0]
    prop = conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
    if not prop:
        raise ValueError("Property not found")

    sequence = property_invoice_sequence_rows(conn, property_id)
    shifted = [
        row
        for row in sequence
        if row["id"] != invoice_id
        and row["prefix"] == deleted_prefix
        and int(row["number"]) > deleted_number
    ]
    shifted.sort(key=lambda row: (int(row["number"]), str(row["period_start"]), int(row["id"])))
    assert_invoice_delete_allowed(conn, invoice_id, [int(row["id"]) for row in shifted])

    old_invoice_number = str(invoice["invoice_number"])
    cleanup = remove_invoice_database_records(
        conn,
        invoice_id,
        clear_audit=True,
    )

    renumbered: list[dict[str, Any]] = []
    shifted_files_deleted = 0
    for row in shifted:
        shifted_files_deleted += clear_invoice_delivery_artifacts(conn, int(row["id"]))
        old_number = str(row["invoice_number"])
        new_number = format_invoice_number(deleted_prefix, int(row["number"]) - 1, deleted_padding)
        before = conn.execute("SELECT * FROM invoices WHERE id = ?", (row["id"],)).fetchone()
        conn.execute(
            """
            UPDATE invoices
            SET invoice_number = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (new_number, row["id"]),
        )
        after = conn.execute("SELECT * FROM invoices WHERE id = ?", (row["id"],)).fetchone()
        audit(
            conn,
            "invoice",
            int(row["id"]),
            "invoice_number_reset_after_delete",
            old=dict(before),
            new=dict(after),
            reason=reason,
        )
        renumbered.append({"invoice_id": int(row["id"]), "old": old_number, "new": new_number})

    next_number = prop["next_invoice_number"]
    if prop["invoice_number_prefix"] == deleted_prefix and next_number:
        current_next = int(next_number)
        remaining_numbers = [
            int(row["number"]) - 1
            for row in shifted
        ] + [
            int(row["number"])
            for row in sequence
            if row["id"] != invoice_id
            and row["prefix"] == deleted_prefix
            and int(row["number"]) <= deleted_number
        ]
        minimum_next = (max(remaining_numbers) + 1) if remaining_numbers else deleted_number
        next_number = max(minimum_next, current_next - 1 if current_next > deleted_number else current_next)
        conn.execute(
            """
            UPDATE properties
            SET next_invoice_number = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (next_number, property_id),
        )

    audit(
        conn,
        "property",
        property_id,
        "invoice_sequence_reset",
        old={"deleted_invoice_number": old_invoice_number},
        new={
            "next_invoice_number": next_number,
            "renumbered": renumbered,
            "shifted_files_deleted": shifted_files_deleted,
            **cleanup,
        },
        reason=reason,
    )
    conn.commit()
    return {
        "invoice_id": invoice_id,
        "invoice_number": old_invoice_number,
        "property_id": property_id,
        "jobs_deleted": cleanup["jobs_deleted"],
        "line_items_deleted": cleanup["line_items_deleted"],
        "email_events_deleted": cleanup["email_events_deleted"],
        "bank_suggestions_cleared": cleanup["bank_suggestions_cleared"],
        "files_deleted": cleanup["files_deleted"],
        "shifted_files_deleted": shifted_files_deleted,
        "renumbered_invoices": renumbered,
        "next_invoice_number": next_number,
    }


def validate_invoice_for_send(conn, invoice_id: int) -> list[str]:
    invoice = get_invoice(conn, invoice_id)
    settings = get_settings(conn)
    lines = get_invoice_lines(conn, invoice_id)
    errors: list[str] = []
    if not invoice:
        return ["Invoice not found"]
    if invoice["status"] not in ("draft", "sent", "overdue", "partially_paid"):
        errors.append("Invoice status cannot be sent")
    if not invoice["invoice_number"]:
        errors.append("Invoice number is required")
    if not invoice["billing_email"]:
        errors.append("Owner email is required")
    if not settings["business_name"]:
        errors.append("Business name is required")
    if not settings["abn"]:
        errors.append("Business ABN is required")
    if not invoice["issue_date"]:
        errors.append("Issue date is required")
    if not invoice["due_date"]:
        errors.append("Due date is required")
    if not lines:
        errors.append("At least one line item is required")
    if int(invoice["total_inc_gst_cents"]) <= 0:
        errors.append("Invoice total must be greater than zero")
    if int(invoice["subtotal_ex_gst_cents"]) + int(invoice["gst_total_cents"]) != int(
        invoice["total_inc_gst_cents"]
    ):
        errors.append("GST totals are inconsistent")
    if not settings["bsb"] and not settings["payid"]:
        errors.append("Payment details are required")
    return errors


def record_file(conn, *, path: str, filename: str, mime_type: str, file_type: str, data: bytes) -> int:
    checksum = hashlib.sha256(data).hexdigest()
    cur = conn.execute(
        """
        INSERT INTO files (file_type, storage_path, filename, mime_type, size_bytes, checksum)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (file_type, path, filename, mime_type, len(data), checksum),
    )
    return int(cur.lastrowid)


def invoice_period_label(invoice) -> str:
    period_start = str(invoice["period_start"] or invoice["issue_date"])
    try:
        return date.fromisoformat(period_start).strftime("%B %Y")
    except ValueError:
        return period_start


def owner_first_name(owner_name: str | None) -> str:
    return (owner_name or "there").strip().split()[0]


def invoice_email_body(conn, invoice_id: int) -> str:
    invoice = get_invoice(conn, invoice_id)
    if not invoice:
        raise ValueError("Invoice not found")
    settings = get_settings(conn)
    payment_details = [
        f"Account Name: {settings['account_name']}" if settings["account_name"] else "",
        f"BSB: {settings['bsb']}" if settings["bsb"] else "",
        f"Account Number: {settings['account_number']}" if settings["account_number"] else "",
        f"PayID: {settings['payid']}" if settings["payid"] else "",
        f"Payment Reference: {invoice['invoice_number']}",
    ]
    return "\n".join(
        [
            f"Hi {owner_first_name(invoice['owner_name'])},",
            "",
            "Hope this email finds you well.",
            "",
            f"Please find attached the Invoice for {invoice_period_label(invoice)}, which details the work done and amount due. "
            "The Total Amount Due is to be paid within the next 7 days via bank transfer as per the Account Details mentioned in the Invoice.",
            "",
            "If additional time is required, the details are mentioned in the invoice.",
            "",
            "Do not hesitate to get back to us if you have any queries. We appreciate your prompt response and your continued business.",
            "",
            "Thank You & Kind Regards,",
            "",
            *[line for line in payment_details if line],
        ]
    ).strip()


def normalize_email_body(email_body: str | None) -> str:
    body = str(email_body or "").replace("\r\n", "\n").strip()
    if not body:
        raise ValueError("Email message is required")
    return body


def write_invoice_eml(
    conn,
    *,
    invoice,
    pdf_file_id: int,
    subject: str,
    email_body: str,
    email_dir,
    message_id: str,
) -> Path:
    settings = get_settings(conn)
    attachment = conn.execute("SELECT * FROM files WHERE id = ?", (pdf_file_id,)).fetchone()
    if not attachment:
        raise ValueError("Invoice PDF attachment not found")
    attachment_path = Path(attachment["storage_path"])
    attachment_data = attachment_path.read_bytes()

    message = build_invoice_email_message(
        settings=settings,
        invoice=invoice,
        subject=subject,
        email_body=email_body,
        attachment_filename=attachment["filename"],
        attachment_data=attachment_data,
        message_id=message_id,
    )

    email_dir.mkdir(parents=True, exist_ok=True)
    eml_path = email_dir / f"{message_id}.eml"
    eml_path.write_bytes(message.as_bytes())
    return eml_path


def build_invoice_email_message(
    *,
    settings,
    invoice,
    subject: str,
    email_body: str,
    attachment_filename: str,
    attachment_data: bytes,
    message_id: str,
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = f"{settings['business_name']} <{settings['email']}>"
    message["To"] = invoice["billing_email"]
    message["Subject"] = subject
    message["Date"] = format_datetime(datetime.now(UTC))
    message["Message-ID"] = make_msgid(idstring=message_id)
    message.set_content(email_body)
    message.add_attachment(
        attachment_data,
        maintype="application",
        subtype="pdf",
        filename=attachment_filename,
    )
    return message


def send_invoice(conn, invoice_id: int, *, pdf_file_id: int, email_body: str, email_dir) -> None:
    invoice = get_invoice(conn, invoice_id)
    if not invoice:
        raise ValueError("Invoice not found")
    email_body = normalize_email_body(email_body)
    errors = validate_invoice_for_send(conn, invoice_id)
    if errors:
        raise ValueError("; ".join(errors))
    if invoice["status"] == "draft":
        conn.execute(
            """
            UPDATE invoices
            SET status = 'sent', sent_at = CURRENT_TIMESTAMP, pdf_file_id = ?,
                email_status = 'spooled', updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (pdf_file_id, invoice_id),
        )
        conn.execute(
            """
            UPDATE cleaning_jobs
            SET status = 'invoiced', updated_at = CURRENT_TIMESTAMP
            WHERE invoice_id = ?
            """,
            (invoice_id,),
        )
    else:
        conn.execute(
            """
            UPDATE invoices
            SET email_status = 'spooled', updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (invoice_id,),
        )
    subject = f"Tax Invoice {invoice['invoice_number']} from {get_settings(conn)['business_name']}"
    message_id = f"dev-{invoice['invoice_number']}-{int(datetime.now(UTC).timestamp())}"
    write_invoice_eml(
        conn,
        invoice=invoice,
        pdf_file_id=pdf_file_id,
        subject=subject,
        email_body=email_body,
        email_dir=email_dir,
        message_id=message_id,
    )
    conn.execute(
        """
        INSERT INTO email_events (
            invoice_id, event_type, recipient, subject, body, attachment_file_id,
            provider_message_id, status
        )
        VALUES (?, 'invoice_sent', ?, ?, ?, ?, ?, 'spooled')
        """,
        (invoice_id, invoice["billing_email"], subject, email_body, pdf_file_id, message_id),
    )
    audit(
        conn,
        "invoice",
        invoice_id,
        "invoice_sent",
        new={"invoice_number": invoice["invoice_number"], "message_id": message_id},
    )
    conn.commit()


def create_payment_allocation(
    conn,
    *,
    bank_transaction_id: int | None,
    invoice_id: int,
    amount_cents: int,
    received_date: str,
    payer_description: str,
    allocation_type: str,
    created_by: str = "system",
) -> int:
    invoice = get_invoice(conn, invoice_id)
    if not invoice:
        raise ValueError("Invoice not found")
    if invoice["status"] in ("void", "written_off"):
        raise ValueError("Cannot allocate payment to a void or written off invoice")
    if amount_cents <= 0:
        raise ValueError("Payment amount must be positive")
    outstanding = int(invoice["amount_outstanding_cents"])
    if amount_cents > outstanding:
        raise ValueError("Payment allocation cannot exceed invoice outstanding")
    cur = conn.execute(
        """
        INSERT INTO payments (bank_transaction_id, received_date, amount_cents, payer_description, created_by)
        VALUES (?, ?, ?, ?, ?)
        """,
        (bank_transaction_id, received_date, amount_cents, payer_description, created_by),
    )
    payment_id = int(cur.lastrowid)
    alloc_amount = amount_cents
    if alloc_amount <= 0:
        raise ValueError("Invoice is already paid")
    conn.execute(
        """
        INSERT INTO payment_allocations (
            payment_id, invoice_id, amount_allocated_cents, allocation_type, created_by
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (payment_id, invoice_id, alloc_amount, allocation_type, created_by),
    )
    if bank_transaction_id:
        conn.execute(
            """
            UPDATE bank_transactions
            SET status = ?, matched_payment_id = ?, match_reason = ?
            WHERE id = ?
            """,
            ("matched", payment_id, allocation_type, bank_transaction_id),
        )
    refresh_invoice_payment_status(conn, invoice_id)
    audit(
        conn,
        "payment",
        payment_id,
        "payment_allocated",
        new={"invoice_id": invoice_id, "amount_cents": alloc_amount, "type": allocation_type},
        user_id=created_by,
    )
    return payment_id


def refresh_invoice_payment_status(conn, invoice_id: int) -> None:
    invoice = get_invoice(conn, invoice_id)
    if not invoice:
        return
    paid = conn.execute(
        """
        SELECT COALESCE(SUM(amount_allocated_cents), 0) AS paid
        FROM payment_allocations
        WHERE invoice_id = ? AND reversed_at IS NULL
        """,
        (invoice_id,),
    ).fetchone()["paid"]
    total = int(invoice["total_inc_gst_cents"])
    outstanding = max(total - int(paid), 0)
    if outstanding == 0:
        status = "paid"
        paid_at = "CURRENT_TIMESTAMP"
    elif paid > 0:
        status = "partially_paid"
        paid_at = "NULL"
    elif invoice["status"] == "draft":
        status = "draft"
        paid_at = "NULL"
    else:
        status = "sent"
        paid_at = "NULL"
    conn.execute(
        f"""
        UPDATE invoices
        SET amount_paid_cents = ?, amount_outstanding_cents = ?, status = ?,
            paid_at = {paid_at}, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (int(paid), outstanding, status, invoice_id),
    )


def update_overdue_statuses(conn, today: date | None = None) -> None:
    current = (today or date.today()).isoformat()
    conn.execute(
        """
        UPDATE invoices
        SET status = 'overdue', updated_at = CURRENT_TIMESTAMP
        WHERE status IN ('sent', 'partially_paid')
          AND amount_outstanding_cents > 0
          AND due_date < ?
        """,
        (current,),
    )
    conn.commit()


def clean_header(value: str | None) -> str:
    return normalize(value).replace(" ", "_")


def looks_like_bank_header(row: list[str]) -> bool:
    headers = {clean_header(cell) for cell in row}
    return bool(headers & {"date", "transaction_date", "description", "amount", "debit", "credit", "balance"})


def looks_like_bank_date(value: str | None) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%m-%y"):
        try:
            datetime.strptime(text, fmt)
            return True
        except ValueError:
            continue
    try:
        date.fromisoformat(text)
        return True
    except ValueError:
        return False


def looks_like_bank_money(value: str | None) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    text = re.sub(r"(CR|CREDIT|DR|DEBIT)$", "", text, flags=re.IGNORECASE).strip()
    text = text.replace("$", "").replace(",", "")
    try:
        Decimal(text)
        return True
    except InvalidOperation:
        return False


def infer_headerless_bank_headers(rows: list[list[str]]) -> list[str] | None:
    for row in rows[:8]:
        if len(row) < 3 or not looks_like_bank_date(row[0]):
            continue
        column_1_is_money = looks_like_bank_money(row[1]) if len(row) > 1 else False
        column_2_is_money = looks_like_bank_money(row[2]) if len(row) > 2 else False
        column_3_is_money = looks_like_bank_money(row[3]) if len(row) > 3 else False
        if len(row) >= 4 and column_1_is_money and not column_2_is_money:
            return ["date", "amount", "description", "balance"]
        if len(row) == 4 and not column_1_is_money and column_2_is_money:
            return ["date", "description", "amount", "balance"]
        if len(row) >= 5 and not column_1_is_money and (column_2_is_money or column_3_is_money):
            return ["date", "description", "credit", "debit", "balance", "account"]
    return None


def bank_row_from_cells(headers: list[str], row: list[str]) -> dict[str, str]:
    return {
        headers[index] if index < len(headers) else f"column_{index}": cell
        for index, cell in enumerate(row)
    }


def bank_rows_from_csv(csv_text: str) -> list[dict[str, str]]:
    text = csv_text.strip().lstrip("\ufeff")
    if not text:
        return []
    sample = text[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample)
    except csv.Error:
        dialect = csv.excel
    raw_rows = [
        [cell.strip() for cell in row]
        for row in csv.reader(io.StringIO(text), dialect)
        if any(cell.strip() for cell in row)
    ]
    if not raw_rows:
        return []
    first_header_index = next((i for i, row in enumerate(raw_rows[:8]) if looks_like_bank_header(row)), None)
    if first_header_index is not None:
        headers = [clean_header(cell) or f"column_{index}" for index, cell in enumerate(raw_rows[first_header_index])]
        return [bank_row_from_cells(headers, row) for row in raw_rows[first_header_index + 1 :]]
    inferred_headers = infer_headerless_bank_headers(raw_rows)
    if inferred_headers is not None:
        return [bank_row_from_cells(inferred_headers, row) for row in raw_rows]
    return [
        {f"column_{index}": cell for index, cell in enumerate(row)}
        for row in raw_rows
    ]


def row_value(row: dict[str, str], *keys: str) -> str:
    normalized = {clean_header(key): value for key, value in row.items()}
    for key in keys:
        value = normalized.get(clean_header(key))
        if value not in (None, ""):
            return value.strip()
    return ""


def parse_bank_date(value: str) -> str:
    text = value.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%m-%y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return date.fromisoformat(text).isoformat()


def bank_money(value: str | int | float | Decimal | None) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]
    upper = text.upper()
    if upper.endswith(("DR", "DEBIT")):
        negative = True
        text = re.sub(r"(DR|DEBIT)$", "", text, flags=re.IGNORECASE).strip()
    if upper.endswith(("CR", "CREDIT")):
        text = re.sub(r"(CR|CREDIT)$", "", text, flags=re.IGNORECASE).strip()
    amount = cents(text)
    return -abs(amount) if negative else amount


def parse_bank_transaction_row(row: dict[str, str]) -> dict[str, Any]:
    transaction_date = parse_bank_date(
        row_value(row, "date", "transaction_date", "transaction date", "effective date", "processed date", "column_0")
    )
    description_parts = [
        row_value(row, "description", "transaction description", "details", "narrative", "reference", "transaction details", "column_1"),
        row_value(row, "reference", "payment reference", "memo"),
    ]
    description = " ".join(part for part in description_parts if part).strip()
    if not description:
        raise ValueError("Description is required")

    debit = bank_money(row_value(row, "debit", "withdrawal", "withdrawals", "money out"))
    credit = bank_money(row_value(row, "credit", "deposit", "deposits", "money in"))
    amount_text = row_value(row, "amount", "transaction amount", "value", "column_2")
    amount = 0
    if debit or credit:
        amount = abs(credit) - abs(debit)
    elif amount_text:
        amount = bank_money(amount_text)
        credit = amount if amount > 0 else 0
        debit = abs(amount) if amount < 0 else 0
    else:
        raise ValueError("Amount is required")

    balance_text = row_value(row, "balance", "account balance", "running balance", "column_3")
    balance_value = bank_money(balance_text) if balance_text else None
    account_identifier = row_value(row, "account", "account_identifier", "account number", "account no", "account name")
    return {
        "transaction_date": transaction_date,
        "description": description,
        "debit_cents": abs(debit),
        "credit_cents": abs(credit),
        "amount_cents": amount,
        "balance_cents": balance_value,
        "account_identifier": account_identifier,
    }


def import_bank_csv(conn, csv_text: str, filename: str = "bank.csv") -> dict[str, Any]:
    rows = bank_rows_from_csv(csv_text)
    account = ""
    for row in rows:
        account = row_value(row, "account", "account_identifier", "account number", "account no") or account
    cur = conn.execute(
        """
        INSERT INTO bank_imports (filename, source_bank, account_identifier, row_count)
        VALUES (?, ?, ?, ?)
        """,
        (filename, "Commonwealth Bank", account, len(rows)),
    )
    import_id = int(cur.lastrowid)
    imported = duplicates = errors = 0
    error_rows: list[dict[str, str]] = []
    for index, row in enumerate(rows, start=2):
        try:
            parsed = parse_bank_transaction_row(row)
            account_identifier = parsed["account_identifier"] or account
            unique = transaction_hash(
                account_identifier,
                parsed["transaction_date"],
                parsed["amount_cents"],
                parsed["description"],
                parsed["balance_cents"],
            )
            try:
                conn.execute(
                    """
                    INSERT INTO bank_transactions (
                        bank_import_id, transaction_date, description, debit_cents, credit_cents,
                        amount_cents, balance_cents, account_identifier, unique_hash
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        import_id,
                        parsed["transaction_date"],
                        parsed["description"],
                        parsed["debit_cents"],
                        parsed["credit_cents"],
                        parsed["amount_cents"],
                        parsed["balance_cents"],
                        account_identifier,
                        unique,
                    ),
                )
                imported += 1
            except Exception as exc:
                if "UNIQUE constraint failed: bank_transactions.unique_hash" in str(exc):
                    duplicates += 1
                else:
                    raise
        except Exception as exc:
            errors += 1
            row["row_number"] = str(index)
            row["error"] = str(exc)
            error_rows.append(row)
    conn.execute(
        """
        UPDATE bank_imports
        SET imported_count = ?, duplicate_count = ?, error_count = ?
        WHERE id = ?
        """,
        (imported, duplicates, errors, import_id),
    )
    audit(
        conn,
        "bank_import",
        import_id,
        "bank_import_completed",
        new={"imported": imported, "duplicates": duplicates, "errors": errors},
    )
    run_reconciliation(conn)
    conn.commit()
    return {
        "import_id": import_id,
        "row_count": len(rows),
        "imported": imported,
        "duplicates": duplicates,
        "errors": errors,
        "error_rows": error_rows,
    }


def invoice_reference_patterns(invoice_number: str) -> list[str]:
    parts = invoice_number.split("-")
    patterns = [re.escape(invoice_number)]
    if len(parts) >= 3:
        patterns.append(re.escape("-".join(parts[1:])))
        patterns.append(r"\b" + re.escape(parts[-1]) + r"\b")
    return patterns


def compact_reference(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def invoice_like_references(text: str | None) -> list[str]:
    references = []
    month_prefixes = {"JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"}
    for match in re.finditer(r"\b([A-Z]{2,6})[\s-]*(\d{2,})\b", text or "", flags=re.IGNORECASE):
        if match.group(1).upper() in month_prefixes:
            continue
        reference = compact_reference(match.group(0)).upper()
        if len(reference) >= 5 and any(char.isalpha() for char in reference) and any(char.isdigit() for char in reference):
            references.append(reference)
    return list(dict.fromkeys(references))


def invoice_reference_matches(invoice_number: str, description: str) -> bool:
    if any(
        re.search(pattern, description, flags=re.IGNORECASE)
        for pattern in invoice_reference_patterns(invoice_number)
    ):
        return True
    compact_invoice = compact_reference(invoice_number)
    return len(compact_invoice) >= 5 and compact_invoice in compact_reference(description)


def invoice_match_text(invoice) -> str:
    return " ".join(
        str(value or "")
        for value in (
            invoice["invoice_number"],
            invoice["owner_name"],
            invoice["billing_email"] if "billing_email" in invoice.keys() else "",
            invoice["property_names"],
            invoice["property_addresses"] if "property_addresses" in invoice.keys() else "",
        )
    )


def days_between_iso(left: str, right: str) -> int:
    return abs((date.fromisoformat(left) - date.fromisoformat(right)).days)


def score_reconciliation_candidate(tx, invoice) -> tuple[int, list[str]]:
    amount = int(tx["amount_cents"])
    outstanding = int(invoice["amount_outstanding_cents"])
    total = int(invoice["total_inc_gst_cents"])
    description = tx["description"] or ""
    score = 0
    reasons: list[str] = []

    if amount == outstanding:
        score += 48
        reasons.append("exact outstanding amount")
    elif amount == total:
        score += 42
        reasons.append("exact invoice total")
    elif 0 < amount < outstanding:
        score += 18
        reasons.append("possible partial payment")
    else:
        return 0, ["amount does not fit invoice"]

    owner_similarity = token_similarity(description, invoice["owner_name"])
    property_similarity = token_similarity(description, invoice["property_names"])
    property_address_similarity = token_similarity(
        description,
        invoice["property_addresses"] if "property_addresses" in invoice.keys() else "",
    )
    text_similarity = max(
        owner_similarity,
        property_similarity,
        property_address_similarity,
        token_similarity(description, invoice_match_text(invoice)),
    )
    if text_similarity >= 0.8:
        score += 36
        reasons.append("strong owner/property text match")
    elif text_similarity >= 0.55:
        score += 24
        reasons.append("owner/property text match")
    elif text_similarity >= 0.34:
        score += 12
        reasons.append("weak owner/property text hint")

    tx_date = str(tx["transaction_date"])
    if invoice["issue_date"] <= tx_date <= invoice["due_date"]:
        score += 12
        reasons.append("paid inside invoice window")
    elif days_between_iso(tx_date, invoice["due_date"]) <= 14:
        score += 8
        reasons.append("paid near due date")
    elif days_between_iso(tx_date, invoice["issue_date"]) <= 14:
        score += 5
        reasons.append("paid near issue date")

    if normalize(invoice["owner_name"]) and normalize(invoice["owner_name"]) in normalize(description):
        score += 8
        reasons.append("owner name appears in transaction")
    if normalize(invoice["property_names"]) and normalize(invoice["property_names"]) in normalize(description):
        score += 8
        reasons.append("property appears in transaction")
    property_addresses = invoice["property_addresses"] if "property_addresses" in invoice.keys() else ""
    if normalize(property_addresses) and normalize(property_addresses) in normalize(description):
        score += 8
        reasons.append("property address appears in transaction")

    return min(score, 100), reasons


def ranked_reconciliation_candidates(tx, invoices) -> list[dict[str, Any]]:
    candidates = []
    for invoice in invoices:
        score, reasons = score_reconciliation_candidate(tx, invoice)
        if score:
            candidates.append({"invoice": invoice, "score": score, "reasons": reasons})
    return sorted(candidates, key=lambda item: item["score"], reverse=True)


def run_reconciliation(conn) -> None:
    transactions = conn.execute(
        """
        SELECT * FROM bank_transactions
        WHERE status IN ('unreconciled', 'suggested', 'exception')
        ORDER BY transaction_date, id
        """
    ).fetchall()
    invoices = conn.execute(
        """
        SELECT i.*, o.name AS owner_name, o.billing_email,
               GROUP_CONCAT(DISTINCT p.nickname) AS property_names,
               GROUP_CONCAT(DISTINCT p.address_line_1) AS property_addresses
        FROM invoices i
        JOIN owners o ON o.id = i.owner_id
        LEFT JOIN invoice_line_items li ON li.invoice_id = i.id
        LEFT JOIN cleaning_jobs j ON j.id = li.cleaning_job_id
        LEFT JOIN properties p ON p.id = COALESCE(li.property_id, j.property_id)
        WHERE i.status IN ('sent', 'overdue', 'partially_paid')
          AND i.amount_outstanding_cents > 0
        GROUP BY i.id
        """
    ).fetchall()
    for tx in transactions:
        if int(tx["amount_cents"]) <= 0:
            conn.execute(
                "UPDATE bank_transactions SET status = 'exception', match_reason = ? WHERE id = ?",
                ("Debit or zero amount cannot be matched automatically", tx["id"]),
            )
            continue
        description = tx["description"] or ""
        referenced = []
        for invoice in invoices:
            if invoice_reference_matches(invoice["invoice_number"], description):
                referenced.append(invoice)
        if len(referenced) == 1:
            invoice = referenced[0]
            if int(tx["amount_cents"]) <= int(invoice["amount_outstanding_cents"]):
                create_payment_allocation(
                    conn,
                    bank_transaction_id=tx["id"],
                    invoice_id=invoice["id"],
                    amount_cents=int(tx["amount_cents"]),
                    received_date=tx["transaction_date"],
                    payer_description=description,
                    allocation_type="auto",
                    created_by="system",
                )
            else:
                conn.execute(
                    """
                    UPDATE bank_transactions
                    SET status = 'exception', suggested_invoice_id = ?, match_reason = ?
                    WHERE id = ?
                    """,
                    (invoice["id"], "Overpayment requires manual review", tx["id"]),
                )
            continue
        if len(referenced) > 1:
            conn.execute(
                "UPDATE bank_transactions SET status = 'exception', match_reason = ? WHERE id = ?",
                ("Multiple invoice references found", tx["id"]),
            )
            continue
        explicit_references = invoice_like_references(description)
        if explicit_references:
            conn.execute(
                """
                UPDATE bank_transactions
                SET status = 'exception', suggested_invoice_id = NULL, match_reason = ?
                WHERE id = ?
                """,
                (
                    f"Payment references {', '.join(explicit_references)}, but no open invoice matches",
                    tx["id"],
                ),
            )
            continue
        candidates = ranked_reconciliation_candidates(tx, invoices)
        top = candidates[0] if candidates else None
        runner_up = candidates[1] if len(candidates) > 1 else None
        margin = int(top["score"] - runner_up["score"]) if top and runner_up else 100
        if top and int(top["score"]) >= 92 and margin >= 18 and int(tx["amount_cents"]) == int(top["invoice"]["amount_outstanding_cents"]):
            create_payment_allocation(
                conn,
                bank_transaction_id=tx["id"],
                invoice_id=top["invoice"]["id"],
                amount_cents=int(tx["amount_cents"]),
                received_date=tx["transaction_date"],
                payer_description=description,
                allocation_type="auto_no_reference",
                created_by="system",
            )
        elif top and int(top["score"]) >= 68 and margin >= 12:
            conn.execute(
                """
                UPDATE bank_transactions
                SET status = 'suggested', suggested_invoice_id = ?, match_reason = ?
                WHERE id = ?
                """,
                (
                    top["invoice"]["id"],
                    f"Suggested {top['score']}%: {', '.join(top['reasons'])}",
                    tx["id"],
                ),
            )
        elif top and runner_up and int(top["score"]) >= 60 and margin < 12:
            conn.execute(
                "UPDATE bank_transactions SET status = 'exception', match_reason = ? WHERE id = ?",
                ("Ambiguous payment: multiple invoices have similar evidence", tx["id"]),
            )
        else:
            conn.execute(
                "UPDATE bank_transactions SET status = 'unreconciled', match_reason = ? WHERE id = ?",
                ("No high-confidence match", tx["id"]),
            )


def manual_match_transaction(
    conn, transaction_id: int, invoice_id: int, amount_cents: int | None = None, reason: str = "Manual match"
) -> int:
    tx = conn.execute("SELECT * FROM bank_transactions WHERE id = ?", (transaction_id,)).fetchone()
    if not tx:
        raise ValueError("Transaction not found")
    if int(tx["amount_cents"]) <= 0:
        raise ValueError("Only credit transactions can be matched")
    invoice = get_invoice(conn, invoice_id)
    if not invoice:
        raise ValueError("Invoice not found")
    explicit_references = invoice_like_references(tx["description"])
    selected_reference = compact_reference(invoice["invoice_number"]).upper()
    if explicit_references and selected_reference not in explicit_references:
        raise ValueError(
            "Transaction reference "
            f"{', '.join(explicit_references)} does not match selected invoice {invoice['invoice_number']}"
        )
    amount = amount_cents or int(tx["amount_cents"])
    if amount > int(tx["amount_cents"]):
        raise ValueError("Allocation cannot exceed transaction amount")
    if amount > int(invoice["amount_outstanding_cents"]):
        raise ValueError("Allocation cannot exceed invoice outstanding")
    payment_id = create_payment_allocation(
        conn,
        bank_transaction_id=transaction_id,
        invoice_id=invoice_id,
        amount_cents=amount,
        received_date=tx["transaction_date"],
        payer_description=tx["description"],
        allocation_type="manual",
        created_by="admin",
    )
    audit(
        conn,
        "bank_transaction",
        transaction_id,
        "manual_match",
        new={"invoice_id": invoice_id, "amount_cents": amount},
        reason=reason,
        user_id="admin",
    )
    conn.commit()
    return payment_id


def list_reconciliation_transactions(conn) -> list[Any]:
    return conn.execute(
        """
        SELECT bt.*, i.invoice_number, i.amount_outstanding_cents, o.name AS owner_name
        FROM bank_transactions bt
        LEFT JOIN invoices i ON i.id = bt.suggested_invoice_id
        LEFT JOIN owners o ON o.id = i.owner_id
        ORDER BY
            CASE bt.status
                WHEN 'exception' THEN 1
                WHEN 'suggested' THEN 2
                WHEN 'unreconciled' THEN 3
                WHEN 'matched' THEN 4
                ELSE 5
            END,
            bt.transaction_date DESC,
            bt.id DESC
        """
    ).fetchall()


def export_csv(conn, kind: str) -> str:
    queries = {
        "jobs": """
            SELECT j.service_date, o.name AS owner, p.nickname AS property, j.description,
                   j.hours, j.rate_ex_gst_cents, j.gst_amount_cents, j.amount_inc_gst_cents,
                   j.status, i.invoice_number
            FROM cleaning_jobs j
            JOIN owners o ON o.id = j.owner_id
            JOIN properties p ON p.id = j.property_id
            LEFT JOIN invoices i ON i.id = j.invoice_id
            ORDER BY j.service_date, j.id
        """,
        "invoices": """
            SELECT i.invoice_number, o.name AS owner, i.period_start, i.period_end,
                   i.issue_date, i.due_date, i.subtotal_ex_gst_cents, i.gst_total_cents,
                   i.total_inc_gst_cents, i.amount_paid_cents, i.amount_outstanding_cents,
                   i.status
            FROM invoices i
            JOIN owners o ON o.id = i.owner_id
            ORDER BY i.issue_date, i.id
        """,
        "payments": """
            SELECT p.id, p.received_date, p.amount_cents, p.payer_description,
                   i.invoice_number, pa.amount_allocated_cents, pa.allocation_type
            FROM payments p
            JOIN payment_allocations pa ON pa.payment_id = p.id
            JOIN invoices i ON i.id = pa.invoice_id
            ORDER BY p.received_date, p.id
        """,
        "bank-transactions": """
            SELECT transaction_date, description, debit_cents, credit_cents, amount_cents,
                   balance_cents, account_identifier, status, match_reason
            FROM bank_transactions
            ORDER BY transaction_date, id
        """,
        "gst-summary": """
            SELECT i.period_start, i.period_end, i.invoice_number, i.issue_date,
                   o.name AS owner, i.subtotal_ex_gst_cents, i.gst_total_cents,
                   i.total_inc_gst_cents, i.amount_paid_cents, i.status
            FROM invoices i
            JOIN owners o ON o.id = i.owner_id
            ORDER BY i.issue_date, i.id
        """,
    }
    if kind not in queries:
        raise ValueError("Unknown export")
    rows = conn.execute(queries[kind]).fetchall()
    output = io.StringIO()
    if not rows:
        return ""
    writer = csv.DictWriter(output, fieldnames=rows[0].keys())
    writer.writeheader()
    for row in rows:
        writer.writerow(dict(row))
    return output.getvalue()


def seed_demo(conn) -> None:
    if conn.execute("SELECT COUNT(*) AS c FROM owners").fetchone()["c"]:
        return
    owner_a = find_or_create_owner(
        conn,
        "Harbour Property Group",
        billing_email="accounts@harbour.example",
        default_rate_ex_gst_cents=cents("72.50"),
    )
    owner_b = find_or_create_owner(
        conn,
        "Southern Stays",
        billing_email="finance@southernstays.example",
        default_rate_ex_gst_cents=cents("68.00"),
    )
    prop_a1 = find_or_create_property(
        conn, owner_a, "12 Salamanca Place, Hobart TAS", nickname="Salamanca 12"
    )
    prop_a2 = find_or_create_property(
        conn, owner_a, "44 Battery Point Road, Battery Point TAS", nickname="Battery Point 44"
    )
    prop_b1 = find_or_create_property(
        conn, owner_b, "8 Sandy Bay Road, Sandy Bay TAS", nickname="Sandy Bay 8"
    )
    today = date.today()
    first = date(today.year, today.month, 1)
    previous_month = first - timedelta(days=15)
    month_start = date(previous_month.year, previous_month.month, 1)
    create_job(
        conn,
        owner_id=owner_a,
        property_id=prop_a1,
        service_date=(month_start + timedelta(days=2)).isoformat(),
        description="Standard turnover clean",
        hours="2.5",
        rate_ex_gst_cents=cents("72.50"),
    )
    create_job(
        conn,
        owner_id=owner_a,
        property_id=prop_a2,
        service_date=(month_start + timedelta(days=10)).isoformat(),
        description="Deep clean after checkout",
        hours="4",
        rate_ex_gst_cents=cents("72.50"),
    )
    create_job(
        conn,
        owner_id=owner_b,
        property_id=prop_b1,
        service_date=(month_start + timedelta(days=14)).isoformat(),
        description="Linen change and kitchen reset",
        hours="3",
        rate_ex_gst_cents=cents("68.00"),
    )
    conn.commit()
