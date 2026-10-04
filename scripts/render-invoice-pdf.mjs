import React from "react";
import { existsSync, readdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import {
  Document,
  Font,
  Page,
  StyleSheet,
  Text,
  View,
  renderToBuffer,
} from "@react-pdf/renderer";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const INTER = "Inter";
const SF_PRO_TEXT = "SF Pro Text";
const SF_PRO_TEXT_REGULAR = "/Library/Fonts/SF-Pro-Text-Regular.otf";
const SF_PRO_TEXT_MEDIUM = "/Library/Fonts/SF-Pro-Text-Medium.otf";
const SF_PRO_TEXT_SEMIBOLD = "/Library/Fonts/SF-Pro-Text-Semibold.otf";
const SF_PRO_TEXT_BOLD = "/Library/Fonts/SF-Pro-Text-Bold.otf";

const FONT_DIRS = [
  process.env.DOMESTICA_INTER_FONT_DIR,
  join(ROOT, "node_modules", "@fontsource", "inter", "files"),
  join(ROOT, "node_modules", "@fontsource-variable", "inter", "files"),
].filter(Boolean);

function findInterFont(preferredNames, fallbackPattern) {
  for (const fontDir of FONT_DIRS) {
    if (!existsSync(fontDir)) continue;
    const files = readdirSync(fontDir);
    for (const name of preferredNames) {
      if (files.includes(name)) return join(fontDir, name);
    }
    const fallback = files.find(
      (name) => name.endsWith(".woff2") && fallbackPattern.test(name)
    );
    if (fallback) return join(fontDir, fallback);
  }
  return "";
}

const INTER_REGULAR = findInterFont(
  ["inter-latin-400-normal.woff", "inter-latin-400-normal.woff2", "inter-latin-wght-normal.woff2"],
  /inter-latin.*(wght|400).*normal/
);
const INTER_MEDIUM =
  findInterFont(
    ["inter-latin-500-normal.woff", "inter-latin-500-normal.woff2"],
    /inter-latin.*500.*normal/
  ) || INTER_REGULAR;
const INTER_SEMIBOLD =
  findInterFont(
    ["inter-latin-600-normal.woff", "inter-latin-600-normal.woff2"],
    /inter-latin.*600.*normal/
  ) || INTER_REGULAR;
const INTER_BOLD =
  findInterFont(
    ["inter-latin-700-normal.woff", "inter-latin-700-normal.woff2"],
    /inter-latin.*700.*normal/
  ) || INTER_REGULAR;
const PDF_FONT_FAMILY = INTER_REGULAR
  ? INTER
  : existsSync(SF_PRO_TEXT_REGULAR)
    ? SF_PRO_TEXT
    : "Helvetica";

if (PDF_FONT_FAMILY === INTER) {
  Font.register({
    family: INTER,
    fonts: [
      { src: INTER_REGULAR, fontWeight: 400 },
      { src: INTER_MEDIUM, fontWeight: 500 },
      { src: INTER_SEMIBOLD, fontWeight: 600 },
      { src: INTER_BOLD, fontWeight: 700 },
    ],
  });
} else if (PDF_FONT_FAMILY === SF_PRO_TEXT) {
  Font.register({
    family: SF_PRO_TEXT,
    fonts: [
      { src: SF_PRO_TEXT_REGULAR, fontWeight: 400 },
      { src: SF_PRO_TEXT_MEDIUM, fontWeight: 500 },
      { src: SF_PRO_TEXT_SEMIBOLD, fontWeight: 600 },
      { src: SF_PRO_TEXT_BOLD, fontWeight: 700 },
    ],
  });
}

const styles = StyleSheet.create({
  page: {
    paddingTop: 0,
    paddingRight: 56,
    paddingBottom: 52,
    paddingLeft: 56,
    fontFamily: PDF_FONT_FAMILY,
    color: "#1a1f36",
    backgroundColor: "#ffffff",
    fontSize: 10.2,
  },
  topBar: {
    height: 3,
    marginLeft: -56,
    marginRight: -56,
    marginBottom: 46,
    backgroundColor: "#2eb67d",
  },
  header: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-start",
    marginBottom: 34,
  },
  titleBlock: {
    width: 240,
  },
  invoiceWord: {
    fontSize: 18.5,
    lineHeight: 1,
    fontWeight: 700,
    color: "#0a0f1f",
    marginBottom: 25,
  },
  detailGrid: {
    flexDirection: "row",
  },
  detailLabels: {
    width: 84,
  },
  detailValues: {
    width: 142,
  },
  detailLabel: {
    fontSize: 9,
    lineHeight: 1.48,
    fontWeight: 700,
    color: "#1a1f36",
  },
  detailValue: {
    fontSize: 9,
    lineHeight: 1.48,
    fontWeight: 700,
    color: "#1a1f36",
  },
  brandBlock: {
    width: 230,
    alignItems: "flex-end",
  },
  brandLine: {
    color: "#2eb67d",
    fontSize: 18,
    lineHeight: 0.95,
    fontWeight: 700,
  },
  brandDot: {
    color: "#635bff",
  },
  partyRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    marginBottom: 36,
  },
  partyBlock: {
    width: 230,
  },
  partyBlockRight: {
    width: 230,
    marginRight: 0,
  },
  partyLabel: {
    fontSize: 9.5,
    lineHeight: 1.35,
    fontWeight: 700,
    color: "#1a1f36",
    marginBottom: 3,
  },
  partyText: {
    fontSize: 9.8,
    lineHeight: 1.42,
    color: "#3c4257",
  },
  amountDue: {
    marginBottom: 43,
  },
  amountDueText: {
    fontSize: 14.6,
    lineHeight: 1.25,
    fontWeight: 700,
    color: "#0a0f1f",
  },
  amountDueSubtext: {
    marginTop: 8,
    fontSize: 9.7,
    color: "#4f566b",
  },
  table: {
    marginBottom: 17,
  },
  tableHeader: {
    flexDirection: "row",
    paddingBottom: 7,
    borderBottomWidth: 0.7,
    borderColor: "#0a0f1f",
  },
  tableRow: {
    flexDirection: "row",
    paddingTop: 8.5,
    paddingBottom: 10,
    borderBottomWidth: 0.5,
    borderColor: "#e6e8eb",
  },
  th: {
    fontSize: 9,
    fontWeight: 400,
    color: "#1a1f36",
  },
  td: {
    fontSize: 9.9,
    lineHeight: 1.35,
    color: "#1a1f36",
  },
  tdStrong: {
    fontSize: 10,
    lineHeight: 1.35,
    fontWeight: 700,
    color: "#1a1f36",
  },
  tdMuted: {
    marginTop: 1.8,
    fontSize: 9.1,
    lineHeight: 1.32,
    color: "#4f566b",
  },
  descCol: {
    width: 248,
  },
  qtyCol: {
    width: 61,
    textAlign: "right",
  },
  unitCol: {
    width: 83,
    textAlign: "right",
  },
  taxCol: {
    width: 87,
    textAlign: "right",
  },
  amountCol: {
    width: 89,
    textAlign: "right",
  },
  summarySection: {
    flexDirection: "row",
    justifyContent: "flex-end",
    alignItems: "flex-start",
    marginTop: 21,
  },
  invoiceFooter: {
    marginTop: 28,
    width: 310,
  },
  invoiceFooterText: {
    fontSize: 9.9,
    color: "#4f566b",
    lineHeight: 1.42,
  },
  summary: {
    width: 255,
    marginLeft: "auto",
  },
  summaryRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    paddingTop: 6.5,
    paddingBottom: 6.5,
    borderTopWidth: 0.5,
    borderColor: "#e6e8eb",
  },
  summaryLabel: {
    width: 168,
    fontSize: 9.9,
    lineHeight: 1.25,
    color: "#1a1f36",
  },
  summaryValue: {
    width: 86,
    fontSize: 9.9,
    lineHeight: 1.25,
    textAlign: "right",
    color: "#1a1f36",
  },
  amountDueRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    paddingTop: 9,
    borderTopWidth: 0.9,
    borderColor: "#0a0f1f",
  },
  amountDueLabel: {
    fontSize: 10.2,
    fontWeight: 700,
    color: "#0a0f1f",
  },
  amountDueValue: {
    fontSize: 10.2,
    fontWeight: 700,
    textAlign: "right",
    color: "#0a0f1f",
  },
  pageNumber: {
    position: "absolute",
    right: 54,
    bottom: 14,
    fontSize: 8.5,
    color: "#98a2b3",
  },
});

const AUD_FORMAT = new Intl.NumberFormat("en-AU", {
  style: "currency",
  currency: "AUD",
});

const DATE_FORMAT = new Intl.DateTimeFormat("en-AU", {
  day: "numeric",
  month: "long",
  year: "numeric",
});

const MONTH_FORMAT = new Intl.DateTimeFormat("en-AU", {
  month: "long",
  year: "numeric",
});

function money(cents) {
  const value = Number(cents || 0) / 100;
  return AUD_FORMAT.format(value);
}

function parseDate(value) {
  if (!value) return null;
  const parsed = new Date(`${value}T00:00:00`);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function longDate(value) {
  const parsed = parseDate(value);
  return parsed ? DATE_FORMAT.format(parsed) : String(value || "");
}

function date(value) {
  if (!value) return "";
  const [year, month, day] = String(value).split("-");
  if (!year || !month || !day) return String(value);
  return `${Number(day)}/${Number(month)}/${year}`;
}

function invoiceMonth(invoice) {
  const parsed = parseDate(invoice.period_start || invoice.issue_date);
  return parsed ? MONTH_FORMAT.format(parsed) : "";
}

function gstRateLabel(line) {
  const rate = Number(line.gst_rate_bps || 0) / 100;
  return Number.isInteger(rate) ? `${rate}% incl.` : `${rate.toFixed(2)}% incl.`;
}

function compactHours(value) {
  if (value === null || value === undefined || String(value).trim() === "") return "-";
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value || "");
  return Number.isInteger(number) ? String(number) : String(number).replace(/0+$/, "").replace(/\.$/, "");
}

function isManualLine(line) {
  return line.line_type === "manual" || String(line.quantity_hours || "").trim() === "";
}

function lineRateIncGst(line) {
  const unit = Number(line.unit_rate_ex_gst_cents || 0);
  const gstBps = Number(line.gst_rate_bps || 0);
  return Math.round(unit * (1 + gstBps / 10000));
}

function brandNameParts(name) {
  const fallback = "domestica hobart";
  const parts = String(name || fallback).trim().toLowerCase().split(/\s+/);
  if (parts.length >= 2) return [parts[0], parts.slice(1).join(" ")];
  return [parts[0] || "domestica", "hobart"];
}

function addressParts(...values) {
  return values.flatMap((value) => {
    if (!value) return [];
    return String(value)
      .split(/\n+/)
      .map((line) => line.trim())
      .filter(Boolean);
  });
}

function businessLines(settings) {
  return [
    settings.business_name,
    ...addressParts(settings.address),
    settings.email,
    settings.phone,
    settings.abn ? `AU ABN ${settings.abn}` : null,
  ].filter(Boolean);
}

function billingLines(invoice) {
  return [
    invoice.owner_name,
    ...addressParts(invoice.billing_address_line_1),
    [invoice.billing_city, invoice.billing_state, invoice.billing_postcode].filter(Boolean).join(" "),
    invoice.billing_email,
    invoice.owner_abn ? `AU ABN ${invoice.owner_abn}` : null,
  ].filter(Boolean);
}

function lineDescription(line) {
  return [line.description, [line.property_label, date(line.service_date)].filter(Boolean).join(" · ")].filter(Boolean);
}

function InvoiceDocument({ payload }) {
  const { settings, invoice, lines } = payload;
  const [brandFirst, brandSecond] = brandNameParts(settings.business_name);
  const invoiceFooter = String(settings.invoice_footer || "").trim();

  return React.createElement(
    Document,
    {
      title: `Tax Invoice ${invoice.invoice_number}`,
      author: settings.business_name,
      creator: "Domestica Invoice",
      producer: "Domestica Invoice React PDF",
    },
    React.createElement(
      Page,
      { size: "A4", style: styles.page, wrap: true },
      React.createElement(View, { style: styles.topBar, fixed: true }),
      React.createElement(
        View,
        { style: styles.header, fixed: true },
        React.createElement(
          View,
          { style: styles.titleBlock },
          React.createElement(Text, { style: styles.invoiceWord }, "Invoice"),
          React.createElement(
            View,
            { style: styles.detailGrid },
            React.createElement(
              View,
              { style: styles.detailLabels },
              React.createElement(Text, { style: styles.detailLabel }, "Invoice number"),
              React.createElement(Text, { style: styles.detailLabel }, "Date of issue"),
              React.createElement(Text, { style: styles.detailLabel }, "Date due"),
              React.createElement(Text, { style: styles.detailLabel }, "AU")
            ),
            React.createElement(
              View,
              { style: styles.detailValues },
              React.createElement(Text, { style: styles.detailValue }, invoice.invoice_number),
              React.createElement(Text, { style: styles.detailValue }, longDate(invoice.issue_date)),
              React.createElement(Text, { style: styles.detailValue }, longDate(invoice.due_date)),
              React.createElement(Text, { style: styles.detailValue }, "Tax Invoice")
            )
          )
        ),
        React.createElement(
          View,
          { style: styles.brandBlock },
          React.createElement(Text, { style: styles.brandLine }, brandFirst),
          React.createElement(
            Text,
            { style: styles.brandLine },
            brandSecond,
            React.createElement(Text, { style: styles.brandDot }, ".")
          )
        )
      ),
      React.createElement(
        View,
        { style: styles.partyRow },
        React.createElement(
          View,
          { style: styles.partyBlock },
          businessLines(settings).map((line, index) =>
            React.createElement(Text, { key: `${line}-${index}`, style: index === 0 ? styles.partyLabel : styles.partyText }, line)
          )
        ),
        React.createElement(
          View,
          { style: styles.partyBlockRight },
          React.createElement(Text, { style: styles.partyLabel }, "Bill to"),
          billingLines(invoice).map((line, index) =>
            React.createElement(Text, { key: `${line}-${index}`, style: styles.partyText }, line)
          )
        )
      ),
      React.createElement(
        View,
        { style: styles.amountDue },
        React.createElement(
          Text,
          { style: styles.amountDueText },
          `${money(invoice.amount_outstanding_cents)} due`
        ),
        React.createElement(Text, { style: styles.amountDueSubtext }, `Invoice for ${invoiceMonth(invoice)}`)
      ),
      React.createElement(
        View,
        { style: styles.table },
        React.createElement(
          View,
          { style: styles.tableHeader, fixed: true },
          React.createElement(Text, { style: [styles.th, styles.descCol] }, "Description"),
          React.createElement(Text, { style: [styles.th, styles.qtyCol] }, "Qty"),
          React.createElement(Text, { style: [styles.th, styles.unitCol] }, "Unit price"),
          React.createElement(Text, { style: [styles.th, styles.taxCol] }, "Tax"),
          React.createElement(Text, { style: [styles.th, styles.amountCol] }, "Amount")
        ),
        lines.map((line, index) =>
          React.createElement(
            View,
            { key: line.id || index, style: styles.tableRow, wrap: false },
            React.createElement(
              View,
              { style: styles.descCol },
              lineDescription(line).map((text, descriptionIndex) =>
                React.createElement(
                  Text,
                  { key: `${text}-${descriptionIndex}`, style: descriptionIndex === 0 ? styles.tdStrong : styles.tdMuted },
                  text
                )
              )
            ),
            React.createElement(Text, { style: [styles.td, styles.qtyCol] }, compactHours(line.quantity_hours)),
            React.createElement(Text, { style: [styles.td, styles.unitCol] }, isManualLine(line) ? "-" : money(lineRateIncGst(line))),
            React.createElement(
              View,
              { style: styles.taxCol },
              React.createElement(Text, { style: styles.td }, gstRateLabel(line)),
              React.createElement(Text, { style: styles.tdMuted }, `(${money(line.gst_amount_cents)})`)
            ),
            React.createElement(Text, { style: [styles.td, styles.amountCol] }, money(line.amount_inc_gst_cents))
          )
        )
      ),
      React.createElement(
        View,
        { style: styles.summarySection },
        React.createElement(
          View,
          { style: styles.summary },
          React.createElement(
            View,
            { style: styles.summaryRow },
            React.createElement(Text, { style: styles.summaryLabel }, "Subtotal"),
            React.createElement(Text, { style: styles.summaryValue }, money(invoice.total_inc_gst_cents))
          ),
          React.createElement(
            View,
            { style: styles.summaryRow },
            React.createElement(Text, { style: styles.summaryLabel }, "Total excluding tax"),
            React.createElement(Text, { style: styles.summaryValue }, money(invoice.subtotal_ex_gst_cents))
          ),
          React.createElement(
            View,
            { style: styles.summaryRow },
            React.createElement(Text, { style: styles.summaryLabel }, "GST - Australia (10% incl.)"),
            React.createElement(Text, { style: styles.summaryValue }, money(invoice.gst_total_cents))
          ),
          React.createElement(
            View,
            { style: styles.summaryRow },
            React.createElement(Text, { style: styles.summaryLabel }, "Total"),
            React.createElement(Text, { style: styles.summaryValue }, money(invoice.total_inc_gst_cents))
          ),
          React.createElement(
            View,
            { style: styles.amountDueRow },
            React.createElement(Text, { style: styles.amountDueLabel }, "Amount due"),
            React.createElement(Text, { style: styles.amountDueValue }, money(invoice.amount_outstanding_cents))
          )
        )
      ),
      invoiceFooter
        ? React.createElement(
            View,
            { style: styles.invoiceFooter },
            React.createElement(Text, { style: styles.invoiceFooterText }, invoiceFooter)
          )
        : null,
      React.createElement(Text, {
        style: styles.pageNumber,
        fixed: true,
        render: ({ pageNumber, totalPages }) => (totalPages > 1 ? `${pageNumber} / ${totalPages}` : ""),
      })
    )
  );
}

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  return Buffer.concat(chunks).toString("utf8");
}

try {
  const input = await readStdin();
  const payload = JSON.parse(input);
  const pdf = await renderToBuffer(React.createElement(InvoiceDocument, { payload }));
  process.stdout.write(pdf);
} catch (error) {
  process.stderr.write(`${error.stack || error.message}\n`);
  process.exit(1);
}
