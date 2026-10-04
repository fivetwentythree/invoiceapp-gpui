from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from clean_ledger.services import get_invoice, get_invoice_lines, get_settings, record_file


ROOT = Path(__file__).resolve().parents[1]
PDF_RENDERER = Path(os.environ.get("CLEAN_LEDGER_PDF_RENDERER", ROOT / "scripts" / "render-invoice-pdf.mjs"))
NODE_BINARY = os.environ.get("CLEAN_LEDGER_NODE_BINARY", "node")


def _row_dict(row) -> dict:
    return dict(row)


def invoice_pdf_payload(conn, invoice_id: int) -> dict:
    invoice = get_invoice(conn, invoice_id)
    if not invoice:
        raise ValueError("Invoice not found")
    return {
        "settings": _row_dict(get_settings(conn)),
        "invoice": _row_dict(invoice),
        "lines": [_row_dict(line) for line in get_invoice_lines(conn, invoice_id)],
    }


def invoice_pdf_bytes(conn, invoice_id: int) -> bytes:
    payload = json.dumps(invoice_pdf_payload(conn, invoice_id), default=str).encode("utf-8")
    env = os.environ.copy()
    if os.environ.get("CLEAN_LEDGER_ELECTRON_RUN_AS_NODE") == "1":
        env["ELECTRON_RUN_AS_NODE"] = "1"
    try:
        result = subprocess.run(
            [NODE_BINARY, str(PDF_RENDERER)],
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=Path(PDF_RENDERER).resolve().parent,
            env=env,
            check=True,
            timeout=30,
        )
    except subprocess.CalledProcessError as exc:
        error = exc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Invoice PDF renderer failed: {error}") from exc
    except FileNotFoundError as exc:
        raise RuntimeError("Node.js or Electron-as-Node is required to render invoice PDFs") from exc
    if not result.stdout.startswith(b"%PDF"):
        raise RuntimeError("Invoice PDF renderer returned invalid PDF data")
    return result.stdout


def store_invoice_pdf(conn, invoice_id: int, invoice_dir: Path) -> int:
    invoice = get_invoice(conn, invoice_id)
    if not invoice:
        raise ValueError("Invoice not found")
    data = invoice_pdf_bytes(conn, invoice_id)
    filename = f"{invoice['invoice_number']}.pdf"
    path = invoice_dir / filename
    path.write_bytes(data)
    return record_file(
        conn,
        path=str(path),
        filename=filename,
        mime_type="application/pdf",
        file_type="invoice_pdf",
        data=data,
    )
