from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from email import policy
from email.parser import BytesParser
from pathlib import Path

from clean_ledger.database import connect, reset_database
from clean_ledger.excel_import import import_excel_paste_jobs, parse_excel_paste, rate_ex_gst_from_inc_gst
from clean_ledger.excel_invoice import invoice_excel_workbook_bytes
from clean_ledger.pdf import invoice_pdf_bytes, invoice_pdf_payload, store_invoice_pdf
from clean_ledger.services import (
    add_manual_invoice_line,
    calculate_totals,
    cents,
    create_draft_invoices,
    create_job,
    create_payment_allocation,
    delete_job,
    delete_invoice,
    delete_invoice_line,
    find_property_by_label,
    find_or_create_owner,
    find_or_create_property,
    get_invoice,
    get_invoice_lines,
    group_unbilled_jobs,
    import_bank_csv,
    import_jobs_csv,
    invoice_email_body,
    list_invoices,
    list_reconciliation_transactions,
    manual_match_transaction,
    purge_inactive_uninvoiced_properties,
    purge_placeholder_import_properties,
    remove_property,
    resolve_import_owner_property,
    send_invoice,
    update_invoice_line,
    update_owner_details,
    update_property_details,
)
from clean_ledger.web import is_file_within_directory


class CleanLedgerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = connect(":memory:")
        reset_database(self.conn)

    def tearDown(self) -> None:
        self.conn.close()

    def owner_property(
        self,
        *,
        owner_name: str = "Harbour Property Group",
        email: str = "ap@example.com",
        invoice_number_seed: str | None = None,
    ):
        owner_id = find_or_create_owner(
            self.conn,
            owner_name,
            billing_email=email,
            default_rate_ex_gst_cents=cents("72.50"),
        )
        property_id = find_or_create_property(
            self.conn,
            owner_id,
            "12 Salamanca Place, Hobart TAS",
            nickname="Salamanca 12",
            invoice_number_seed=invoice_number_seed,
        )
        self.conn.commit()
        return owner_id, property_id

    def draft_invoice(self):
        owner_id, property_id = self.owner_property(invoice_number_seed="HPG001")
        create_job(
            self.conn,
            owner_id=owner_id,
            property_id=property_id,
            service_date="2026-05-03",
            description="Standard turnover clean",
            hours="2.5",
            rate_ex_gst_cents=cents("72.50"),
        )
        create_job(
            self.conn,
            owner_id=owner_id,
            property_id=property_id,
            service_date="2026-05-12",
            description="Deep clean",
            hours="4",
            rate_ex_gst_cents=cents("72.50"),
        )
        created = create_draft_invoices(self.conn, "2026-05")
        self.assertEqual(len(created), 1)
        return created[0]


class MoneyTests(CleanLedgerTestCase):
    def test_gst_rounding_uses_integer_cents(self) -> None:
        totals = calculate_totals("2.5", cents("72.50"))
        self.assertEqual(totals["amount_ex_gst_cents"], 18125)
        self.assertEqual(totals["gst_amount_cents"], 1813)
        self.assertEqual(totals["amount_inc_gst_cents"], 19938)

    def test_duplicate_job_is_skipped_deterministically(self) -> None:
        owner_id, property_id = self.owner_property()
        first_id, first_status = create_job(
            self.conn,
            owner_id=owner_id,
            property_id=property_id,
            service_date="2026-05-03",
            description="Standard turnover clean",
            hours="2.5",
            rate_ex_gst_cents=cents("72.50"),
        )
        second_id, second_status = create_job(
            self.conn,
            owner_id=owner_id,
            property_id=property_id,
            service_date="2026-05-03",
            description="Standard turnover clean",
            hours="2.5",
            rate_ex_gst_cents=cents("72.50"),
        )
        self.assertIsNotNone(first_id)
        self.assertEqual(first_status, "created")
        self.assertIsNone(second_id)
        self.assertEqual(second_status, "duplicate")

    def test_existing_owner_details_are_updated_from_manual_entry(self) -> None:
        owner_id = find_or_create_owner(
            self.conn,
            "Whiskey Owner",
            billing_email="old@example.com",
            default_rate_ex_gst_cents=cents("40.00"),
        )
        updated_id = find_or_create_owner(
            self.conn,
            "Whiskey Owner",
            billing_email="accounts@example.com",
            phone="0400 000 000",
            abn="12 345 678 901",
            billing_address_line_1="1 Billing Street, Hobart TAS",
            default_payment_terms_days=21,
            default_rate_ex_gst_cents=cents("45.00"),
        )
        owner = self.conn.execute("SELECT * FROM owners WHERE id = ?", (owner_id,)).fetchone()

        self.assertEqual(updated_id, owner_id)
        self.assertEqual(owner["billing_email"], "accounts@example.com")
        self.assertEqual(owner["phone"], "0400 000 000")
        self.assertEqual(owner["abn"], "12 345 678 901")
        self.assertEqual(owner["billing_address_line_1"], "1 Billing Street, Hobart TAS")
        self.assertEqual(owner["default_payment_terms_days"], 21)
        self.assertEqual(owner["default_rate_ex_gst_cents"], cents("45.00"))

    def test_manual_property_owner_details_match_imported_placeholder_property(self) -> None:
        owner_id = find_or_create_owner(self.conn, "Warwick Owner")
        property_id = find_or_create_property(self.conn, owner_id, "Warwick", nickname="Warwick")
        create_job(
            self.conn,
            owner_id=owner_id,
            property_id=property_id,
            service_date="2026-05-31",
            description="Airbnb Cleaning",
            hours="3",
            rate_ex_gst_cents=cents("45.00"),
        )

        matched_property = find_property_by_label(self.conn, "Warwick")
        self.assertEqual(matched_property["id"], property_id)
        update_owner_details(
            self.conn,
            int(matched_property["owner_id"]),
            name="Stuart Thissen",
            billing_email="stuart@gmail.com",
        )
        update_property_details(
            self.conn,
            int(matched_property["id"]),
            address_line_1="1/34, Warwick Street, Hobart TAS 7000",
            nickname="Warwick",
        )
        job = self.conn.execute(
            """
            SELECT o.name AS owner_name, p.address_line_1
            FROM cleaning_jobs j
            JOIN owners o ON o.id = j.owner_id
            JOIN properties p ON p.id = j.property_id
            WHERE j.id = 1
            """
        ).fetchone()

        self.assertEqual(job["owner_name"], "Stuart Thissen")
        self.assertEqual(job["address_line_1"], "1/34, Warwick Street, Hobart TAS 7000")

    def test_import_reuses_corrected_property_owner_by_label(self) -> None:
        owner_id = find_or_create_owner(self.conn, "Warwick Owner")
        property_id = find_or_create_property(self.conn, owner_id, "Warwick", nickname="Warwick")
        update_owner_details(
            self.conn,
            owner_id,
            name="Stuart Thiessen",
            billing_email="stuart@example.com",
        )
        update_property_details(
            self.conn,
            property_id,
            address_line_1="214 Warwick Street, West Hobart TAS 7000",
            nickname="Warwick",
        )

        result = import_jobs_csv(
            self.conn,
            "\n".join(
                [
                    "service_date,property_nickname,property_address,owner_name,owner_email,hours,rate_ex_gst,description",
                    "2026-05-31,Warwick,,Warwick Owner,,3,45,Airbnb Cleaning",
                ]
            ),
        )
        job = self.conn.execute("SELECT * FROM cleaning_jobs").fetchone()
        property_count = self.conn.execute("SELECT COUNT(*) AS count FROM properties").fetchone()
        owner_count = self.conn.execute("SELECT COUNT(*) AS count FROM owners").fetchone()

        self.assertEqual(result["imported"], 1)
        self.assertEqual(job["property_id"], property_id)
        self.assertEqual(job["owner_id"], owner_id)
        self.assertEqual(property_count["count"], 1)
        self.assertEqual(owner_count["count"], 1)

    def test_import_resolver_does_not_use_conflicting_owner_for_existing_property(self) -> None:
        owner_id = find_or_create_owner(self.conn, "Stuart Thiessen")
        property_id = find_or_create_property(self.conn, owner_id, "Warwick", nickname="Warwick")

        resolved_owner_id, resolved_property_id = resolve_import_owner_property(
            self.conn,
            property_label="Warwick",
            owner_name="Warwick Owner",
            property_nickname="Warwick",
            default_rate_ex_gst_cents=cents("45.00"),
        )

        self.assertEqual(resolved_owner_id, owner_id)
        self.assertEqual(resolved_property_id, property_id)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) AS count FROM owners").fetchone()["count"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) AS count FROM properties").fetchone()["count"], 1)

    def test_csv_import_requires_owner_name_for_unknown_property(self) -> None:
        result = import_jobs_csv(
            self.conn,
            "\n".join(
                [
                    "service_date,property_nickname,property_address,owner_name,owner_email,hours,rate_ex_gst,description",
                    "2026-05-31,New Place,,,,3,45,Airbnb Cleaning",
                ]
            ),
        )
        owner_count = self.conn.execute("SELECT COUNT(*) AS count FROM owners").fetchone()
        property_count = self.conn.execute("SELECT COUNT(*) AS count FROM properties").fetchone()
        job_count = self.conn.execute("SELECT COUNT(*) AS count FROM cleaning_jobs").fetchone()

        self.assertEqual(result["imported"], 0)
        self.assertEqual(result["errors"], 1)
        self.assertIn("Owner name is required", result["error_rows"][0]["error"])
        self.assertEqual(owner_count["count"], 0)
        self.assertEqual(property_count["count"], 0)
        self.assertEqual(job_count["count"], 0)

    def test_removed_property_is_soft_deleted_and_not_import_matched(self) -> None:
        owner_id = find_or_create_owner(self.conn, "Warwick Owner")
        property_id = find_or_create_property(self.conn, owner_id, "Warwick", nickname="Warwick")

        self.assertIsNotNone(find_property_by_label(self.conn, "Warwick"))
        remove_property(self.conn, property_id)
        property_row = self.conn.execute(
            "SELECT active FROM properties WHERE id = ?",
            (property_id,),
        ).fetchone()

        self.assertEqual(property_row["active"], 0)
        self.assertIsNone(find_property_by_label(self.conn, "Warwick"))

    def test_remove_property_blocks_open_jobs(self) -> None:
        owner_id = find_or_create_owner(self.conn, "Removed Owner")
        property_id = find_or_create_property(self.conn, owner_id, "Removed Property", nickname="Removed")
        create_job(
            self.conn,
            owner_id=owner_id,
            property_id=property_id,
            service_date="2026-05-31",
            description="Airbnb Cleaning",
            hours="3",
            rate_ex_gst_cents=cents("45.00"),
        )

        with self.assertRaisesRegex(ValueError, "unbilled jobs"):
            remove_property(self.conn, property_id)

        property_row = self.conn.execute(
            "SELECT active FROM properties WHERE id = ?",
            (property_id,),
        ).fetchone()
        self.assertEqual(property_row["active"], 1)

    def test_cleanup_purges_inactive_uninvoiced_properties(self) -> None:
        owner_id = find_or_create_owner(self.conn, "Removed Owner")
        property_id = find_or_create_property(self.conn, owner_id, "Removed Property", nickname="Removed")
        create_job(
            self.conn,
            owner_id=owner_id,
            property_id=property_id,
            service_date="2026-05-31",
            description="Airbnb Cleaning",
            hours="3",
            rate_ex_gst_cents=cents("45.00"),
        )
        self.conn.execute("UPDATE properties SET active = 0 WHERE id = ?", (property_id,))

        result = purge_inactive_uninvoiced_properties(self.conn)
        property_count = self.conn.execute("SELECT COUNT(*) AS count FROM properties").fetchone()
        job_count = self.conn.execute("SELECT COUNT(*) AS count FROM cleaning_jobs").fetchone()
        owner = self.conn.execute("SELECT active FROM owners WHERE id = ?", (owner_id,)).fetchone()

        self.assertEqual(result, {"properties": 1, "jobs": 1, "owners_deactivated": 1})
        self.assertEqual(property_count["count"], 0)
        self.assertEqual(job_count["count"], 0)
        self.assertEqual(owner["active"], 0)

    def test_cleanup_keeps_inactive_properties_with_invoice_history(self) -> None:
        invoice_id = self.draft_invoice()
        prop = self.conn.execute("SELECT property_id FROM cleaning_jobs LIMIT 1").fetchone()
        remove_property(self.conn, int(prop["property_id"]))

        result = purge_inactive_uninvoiced_properties(self.conn)
        invoice = get_invoice(self.conn, invoice_id)
        property_count = self.conn.execute("SELECT COUNT(*) AS count FROM properties").fetchone()
        job_count = self.conn.execute("SELECT COUNT(*) AS count FROM cleaning_jobs").fetchone()

        self.assertEqual(result, {"properties": 0, "jobs": 0, "owners_deactivated": 0})
        self.assertIsNotNone(invoice)
        self.assertEqual(property_count["count"], 1)
        self.assertEqual(job_count["count"], 2)

    def test_cleanup_purges_placeholder_workbook_import_property(self) -> None:
        owner_id = find_or_create_owner(self.conn, "Whiskey Owner")
        property_id = find_or_create_property(self.conn, owner_id, "Whiskey", nickname="Whiskey")
        create_job(
            self.conn,
            owner_id=owner_id,
            property_id=property_id,
            service_date="2026-05-31",
            description="Airbnb Cleaning",
            hours="3",
            rate_ex_gst_cents=cents("45.00"),
            internal_note="Imported from May invoices.xlsx; property label: Whiskey; income inc GST: 14850",
        )

        result = purge_placeholder_import_properties(self.conn)
        owner_count = self.conn.execute("SELECT COUNT(*) AS count FROM owners").fetchone()
        property_count = self.conn.execute("SELECT COUNT(*) AS count FROM properties").fetchone()
        job_count = self.conn.execute("SELECT COUNT(*) AS count FROM cleaning_jobs").fetchone()

        self.assertEqual(result, {"properties": 1, "jobs": 1, "owners_deleted": 1})
        self.assertEqual(owner_count["count"], 0)
        self.assertEqual(property_count["count"], 0)
        self.assertEqual(job_count["count"], 0)

    def test_cleanup_keeps_real_owner_workbook_import_property(self) -> None:
        owner_id = find_or_create_owner(self.conn, "Bert Carson")
        property_id = find_or_create_property(self.conn, owner_id, "Whiskey", nickname="Whiskey")
        create_job(
            self.conn,
            owner_id=owner_id,
            property_id=property_id,
            service_date="2026-05-31",
            description="Airbnb Cleaning",
            hours="3",
            rate_ex_gst_cents=cents("45.00"),
            internal_note="Imported from May invoices.xlsx; property label: Whiskey; income inc GST: 14850",
        )

        result = purge_placeholder_import_properties(self.conn)
        owner_count = self.conn.execute("SELECT COUNT(*) AS count FROM owners").fetchone()
        property_count = self.conn.execute("SELECT COUNT(*) AS count FROM properties").fetchone()
        job_count = self.conn.execute("SELECT COUNT(*) AS count FROM cleaning_jobs").fetchone()

        self.assertEqual(result, {"properties": 0, "jobs": 0, "owners_deleted": 0})
        self.assertEqual(owner_count["count"], 1)
        self.assertEqual(property_count["count"], 1)
        self.assertEqual(job_count["count"], 1)

    def test_static_file_containment_rejects_sibling_prefix_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "static"
            sibling = Path(tmp) / "static_evil"
            root.mkdir()
            sibling.mkdir()
            inside = root / "styles.css"
            outside = sibling / "secret.css"
            inside.write_text("body {}", encoding="utf-8")
            outside.write_text("secret", encoding="utf-8")

            self.assertTrue(is_file_within_directory(inside.resolve(), root.resolve()))
            self.assertFalse(is_file_within_directory(outside.resolve(), root.resolve()))
            self.assertFalse(is_file_within_directory(root.resolve(), root.resolve()))

    def test_excel_paste_parser_ignores_extras_and_total_rows(self) -> None:
        text = "\n".join(
            [
                "Date\tproperty\tHr\tRate incl.GST\tIncome",
                "46164\t680 sb\t4\t49.5\t198",
                "\textras\t\t\t235",
                "Total\t\t\t\t5531.5",
            ]
        )
        rows, errors = parse_excel_paste(text)

        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].service_date, "2026-05-22")
        self.assertEqual(rows[0].property_label, "680 sb")
        self.assertEqual(rate_ex_gst_from_inc_gst(rows[0].rate_inc_gst_cents), cents("45.00"))

    def test_excel_paste_import_reuses_existing_property_from_may_sheet_shape(self) -> None:
        owner_id = find_or_create_owner(self.conn, "Mark Goodwin")
        property_id = find_or_create_property(
            self.conn,
            owner_id,
            "680 Sandy Bay Road, Sandy Bay TAS 7005",
            nickname="680",
        )
        text = "\n".join(
            [
                "Date\tproperty\tHr\tRate incl.GST\tIncome",
                "46164\t680 sb\t4\t49.5\t198",
                "46167\t680 sb\t4\t49.5\t198",
                "\textras\t\t\t235",
            ]
        )
        result = import_excel_paste_jobs(self.conn, text)
        jobs = self.conn.execute("SELECT * FROM cleaning_jobs ORDER BY service_date").fetchall()
        owner_count = self.conn.execute("SELECT COUNT(*) AS count FROM owners").fetchone()
        property_count = self.conn.execute("SELECT COUNT(*) AS count FROM properties").fetchone()

        self.assertEqual(result["imported"], 2)
        self.assertEqual(result["errors"], 0)
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0]["owner_id"], owner_id)
        self.assertEqual(jobs[0]["property_id"], property_id)
        self.assertEqual(jobs[0]["rate_ex_gst_cents"], cents("45.00"))
        self.assertEqual(jobs[0]["amount_inc_gst_cents"], cents("198.00"))
        self.assertEqual(owner_count["count"], 1)
        self.assertEqual(property_count["count"], 1)

    def test_excel_paste_import_does_not_create_placeholder_property(self) -> None:
        text = "\n".join(
            [
                "Date\tproperty\tHr\tRate incl.GST\tIncome",
                "46164\tNew Place\t4\t49.5\t198",
            ]
        )

        result = import_excel_paste_jobs(self.conn, text)
        job_count = self.conn.execute("SELECT COUNT(*) AS count FROM cleaning_jobs").fetchone()
        owner_count = self.conn.execute("SELECT COUNT(*) AS count FROM owners").fetchone()
        property_count = self.conn.execute("SELECT COUNT(*) AS count FROM properties").fetchone()

        self.assertEqual(result["imported"], 0)
        self.assertEqual(result["errors"], 1)
        self.assertIn("Unknown property", result["error_rows"][0]["error"])
        self.assertEqual(job_count["count"], 0)
        self.assertEqual(owner_count["count"], 0)
        self.assertEqual(property_count["count"], 0)


class InvoiceTests(CleanLedgerTestCase):
    def test_month_end_generates_one_invoice_per_property(self) -> None:
        invoice_id = self.draft_invoice()
        invoice = get_invoice(self.conn, invoice_id)
        lines = get_invoice_lines(self.conn, invoice_id)
        jobs = self.conn.execute("SELECT status, invoice_id FROM cleaning_jobs").fetchall()

        self.assertEqual(invoice["status"], "draft")
        self.assertEqual(invoice["issue_date"], "2026-05-31")
        self.assertEqual(invoice["amount_outstanding_cents"], invoice["total_inc_gst_cents"])
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(job["status"] == "draft_invoice" for job in jobs))
        self.assertTrue(all(job["invoice_id"] == invoice_id for job in jobs))

    def test_list_invoices_filters_by_billing_month(self) -> None:
        owner_id, property_id = self.owner_property(invoice_number_seed="HPG001")
        create_job(
            self.conn,
            owner_id=owner_id,
            property_id=property_id,
            service_date="2026-05-03",
            description="May turnover clean",
            hours="2.5",
            rate_ex_gst_cents=cents("72.50"),
        )
        create_job(
            self.conn,
            owner_id=owner_id,
            property_id=property_id,
            service_date="2026-06-04",
            description="June turnover clean",
            hours="2",
            rate_ex_gst_cents=cents("72.50"),
        )
        may_id = create_draft_invoices(self.conn, "2026-05")[0]
        june_id = create_draft_invoices(self.conn, "2026-06")[0]

        may_invoices = list_invoices(self.conn, month="2026-05")
        june_invoices = list_invoices(self.conn, month="2026-06")
        may_draft = list_invoices(self.conn, month="2026-05", status="draft")
        may_sent = list_invoices(self.conn, month="2026-05", status="sent")

        self.assertEqual([invoice["id"] for invoice in may_invoices], [may_id])
        self.assertEqual([invoice["id"] for invoice in june_invoices], [june_id])
        self.assertEqual([invoice["id"] for invoice in may_draft], [may_id])
        self.assertEqual(may_sent, [])

    def test_delete_unbilled_job_removes_job_and_audits(self) -> None:
        owner_id, property_id = self.owner_property()
        job_id, status = create_job(
            self.conn,
            owner_id=owner_id,
            property_id=property_id,
            service_date="2026-05-21",
            description="Window clean",
            hours="1.5",
            rate_ex_gst_cents=cents("72.50"),
        )
        self.assertEqual(status, "created")

        result = delete_job(self.conn, int(job_id))
        deleted = self.conn.execute("SELECT * FROM cleaning_jobs WHERE id = ?", (job_id,)).fetchone()
        audit_row = self.conn.execute(
            """
            SELECT *
            FROM audit_log
            WHERE entity_type = 'cleaning_job'
              AND entity_id = ?
              AND action = 'job_deleted'
            """,
            (job_id,),
        ).fetchone()

        self.assertEqual(result["status"], "deleted")
        self.assertIsNone(result["invoice_id"])
        self.assertIsNone(deleted)
        self.assertIsNotNone(audit_row)

    def test_delete_job_from_draft_invoice_removes_line_and_recalculates_totals(self) -> None:
        invoice_id = self.draft_invoice()
        lines = get_invoice_lines(self.conn, invoice_id)
        deleted_line = lines[0]
        remaining_line = lines[1]
        job_id = int(deleted_line["cleaning_job_id"])

        result = delete_job(self.conn, job_id)

        invoice = get_invoice(self.conn, invoice_id)
        remaining = get_invoice_lines(self.conn, invoice_id)
        deleted_job = self.conn.execute("SELECT * FROM cleaning_jobs WHERE id = ?", (job_id,)).fetchone()

        self.assertEqual(result["status"], "deleted_from_draft_invoice")
        self.assertEqual(result["invoice_id"], invoice_id)
        self.assertEqual(result["invoice_number"], "HPG001")
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["id"], remaining_line["id"])
        self.assertIsNone(deleted_job)
        self.assertEqual(invoice["total_inc_gst_cents"], remaining_line["amount_inc_gst_cents"])
        self.assertEqual(invoice["amount_outstanding_cents"], remaining_line["amount_inc_gst_cents"])

    def test_delete_job_blocks_locked_invoices_and_last_invoice_line(self) -> None:
        invoice_id = self.draft_invoice()
        first_line = get_invoice_lines(self.conn, invoice_id)[0]
        self.conn.execute("UPDATE invoices SET status = 'sent' WHERE id = ?", (invoice_id,))

        with self.assertRaisesRegex(ValueError, "draft invoices"):
            delete_job(self.conn, int(first_line["cleaning_job_id"]))

        owner_id, property_id = self.owner_property(
            owner_name="Single Line Owner",
            email="single@example.com",
            invoice_number_seed="SNG001",
        )
        create_job(
            self.conn,
            owner_id=owner_id,
            property_id=property_id,
            service_date="2026-05-23",
            description="Single visit",
            hours="2",
            rate_ex_gst_cents=cents("72.50"),
        )
        single_invoice_id = create_draft_invoices(self.conn, "2026-05")[0]
        single_line = get_invoice_lines(self.conn, single_invoice_id)[0]

        with self.assertRaisesRegex(ValueError, "at least one line item"):
            delete_job(self.conn, int(single_line["cleaning_job_id"]))

    def test_manual_invoice_line_add_edit_delete_recalculates_totals(self) -> None:
        invoice_id = self.draft_invoice()
        before = get_invoice(self.conn, invoice_id)

        line_id = add_manual_invoice_line(
            self.conn,
            invoice_id,
            service_date="2026-05-31",
            description="Extra supplies",
            amount_inc_gst_cents=cents("235.00"),
            gst_rate_bps=1000,
        )
        added = get_invoice(self.conn, invoice_id)
        manual_line = self.conn.execute(
            "SELECT * FROM invoice_line_items WHERE id = ?",
            (line_id,),
        ).fetchone()

        self.assertEqual(manual_line["line_type"], "manual")
        self.assertIsNone(manual_line["cleaning_job_id"])
        self.assertEqual(manual_line["quantity_hours"], "")
        self.assertEqual(manual_line["unit_rate_ex_gst_cents"], 0)
        self.assertEqual(manual_line["amount_ex_gst_cents"], cents("213.64"))
        self.assertEqual(manual_line["gst_amount_cents"], cents("21.36"))
        self.assertEqual(added["total_inc_gst_cents"], before["total_inc_gst_cents"] + cents("235.00"))
        self.assertEqual(added["amount_outstanding_cents"], added["total_inc_gst_cents"])

        update_invoice_line(
            self.conn,
            invoice_id,
            line_id,
            service_date="2026-05-30",
            description="Replacement linen",
            amount_inc_gst_cents=cents("110.00"),
            gst_rate_bps=1000,
        )
        edited = get_invoice(self.conn, invoice_id)
        edited_line = self.conn.execute(
            "SELECT * FROM invoice_line_items WHERE id = ?",
            (line_id,),
        ).fetchone()

        self.assertEqual(edited_line["description"], "Replacement linen")
        self.assertEqual(edited_line["service_date"], "2026-05-30")
        self.assertEqual(edited_line["amount_ex_gst_cents"], cents("100.00"))
        self.assertEqual(edited_line["gst_amount_cents"], cents("10.00"))
        self.assertEqual(edited["total_inc_gst_cents"], before["total_inc_gst_cents"] + cents("110.00"))

        delete_invoice_line(self.conn, invoice_id, line_id)
        after_delete = get_invoice(self.conn, invoice_id)
        self.assertEqual(after_delete["total_inc_gst_cents"], before["total_inc_gst_cents"])
        self.assertEqual(len(get_invoice_lines(self.conn, invoice_id)), 2)

    def test_job_invoice_line_edit_updates_source_job_and_totals(self) -> None:
        invoice_id = self.draft_invoice()
        before = get_invoice(self.conn, invoice_id)
        line = get_invoice_lines(self.conn, invoice_id)[0]
        job_id = int(line["cleaning_job_id"])

        update_invoice_line(
            self.conn,
            invoice_id,
            int(line["id"]),
            service_date="2026-05-20",
            description="Priority turnover clean",
            hours="3",
            rate_ex_gst_cents=cents("80.00"),
            gst_rate_bps=1000,
        )

        edited = get_invoice(self.conn, invoice_id)
        edited_line = self.conn.execute(
            "SELECT * FROM invoice_line_items WHERE id = ?",
            (line["id"],),
        ).fetchone()
        edited_job = self.conn.execute(
            "SELECT * FROM cleaning_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()

        self.assertGreater(edited["total_inc_gst_cents"], before["total_inc_gst_cents"])
        self.assertEqual(edited["total_inc_gst_cents"], cents("583.00"))
        self.assertEqual(edited["amount_outstanding_cents"], edited["total_inc_gst_cents"])
        self.assertEqual(edited_line["service_date"], "2026-05-20")
        self.assertEqual(edited_line["description"], "Priority turnover clean")
        self.assertEqual(edited_line["quantity_hours"], "3.00")
        self.assertEqual(edited_line["unit_rate_ex_gst_cents"], cents("80.00"))
        self.assertEqual(edited_line["amount_inc_gst_cents"], cents("264.00"))
        self.assertEqual(edited_job["service_date"], "2026-05-20")
        self.assertEqual(edited_job["description"], "Priority turnover clean")
        self.assertEqual(edited_job["hours"], "3.00")
        self.assertEqual(edited_job["rate_ex_gst_cents"], cents("80.00"))
        self.assertEqual(edited_job["amount_inc_gst_cents"], cents("264.00"))

    def test_job_invoice_line_delete_removes_source_job_and_recalculates_totals(self) -> None:
        invoice_id = self.draft_invoice()
        lines = get_invoice_lines(self.conn, invoice_id)
        deleted_line = lines[0]
        remaining_line = lines[1]
        job_id = int(deleted_line["cleaning_job_id"])

        delete_invoice_line(self.conn, invoice_id, int(deleted_line["id"]))

        invoice = get_invoice(self.conn, invoice_id)
        remaining = get_invoice_lines(self.conn, invoice_id)
        deleted_job = self.conn.execute("SELECT * FROM cleaning_jobs WHERE id = ?", (job_id,)).fetchone()

        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["id"], remaining_line["id"])
        self.assertIsNone(deleted_job)
        self.assertEqual(invoice["total_inc_gst_cents"], remaining_line["amount_inc_gst_cents"])
        self.assertEqual(invoice["amount_outstanding_cents"], remaining_line["amount_inc_gst_cents"])
        with self.assertRaisesRegex(ValueError, "at least one line item"):
            delete_invoice_line(self.conn, invoice_id, int(remaining_line["id"]))

    def test_invoice_line_edits_are_draft_only_and_duplicate_safe(self) -> None:
        invoice_id = self.draft_invoice()
        lines = get_invoice_lines(self.conn, invoice_id)
        first_line = lines[0]
        second_line = lines[1]
        manual_line_id = add_manual_invoice_line(
            self.conn,
            invoice_id,
            service_date="2026-05-31",
            description="Extra supplies",
            amount_inc_gst_cents=cents("55.00"),
            gst_rate_bps=1000,
        )

        with self.assertRaisesRegex(ValueError, "matching cleaning job"):
            update_invoice_line(
                self.conn,
                invoice_id,
                int(first_line["id"]),
                service_date=second_line["service_date"],
                description=second_line["description"],
                hours=second_line["quantity_hours"],
                rate_ex_gst_cents=int(second_line["unit_rate_ex_gst_cents"]),
                gst_rate_bps=1000,
            )

        self.conn.execute("UPDATE invoices SET status = 'sent' WHERE id = ?", (invoice_id,))
        with self.assertRaisesRegex(ValueError, "Only draft invoices"):
            add_manual_invoice_line(
                self.conn,
                invoice_id,
                service_date="2026-05-31",
                description="Sent invoice extra",
                amount_inc_gst_cents=cents("55.00"),
                gst_rate_bps=1000,
            )
        with self.assertRaisesRegex(ValueError, "Only draft invoices"):
            update_invoice_line(
                self.conn,
                invoice_id,
                manual_line_id,
                service_date="2026-05-31",
                description="Sent invoice edit",
                amount_inc_gst_cents=cents("60.00"),
                gst_rate_bps=1000,
            )
        with self.assertRaisesRegex(ValueError, "Only draft invoices"):
            update_invoice_line(
                self.conn,
                invoice_id,
                int(first_line["id"]),
                service_date="2026-05-31",
                description="Sent invoice job edit",
                hours="2",
                rate_ex_gst_cents=cents("72.50"),
                gst_rate_bps=1000,
            )
        with self.assertRaisesRegex(ValueError, "Only draft invoices"):
            delete_invoice_line(self.conn, invoice_id, manual_line_id)
        with self.assertRaisesRegex(ValueError, "Only draft invoices"):
            delete_invoice_line(self.conn, invoice_id, int(first_line["id"]))

    def test_delete_invoice_with_manual_line_resets_numbering(self) -> None:
        invoice_id = self.draft_invoice()
        add_manual_invoice_line(
            self.conn,
            invoice_id,
            service_date="2026-05-31",
            description="Extra supplies",
            amount_inc_gst_cents=cents("55.00"),
            gst_rate_bps=1000,
        )

        result = delete_invoice(self.conn, invoice_id)

        self.assertEqual(result["invoice_number"], "HPG001")
        self.assertEqual(result["jobs_deleted"], 2)
        self.assertEqual(result["line_items_deleted"], 3)
        self.assertIsNone(get_invoice(self.conn, invoice_id))

    def test_invoice_exports_allow_manual_amount_only_lines(self) -> None:
        invoice_id = self.draft_invoice()
        add_manual_invoice_line(
            self.conn,
            invoice_id,
            service_date="2026-05-31",
            description="Extra supplies",
            amount_inc_gst_cents=cents("55.00"),
            gst_rate_bps=1000,
        )

        pdf = invoice_pdf_bytes(self.conn, invoice_id)
        workbook = invoice_excel_workbook_bytes(self.conn, invoice_id)

        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertTrue(workbook.startswith(b"PK"))

    def test_property_invoice_number_sequence_is_used_and_incremented(self) -> None:
        owner_id, property_id = self.owner_property(invoice_number_seed="STSB070")
        create_job(
            self.conn,
            owner_id=owner_id,
            property_id=property_id,
            service_date="2026-05-03",
            description="Standard turnover clean",
            hours="2.5",
            rate_ex_gst_cents=cents("72.50"),
        )
        first_created = create_draft_invoices(self.conn, "2026-05")
        first_invoice = get_invoice(self.conn, first_created[0])
        create_job(
            self.conn,
            owner_id=owner_id,
            property_id=property_id,
            service_date="2026-06-03",
            description="Standard turnover clean",
            hours="2.5",
            rate_ex_gst_cents=cents("72.50"),
        )
        second_created = create_draft_invoices(self.conn, "2026-06")
        second_invoice = get_invoice(self.conn, second_created[0])
        prop = self.conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()

        self.assertEqual(first_invoice["invoice_number"], "STSB070")
        self.assertEqual(second_invoice["invoice_number"], "STSB071")
        self.assertEqual(prop["next_invoice_number"], 72)
        self.assertEqual(prop["invoice_number_padding"], 3)

    def test_saving_property_invoice_number_renumbers_existing_invoice(self) -> None:
        owner_id, property_id = self.owner_property(invoice_number_seed="HPG001")
        create_job(
            self.conn,
            owner_id=owner_id,
            property_id=property_id,
            service_date="2026-05-03",
            description="Standard turnover clean",
            hours="2.5",
            rate_ex_gst_cents=cents("72.50"),
        )
        created = create_draft_invoices(self.conn, "2026-05")
        before = get_invoice(self.conn, created[0])
        self.conn.execute("UPDATE invoices SET status = 'sent' WHERE id = ?", (created[0],))

        update_property_details(self.conn, property_id, invoice_number_seed="STSB070")
        after = get_invoice(self.conn, created[0])
        prop = self.conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()

        self.assertEqual(before["invoice_number"], "HPG001")
        self.assertEqual(after["invoice_number"], "STSB070")
        self.assertEqual(prop["next_invoice_number"], 71)

    def test_missing_owner_email_does_not_block_draft_generation(self) -> None:
        owner_id, property_id = self.owner_property(email="", invoice_number_seed="HPG001")
        create_job(
            self.conn,
            owner_id=owner_id,
            property_id=property_id,
            service_date="2026-05-03",
            description="Standard turnover clean",
            hours="2.5",
            rate_ex_gst_cents=cents("72.50"),
        )
        groups = group_unbilled_jobs(self.conn, "2026-05")
        created = create_draft_invoices(self.conn, "2026-05")

        self.assertEqual(groups[0].issues, [])
        self.assertEqual(len(created), 1)

    def test_missing_property_invoice_number_blocks_draft_generation(self) -> None:
        owner_id, property_id = self.owner_property()
        create_job(
            self.conn,
            owner_id=owner_id,
            property_id=property_id,
            service_date="2026-05-03",
            description="Standard turnover clean",
            hours="2.5",
            rate_ex_gst_cents=cents("72.50"),
        )
        groups = group_unbilled_jobs(self.conn, "2026-05")
        created = create_draft_invoices(self.conn, "2026-05")

        self.assertEqual(groups[0].issues, ["Missing property invoice number"])
        self.assertEqual(created, [])

    def test_one_off_property_invoice_number_can_be_set_then_billed(self) -> None:
        owner_id, property_id = self.owner_property(
            owner_name="John Appleseed",
            email="john@example.com",
        )
        create_job(
            self.conn,
            owner_id=owner_id,
            property_id=property_id,
            service_date="2026-05-03",
            description="One-off garden clean",
            hours="10",
            rate_ex_gst_cents=cents("45.00"),
        )

        before = group_unbilled_jobs(self.conn, "2026-05")
        self.assertEqual(before[0].issues, ["Missing property invoice number"])

        update_property_details(self.conn, property_id, invoice_number_seed="JAP001")
        after = group_unbilled_jobs(self.conn, "2026-05")
        created = create_draft_invoices(self.conn, "2026-05")
        invoice = get_invoice(self.conn, created[0])
        prop = self.conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()

        self.assertEqual(after[0].issues, [])
        self.assertEqual(invoice["invoice_number"], "JAP001")
        self.assertEqual(prop["next_invoice_number"], 2)

    def test_delete_invoice_removes_invoice_and_source_jobs(self) -> None:
        invoice_id = self.draft_invoice()

        result = delete_invoice(self.conn, invoice_id)
        invoice = get_invoice(self.conn, invoice_id)
        lines = get_invoice_lines(self.conn, invoice_id)
        jobs = self.conn.execute(
            "SELECT status, invoice_id FROM cleaning_jobs ORDER BY id"
        ).fetchall()
        month_end_groups = group_unbilled_jobs(self.conn, "2026-05")

        self.assertEqual(result["invoice_number"], "HPG001")
        self.assertEqual(result["jobs_deleted"], 2)
        self.assertIsNone(invoice)
        self.assertEqual(lines, [])
        self.assertEqual(jobs, [])
        self.assertEqual(month_end_groups, [])

    def test_delete_invoice_clears_reconciliation_suggestions(self) -> None:
        invoice_id = self.draft_invoice()
        self.conn.execute(
            """
            INSERT INTO bank_transactions (
                transaction_date, description, credit_cents, amount_cents, unique_hash,
                status, suggested_invoice_id, match_reason
            )
            VALUES ('2026-06-04', 'Possible payment HPG001', 1000, 1000, 'suggested-hash',
                    'suggested', ?, 'candidate')
            """,
            (invoice_id,),
        )

        delete_invoice(self.conn, invoice_id)
        tx = self.conn.execute("SELECT * FROM bank_transactions WHERE unique_hash = 'suggested-hash'").fetchone()

        self.assertEqual(tx["status"], "unreconciled")
        self.assertIsNone(tx["suggested_invoice_id"])
        self.assertIsNone(tx["match_reason"])

    def test_delete_invoice_with_payment_history_is_blocked(self) -> None:
        invoice_id = self.draft_invoice()
        create_payment_allocation(
            self.conn,
            bank_transaction_id=None,
            invoice_id=invoice_id,
            amount_cents=cents("10.00"),
            received_date="2026-06-04",
            payer_description="Part payment",
            allocation_type="manual",
        )

        with self.assertRaisesRegex(ValueError, "payment history"):
            delete_invoice(self.conn, invoice_id)

        self.assertIsNotNone(get_invoice(self.conn, invoice_id))

    def test_delete_middle_invoice_shifts_property_sequence(self) -> None:
        owner_id, property_id = self.owner_property(invoice_number_seed="STSB070")
        for service_date in ("2026-05-03", "2026-06-03", "2026-07-03"):
            create_job(
                self.conn,
                owner_id=owner_id,
                property_id=property_id,
                service_date=service_date,
                description="Standard turnover clean",
                hours="2.5",
                rate_ex_gst_cents=cents("72.50"),
            )
        may_id = create_draft_invoices(self.conn, "2026-05")[0]
        june_id = create_draft_invoices(self.conn, "2026-06")[0]
        july_id = create_draft_invoices(self.conn, "2026-07")[0]

        result = delete_invoice(self.conn, june_id)
        may_invoice = get_invoice(self.conn, may_id)
        june_invoice = get_invoice(self.conn, june_id)
        july_invoice = get_invoice(self.conn, july_id)
        prop = self.conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()
        deleted_jobs = self.conn.execute(
            "SELECT status, invoice_id FROM cleaning_jobs WHERE service_date = '2026-06-03'"
        ).fetchall()
        month_end_groups = group_unbilled_jobs(self.conn, "2026-06")

        self.assertEqual(result["invoice_number"], "STSB071")
        self.assertEqual(result["jobs_deleted"], 1)
        self.assertEqual(result["renumbered_invoices"], [{"invoice_id": july_id, "old": "STSB072", "new": "STSB071"}])
        self.assertEqual(may_invoice["invoice_number"], "STSB070")
        self.assertIsNone(june_invoice)
        self.assertEqual(july_invoice["invoice_number"], "STSB071")
        self.assertEqual(prop["next_invoice_number"], 72)
        self.assertEqual(deleted_jobs, [])
        self.assertEqual(month_end_groups, [])

    def test_delete_latest_invoice_rolls_back_next_number(self) -> None:
        owner_id, property_id = self.owner_property(invoice_number_seed="STSB070")
        for service_date in ("2026-05-03", "2026-06-03"):
            create_job(
                self.conn,
                owner_id=owner_id,
                property_id=property_id,
                service_date=service_date,
                description="Standard turnover clean",
                hours="2.5",
                rate_ex_gst_cents=cents("72.50"),
            )
        create_draft_invoices(self.conn, "2026-05")
        june_id = create_draft_invoices(self.conn, "2026-06")[0]

        result = delete_invoice(self.conn, june_id)
        prop = self.conn.execute("SELECT * FROM properties WHERE id = ?", (property_id,)).fetchone()

        self.assertEqual(result["invoice_number"], "STSB071")
        self.assertEqual(result["jobs_deleted"], 1)
        self.assertEqual(result["renumbered_invoices"], [])
        self.assertEqual(prop["next_invoice_number"], 71)

    def test_delete_blocks_when_later_invoice_has_payment_history(self) -> None:
        owner_id, property_id = self.owner_property(invoice_number_seed="STSB070")
        for service_date in ("2026-05-03", "2026-06-03"):
            create_job(
                self.conn,
                owner_id=owner_id,
                property_id=property_id,
                service_date=service_date,
                description="Standard turnover clean",
                hours="2.5",
                rate_ex_gst_cents=cents("72.50"),
            )
        may_id = create_draft_invoices(self.conn, "2026-05")[0]
        june_id = create_draft_invoices(self.conn, "2026-06")[0]
        create_payment_allocation(
            self.conn,
            bank_transaction_id=None,
            invoice_id=june_id,
            amount_cents=cents("10.00"),
            received_date="2026-06-04",
            payer_description="Part payment",
            allocation_type="manual",
        )

        with self.assertRaisesRegex(ValueError, "payment history"):
            delete_invoice(self.conn, may_id)

        self.assertEqual(get_invoice(self.conn, may_id)["invoice_number"], "STSB070")
        self.assertEqual(get_invoice(self.conn, june_id)["invoice_number"], "STSB071")

    def test_send_invoice_locks_jobs_and_stores_pdf_snapshot(self) -> None:
        invoice_id = self.draft_invoice()
        with tempfile.TemporaryDirectory() as pdf_dir, tempfile.TemporaryDirectory() as email_dir:
            pdf_file_id = store_invoice_pdf(self.conn, invoice_id, Path(pdf_dir))
            send_invoice(
                self.conn,
                invoice_id,
                pdf_file_id=pdf_file_id,
                email_body="Please pay this invoice.",
                email_dir=Path(email_dir),
            )
            invoice = get_invoice(self.conn, invoice_id)
            jobs = self.conn.execute("SELECT status FROM cleaning_jobs").fetchall()
            emails = list(Path(email_dir).glob("*.eml"))
            message = BytesParser(policy=policy.default).parsebytes(emails[0].read_bytes())
            attachments = list(message.iter_attachments())
            email_event = self.conn.execute(
                "SELECT * FROM email_events WHERE invoice_id = ?",
                (invoice_id,),
            ).fetchone()

        self.assertEqual(invoice["status"], "sent")
        self.assertIsNotNone(invoice["sent_at"])
        self.assertEqual(invoice["pdf_file_id"], pdf_file_id)
        self.assertTrue(all(job["status"] == "invoiced" for job in jobs))
        self.assertEqual(email_event["body"], "Please pay this invoice.")
        self.assertIn("Tax Invoice HPG001", email_event["subject"])
        self.assertEqual(len(emails), 1)
        self.assertEqual(message["To"], "ap@example.com")
        self.assertIn("Tax Invoice HPG001", message["Subject"])
        self.assertIn("Please pay this invoice.", message.get_body(preferencelist=("plain",)).get_content())
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].get_content_type(), "application/pdf")
        self.assertEqual(attachments[0].get_filename(), "HPG001.pdf")
        self.assertEqual(list(message.iter_parts())[-1].get_filename(), "HPG001.pdf")

    def test_send_invoice_requires_email_message(self) -> None:
        invoice_id = self.draft_invoice()
        with tempfile.TemporaryDirectory() as pdf_dir, tempfile.TemporaryDirectory() as email_dir:
            pdf_file_id = store_invoice_pdf(self.conn, invoice_id, Path(pdf_dir))

            with self.assertRaisesRegex(ValueError, "Email message is required"):
                send_invoice(
                    self.conn,
                    invoice_id,
                    pdf_file_id=pdf_file_id,
                    email_body="  \n  ",
                    email_dir=Path(email_dir),
                )

        invoice = get_invoice(self.conn, invoice_id)
        email_count = self.conn.execute(
            "SELECT COUNT(*) AS count FROM email_events WHERE invoice_id = ?",
            (invoice_id,),
        ).fetchone()

        self.assertEqual(invoice["status"], "draft")
        self.assertEqual(email_count["count"], 0)

    def test_reconciliation_uses_manual_line_property_names(self) -> None:
        invoice_id = self.draft_invoice()
        add_manual_invoice_line(
            self.conn,
            invoice_id,
            service_date="2026-05-31",
            description="Manual-only adjustment",
            amount_inc_gst_cents=cents("110.00"),
            gst_rate_bps=1000,
        )
        for line in list(get_invoice_lines(self.conn, invoice_id)):
            if line["cleaning_job_id"] is not None:
                delete_invoice_line(self.conn, invoice_id, int(line["id"]))
        invoice = get_invoice(self.conn, invoice_id)
        self.conn.execute("UPDATE invoices SET status = 'sent' WHERE id = ?", (invoice_id,))
        self.conn.commit()

        csv_text = "\n".join(
            [
                "date,description,credit,debit,balance,account",
                f"2026-06-04,Salamanca 12 payment,{invoice['amount_outstanding_cents'] / 100:.2f},,1000.00,main",
            ]
        )
        import_bank_csv(self.conn, csv_text)
        tx = list_reconciliation_transactions(self.conn)[0]
        paid_invoice = get_invoice(self.conn, invoice_id)

        self.assertEqual(tx["status"], "matched")
        self.assertEqual(paid_invoice["status"], "paid")

    def test_invoice_email_body_uses_requested_client_copy(self) -> None:
        invoice_id = self.draft_invoice()
        body = invoice_email_body(self.conn, invoice_id)

        self.assertIn("Hi Harbour,", body)
        self.assertIn("Hope this email finds you well.", body)
        self.assertIn("Please find attached the Invoice for May 2026", body)
        self.assertIn("The Total Amount Due is to be paid within the next 7 days", body)
        self.assertNotIn("The Total Amount Due of", body)
        self.assertIn(
            "Thank You & Kind Regards,\n\nAccount Name: Clean Ledger Demo Co\nBSB: 123-456\nAccount Number: 12345678",
            body,
        )
        self.assertTrue(body.endswith("Payment Reference: HPG001"))

    def test_owner_terms_update_recalculates_open_invoice_due_date(self) -> None:
        invoice_id = self.draft_invoice()
        invoice = get_invoice(self.conn, invoice_id)

        update_owner_details(
            self.conn,
            int(invoice["owner_id"]),
            default_payment_terms_days=7,
        )
        updated = get_invoice(self.conn, invoice_id)
        expected_due_date = (
            date.fromisoformat(invoice["issue_date"]) + timedelta(days=7)
        ).isoformat()

        self.assertEqual(updated["due_date"], expected_due_date)

    def test_invoice_pdf_is_rendered_from_invoice_payload(self) -> None:
        invoice_id = self.draft_invoice()
        payload = invoice_pdf_payload(self.conn, invoice_id)
        pdf = invoice_pdf_bytes(self.conn, invoice_id)

        self.assertEqual(payload["invoice"]["invoice_number"], "HPG001")
        self.assertEqual(len(payload["lines"]), 2)
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 3000)

    def test_invoice_excel_export_is_xlsx_package(self) -> None:
        invoice_id = self.draft_invoice()
        data = invoice_excel_workbook_bytes(self.conn, invoice_id)

        self.assertTrue(data.startswith(b"PK"))
        self.assertIn(b"xl/workbook.xml", data)


class ReconciliationTests(CleanLedgerTestCase):
    def sent_invoice(self):
        invoice_id = self.draft_invoice()
        self.conn.execute("UPDATE invoices SET status = 'sent' WHERE id = ?", (invoice_id,))
        self.conn.execute("UPDATE cleaning_jobs SET status = 'invoiced' WHERE invoice_id = ?", (invoice_id,))
        self.conn.commit()
        return get_invoice(self.conn, invoice_id)

    def test_exact_reference_auto_matches_and_duplicate_import_is_skipped(self) -> None:
        invoice = self.sent_invoice()
        csv_text = "\n".join(
            [
                "date,description,credit,debit,balance,account",
                f"2026-06-04,Payment {invoice['invoice_number']},{invoice['amount_outstanding_cents'] / 100:.2f},,1000.00,main",
            ]
        )
        result = import_bank_csv(self.conn, csv_text)
        duplicate_result = import_bank_csv(self.conn, csv_text)
        paid_invoice = get_invoice(self.conn, invoice["id"])
        tx = list_reconciliation_transactions(self.conn)[0]

        self.assertEqual(result["imported"], 1)
        self.assertEqual(duplicate_result["duplicates"], 1)
        self.assertEqual(paid_invoice["status"], "paid")
        self.assertEqual(paid_invoice["amount_outstanding_cents"], 0)
        self.assertEqual(tx["status"], "matched")

    def test_no_reference_exact_amount_with_strong_owner_hint_auto_matches(self) -> None:
        invoice = self.sent_invoice()
        csv_text = "\n".join(
            [
                "date,description,credit,debit,balance,account",
                f"2026-06-04,Harbour Property Group payment,{invoice['amount_outstanding_cents'] / 100:.2f},,1000.00,main",
            ]
        )
        import_bank_csv(self.conn, csv_text)
        tx = list_reconciliation_transactions(self.conn)[0]
        invoice_after = get_invoice(self.conn, invoice["id"])

        self.assertEqual(tx["status"], "matched")
        self.assertEqual(invoice_after["status"], "paid")

    def test_commonwealth_style_csv_without_iso_dates_is_imported(self) -> None:
        invoice = self.sent_invoice()
        csv_text = "\n".join(
            [
                "Date,Amount,Description,Balance",
                f"04/06/2026,{invoice['amount_outstanding_cents'] / 100:.2f} CR,Payment {invoice['invoice_number']},1000.00",
            ]
        )
        result = import_bank_csv(self.conn, csv_text, filename="cba.csv")
        tx = list_reconciliation_transactions(self.conn)[0]

        self.assertEqual(result["imported"], 1)
        self.assertEqual(tx["transaction_date"], "2026-06-04")
        self.assertEqual(tx["status"], "matched")

    def test_commonwealth_headerless_amount_description_csv_is_imported(self) -> None:
        invoice = self.sent_invoice()
        csv_text = "\n".join(
            [
                f'12/06/2026,"+{invoice["amount_outstanding_cents"] / 100:.2f}",'
                f'"Fast Transfer From Harbour Property Group Thank you","+4106.29"',
                '12/06/2026,"-29.00","Transfer To Someone CommBank App San churros","+4081.29"',
            ]
        )
        result = import_bank_csv(self.conn, csv_text, filename="CSVData.csv")
        txs = list_reconciliation_transactions(self.conn)
        matched = next(tx for tx in txs if int(tx["amount_cents"]) > 0)
        debit = next(tx for tx in txs if int(tx["amount_cents"]) < 0)
        invoice_after = get_invoice(self.conn, invoice["id"])

        self.assertEqual(result["imported"], 2)
        self.assertEqual(matched["transaction_date"], "2026-06-12")
        self.assertEqual(matched["description"], "Fast Transfer From Harbour Property Group Thank you")
        self.assertEqual(matched["amount_cents"], invoice["amount_outstanding_cents"])
        self.assertEqual(matched["balance_cents"], cents("4106.29"))
        self.assertEqual(matched["status"], "matched")
        self.assertEqual(debit["status"], "exception")
        self.assertEqual(invoice_after["status"], "paid")

    def test_invoice_reference_matches_when_bank_adds_spaces(self) -> None:
        invoice = self.sent_invoice()
        csv_text = "\n".join(
            [
                f'12/06/2026,"+{invoice["amount_outstanding_cents"] / 100:.2f}",'
                '"Fast Transfer From Customer CREDIT TO ACCOUNT Invoice HPG 001","+4106.29"',
            ]
        )
        import_bank_csv(self.conn, csv_text, filename="CSVData.csv")
        tx = list_reconciliation_transactions(self.conn)[0]
        invoice_after = get_invoice(self.conn, invoice["id"])

        self.assertEqual(tx["status"], "matched")
        self.assertEqual(invoice_after["status"], "paid")

    def test_prior_invoice_reference_blocks_fuzzy_matching_to_current_invoice(self) -> None:
        owner_id, property_id = self.owner_property(
            owner_name="Thom Port",
            email="thom@example.com",
            invoice_number_seed="TP1073",
        )
        create_job(
            self.conn,
            owner_id=owner_id,
            property_id=property_id,
            service_date="2026-05-03",
            description="Standard turnover clean",
            hours="6.5",
            rate_ex_gst_cents=cents("72.50"),
        )
        invoice_id = create_draft_invoices(self.conn, "2026-05")[0]
        self.conn.execute("UPDATE invoices SET status = 'sent' WHERE id = ?", (invoice_id,))
        self.conn.execute("UPDATE cleaning_jobs SET status = 'invoiced' WHERE invoice_id = ?", (invoice_id,))
        self.conn.commit()
        invoice = get_invoice(self.conn, invoice_id)

        csv_text = "\n".join(
            [
                "date,description,credit,debit,balance,account",
                f"2026-06-01,Fast Transfer From Mr Thomas Michael Wor TP1072 TP1072,{invoice['amount_outstanding_cents'] / 100:.2f},,1000.00,main",
            ]
        )
        import_bank_csv(self.conn, csv_text)
        tx = list_reconciliation_transactions(self.conn)[0]
        invoice_after = get_invoice(self.conn, invoice_id)

        self.assertEqual(tx["status"], "exception")
        self.assertIn("TP1072", tx["match_reason"])
        self.assertEqual(invoice_after["status"], "sent")
        self.assertEqual(invoice_after["amount_paid_cents"], 0)

    def test_manual_match_rejects_transaction_reference_for_different_invoice(self) -> None:
        invoice = self.sent_invoice()
        csv_text = "\n".join(
            [
                "date,description,credit,debit,balance,account",
                "2026-06-01,Direct Credit 333732 The Whisky Club STWC008,1039.50,,1000.00,main",
            ]
        )
        import_bank_csv(self.conn, csv_text)
        tx = list_reconciliation_transactions(self.conn)[0]

        with self.assertRaisesRegex(ValueError, "does not match selected invoice"):
            manual_match_transaction(self.conn, int(tx["id"]), int(invoice["id"]))

        invoice_after = get_invoice(self.conn, invoice["id"])
        self.assertEqual(invoice_after["status"], "sent")
        self.assertEqual(invoice_after["amount_paid_cents"], 0)

    def test_manual_match_rejects_allocation_above_invoice_outstanding(self) -> None:
        invoice = self.sent_invoice()
        csv_text = "\n".join(
            [
                "date,description,credit,debit,balance,account",
                f"2026-06-01,Harbour Property Group bulk payment,{(invoice['amount_outstanding_cents'] + 1000) / 100:.2f},,1000.00,main",
            ]
        )
        import_bank_csv(self.conn, csv_text)
        tx = list_reconciliation_transactions(self.conn)[0]

        with self.assertRaisesRegex(ValueError, "invoice outstanding"):
            manual_match_transaction(self.conn, int(tx["id"]), int(invoice["id"]))

        invoice_after = get_invoice(self.conn, invoice["id"])
        self.assertEqual(invoice_after["status"], "sent")
        self.assertEqual(invoice_after["amount_paid_cents"], 0)

    def test_no_reference_ambiguous_exact_amount_is_exception(self) -> None:
        first = self.sent_invoice()
        owner_id = find_or_create_owner(
            self.conn,
            "Salamanca Holdings",
            billing_email="sal@example.com",
            default_rate_ex_gst_cents=cents("72.50"),
        )
        property_id = find_or_create_property(
            self.conn,
            owner_id,
            "44 Battery Point Road, Hobart TAS",
            nickname="Battery Point 44",
            invoice_number_seed="BPT001",
        )
        create_job(
            self.conn,
            owner_id=owner_id,
            property_id=property_id,
            service_date="2026-05-03",
            description="Standard turnover clean",
            hours="6.5",
            rate_ex_gst_cents=cents("72.50"),
        )
        second_id = create_draft_invoices(self.conn, "2026-05")[0]
        self.conn.execute("UPDATE invoices SET status = 'sent' WHERE id = ?", (second_id,))
        self.conn.execute("UPDATE cleaning_jobs SET status = 'invoiced' WHERE invoice_id = ?", (second_id,))
        self.conn.commit()

        csv_text = "\n".join(
            [
                "date,description,credit,debit,balance,account",
                f"2026-06-04,Invoice payment,{first['amount_outstanding_cents'] / 100:.2f},,1000.00,main",
            ]
        )
        import_bank_csv(self.conn, csv_text)
        tx = list_reconciliation_transactions(self.conn)[0]

        self.assertEqual(tx["status"], "exception")
        self.assertIn("Ambiguous", tx["match_reason"])


if __name__ == "__main__":
    unittest.main()
