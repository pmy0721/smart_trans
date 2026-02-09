import AMapLoader from '@amap/amap-jsapi-loader'

type AMapNS = any

declare global {
  interface Window {
    _AMapSecurityConfig?: { securityJsCode?: string }
  }
}

let loadPromise: Promise<AMapNS> | null = null

export function getAmapEnv() {
  const key = (import.meta as any).env?.VITE_AMAP_KEY as string | undefined
  const securityJsCode = (import.meta as any).env?.VITE_AMAP_SECURITY_CODE as string | undefined
  const coordModeRaw = ((import.meta as any).env?.VITE_AMAP_COORD_MODE as string | undefined) || 'gps'
  const coordMode = coordModeRaw === 'gcj' ? 'gcj' : 'gps'
  return { key: (key || '').trim(), securityJsCode: (securityJsCode || '').trim(), coordMode }
}

export async function loadAmap(): Promise<AMapNS> {
  const { key, securityJsCode } = getAmapEnv()
  if (!key) throw new Error('缺少 VITE_AMAP_KEY')

  if (securityJsCode) {
    window._AMapSecurityConfig = { securityJsCode }
  }

  if (!loadPromise) {
    loadPromise = AMapLoader.load({
      key,
      version: '2.0',
      plugins: ['AMap.Convertor'],
    }) as Promise<AMapNS>
  }
  return loadPromise
}

export type LatLng = { lat: number; lng: number }

export async function convertGpsToGcj(amap: AMapNS, points: LatLng[]): Promise<LatLng[]> {
  if (!points.length) return []
  if (!amap || typeof amap.convertFrom !== 'function') return points

  const chunkSize = 40
  const out: LatLng[] = []
  for (let i = 0; i < points.length; i += chunkSize) {
    const chunk = points.slice(i, i + chunkSize)
    // AMap expects [lng,lat] pairs.
    const input = chunk.map((p) => [p.lng, p.lat])
    // eslint-disable-next-line no-await-in-loop
    const converted: LatLng[] = await new Promise((resolve) => {
      try {
        amap.convertFrom(input, 'gps', (status: string, result: any) => {
          if (status !== 'complete' || !result || !Array.isArray(result.locations)) {
            resolve(chunk)
            return
          }
          const locs = result.locations
          const mapped: LatLng[] = locs.map((ll: any, idx: number) => {
            try {
              const lng = typeof ll.getLng === 'function' ? ll.getLng() : ll.lng
              const lat = typeof ll.getLat === 'function' ? ll.getLat() : ll.lat
              if (typeof lat === 'number' && typeof lng === 'number') return { lat, lng }
            } catch {
              // ignore
            }
            return chunk[idx]
          })
          resolve(mapped)
        })
      } catch {
        resolve(chunk)
      }
    })
    out.push(...converted)
  }
  return out
}
