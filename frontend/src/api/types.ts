export type AccidentRead = {
  id: number
  created_at: string
  source: string
  image_path: string | null
  image_url: string | null
  hint: string | null
  has_accident: boolean
  accident_type: string
  severity: string
  description: string
  confidence: number
  location_text: string | null
  lat: number | null
  lng: number | null
  location_source: string | null
  location_confidence: number | null
  raw_model_output: string | null
}

export type AccidentListResponse = {
  items: AccidentRead[]
  total: number
  page: number
  page_size: number
}

export type SummaryStats = {
  total: number
  last_7d: number
  severe: number
  severe_ratio: number
}

export type BucketCount = {
  key: string
  count: number
}

export type TimelinePoint = {
  date: string
  count: number
}

export type GeoBucket = {
  lat: number
  lng: number
  count: number
}
