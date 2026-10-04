import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function dollars(value?: number | null) {
  return new Intl.NumberFormat("en-AU", {
    style: "currency",
    currency: "AUD",
  }).format((value ?? 0) / 100)
}

export function previousMonth() {
  const now = new Date()
  const first = new Date(now.getFullYear(), now.getMonth(), 1)
  first.setDate(first.getDate() - 1)
  return `${first.getFullYear()}-${String(first.getMonth() + 1).padStart(2, "0")}`
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  })

  if (!response.ok) {
    const text = await response.text()
    let message = text
    try {
      const payload = JSON.parse(text)
      message = payload.error || text
    } catch {
      message = text
    }
    throw new Error(message)
  }

  return response.json() as Promise<T>
}
