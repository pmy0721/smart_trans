import type { AccidentListResponse, AccidentRead, BucketCount, GeoBucket, SummaryStats, TimelinePoint } from './types'

const API_BASE = (import.meta as any).env?.VITE_API_BASE || ''

async function requestJson<T>(path: string): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, {
    headers: {
      Accept: 'application/json',
    },
  })

  if (!resp.ok) {
    const text = await resp.text().catch(() => '')
    throw new Error(`${resp.status} ${resp.statusText}${text ? `: ${text}` : ''}`)
  }
  return (await resp.json()) as T
}

export function listAccidents(params: {
  page: number
  page_size: number
  has_accident?: string
  severity?: string
  type?: string
}): Promise<AccidentListResponse> {
  const qs = new URLSearchParams()
  qs.set('page', String(params.page))
  qs.set('page_size', String(params.page_size))
  if (params.has_accident) qs.set('has_accident', params.has_accident)
  if (params.severity) qs.set('severity', params.severity)
  if (params.type) qs.set('type', params.type)
  return requestJson(`/api/accidents?${qs.toString()}`)
}

export function getAccident(id: string): Promise<AccidentRead> {
  return requestJson(`/api/accidents/${encodeURIComponent(id)}`)
}

export function getSummary(): Promise<SummaryStats> {
  return requestJson('/api/stats/summary')
}

export function getByType(): Promise<BucketCount[]> {
  return requestJson('/api/stats/by_type')
}

export function getBySeverity(): Promise<BucketCount[]> {
  return requestJson('/api/stats/by_severity')
}

export function getTimeline(days = 30): Promise<TimelinePoint[]> {
  return requestJson(`/api/stats/timeline?days=${encodeURIComponent(String(days))}`)
}

export function getGeoBuckets(params?: { precision?: number; limit?: number }): Promise<GeoBucket[]> {
  const qs = new URLSearchParams()
  if (params?.precision != null) qs.set('precision', String(params.precision))
  if (params?.limit != null) qs.set('limit', String(params.limit))
  const s = qs.toString()
  return requestJson(`/api/stats/geo${s ? `?${s}` : ''}`)
}
