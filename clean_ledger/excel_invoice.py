from __future__ import annotations

import html
import zipfile
from datetime import date
from io import BytesIO

from clean_ledger.services import get_invoice, get_invoice_lines, get_settings


def excel_serial(iso_date: str) -> int:
    return (date.fromisoformat(iso_date) - date(1899, 12, 30)).days


def col_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def cell_xml(ref: str, value: object, style: int = 0) -> str:
    style_attr = f' s="{style}"' if style else ""
    if value is None:
        return f'<c r="{ref}"{style_attr}/>'
    if isinstance(value, int | float):
        return f'<c r="{ref}"{style_attr}><v>{value}</v></c>'
    escaped = html.escape(str(value), quote=False)
    return f'<c r="{ref}" t="inlineStr"{style_attr}><is><t>{escaped}</t></is></c>'


def row_xml(row_number: int, cells: dict[int, tuple[object, int]]) -> str:
    cell_parts = []
    for col_index, (value, style) in sorted(cells.items()):
        cell_parts.append(cell_xml(f"{col_name(col_index)}{row_number}", value, style))
    return f'<row r="{row_number}">{"".join(cell_parts)}</row>'


def invoice_excel_workbook_bytes(conn, invoice_id: int) -> bytes:
    invoice = get_invoice(conn, invoice_id)
    if not invoice:
        raise ValueError("Invoice not found")
    settings = get_settings(conn)
    line_items = get_invoice_lines(conn, invoice_id)

    business_phone = settings["phone"] or ""
    business_email = settings["email"] or ""
    bank_line = " | ".join(
        part
        for part in [
            f"BSB: {settings['bsb']}" if settings["bsb"] else "",
            f"Acc. No: {settings['account_number']}" if settings["account_number"] else "",
        ]
        if part
    )
    bill_lines = [
        invoice["owner_name"],
        invoice["billing_address_line_1"] or "",
        " ".join(
            part
            for part in [
                invoice["billing_city"],
                invoice["billing_state"],
                invoice["billing_postcode"],
            ]
            if part
        ),
    ]
    if not any(line for line in bill_lines[1:]) and line_items:
        bill_lines[1] = line_items[0]["property_label"]

    rows: list[tuple[int, dict[int, tuple[object, int]]]] = [
        (1, {4: ("TAX INVOICE", 1)}),
        (3, {4: (business_phone, 0)}),
        (4, {1: (f"ABN: {settings['abn']}", 0), 3: (business_email, 0)}),
        (7, {1: ("Billed To:", 2), 3: ("Invoice Number:", 2), 4: (invoice["invoice_number"], 0), 5: ("Invoice total:", 2)}),
        (8, {1: (bill_lines[0], 0), 3: ("Invoice Date:", 2), 4: (excel_serial(invoice["issue_date"]), 4), 5: (invoice["total_inc_gst_cents"] / 100, 3)}),
        (9, {1: (bill_lines[1], 0)}),
        (10, {1: (bill_lines[2], 0)}),
        (12, {1: ("Date", 2), 2: ("Description", 2), 3: ("Unit Cost", 2), 4: ("Hr Rate", 2), 5: ("Amount", 2)}),
    ]

    row_number = 13
    for item in line_items:
        is_manual = item["line_type"] == "manual" or not str(item["quantity_hours"] or "").strip()
        rate_inc_gst = ""
        hours = ""
        if not is_manual:
            rate_inc_gst = (
                int(item["unit_rate_ex_gst_cents"])
                + round(int(item["unit_rate_ex_gst_cents"]) * int(item["gst_rate_bps"]) / 10000)
            ) / 100
            hours = float(item["quantity_hours"])
        rows.append(
            (
                row_number,
                {
                    1: (excel_serial(item["service_date"]), 4),
                    2: (item["description"], 0),
                    3: (rate_inc_gst, 3),
                    4: (hours, 0),
                    5: (item["amount_inc_gst_cents"] / 100, 3),
                },
            )
        )
        row_number += 1

    summary_row = row_number + 1
    rows.extend(
        [
            (summary_row, {1: ("THANK YOU FOR YOUR BUSINESS!", 2), 4: ("GST incl.", 2), 5: (invoice["gst_total_cents"] / 100, 3)}),
            (summary_row + 1, {1: ("Please pay your invoice within the next 7 days", 0), 4: ("Amount Due", 2), 5: (invoice["amount_outstanding_cents"] / 100, 3)}),
            (summary_row + 2, {1: ("to the following account", 0)}),
            (summary_row + 3, {1: (f"Name: {settings['account_name'] or settings['business_name']}", 0)}),
            (summary_row + 4, {1: (bank_line, 0)}),
        ]
    )

    sheet_data = "".join(row_xml(row_number, cells) for row_number, cells in rows)
    sheet_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <cols>
    <col min="1" max="1" width="30" customWidth="1"/>
    <col min="2" max="2" width="24" customWidth="1"/>
    <col min="3" max="5" width="16" customWidth="1"/>
  </cols>
  <sheetData>{sheet_data}</sheetData>
</worksheet>"""
    workbook_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""
    styles_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <fonts count="3"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="16"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>
  <fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
  <borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
  <numFmts count="2"><numFmt numFmtId="164" formatCode="$#,##0.00"/><numFmt numFmtId="165" formatCode="dd/mm/yyyy"/></numFmts>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="5">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>
    <xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>
    <xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
    <xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
  </cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>"""
    root_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""
    workbook_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>"""

    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)
        zf.writestr("xl/styles.xml", styles_xml)
    return output.getvalue()
