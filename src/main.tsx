import React, { useEffect, useState } from "react"
import { createRoot } from "react-dom/client"
import {
  Check,
  ChevronLeft,
  ChevronRight,
  Pencil,
  Plus,
  Trash2,
  Upload,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Textarea } from "@/components/ui/textarea"
import { api, cn, dollars, previousMonth } from "@/lib/utils"
import "./styles.css"

type Job = Record<string, any>
type Invoice = Record<string, any>
type InvoiceGroup = Record<string, any>
type Transaction = Record<string, any>
type Owner = Record<string, any>
type Property = Record<string, any>
type JobForm = {
  owner_name: string
  owner_email: string
  property_nickname: string
  property_address: string
  service_date: string
  hours: string
  rate_ex_gst: string
  description: string
}
type ManualLineForm = {
  service_date: string
  description: string
  amount_inc_gst: string
  gst_rate: string
}

type InvoiceLineEditForm = ManualLineForm & {
  hours: string
  rate_ex_gst: string
}

const emptyManualLineForm: ManualLineForm = {
  service_date: "",
  description: "",
  amount_inc_gst: "",
  gst_rate: "",
}

const emptyJobForm: JobForm = {
  owner_name: "",
  owner_email: "",
  property_nickname: "",
  property_address: "",
  service_date: "",
  hours: "",
  rate_ex_gst: "",
  description: "",
}

const emptyInvoiceLineEditForm: InvoiceLineEditForm = {
  ...emptyManualLineForm,
  hours: "",
  rate_ex_gst: "",
}

const createOwnerValue = "__create_owner__"
const createPropertyValue = "__create_property__"

function moneyInput(centsValue?: number | null) {
  return ((Number(centsValue || 0)) / 100).toFixed(2)
}

function gstRateInput(bps?: number | null) {
  const rate = Number(bps ?? 1000) / 100
  return Number.isInteger(rate) ? String(rate) : String(rate)
}

function isManualLine(line: Record<string, any>) {
  return line.line_type === "manual" || !String(line.quantity_hours || "").trim()
}

const nav = [
  { id: "jobs", label: "Jobs" },
  { id: "owners", label: "Owners" },
  { id: "month-end", label: "Month End" },
  { id: "invoices", label: "Invoices" },
  { id: "reconcile", label: "Reconcile" },
  { id: "settings", label: "Settings" },
]

function routeFromHash() {
  return location.hash.replace(/^#\/?/, "") || "jobs"
}

function useRoute() {
  const [route, setRoute] = useState(routeFromHash())

  useEffect(() => {
    const update = () => setRoute(routeFromHash())
    addEventListener("hashchange", update)
    return () => removeEventListener("hashchange", update)
  }, [])

  return route
}

function StatusBadge({ status }: { status: string }) {
  const normalized = String(status).replaceAll("_", " ")
  const key = String(status).replaceAll("_", "-")

  return (
    <Badge
      variant="outline"
      className={cn(
        "capitalize shadow-none",
        ["paid", "matched", "ready"].includes(key) &&
          "border-transparent bg-[#d6f6d0] text-[#0e6245]",
        ["draft", "sent", "suggested", "draft-invoice"].includes(key) &&
          "border-transparent bg-[#c7f0ff] text-[#075985]",
        ["overdue", "exception", "needs-review", "partially-paid"].includes(
          key
        ) && "border-transparent bg-[#fef3c7] text-[#92400e]",
        ["void", "written-off"].includes(key) &&
          "border-transparent bg-[#fee2e2] text-[#991b1b]"
      )}
    >
      {normalized}
    </Badge>
  )
}

function PageHead({
  eyebrow,
  title,
  actions,
}: {
  eyebrow: string
  title: string
  actions?: React.ReactNode
}) {
  return (
    <div className="mb-5 flex items-end justify-between gap-4">
      <div>
        <p className="mb-1 text-[12px] font-medium text-muted-foreground">
          {eyebrow}
        </p>
        <h1 className="text-[26px] font-semibold tracking-normal text-[#1a1f36]">
          {title}
        </h1>
      </div>
      <div className="flex flex-wrap items-center gap-2">{actions}</div>
    </div>
  )
}

function Metric({
  label,
  value,
  tone,
}: {
  label: string
  value: string
  tone?: "success" | "warning"
}) {
  return (
    <Card size="sm">
      <CardContent className="grid gap-1.5">
        <span className="text-[12px] font-medium text-muted-foreground">
          {label}
        </span>
        <strong
          className={cn(
            "text-right text-[26px] font-semibold tabular-nums tracking-normal text-[#1a1f36]",
            tone === "success" && "text-emerald-700",
            tone === "warning" && "text-amber-800"
          )}
        >
          {value}
        </strong>
      </CardContent>
    </Card>
  )
}

function monthToDate(value: string) {
  const [year, month] = value.split("-").map(Number)
  if (!year || !month) return new Date()
  return new Date(year, month - 1, 1)
}

function dateToMonth(value: Date) {
  return `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}`
}

const monthLabelFormatter = new Intl.DateTimeFormat("en-AU", {
  month: "long",
  year: "numeric",
})

function MonthPicker({
  value,
  onChange,
}: {
  value: string
  onChange: (value: string) => void
}) {
  const selected = monthToDate(value)
  const changeMonth = (offset: number) => {
    onChange(dateToMonth(new Date(selected.getFullYear(), selected.getMonth() + offset, 1)))
  }

  return (
    <div className="grid grid-cols-[2rem_1fr_2rem] items-center rounded-md border bg-card shadow-[0_1px_1px_rgba(16,24,40,0.025)]">
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="rounded-r-none border-r text-muted-foreground hover:text-foreground"
        aria-label="Previous month"
        onClick={() => changeMonth(-1)}
      >
        <ChevronLeft className="size-4" />
      </Button>
      <div className="px-3 text-center text-[14px] font-medium tabular-nums text-[#1a1f36]">
        {monthLabelFormatter.format(selected)}
      </div>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="rounded-l-none border-l text-muted-foreground hover:text-foreground"
        aria-label="Next month"
        onClick={() => changeMonth(1)}
      >
        <ChevronRight className="size-4" />
      </Button>
    </div>
  )
}

const allInvoiceMonthsValue = "__all_invoice_months__"

function invoiceMonth(invoice: Invoice) {
  const value = String(invoice.period_start || invoice.issue_date || "")
  return /^\d{4}-\d{2}/.test(value) ? value.slice(0, 7) : ""
}

function invoiceMonthLabel(month: string) {
  return month
    ? monthLabelFormatter.format(monthToDate(month))
    : "Unscheduled"
}

function groupInvoicesByMonth(invoices: Invoice[]) {
  const groups = new Map<string, Invoice[]>()
  for (const invoice of invoices) {
    const month = invoiceMonth(invoice) || ""
    const monthInvoices = groups.get(month)
    if (monthInvoices) {
      monthInvoices.push(invoice)
    } else {
      groups.set(month, [invoice])
    }
  }
  return Array.from(groups.entries()).map(([month, monthInvoices]) => ({
    month,
    label: invoiceMonthLabel(month),
    invoices: monthInvoices,
    outstanding: monthInvoices.reduce(
      (sum, invoice) => sum + Number(invoice.amount_outstanding_cents),
      0
    ),
  }))
}

function App() {
  const route = useRoute()
  const section = route.split("/")[0]
  const activeNavIndex = Math.max(
    0,
    nav.findIndex((item) => item.id === section)
  )

  return (
    <div className="grid min-h-screen grid-cols-[196px_minmax(0,1fr)] bg-background max-md:grid-cols-1">
      <aside className="sticky top-0 h-screen border-r bg-[#fbfcfe] px-8 py-7 max-md:static max-md:h-auto max-md:border-b max-md:border-r-0 max-md:px-4">
        <a
          className="mb-7 flex h-7 items-center gap-2 text-[14px] font-semibold text-[#1a1f36]"
          href="#jobs"
        >
          <span className="size-3 rounded-sm bg-[#27cfa3] shadow-[inset_-4px_0_0_#635bff]" />
          Domestica Invoice
        </a>
        <nav className="relative grid gap-0.5 border-l border-[#e6e8eb]">
          <span
            className="absolute -left-[2px] top-0.5 z-10 h-7 w-[3px] rounded-full bg-[#635bff] transition-transform duration-200 ease-out"
            style={{ transform: `translateY(${activeNavIndex * 34}px)` }}
            aria-hidden="true"
          />
          {nav.map((item) => {
            const active = section === item.id
            return (
              <a
                key={item.id}
                className={cn(
                  "relative flex h-8 items-center pl-5 pr-2 text-[14px] font-medium text-[#4f566b] transition-colors hover:text-[#1a1f36] max-md:h-7 max-md:shrink-0 max-md:px-0",
                  active && "font-semibold text-primary"
                )}
                href={`#${item.id}`}
              >
                <span>{item.label}</span>
              </a>
            )
          })}
        </nav>
      </aside>
      <main className="w-full max-w-[1180px] px-8 py-7 max-md:p-4">
        {section === "jobs" && <JobsPage />}
        {section === "owners" && <OwnersPage />}
        {section === "month-end" && <MonthEndPage />}
        {section === "invoices" &&
          (route.includes("/") ? (
            <InvoiceDetail id={Number(route.split("/")[1])} />
          ) : (
            <InvoicesPage />
          ))}
        {section === "reconcile" && <ReconcilePage />}
        {section === "settings" && <SettingsPage />}
      </main>
    </div>
  )
}

function JobsPage() {
  const [month, setMonth] = useState(previousMonth())
  const [jobs, setJobs] = useState<Job[]>([])
  const [owners, setOwners] = useState<Owner[]>([])
  const [properties, setProperties] = useState<Property[]>([])
  const [ownerId, setOwnerId] = useState("")
  const [propertyId, setPropertyId] = useState("")
  const [jobForm, setJobForm] = useState<JobForm>(emptyJobForm)
  const [message, setMessage] = useState("")
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(false)
  const [pendingDeleteJobId, setPendingDeleteJobId] = useState<number | null>(null)
  const [deletingJobId, setDeletingJobId] = useState<number | null>(null)

  const selectedOwner = owners.find((owner) => String(owner.id) === ownerId)
  const selectedProperty = properties.find((property) => String(property.id) === propertyId)
  const visibleProperties = ownerId
    ? properties.filter((property) => String(property.owner_id) === ownerId)
    : properties

  async function load() {
    const [jobsData, ownerData] = await Promise.all([
      api<{ jobs: Job[] }>(`/api/jobs?month=${month}`),
      api<{ owners: Owner[]; properties: Property[] }>("/api/owners-properties"),
    ])
    setJobs(jobsData.jobs)
    setOwners(ownerData.owners)
    setProperties(ownerData.properties)
  }

  useEffect(() => {
    void load()
  }, [month])

  function defaultRateFor(owner: Owner | undefined, property?: Property) {
    return (
      rateFromCents(property?.default_rate_ex_gst_cents) ||
      rateFromCents(owner?.default_rate_ex_gst_cents)
    )
  }

  function handleOwnerChange(value: string) {
    if (value === createOwnerValue) {
      setOwnerId("")
      setPropertyId("")
      setJobForm((current) => ({
        ...current,
        owner_name: "",
        owner_email: "",
        property_nickname: "",
        property_address: "",
        rate_ex_gst: "",
        description: "",
      }))
      return
    }

    const owner = owners.find((candidate) => String(candidate.id) === value)
    if (!owner) return

    const propertyStillMatches = selectedProperty && String(selectedProperty.owner_id) === value
    setOwnerId(value)
    if (!propertyStillMatches) {
      setPropertyId("")
    }
    setJobForm((current) => ({
      ...current,
      owner_name: owner.name || "",
      owner_email: owner.billing_email || "",
      property_nickname: propertyStillMatches ? current.property_nickname : "",
      property_address: propertyStillMatches ? current.property_address : "",
      rate_ex_gst: propertyStillMatches
        ? current.rate_ex_gst
        : defaultRateFor(owner),
      description: propertyStillMatches ? current.description : "",
    }))
  }

  function handlePropertyChange(value: string) {
    if (value === createPropertyValue) {
      setPropertyId("")
      setJobForm((current) => ({
        ...current,
        property_nickname: "",
        property_address: "",
        rate_ex_gst: defaultRateFor(selectedOwner) || current.rate_ex_gst,
        description: current.description,
      }))
      return
    }

    const property = properties.find((candidate) => String(candidate.id) === value)
    if (!property) return

    const owner = owners.find((candidate) => Number(candidate.id) === Number(property.owner_id))
    setPropertyId(value)
    if (owner) {
      setOwnerId(String(owner.id))
    }
    setJobForm((current) => ({
      ...current,
      owner_name: owner?.name || current.owner_name,
      owner_email: owner?.billing_email || current.owner_email,
      property_nickname: property.nickname || "",
      property_address: property.address_line_1 || "",
      rate_ex_gst: defaultRateFor(owner, property) || current.rate_ex_gst,
      description: property.default_description || current.description,
    }))
  }

  async function importWorkbook(file: File) {
    setLoading(true)
    setMessage("")
    setError("")
    try {
      const XLSX = await import("xlsx")
      const workbook = XLSX.read(await file.arrayBuffer(), {
        type: "array",
        cellDates: false,
      })
      const sheet = workbook.Sheets[workbook.SheetNames[0]]
      const rows = XLSX.utils.sheet_to_json<Record<string, unknown>>(sheet, {
        defval: "",
      })
      const result = await api<any>("/api/import/xlsx-jobs", {
        method: "POST",
        body: JSON.stringify({ filename: file.name, rows }),
      })
      setMessage(
        `Imported ${result.imported} rows. Skipped ${result.duplicates} duplicates. ${result.errors} errors.`
      )
      if (result.errors > 0 && Array.isArray(result.error_rows)) {
        const details = result.error_rows
          .slice(0, 3)
          .map((row: Record<string, unknown>) =>
            `${row.property || "row"}: ${row.error || "Could not import"}`
          )
          .join(" ")
        setError(details)
      }
      await load()
    } catch (importError) {
      setError(importError instanceof Error ? importError.message : "Could not import workbook.")
    } finally {
      setLoading(false)
    }
  }

  async function addJob(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setMessage("")
    setError("")
    try {
      await api("/api/jobs", {
        method: "POST",
        body: JSON.stringify({
          ...jobForm,
          owner_id: ownerId,
          property_id: propertyId,
        }),
      })
      setOwnerId("")
      setPropertyId("")
      setJobForm(emptyJobForm)
      setMessage("Job saved.")
      await load()
    } catch (jobError) {
      setError(jobError instanceof Error ? jobError.message : "Could not save job.")
    }
  }

  function canDeleteJob(job: Job) {
    return ["unbilled", "draft_invoice"].includes(String(job.status))
  }

  function jobDeleteLabel(job: Job) {
    const property = job.property_nickname || job.property_address || "this property"
    return `${property} on ${job.service_date}`
  }

  async function deleteJob(job: Job) {
    const jobId = Number(job.id)
    setDeletingJobId(jobId)
    setMessage("")
    setError("")
    try {
      await api(`/api/jobs/${job.id}`, { method: "DELETE" })
      setMessage(`Deleted job for ${jobDeleteLabel(job)}.`)
      setPendingDeleteJobId(null)
      await load()
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Could not delete job.")
    } finally {
      setDeletingJobId(null)
    }
  }

  return (
    <>
      <PageHead
        eyebrow="Work capture"
        title="Jobs"
        actions={
          <Button asChild variant="outline">
            <a href="/export/jobs.csv">Export CSV</a>
          </Button>
        }
      />

      {message && (
        <div className="mb-4 rounded-md border border-[#c7d2fe] bg-[#f5f8ff] px-3 py-2 text-[12px] font-medium text-[#3c4257]">
          {message}
        </div>
      )}
      {error && (
        <div className="mb-4 rounded-md border border-[#fecdd3] bg-[#fff1f2] px-3 py-2 text-[12px] font-medium text-[#9f1239]">
          {error}
        </div>
      )}

      <div className="mb-5 grid grid-cols-[2fr_1fr] gap-4 max-lg:grid-cols-1">
        <Card>
          <CardHeader>
            <CardTitle>Import Excel workbook</CardTitle>
            <CardDescription>
              Attach the Excel workbook (.xlsx or .xls). Rows labelled extras
              or Total are ignored. Properties must already exist in Owners.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-3">
            <Label className="flex items-center gap-2" htmlFor="xlsx-upload">
              <Upload className="size-4" />
              {loading ? "Importing..." : "Attach Excel workbook"}
            </Label>
            <Input
              id="xlsx-upload"
              type="file"
              accept=".xlsx,.xls"
              disabled={loading}
              onChange={(event) => {
                const file = event.currentTarget.files?.[0]
                if (file) void importWorkbook(file)
              }}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Filter</CardTitle>
            <CardDescription>Review imported work by billing month.</CardDescription>
          </CardHeader>
          <CardContent>
            <MonthPicker value={month} onChange={setMonth} />
          </CardContent>
        </Card>
      </div>

      <Card className="mb-5">
        <Table className="min-w-[1040px]">
          <TableHeader>
            <TableRow>
              <TableHead>Date</TableHead>
              <TableHead>Property</TableHead>
              <TableHead>Owner</TableHead>
              <TableHead>Description</TableHead>
              <TableHead className="text-right">Hours</TableHead>
              <TableHead className="text-right">Rate</TableHead>
              <TableHead className="text-right">GST</TableHead>
              <TableHead className="text-right">Total</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {jobs.length === 0 ? (
              <TableRow>
                <TableCell colSpan={10} className="h-24 text-center text-muted-foreground">
                  No jobs for this month.
                </TableCell>
              </TableRow>
            ) : (
              jobs.map((job) => {
                const jobId = Number(job.id)
                const pendingDelete = pendingDeleteJobId === jobId
                const deleting = deletingJobId === jobId
                const deletable = canDeleteJob(job)

                return (
                  <TableRow key={job.id}>
                    <TableCell>{job.service_date}</TableCell>
                    <TableCell>{job.property_nickname || job.property_address}</TableCell>
                    <TableCell>{job.owner_name}</TableCell>
                    <TableCell>{job.description}</TableCell>
                    <TableCell className="text-right tabular-nums">{job.hours}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {dollars(job.rate_ex_gst_cents)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {dollars(job.gst_amount_cents)}
                    </TableCell>
                    <TableCell className="text-right font-medium tabular-nums">
                      {dollars(job.amount_inc_gst_cents)}
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={job.status} />
                    </TableCell>
                    <TableCell className="text-right">
                      {deletable ? (
                        pendingDelete ? (
                          <div className="flex justify-end gap-2">
                            <Button
                              type="button"
                              variant="destructive"
                              size="sm"
                              disabled={deleting}
                              onClick={() => void deleteJob(job)}
                            >
                              <Trash2 className="size-4" />
                              {deleting ? "Deleting..." : "Confirm"}
                            </Button>
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              disabled={deleting}
                              onClick={() => setPendingDeleteJobId(null)}
                            >
                              Cancel
                            </Button>
                          </div>
                        ) : (
                          <Button
                            type="button"
                            variant="destructive"
                            size="sm"
                            disabled={deleting}
                            onClick={() => {
                              setMessage("")
                              setError("")
                              setPendingDeleteJobId(jobId)
                            }}
                          >
                            <Trash2 className="size-4" />
                            Delete
                          </Button>
                        )
                      ) : (
                        <span className="text-[12px] text-muted-foreground">Invoiced</span>
                      )}
                    </TableCell>
                  </TableRow>
                )
              })
            )}
          </TableBody>
        </Table>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Add job manually</CardTitle>
          <CardDescription>
            Use this for one-off adjustments outside the workbook import.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form className="grid grid-cols-2 gap-3 max-md:grid-cols-1" onSubmit={addJob}>
            <div className="grid gap-2">
              <Label>Existing owner</Label>
              <Select value={ownerId || createOwnerValue} onValueChange={handleOwnerChange}>
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={createOwnerValue}>Create from owner name</SelectItem>
                  {owners.map((owner) => (
                    <SelectItem key={owner.id} value={String(owner.id)}>
                      {owner.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">
                Selecting an owner fills the saved name and billing email.
              </p>
            </div>

            <div className="grid gap-2">
              <Label>Existing property</Label>
              <Select value={propertyId || createPropertyValue} onValueChange={handlePropertyChange}>
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={createPropertyValue}>Create from property</SelectItem>
                  {visibleProperties.map((property) => (
                    <SelectItem key={property.id} value={String(property.id)}>
                      {property.owner_name} -{" "}
                      {property.nickname || property.address_line_1}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">
                Selecting a property fills its nickname, address, rate, and default description.
              </p>
            </div>

            <div className="grid gap-2">
              <Label htmlFor="job-owner-name">Owner name</Label>
              <Input
                id="job-owner-name"
                name="owner_name"
                value={jobForm.owner_name}
                onChange={(event) =>
                  setJobForm((current) => ({ ...current, owner_name: event.target.value }))
                }
                placeholder="Owner name"
                required={!ownerId}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="job-owner-email">Owner email</Label>
              <Input
                id="job-owner-email"
                name="owner_email"
                type="email"
                value={jobForm.owner_email}
                onChange={(event) =>
                  setJobForm((current) => ({ ...current, owner_email: event.target.value }))
                }
                placeholder="owner@example.com"
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="job-property-nickname">Property nickname</Label>
              <Input
                id="job-property-nickname"
                name="property_nickname"
                value={jobForm.property_nickname}
                onChange={(event) =>
                  setJobForm((current) => ({ ...current, property_nickname: event.target.value }))
                }
                placeholder="Warwick"
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="job-property-address">Property address</Label>
              <Input
                id="job-property-address"
                name="property_address"
                value={jobForm.property_address}
                onChange={(event) =>
                  setJobForm((current) => ({ ...current, property_address: event.target.value }))
                }
                placeholder="Street address"
                required={!propertyId && !jobForm.property_nickname.trim()}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="job-service-date">Service date</Label>
              <Input
                id="job-service-date"
                name="service_date"
                type="date"
                value={jobForm.service_date}
                onChange={(event) =>
                  setJobForm((current) => ({ ...current, service_date: event.target.value }))
                }
                required
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="job-hours">Hours</Label>
              <Input
                id="job-hours"
                name="hours"
                inputMode="decimal"
                value={jobForm.hours}
                onChange={(event) =>
                  setJobForm((current) => ({ ...current, hours: event.target.value }))
                }
                placeholder="2.5"
                required
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="job-rate">Rate ex GST</Label>
              <Input
                id="job-rate"
                name="rate_ex_gst"
                inputMode="decimal"
                value={jobForm.rate_ex_gst}
                onChange={(event) =>
                  setJobForm((current) => ({ ...current, rate_ex_gst: event.target.value }))
                }
                placeholder="45.00"
                required
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="job-description">Description</Label>
              <Input
                id="job-description"
                name="description"
                value={jobForm.description}
                onChange={(event) =>
                  setJobForm((current) => ({ ...current, description: event.target.value }))
                }
                placeholder="Airbnb Cleaning"
                required
              />
            </div>
            <div className="col-span-full">
              <Button type="submit">Save job</Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </>
  )
}

const emptyOwnerForm = {
  property_id: "",
  owner_name: "",
  owner_email: "",
  owner_phone: "",
  owner_abn: "",
  owner_billing_address: "",
  default_payment_terms_days: "",
  property_nickname: "",
  property_address: "",
  default_rate_ex_gst: "",
  default_description: "",
  property_invoice_number: "",
}

function rateFromCents(value: unknown) {
  const centsValue = Number(value || 0)
  return centsValue > 0 ? (centsValue / 100).toFixed(2) : ""
}

function propertyInvoiceNumber(property: Property) {
  const nextNumber = Number(property.next_invoice_number || 0)
  if (!nextNumber) return ""
  const prefix = String(property.invoice_number_prefix || "")
  const padding = Number(property.invoice_number_padding || 0)
  return `${prefix}${String(nextNumber).padStart(padding, "0")}`
}

function OwnersPage() {
  const [owners, setOwners] = useState<Owner[]>([])
  const [properties, setProperties] = useState<Property[]>([])
  const [form, setForm] = useState(emptyOwnerForm)
  const [message, setMessage] = useState("")

  async function load() {
    const data = await api<{ owners: Owner[]; properties: Property[] }>(
      "/api/owners-properties"
    )
    setOwners(data.owners)
    setProperties(data.properties)
  }

  useEffect(() => {
    void load()
  }, [])

  function updateField(name: string, value: string) {
    setForm((current) => ({ ...current, [name]: value }))
  }

  function selectProperty(propertyId: string) {
    if (propertyId === "new") {
      setForm(emptyOwnerForm)
      return
    }
    const property = properties.find((item) => String(item.id) === propertyId)
    if (!property) return
    const owner = owners.find((item) => String(item.id) === String(property.owner_id))
    setForm({
      property_id: String(property.id),
      owner_name: String(owner?.name || property.owner_name || ""),
      owner_email: String(owner?.billing_email || ""),
      owner_phone: String(owner?.phone || ""),
      owner_abn: String(owner?.abn || ""),
      owner_billing_address: String(owner?.billing_address_line_1 || ""),
      default_payment_terms_days: String(owner?.default_payment_terms_days || ""),
      property_nickname: String(property.nickname || ""),
      property_address: String(property.address_line_1 || ""),
      default_rate_ex_gst: rateFromCents(
        property.default_rate_ex_gst_cents || owner?.default_rate_ex_gst_cents
      ),
      default_description: String(property.default_description || ""),
      property_invoice_number: propertyInvoiceNumber(property),
    })
  }

  async function saveOwnerProperty(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const result = await api<{ owner_id: number; property_id: number }>(
      "/api/owners-properties",
      {
        method: "POST",
        body: JSON.stringify(form),
      }
    )
    setMessage("Owner details saved.")
    await load()
    selectProperty(String(result.property_id))
  }

  async function removeProperty(property: Property) {
    const label = property.nickname || property.address_line_1 || "this property"
    if (!window.confirm(`Remove ${label} from owner management?`)) return
    try {
      await api("/api/properties/remove", {
        method: "POST",
        body: JSON.stringify({ property_id: property.id }),
      })
      setMessage(`${label} removed.`)
      if (String(form.property_id) === String(property.id)) {
        setForm(emptyOwnerForm)
      }
      await load()
    } catch (error) {
      setMessage(error instanceof Error ? error.message : `Could not remove ${label}.`)
    }
  }

  const sortedProperties = [...properties].sort((a, b) =>
    String(a.nickname || a.address_line_1).localeCompare(
      String(b.nickname || b.address_line_1)
    )
  )
  const seenProperties = new Set<string>()
  const displayProperties = sortedProperties.filter((property) => {
    const key = [
      property.owner_name,
      property.nickname,
      property.address_line_1,
    ]
      .map((value) => String(value || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim())
      .join("|")
    if (seenProperties.has(key)) return false
    seenProperties.add(key)
    return true
  })

  return (
    <>
      <PageHead eyebrow="Records" title="Owners" />

      {message && (
        <div className="mb-4 rounded-md border border-[#c7d2fe] bg-[#f5f8ff] px-3 py-2 text-[12px] font-medium text-[#3c4257]">
          {message}
        </div>
      )}

      <div className="grid grid-cols-[420px_minmax(0,1fr)] gap-5 max-xl:grid-cols-1">
        <Card>
          <CardHeader>
            <CardTitle>Owner details</CardTitle>
            <CardDescription>
              Select an imported property to update its billing owner.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form className="grid gap-3" onSubmit={saveOwnerProperty}>
              <div className="rounded-lg border bg-muted/40 px-3 py-2">
                <div className="text-xs font-medium uppercase text-muted-foreground">
                  {form.property_id ? "Editing" : "New record"}
                </div>
                <div className="mt-1 font-medium">
                  {form.property_id
                    ? form.property_nickname || form.property_address || form.owner_name
                    : "Owner/property"}
                </div>
              </div>
              <div className="grid gap-2">
                <Label htmlFor="owners-owner-name">Owner name</Label>
                <Input
                  id="owners-owner-name"
                  value={form.owner_name}
                  onChange={(event) => updateField("owner_name", event.target.value)}
                  placeholder="Owner full name"
                  required
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="owners-owner-email">Owner email</Label>
                <Input
                  id="owners-owner-email"
                  type="email"
                  value={form.owner_email}
                  onChange={(event) => updateField("owner_email", event.target.value)}
                  placeholder="accounts@example.com"
                />
              </div>
              <div className="grid grid-cols-2 gap-3 max-sm:grid-cols-1">
                <div className="grid gap-2">
                  <Label htmlFor="owners-owner-phone">Phone</Label>
                  <Input
                    id="owners-owner-phone"
                    value={form.owner_phone}
                    onChange={(event) => updateField("owner_phone", event.target.value)}
                    placeholder="0400 000 000"
                  />
                </div>
                <div className="grid gap-2">
                  <Label htmlFor="owners-owner-abn">ABN</Label>
                  <Input
                    id="owners-owner-abn"
                    value={form.owner_abn}
                    onChange={(event) => updateField("owner_abn", event.target.value)}
                    placeholder="12 345 678 901"
                  />
                </div>
              </div>
              <div className="grid gap-2">
                <Label htmlFor="owners-billing-address">Billing address</Label>
                <Input
                  id="owners-billing-address"
                  value={form.owner_billing_address}
                  onChange={(event) =>
                    updateField("owner_billing_address", event.target.value)
                  }
                  placeholder="Billing address"
                />
              </div>
              <div className="grid grid-cols-2 gap-3 max-sm:grid-cols-1">
                <div className="grid gap-2">
                  <Label htmlFor="owners-property-nickname">Property nickname</Label>
                  <Input
                    id="owners-property-nickname"
                    value={form.property_nickname}
                    onChange={(event) =>
                      updateField("property_nickname", event.target.value)
                    }
                    placeholder="Warwick"
                    required
                  />
                </div>
                <div className="grid gap-2">
                  <Label htmlFor="owners-rate">Default rate ex GST</Label>
                  <Input
                    id="owners-rate"
                    inputMode="decimal"
                    value={form.default_rate_ex_gst}
                    onChange={(event) =>
                      updateField("default_rate_ex_gst", event.target.value)
                    }
                    placeholder="45.00"
                  />
                </div>
              </div>
              <div className="grid gap-2">
                <Label htmlFor="owners-property-address">Property address</Label>
                <Input
                  id="owners-property-address"
                  value={form.property_address}
                  onChange={(event) =>
                    updateField("property_address", event.target.value)
                  }
                  placeholder="Property address"
                />
              </div>
              <div className="grid grid-cols-2 gap-3 max-sm:grid-cols-1">
                <div className="grid gap-2">
                  <Label htmlFor="owners-payment-terms">Payment terms</Label>
                  <Input
                    id="owners-payment-terms"
                    inputMode="numeric"
                    value={form.default_payment_terms_days}
                    onChange={(event) =>
                      updateField("default_payment_terms_days", event.target.value)
                    }
                    placeholder="14"
                  />
                </div>
                <div className="grid gap-2">
                  <Label htmlFor="owners-description">Default description</Label>
                  <Input
                    id="owners-description"
                    value={form.default_description}
                    onChange={(event) =>
                      updateField("default_description", event.target.value)
                    }
                    placeholder="Airbnb Cleaning"
                  />
                </div>
              </div>
              <div className="grid gap-2">
                <Label htmlFor="owners-invoice-number">Next invoice number</Label>
                <Input
                  id="owners-invoice-number"
                  value={form.property_invoice_number}
                  onChange={(event) =>
                    updateField("property_invoice_number", event.target.value)
                  }
                  placeholder="STSB070"
                />
              </div>
              <div className="flex gap-2">
                <Button type="submit">Save owner</Button>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => setForm(emptyOwnerForm)}
                >
                  New
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Properties</CardTitle>
            <CardDescription>Imported and manually added owner mappings.</CardDescription>
          </CardHeader>
          <CardContent className="px-0">
            <Table className="min-w-[1810px]">
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[104px] pl-4">
                    Actions
                  </TableHead>
                  <TableHead className="w-[180px]">Property</TableHead>
                  <TableHead className="w-[280px]">Property address</TableHead>
                  <TableHead className="w-[200px]">Owner</TableHead>
                  <TableHead className="w-[230px]">Email</TableHead>
                  <TableHead className="w-[150px]">Phone</TableHead>
                  <TableHead className="w-[160px]">ABN</TableHead>
                  <TableHead className="w-[280px]">Billing address</TableHead>
                  <TableHead className="w-[180px]">Description</TableHead>
                  <TableHead className="w-[150px]">Next invoice</TableHead>
                  <TableHead className="w-[120px] text-right">Rate</TableHead>
                  <TableHead className="w-[110px] text-right">Terms</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {displayProperties.length === 0 ? (
                  <TableRow>
                    <TableCell
                      colSpan={12}
                      className="h-24 text-center text-muted-foreground"
                    >
                      No owner records yet.
                    </TableCell>
                  </TableRow>
                ) : (
                  displayProperties.map((property) => {
                    const owner = owners.find(
                      (item) => String(item.id) === String(property.owner_id)
                    )
                    return (
                      <TableRow key={property.id}>
                        <TableCell className="pl-4">
                          <div className="flex gap-1.5">
                            <Button
                              type="button"
                              variant="outline"
                              size="icon-sm"
                              title="Edit property"
                              aria-label={`Edit ${property.nickname || property.address_line_1}`}
                              onClick={() => selectProperty(String(property.id))}
                            >
                              <Pencil className="size-4" />
                            </Button>
                            <Button
                              type="button"
                              variant="destructive"
                              size="icon-sm"
                              title="Remove property"
                              aria-label={`Remove ${property.nickname || property.address_line_1}`}
                              onClick={() => void removeProperty(property)}
                            >
                              <Trash2 className="size-4" />
                            </Button>
                          </div>
                        </TableCell>
                        <TableCell className="font-medium">
                          {property.nickname || property.address_line_1}
                        </TableCell>
                        <TableCell className="max-w-[260px] whitespace-normal">
                          {property.address_line_1 || "-"}
                        </TableCell>
                        <TableCell>{property.owner_name}</TableCell>
                        <TableCell>{owner?.billing_email || "-"}</TableCell>
                        <TableCell>{owner?.phone || "-"}</TableCell>
                        <TableCell>{owner?.abn || "-"}</TableCell>
                        <TableCell className="max-w-[260px] whitespace-normal">
                          {owner?.billing_address_line_1 || "-"}
                        </TableCell>
                        <TableCell>{property.default_description || "-"}</TableCell>
                        <TableCell>{propertyInvoiceNumber(property) || "-"}</TableCell>
                        <TableCell className="text-right tabular-nums">
                          {dollars(
                            property.default_rate_ex_gst_cents ||
                              owner?.default_rate_ex_gst_cents
                          )}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {owner?.default_payment_terms_days || 14} days
                        </TableCell>
                      </TableRow>
                    )
                  })
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      </div>
    </>
  )
}

function MonthEndPage() {
  const [month, setMonth] = useState(previousMonth())
  const [groups, setGroups] = useState<InvoiceGroup[]>([])
  const [message, setMessage] = useState("")
  const [error, setError] = useState("")
  const [invoiceNumbers, setInvoiceNumbers] = useState<Record<string, string>>({})
  const [savingPropertyId, setSavingPropertyId] = useState<number | null>(null)

  async function load() {
    setGroups(
      (await api<{ groups: InvoiceGroup[] }>(`/api/month-end?month=${month}`))
        .groups
    )
  }

  useEffect(() => {
    void load()
  }, [month])

  async function generate() {
    setMessage("")
    setError("")
    try {
      const result = await api<any>("/api/month-end/generate", {
        method: "POST",
        body: JSON.stringify({ month }),
      })
      setMessage(`Generated ${result.count} draft invoices.`)
      await load()
    } catch (generateError) {
      setError(
        generateError instanceof Error
          ? generateError.message
          : "Could not generate draft invoices."
      )
    }
  }

  async function savePropertyInvoiceNumber(
    group: InvoiceGroup,
    event: React.FormEvent<HTMLFormElement>
  ) {
    event.preventDefault()
    const propertyId = Number(group.property_id)
    const invoiceNumber = (invoiceNumbers[String(propertyId)] || "").trim()
    if (!invoiceNumber) {
      setError("Next invoice number is required.")
      return
    }
    setSavingPropertyId(propertyId)
    setMessage("")
    setError("")
    try {
      await api("/api/properties/invoice-number", {
        method: "POST",
        body: JSON.stringify({
          property_id: propertyId,
          invoice_number: invoiceNumber,
        }),
      })
      setInvoiceNumbers((current) => {
        const next = { ...current }
        delete next[String(propertyId)]
        return next
      })
      setMessage(`Saved next invoice number for ${group.property_label}.`)
      await load()
    } catch (saveError) {
      setError(
        saveError instanceof Error
          ? saveError.message
          : "Could not save the property invoice number."
      )
    } finally {
      setSavingPropertyId(null)
    }
  }

  function updateInvoiceNumber(propertyId: number, value: string) {
    setInvoiceNumbers((current) => ({
      ...current,
      [String(propertyId)]: value,
    }))
  }

  const ready = groups.filter((group) => group.issues.length === 0)
  const review = groups.filter((group) => group.issues.length > 0)

  return (
    <>
      <PageHead
        eyebrow="Monthly billing"
        title="Month End"
        actions={<Button onClick={generate}>Generate draft invoices</Button>}
      />
      {message && (
        <div className="mb-4 rounded-md border border-[#c7d2fe] bg-[#f5f8ff] px-3 py-2 text-[12px] font-medium text-[#3c4257]">
          {message}
        </div>
      )}
      {error && (
        <div className="mb-4 rounded-md border border-[#fecdd3] bg-[#fff1f2] px-3 py-2 text-[12px] font-medium text-[#9f1239]">
          {error}
        </div>
      )}
      <Card className="mb-5 w-64">
        <CardContent className="pt-4">
        <MonthPicker value={month} onChange={setMonth} />
        </CardContent>
      </Card>
      <GroupTable
        title="Ready"
        groups={ready}
        invoiceNumbers={invoiceNumbers}
        savingPropertyId={savingPropertyId}
        onInvoiceNumberChange={updateInvoiceNumber}
        onSaveInvoiceNumber={savePropertyInvoiceNumber}
      />
      <GroupTable
        title="Needs review"
        groups={review}
        invoiceNumbers={invoiceNumbers}
        savingPropertyId={savingPropertyId}
        onInvoiceNumberChange={updateInvoiceNumber}
        onSaveInvoiceNumber={savePropertyInvoiceNumber}
      />
    </>
  )
}

function GroupTable({
  title,
  groups,
  invoiceNumbers,
  savingPropertyId,
  onInvoiceNumberChange,
  onSaveInvoiceNumber,
}: {
  title: string
  groups: InvoiceGroup[]
  invoiceNumbers: Record<string, string>
  savingPropertyId: number | null
  onInvoiceNumberChange: (propertyId: number, value: string) => void
  onSaveInvoiceNumber: (
    group: InvoiceGroup,
    event: React.FormEvent<HTMLFormElement>
  ) => void
}) {
  return (
    <Card className="mb-5">
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{groups.length} invoice groups</CardDescription>
      </CardHeader>
      <CardContent className="px-0">
        <Table className="min-w-[1180px]">
          <TableHeader>
            <TableRow>
              <TableHead>Owner</TableHead>
              <TableHead>Property</TableHead>
              <TableHead>Jobs</TableHead>
              <TableHead className="text-right">Subtotal</TableHead>
              <TableHead className="text-right">GST</TableHead>
              <TableHead className="text-right">Total</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Resolve</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {groups.length === 0 ? (
              <TableRow>
                <TableCell colSpan={8} className="h-24 text-center text-muted-foreground">
                  No groups.
                </TableCell>
              </TableRow>
            ) : (
              groups.map((group) => {
                const propertyId = Number(group.property_id)
                const propertyKey = String(propertyId)
                const missingInvoiceNumber = group.issues.includes(
                  "Missing property invoice number"
                )
                const saving = savingPropertyId === propertyId

                return (
                  <TableRow key={`${group.owner_id}-${group.property_id}`}>
                    <TableCell>
                      {group.owner_name}
                      <div className="text-muted-foreground">
                        {group.issues.join(", ")}
                      </div>
                    </TableCell>
                    <TableCell>{group.property_label}</TableCell>
                    <TableCell>{group.job_count}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {dollars(group.subtotal_ex_gst_cents)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {dollars(group.gst_total_cents)}
                    </TableCell>
                    <TableCell className="text-right font-medium tabular-nums">
                      {dollars(group.total_inc_gst_cents)}
                    </TableCell>
                    <TableCell>
                      <StatusBadge
                        status={group.issues.length ? "needs_review" : "ready"}
                      />
                    </TableCell>
                    <TableCell className="min-w-[300px] whitespace-normal">
                      {missingInvoiceNumber ? (
                        <form
                          className="grid gap-2"
                          onSubmit={(event) => onSaveInvoiceNumber(group, event)}
                        >
                          <Label htmlFor={`month-end-invoice-number-${propertyId}`}>
                            Next invoice number
                          </Label>
                          <div className="flex gap-2">
                            <Input
                              id={`month-end-invoice-number-${propertyId}`}
                              className="w-36"
                              value={invoiceNumbers[propertyKey] || ""}
                              onChange={(event) =>
                                onInvoiceNumberChange(propertyId, event.target.value)
                              }
                              placeholder="JAP001"
                              autoComplete="off"
                              required
                            />
                            <Button type="submit" size="sm" disabled={saving}>
                              <Check className="size-4" />
                              {saving ? "Saving..." : "Save"}
                            </Button>
                          </div>
                          <p className="text-xs text-muted-foreground">
                            Stored on this property for future one-off invoices.
                          </p>
                        </form>
                      ) : group.issues.length ? (
                        <span className="text-[12px] text-muted-foreground">
                          Update owner/property details.
                        </span>
                      ) : (
                        <span className="text-[12px] text-muted-foreground">Ready</span>
                      )}
                    </TableCell>
                  </TableRow>
                )
              })
            )}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )
}

function InvoicesPage() {
  const [invoices, setInvoices] = useState<Invoice[]>([])
  const [selectedMonth, setSelectedMonth] = useState("")
  const [message, setMessage] = useState("")
  const [error, setError] = useState("")
  const [deletingId, setDeletingId] = useState<number | null>(null)
  const [pendingDelete, setPendingDelete] = useState<Invoice | null>(null)

  async function load() {
    setInvoices((await api<{ invoices: Invoice[] }>("/api/invoices")).invoices)
  }

  useEffect(() => {
    void load()
  }, [])

  const invoiceMonths = Array.from(
    new Set(invoices.map(invoiceMonth).filter(Boolean))
  ).sort((left, right) => right.localeCompare(left))
  const filterMonths =
    selectedMonth && !invoiceMonths.includes(selectedMonth)
      ? [selectedMonth, ...invoiceMonths]
      : invoiceMonths
  const filteredInvoices = selectedMonth
    ? invoices.filter((invoice) => invoiceMonth(invoice) === selectedMonth)
    : invoices
  const groupedInvoices = groupInvoicesByMonth(filteredInvoices)
  const selectedMonthLabel = selectedMonth ? invoiceMonthLabel(selectedMonth) : "All months"
  const outstanding = filteredInvoices.reduce(
    (sum, invoice) => sum + Number(invoice.amount_outstanding_cents),
    0
  )
  const overdue = filteredInvoices.filter((invoice) => invoice.status === "overdue").length
  const drafts = filteredInvoices.filter((invoice) => invoice.status === "draft").length

  async function deleteInvoice(invoice: Invoice) {
    setDeletingId(Number(invoice.id))
    setMessage("")
    setError("")
    try {
      const result = await api<{ invoice_number: string; jobs_deleted: number }>(
        `/api/invoices/${invoice.id}`,
        { method: "DELETE" }
      )
      setMessage(
        `Deleted ${result.invoice_number}. Removed ${result.jobs_deleted} source job${result.jobs_deleted === 1 ? "" : "s"} and reset numbering.`
      )
      setPendingDelete(null)
      await load()
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Could not delete invoice.")
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <>
      <PageHead
        eyebrow="Invoice lifecycle"
        title="Invoices"
        actions={
          <Button asChild variant="outline">
            <a href="/export/invoices.csv">Export CSV</a>
          </Button>
        }
      />
      {message && (
        <div className="mb-4 rounded-md border border-[#c7d2fe] bg-[#f5f8ff] px-3 py-2 text-[12px] font-medium text-[#3c4257]">
          {message}
        </div>
      )}
      {error && (
        <div className="mb-4 rounded-md border border-[#fecdd3] bg-[#fff1f2] px-3 py-2 text-[12px] font-medium text-[#9f1239]">
          {error}
        </div>
      )}
      {pendingDelete && (
        <DeleteInvoiceConfirm
          invoice={pendingDelete}
          busy={deletingId === Number(pendingDelete.id)}
          onCancel={() => setPendingDelete(null)}
          onConfirm={() => void deleteInvoice(pendingDelete)}
        />
      )}
      <Card className="mb-5">
        <CardContent className="flex flex-wrap items-end gap-4 py-4">
          <div className="grid min-w-[240px] gap-2">
            <Label htmlFor="invoice-month-filter">Invoice month</Label>
            <Select
              value={selectedMonth || allInvoiceMonthsValue}
              onValueChange={(value) =>
                setSelectedMonth(value === allInvoiceMonthsValue ? "" : value)
              }
              disabled={filterMonths.length === 0}
            >
              <SelectTrigger id="invoice-month-filter" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={allInvoiceMonthsValue}>All months</SelectItem>
                {filterMonths.map((month) => (
                  <SelectItem key={month} value={month}>
                    {invoiceMonthLabel(month)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              Uses the invoice billing period month, not payment date.
            </p>
          </div>
          <div className="text-[12px] font-medium text-muted-foreground">
            Showing {filteredInvoices.length} invoice
            {filteredInvoices.length === 1 ? "" : "s"} for {selectedMonthLabel}.
          </div>
        </CardContent>
      </Card>
      <div className="mb-5 grid grid-cols-4 gap-4 max-lg:grid-cols-2 max-sm:grid-cols-1">
        <Metric label="Outstanding" value={dollars(outstanding)} />
        <Metric label="Overdue" value={String(overdue)} tone="warning" />
        <Metric label="Drafts" value={String(drafts)} />
      </div>
      <Card>
        <Table className="min-w-[1160px]">
          <TableHeader>
            <TableRow>
              <TableHead>Invoice</TableHead>
              <TableHead>Owner</TableHead>
              <TableHead>Period</TableHead>
              <TableHead>Issued</TableHead>
              <TableHead>Due</TableHead>
              <TableHead className="text-right">Total</TableHead>
              <TableHead className="text-right">Paid</TableHead>
              <TableHead className="text-right">Outstanding</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {filteredInvoices.length === 0 ? (
              <TableRow>
                <TableCell colSpan={10} className="h-24 text-center text-muted-foreground">
                  {invoices.length === 0
                    ? "No invoices yet."
                    : `No invoices for ${selectedMonthLabel}.`}
                </TableCell>
              </TableRow>
            ) : (
              groupedInvoices.map((group) => (
                <React.Fragment key={group.month || "unscheduled"}>
                  <TableRow className="bg-muted/50 hover:bg-muted/50">
                    <TableCell colSpan={10} className="py-2">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="text-[12px] font-semibold uppercase text-muted-foreground">
                          {group.label}
                        </span>
                        <span className="text-[12px] text-muted-foreground">
                          {group.invoices.length} invoice
                          {group.invoices.length === 1 ? "" : "s"} ·{" "}
                          {dollars(group.outstanding)} outstanding
                        </span>
                      </div>
                    </TableCell>
                  </TableRow>
                  {group.invoices.map((invoice) => (
                    <TableRow key={invoice.id}>
                      <TableCell>
                        <a
                          className="font-medium underline underline-offset-2"
                          href={`#invoices/${invoice.id}`}
                        >
                          {invoice.invoice_number}
                        </a>
                      </TableCell>
                      <TableCell>{invoice.owner_name}</TableCell>
                      <TableCell>
                        {invoice.period_start} to {invoice.period_end}
                      </TableCell>
                      <TableCell>{invoice.issue_date}</TableCell>
                      <TableCell>{invoice.due_date}</TableCell>
                      <TableCell className="text-right font-medium tabular-nums">
                        {dollars(invoice.total_inc_gst_cents)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {dollars(invoice.amount_paid_cents)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {dollars(invoice.amount_outstanding_cents)}
                      </TableCell>
                      <TableCell>
                        <StatusBadge status={invoice.status} />
                      </TableCell>
                      <TableCell className="text-right">
                        <Button
                          type="button"
                          variant="destructive"
                          size="sm"
                          disabled={deletingId === Number(invoice.id)}
                          onClick={() => {
                            setMessage("")
                            setError("")
                            setPendingDelete(invoice)
                          }}
                        >
                          <Trash2 className="size-4" />
                          Delete
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </React.Fragment>
              ))
            )}
          </TableBody>
        </Table>
      </Card>
    </>
  )
}

function DeleteInvoiceConfirm({
  invoice,
  busy,
  onCancel,
  onConfirm,
}: {
  invoice: Invoice
  busy: boolean
  onCancel: () => void
  onConfirm: () => void
}) {
  const [confirmation, setConfirmation] = useState("")
  const invoiceNumber = String(invoice.invoice_number || "")
  const matches = confirmation.trim() === invoiceNumber

  function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (matches && !busy) {
      onConfirm()
    }
  }

  return (
    <Card className="mb-5 border-[#fecdd3] bg-[#fff7f7]">
      <CardHeader>
        <CardTitle>Delete invoice {invoiceNumber}</CardTitle>
        <CardDescription>
          This permanently removes the invoice, deletes its source jobs from Month End,
          and resets later invoice numbers for the same property.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form className="grid gap-3" onSubmit={submit}>
          <div className="grid max-w-md gap-2">
            <Label htmlFor={`delete-invoice-${invoice.id}`}>
              Type {invoiceNumber} to confirm
            </Label>
            <Input
              id={`delete-invoice-${invoice.id}`}
              value={confirmation}
              onChange={(event) => setConfirmation(event.target.value)}
              autoFocus
              autoComplete="off"
            />
          </div>
          <div className="flex flex-wrap gap-2">
            <Button type="submit" variant="destructive" disabled={!matches || busy}>
              <Trash2 className="size-4" />
              {busy ? "Deleting..." : "Delete invoice"}
            </Button>
            <Button type="button" variant="outline" disabled={busy} onClick={onCancel}>
              Cancel
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  )
}

function InvoiceDetail({ id }: { id: number }) {
  const [data, setData] = useState<any>(null)
  const [error, setError] = useState("")
  const [deleting, setDeleting] = useState(false)
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)
  const [manualForm, setManualForm] = useState<ManualLineForm>(emptyManualLineForm)
  const [editingLineId, setEditingLineId] = useState<number | null>(null)
  const [editForm, setEditForm] = useState<InvoiceLineEditForm>(emptyInvoiceLineEditForm)
  const [lineBusy, setLineBusy] = useState(false)
  const [emailBody, setEmailBody] = useState("")
  const [sending, setSending] = useState(false)

  async function load(resetEmail = false) {
    const next = await api<any>(`/api/invoices/${id}`)
    setData(next)
    const latestBody = next.emails?.[0]?.body || next.default_email_body || ""
    setEmailBody((current) => (resetEmail || !current ? latestBody : current))
    setManualForm((current) => ({
      ...current,
      service_date: current.service_date || next.invoice?.period_end || next.invoice?.issue_date || "",
      gst_rate: current.gst_rate || gstRateInput(next.settings?.default_gst_rate_bps),
    }))
  }

  useEffect(() => {
    setManualForm(emptyManualLineForm)
    setEditForm(emptyInvoiceLineEditForm)
    setEditingLineId(null)
    setEmailBody("")
    setSending(false)
    void load(true)
  }, [id])

  if (!data) {
    return <div className="text-muted-foreground">Loading invoice...</div>
  }

  const invoice = data.invoice
  const latestEmail = (data.emails || []).find((email: any) => email.eml_url)
  const isDraft = invoice.status === "draft"
  const canSend = ["draft", "sent", "overdue", "partially_paid"].includes(
    invoice.status
  )

  async function send() {
    const body = emailBody.trim()
    if (!body) {
      setError("Email message is required.")
      return
    }
    setSending(true)
    setError("")
    try {
      await api(`/api/invoices/${id}/send`, {
        method: "POST",
        body: JSON.stringify({ email_body: body }),
      })
      setEmailBody(body)
      await load()
    } catch (sendError) {
      setError(sendError instanceof Error ? sendError.message : "Could not send invoice.")
    } finally {
      setSending(false)
    }
  }

  async function addManualLine(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setLineBusy(true)
    setError("")
    try {
      await api(`/api/invoices/${id}/lines`, {
        method: "POST",
        body: JSON.stringify(manualForm),
      })
      await load()
      setManualForm((current) => ({
        ...current,
        description: "",
        amount_inc_gst: "",
      }))
    } catch (lineError) {
      setError(lineError instanceof Error ? lineError.message : "Could not add invoice line.")
    } finally {
      setLineBusy(false)
    }
  }

  function startEditLine(line: any) {
    setError("")
    setEditingLineId(Number(line.id))
    setEditForm({
      service_date: line.service_date || invoice.period_end || invoice.issue_date || "",
      description: line.description || "",
      amount_inc_gst: moneyInput(line.amount_inc_gst_cents),
      gst_rate: gstRateInput(line.gst_rate_bps),
      hours: line.quantity_hours || "",
      rate_ex_gst: moneyInput(line.unit_rate_ex_gst_cents),
    })
  }

  async function saveInvoiceLine(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!editingLineId) return
    setLineBusy(true)
    setError("")
    try {
      await api(`/api/invoices/${id}/lines/${editingLineId}`, {
        method: "POST",
        body: JSON.stringify(editForm),
      })
      setEditingLineId(null)
      setEditForm(emptyInvoiceLineEditForm)
      await load()
    } catch (lineError) {
      setError(lineError instanceof Error ? lineError.message : "Could not update invoice line.")
    } finally {
      setLineBusy(false)
    }
  }

  async function deleteInvoiceLine(line: any) {
    setLineBusy(true)
    setError("")
    try {
      await api(`/api/invoices/${id}/lines/${line.id}`, { method: "DELETE" })
      if (editingLineId === Number(line.id)) {
        setEditingLineId(null)
        setEditForm(emptyInvoiceLineEditForm)
      }
      await load()
    } catch (lineError) {
      setError(lineError instanceof Error ? lineError.message : "Could not delete invoice line.")
    } finally {
      setLineBusy(false)
    }
  }

  async function deleteCurrentInvoice() {
    setDeleting(true)
    setError("")
    try {
      await api(`/api/invoices/${id}`, { method: "DELETE" })
      location.hash = "#invoices"
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : "Could not delete invoice.")
    } finally {
      setDeleting(false)
    }
  }

  return (
    <>
      <PageHead
        eyebrow="Invoice detail"
        title={invoice.invoice_number}
        actions={
          <>
            <Button asChild variant="outline">
              <a href={`/invoices/${id}/pdf`} target="_blank">
                Preview PDF
              </a>
            </Button>
            <Button asChild variant="outline">
              <a href={`/invoices/${id}/excel`}>Export Excel</a>
            </Button>
            {latestEmail?.eml_url && (
              <Button asChild variant="outline">
                <a href={latestEmail.eml_url}>Download .eml</a>
              </Button>
            )}
            <Button
              type="button"
              variant="destructive"
              disabled={deleting}
              onClick={() => {
                setError("")
                setShowDeleteConfirm(true)
              }}
            >
              <Trash2 className="size-4" />
              Delete invoice
            </Button>
          </>
        }
      />
      {error && (
        <div className="mb-4 rounded-md border border-[#fecdd3] bg-[#fff1f2] px-3 py-2 text-[12px] font-medium text-[#9f1239]">
          {error}
        </div>
      )}
      {showDeleteConfirm && (
        <DeleteInvoiceConfirm
          invoice={invoice}
          busy={deleting}
          onCancel={() => setShowDeleteConfirm(false)}
          onConfirm={() => void deleteCurrentInvoice()}
        />
      )}
      <div className="mb-5 grid grid-cols-4 gap-4 max-lg:grid-cols-2 max-sm:grid-cols-1">
        <Metric label="Total" value={dollars(invoice.total_inc_gst_cents)} />
        <Metric label="Paid" value={dollars(invoice.amount_paid_cents)} tone="success" />
        <Metric
          label="Outstanding"
          value={dollars(invoice.amount_outstanding_cents)}
          tone="warning"
        />
        <Metric label="Status" value={String(invoice.status).replaceAll("_", " ")} />
      </div>
      <div className="mb-5 grid grid-cols-2 gap-4 max-md:grid-cols-1">
        <Card>
          <CardHeader>
            <CardTitle>Owner</CardTitle>
          </CardHeader>
          <CardContent>
            <strong>{invoice.owner_name}</strong>
            <p className="text-muted-foreground">{invoice.billing_email}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Dates</CardTitle>
          </CardHeader>
          <CardContent className="text-sm">
            <p>Issued {invoice.issue_date}</p>
            <p>Due {invoice.due_date}</p>
            <p>
              Period {invoice.period_start} to {invoice.period_end}
            </p>
          </CardContent>
        </Card>
      </div>
      {canSend && (
        <Card className="mb-5">
          <CardHeader>
            <CardTitle>Email message</CardTitle>
            <CardDescription>
              Review the message that will be written into the .eml file.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form
              className="grid gap-3"
              onSubmit={(event) => {
                event.preventDefault()
                void send()
              }}
            >
              <div className="grid gap-2">
                <Label htmlFor="invoice-email-body">Message body</Label>
                <Textarea
                  id="invoice-email-body"
                  className="min-h-[260px]"
                  value={emailBody}
                  onChange={(event) => setEmailBody(event.target.value)}
                  required
                />
                <p className="text-xs text-muted-foreground">
                  This exact text is stored in email history and included in the downloaded .eml file.
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button type="submit" disabled={sending}>
                  {sending
                    ? "Saving .eml..."
                    : invoice.status === "draft"
                      ? "Send invoice"
                      : "Resend invoice"}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  disabled={sending}
                  onClick={() => setEmailBody(data.default_email_body || "")}
                >
                  Reset to default
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      )}
      {isDraft && (
        <Card className="mb-5">
          <CardHeader>
            <CardTitle>Add extra line</CardTitle>
            <CardDescription>
              Add amount-only invoice extras that are not cleaning jobs.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form className="grid grid-cols-4 gap-3 max-lg:grid-cols-2 max-sm:grid-cols-1" onSubmit={addManualLine}>
              <div className="grid gap-2">
                <Label htmlFor="manual-line-date">Service date</Label>
                <Input
                  id="manual-line-date"
                  type="date"
                  value={manualForm.service_date}
                  onChange={(event) =>
                    setManualForm((current) => ({ ...current, service_date: event.target.value }))
                  }
                  required
                />
                <p className="text-xs text-muted-foreground">Defaults to the invoice period end.</p>
              </div>
              <div className="grid gap-2">
                <Label htmlFor="manual-line-description">Description</Label>
                <Input
                  id="manual-line-description"
                  value={manualForm.description}
                  onChange={(event) =>
                    setManualForm((current) => ({ ...current, description: event.target.value }))
                  }
                  placeholder="Replacement supplies"
                  required
                />
                <p className="text-xs text-muted-foreground">Shown as the invoice line description.</p>
              </div>
              <div className="grid gap-2">
                <Label htmlFor="manual-line-amount">Amount inc GST</Label>
                <Input
                  id="manual-line-amount"
                  inputMode="decimal"
                  value={manualForm.amount_inc_gst}
                  onChange={(event) =>
                    setManualForm((current) => ({ ...current, amount_inc_gst: event.target.value }))
                  }
                  placeholder="235.00"
                  required
                />
                <p className="text-xs text-muted-foreground">Enter the final dollar amount on the invoice.</p>
              </div>
              <div className="grid gap-2">
                <Label htmlFor="manual-line-gst">GST rate %</Label>
                <Input
                  id="manual-line-gst"
                  inputMode="decimal"
                  value={manualForm.gst_rate}
                  onChange={(event) =>
                    setManualForm((current) => ({ ...current, gst_rate: event.target.value }))
                  }
                  placeholder="10"
                  required
                />
                <p className="text-xs text-muted-foreground">Defaults to the business GST rate.</p>
              </div>
              <div className="col-span-full">
                <Button type="submit" disabled={lineBusy}>
                  <Plus className="size-4" />
                  {lineBusy ? "Saving..." : "Add extra line"}
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      )}
      <Card>
        <Table className="min-w-[960px]">
          <TableHeader>
            <TableRow>
              <TableHead>Date</TableHead>
              <TableHead>Property</TableHead>
              <TableHead>Description</TableHead>
              <TableHead className="text-right">Hours</TableHead>
              <TableHead className="text-right">Rate</TableHead>
              <TableHead className="text-right">GST</TableHead>
              <TableHead className="text-right">Amount</TableHead>
              {isDraft && <TableHead className="text-right">Actions</TableHead>}
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.lines.map((line: any) => {
              const manual = isManualLine(line)
              const editing = editingLineId === Number(line.id)
              const canDeleteLine = data.lines.length > 1
              return (
                <React.Fragment key={line.id}>
                  <TableRow>
                    <TableCell>{line.service_date}</TableCell>
                    <TableCell>{line.property_label}</TableCell>
                    <TableCell>
                      <div className="flex flex-wrap items-center gap-2">
                        <span>{line.description}</span>
                        <Badge variant="outline">{manual ? "Manual" : "Job"}</Badge>
                      </div>
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {manual ? "-" : line.quantity_hours}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {manual ? "-" : dollars(line.unit_rate_ex_gst_cents)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {dollars(line.gst_amount_cents)}
                    </TableCell>
                    <TableCell className="text-right font-medium tabular-nums">
                      {dollars(line.amount_inc_gst_cents)}
                    </TableCell>
                    {isDraft && (
                      <TableCell className="text-right">
                        <div className="flex justify-end gap-2">
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            disabled={lineBusy}
                            onClick={() => startEditLine(line)}
                          >
                            <Pencil className="size-4" />
                            Edit
                          </Button>
                          <Button
                            type="button"
                            size="sm"
                            variant="destructive"
                            disabled={lineBusy || !canDeleteLine}
                            title={!canDeleteLine ? "Invoice must keep at least one line item." : undefined}
                            onClick={() => void deleteInvoiceLine(line)}
                          >
                            <Trash2 className="size-4" />
                            Delete
                          </Button>
                        </div>
                      </TableCell>
                    )}
                  </TableRow>
                  {editing && (
                    <TableRow>
                      <TableCell colSpan={isDraft ? 8 : 7}>
                        <form
                          className={cn(
                            "grid gap-3 max-lg:grid-cols-2 max-sm:grid-cols-1",
                            manual ? "grid-cols-4" : "grid-cols-5"
                          )}
                          onSubmit={saveInvoiceLine}
                        >
                          <div className="grid gap-2">
                            <Label htmlFor={`edit-line-date-${line.id}`}>Service date</Label>
                            <Input
                              id={`edit-line-date-${line.id}`}
                              type="date"
                              value={editForm.service_date}
                              onChange={(event) =>
                                setEditForm((current) => ({ ...current, service_date: event.target.value }))
                              }
                              required
                            />
                          </div>
                          <div className="grid gap-2">
                            <Label htmlFor={`edit-line-description-${line.id}`}>Description</Label>
                            <Input
                              id={`edit-line-description-${line.id}`}
                              value={editForm.description}
                              onChange={(event) =>
                                setEditForm((current) => ({ ...current, description: event.target.value }))
                              }
                              required
                            />
                          </div>
                          {manual ? (
                            <div className="grid gap-2">
                              <Label htmlFor={`edit-line-amount-${line.id}`}>Amount inc GST</Label>
                              <Input
                                id={`edit-line-amount-${line.id}`}
                                inputMode="decimal"
                                value={editForm.amount_inc_gst}
                                onChange={(event) =>
                                  setEditForm((current) => ({ ...current, amount_inc_gst: event.target.value }))
                                }
                                required
                              />
                              <p className="text-xs text-muted-foreground">Final dollar amount for this manual line.</p>
                            </div>
                          ) : (
                            <>
                              <div className="grid gap-2">
                                <Label htmlFor={`edit-line-hours-${line.id}`}>Hours</Label>
                                <Input
                                  id={`edit-line-hours-${line.id}`}
                                  inputMode="decimal"
                                  value={editForm.hours}
                                  onChange={(event) =>
                                    setEditForm((current) => ({ ...current, hours: event.target.value }))
                                  }
                                  required
                                />
                                <p className="text-xs text-muted-foreground">Billable cleaning time for this job.</p>
                              </div>
                              <div className="grid gap-2">
                                <Label htmlFor={`edit-line-rate-${line.id}`}>Rate ex GST</Label>
                                <Input
                                  id={`edit-line-rate-${line.id}`}
                                  inputMode="decimal"
                                  value={editForm.rate_ex_gst}
                                  onChange={(event) =>
                                    setEditForm((current) => ({ ...current, rate_ex_gst: event.target.value }))
                                  }
                                  required
                                />
                                <p className="text-xs text-muted-foreground">Hourly rate before GST.</p>
                              </div>
                            </>
                          )}
                          <div className="grid gap-2">
                            <Label htmlFor={`edit-line-gst-${line.id}`}>GST rate %</Label>
                            <Input
                              id={`edit-line-gst-${line.id}`}
                              inputMode="decimal"
                              value={editForm.gst_rate}
                              onChange={(event) =>
                                setEditForm((current) => ({ ...current, gst_rate: event.target.value }))
                              }
                              required
                            />
                            <p className="text-xs text-muted-foreground">
                              {manual ? "Applied to the inclusive amount." : "Applied to hours multiplied by rate."}
                            </p>
                          </div>
                          <div className="col-span-full flex flex-wrap gap-2">
                            <Button type="submit" disabled={lineBusy}>
                              {lineBusy ? "Saving..." : "Save line"}
                            </Button>
                            <Button
                              type="button"
                              variant="outline"
                              disabled={lineBusy}
                              onClick={() => {
                                setEditingLineId(null)
                                setEditForm(emptyInvoiceLineEditForm)
                              }}
                            >
                              Cancel
                            </Button>
                          </div>
                        </form>
                      </TableCell>
                    </TableRow>
                  )}
                </React.Fragment>
              )
            })}
          </TableBody>
        </Table>
      </Card>
    </>
  )
}

function ReconcilePage() {
  const [data, setData] = useState<{
    transactions: Transaction[]
    invoices: Invoice[]
  }>({ transactions: [], invoices: [] })
  const [message, setMessage] = useState("")
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(false)
  const [matchingId, setMatchingId] = useState<number | null>(null)
  const [matchForms, setMatchForms] = useState<
    Record<string, { invoice_id?: string; amount?: string; reason?: string }>
  >({})

  async function load() {
    setData(await api("/api/reconcile"))
  }

  useEffect(() => {
    void load()
  }, [])

  async function importBank(file: File) {
    setLoading(true)
    setMessage("")
    setError("")
    try {
      const result = await api<any>("/api/reconcile/import", {
        method: "POST",
        body: JSON.stringify({
          filename: file.name,
          csv_text: await file.text(),
        }),
      })
      setMessage(
        `Imported ${result.imported} transactions. Skipped ${result.duplicates} duplicates. ${result.errors} errors.`
      )
      await load()
    } catch (importError) {
      setError(importError instanceof Error ? importError.message : "Could not import bank CSV.")
    } finally {
      setLoading(false)
    }
  }

  function formFor(tx: Transaction) {
    const saved = matchForms[String(tx.id)] || {}
    return {
      invoice_id: saved.invoice_id ?? String(tx.suggested_invoice_id || data.invoices[0]?.id || ""),
      amount: saved.amount ?? moneyInput(Number(tx.amount_cents || 0)),
      reason:
        saved.reason ??
        (tx.status === "suggested" ? "Approved suggested bank match" : "Manual bank match"),
    }
  }

  function updateMatchForm(
    txId: number,
    patch: { invoice_id?: string; amount?: string; reason?: string }
  ) {
    setMatchForms((current) => ({
      ...current,
      [String(txId)]: { ...(current[String(txId)] || {}), ...patch },
    }))
  }

  async function approveMatch(tx: Transaction) {
    const form = formFor(tx)
    if (!form.invoice_id) {
      setError("Choose an invoice before approving the bank transaction.")
      return
    }
    setMatchingId(Number(tx.id))
    setError("")
    setMessage("")
    try {
      await api("/api/reconcile/match", {
        method: "POST",
        body: JSON.stringify({
          transaction_id: Number(tx.id),
          invoice_id: Number(form.invoice_id),
          amount: form.amount,
          reason: form.reason,
        }),
      })
      setMatchForms((current) => {
        const next = { ...current }
        delete next[String(tx.id)]
        return next
      })
      setMessage("Payment matched.")
      await load()
    } catch (matchError) {
      setError(matchError instanceof Error ? matchError.message : "Could not approve match.")
    } finally {
      setMatchingId(null)
    }
  }

  return (
    <>
      <PageHead eyebrow="Payment matching" title="Reconcile" />
      {message && (
        <div className="mb-4 rounded-md border border-[#c7d2fe] bg-[#f5f8ff] px-3 py-2 text-[12px] font-medium text-[#3c4257]">
          {message}
        </div>
      )}
      {error && (
        <div className="mb-4 rounded-md border border-[#fecaca] bg-[#fff7f7] px-3 py-2 text-[12px] font-medium text-[#991b1b]">
          {error}
        </div>
      )}
      <Card className="mb-5">
        <CardHeader>
          <CardTitle>Import bank CSV</CardTitle>
          <CardDescription>
            Upload the Commonwealth Bank transaction CSV. Headered exports and date, amount,
            description, balance rows are supported.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3">
          <Label className="flex w-fit items-center gap-2" htmlFor="bank-csv-upload">
            <Upload className="size-4" />
            {loading ? "Importing..." : "Upload CSV"}
          </Label>
          <Input
            id="bank-csv-upload"
            type="file"
            accept=".csv,text/csv"
            disabled={loading}
            onChange={(event) => {
              const file = event.currentTarget.files?.[0]
              if (file) void importBank(file)
              event.currentTarget.value = ""
            }}
          />
          <p className="text-xs text-muted-foreground">
            The importer uses invoice references first, then payer name, property details, amount,
            and payment date for no-reference transfers.
          </p>
        </CardContent>
      </Card>
      <Card>
        <Table className="min-w-[1120px]">
          <TableHeader>
            <TableRow>
              <TableHead>Date</TableHead>
              <TableHead>Description</TableHead>
              <TableHead className="text-right">Amount</TableHead>
              <TableHead>Suggested invoice</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Review action</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.transactions.length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} className="h-24 text-center text-muted-foreground">
                  No transactions imported.
                </TableCell>
              </TableRow>
            ) : (
              data.transactions.map((tx) => {
                const form = formFor(tx)
                const canReview =
                  tx.status !== "matched" && Number(tx.amount_cents || 0) > 0 && data.invoices.length > 0
                return (
                  <TableRow key={tx.id}>
                    <TableCell>{tx.transaction_date}</TableCell>
                    <TableCell>
                      {tx.description}
                      <div className="text-muted-foreground">{tx.match_reason}</div>
                    </TableCell>
                    <TableCell className="text-right font-medium tabular-nums">
                      {dollars(tx.amount_cents)}
                    </TableCell>
                    <TableCell>{tx.invoice_number}</TableCell>
                    <TableCell>
                      <StatusBadge status={tx.status} />
                    </TableCell>
                    <TableCell>
                      {tx.status === "matched" ? (
                        <span className="text-muted-foreground">Matched</span>
                      ) : Number(tx.amount_cents || 0) <= 0 ? (
                        <span className="text-muted-foreground">Debit transactions are review-only.</span>
                      ) : data.invoices.length === 0 ? (
                        <span className="text-muted-foreground">No open invoices.</span>
                      ) : (
                        <div className="grid min-w-[360px] gap-2">
                          <div className="grid gap-1">
                            <Label htmlFor={`match-invoice-${tx.id}`}>Invoice</Label>
                            <Select
                              value={form.invoice_id}
                              onValueChange={(value) =>
                                updateMatchForm(Number(tx.id), { invoice_id: value })
                              }
                            >
                              <SelectTrigger id={`match-invoice-${tx.id}`} className="w-full">
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                {data.invoices.map((invoice) => (
                                  <SelectItem key={invoice.id} value={String(invoice.id)}>
                                    {invoice.invoice_number} · {invoice.owner_name} ·{" "}
                                    {dollars(invoice.amount_outstanding_cents)}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                          </div>
                          <div className="grid grid-cols-[7rem_1fr_auto] items-end gap-2">
                            <div className="grid gap-1">
                              <Label htmlFor={`match-amount-${tx.id}`}>Amount</Label>
                              <Input
                                id={`match-amount-${tx.id}`}
                                inputMode="decimal"
                                value={form.amount}
                                onChange={(event) =>
                                  updateMatchForm(Number(tx.id), { amount: event.target.value })
                                }
                              />
                            </div>
                            <div className="grid gap-1">
                              <Label htmlFor={`match-reason-${tx.id}`}>Reason</Label>
                              <Input
                                id={`match-reason-${tx.id}`}
                                value={form.reason}
                                onChange={(event) =>
                                  updateMatchForm(Number(tx.id), { reason: event.target.value })
                                }
                              />
                            </div>
                            <Button
                              type="button"
                              size="sm"
                              disabled={!canReview || matchingId === Number(tx.id)}
                              onClick={() => void approveMatch(tx)}
                            >
                              <Check className="size-4" />
                              {matchingId === Number(tx.id) ? "Saving" : "Approve"}
                            </Button>
                          </div>
                        </div>
                      )}
                    </TableCell>
                  </TableRow>
                )
              })
            )}
          </TableBody>
        </Table>
      </Card>
    </>
  )
}

function SettingsPage() {
  const [settings, setSettings] = useState<Record<string, any> | null>(null)

  useEffect(() => {
    void api<{ settings: Record<string, any> }>("/api/settings").then((data) =>
      setSettings(data.settings)
    )
  }, [])

  if (!settings) {
    return <div className="text-muted-foreground">Loading settings...</div>
  }

  async function save(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const next = await api<{ settings: Record<string, any> }>("/api/settings", {
      method: "POST",
      body: JSON.stringify(Object.fromEntries(form.entries())),
    })
    setSettings(next.settings)
  }

  return (
    <>
      <PageHead eyebrow="Configuration" title="Settings" />
      <Card>
        <CardHeader>
          <CardTitle>Business details</CardTitle>
          <CardDescription>Tax invoice and payment defaults.</CardDescription>
        </CardHeader>
        <CardContent>
          <form className="grid grid-cols-2 gap-3 max-md:grid-cols-1" onSubmit={save}>
            <div className="grid gap-2">
              <Label htmlFor="settings-business-name">Business name</Label>
              <Input
                id="settings-business-name"
                name="business_name"
                defaultValue={settings.business_name ?? ""}
                placeholder="Domestica Hobart"
                required
              />
              <p className="text-xs text-muted-foreground">Shown as the seller on invoices.</p>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="settings-abn">ABN</Label>
              <Input
                id="settings-abn"
                name="abn"
                defaultValue={settings.abn ?? ""}
                placeholder="42 534 352 834"
                required
              />
              <p className="text-xs text-muted-foreground">Use the ABN format shown on your tax invoice.</p>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="settings-email">Business email</Label>
              <Input
                id="settings-email"
                name="email"
                type="email"
                defaultValue={settings.email ?? ""}
                placeholder="domesticahobart@gmail.com"
                required
              />
              <p className="text-xs text-muted-foreground">Reply-to address used on invoice emails.</p>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="settings-phone">Business phone</Label>
              <Input
                id="settings-phone"
                name="phone"
                defaultValue={settings.phone ?? ""}
                placeholder="0403 561 241"
              />
              <p className="text-xs text-muted-foreground">Optional contact number printed on invoices.</p>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="settings-bsb">BSB</Label>
              <Input
                id="settings-bsb"
                name="bsb"
                defaultValue={settings.bsb ?? ""}
                placeholder="123-456"
              />
              <p className="text-xs text-muted-foreground">Bank BSB for invoice payment instructions.</p>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="settings-account-number">Account number</Label>
              <Input
                id="settings-account-number"
                name="account_number"
                defaultValue={settings.account_number ?? ""}
                placeholder="12345678"
              />
              <p className="text-xs text-muted-foreground">Bank account number for bank transfer payments.</p>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="settings-account-name">Account name</Label>
              <Input
                id="settings-account-name"
                name="account_name"
                defaultValue={settings.account_name ?? ""}
                placeholder="Domestica Hobart"
              />
              <p className="text-xs text-muted-foreground">Name customers should enter for bank payments.</p>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="settings-payid">PayID</Label>
              <Input
                id="settings-payid"
                name="payid"
                defaultValue={settings.payid ?? ""}
                placeholder="accounts@example.com"
              />
              <p className="text-xs text-muted-foreground">Optional PayID if customers can pay that way.</p>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="settings-payment-terms">Default payment terms</Label>
              <Input
                id="settings-payment-terms"
                name="default_payment_terms_days"
                inputMode="numeric"
                defaultValue={settings.default_payment_terms_days}
                placeholder="14"
              />
              <p className="text-xs text-muted-foreground">Number of days after issue date before invoices are due.</p>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="settings-gst-rate">GST rate</Label>
              <Input
                id="settings-gst-rate"
                name="default_gst_rate"
                inputMode="decimal"
                defaultValue={Number(settings.default_gst_rate_bps) / 100}
                placeholder="10"
              />
              <p className="text-xs text-muted-foreground">Enter the GST percentage, for example 10.</p>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="settings-address">Business address</Label>
              <Textarea
                id="settings-address"
                name="address"
                defaultValue={settings.address ?? ""}
                placeholder="1 Harbour Street, Hobart TAS 7000"
              />
              <p className="text-xs text-muted-foreground">Printed under your business details on invoices.</p>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="settings-footer">Invoice footer</Label>
              <Textarea
                id="settings-footer"
                name="invoice_footer"
                defaultValue={settings.invoice_footer ?? ""}
                placeholder="Thank you for your business."
              />
              <p className="text-xs text-muted-foreground">Optional note shown at the bottom of invoices.</p>
            </div>
            <div className="col-span-full">
              <Button type="submit">Save settings</Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </>
  )
}

createRoot(document.getElementById("root")!).render(<App />)
