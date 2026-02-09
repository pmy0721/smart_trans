import { useEffect, useMemo, useRef, useState } from 'react'
import { convertGpsToGcj, getAmapEnv, loadAmap, type LatLng } from './amap'
import { TEXT } from '../ui/textZh'

type CirclePoint = LatLng & { radius: number; popup?: string }

type Props = {
  height: number | string
  center: LatLng
  zoom: number
  scrollWheel?: boolean
  circles?: CirclePoint[]
  marker?: (LatLng & { popup?: string }) | null
}

export default function AmapView({ height, center, zoom, scrollWheel = false, circles = [], marker = null }: Props) {
  const elRef = useRef<HTMLDivElement | null>(null)
  const mapRef = useRef<any>(null)
  const infoRef = useRef<any>(null)
  const [error, setError] = useState<string | null>(null)

  const env = useMemo(() => getAmapEnv(), [])

  useEffect(() => {
    let cancelled = false

    async function init() {
      if (!elRef.current) return
      if (!env.key) {
        setError(TEXT.amap.missingKey)
        return
      }

      try {
        const AMap = await loadAmap()
        if (cancelled) return

        const map = new AMap.Map(elRef.current, {
          zoom,
          center: [center.lng, center.lat],
          scrollWheel,
          zoomEnable: true,
          dragEnable: true,
        })

        mapRef.current = map
        infoRef.current = new AMap.InfoWindow({ offset: new AMap.Pixel(0, -18) })

        await renderOverlays(AMap)
      } catch (e) {
        if (cancelled) return
        setError(e instanceof Error ? e.message : String(e))
      }
    }

    async function renderOverlays(AMap: any) {
      const map = mapRef.current
      if (!map) return

      try {
        map.clearMap()
      } catch {
        // ignore
      }

      const points: LatLng[] = []
      if (marker) points.push({ lat: marker.lat, lng: marker.lng })
      for (const c of circles) points.push({ lat: c.lat, lng: c.lng })

      const converted = env.coordMode === 'gps' ? await convertGpsToGcj(AMap, points) : points
      if (cancelled) return

      let idx = 0
      const getNext = () => converted[idx++]

      if (marker) {
        const p = getNext() || marker
        const m = new AMap.Marker({ position: [p.lng, p.lat] })
        m.setMap(map)
        if (marker.popup) {
          m.on('click', () => {
            infoRef.current?.setContent(marker.popup)
            infoRef.current?.open(map, m.getPosition())
          })
        }
        try {
          map.setCenter([p.lng, p.lat])
          map.setZoom(zoom)
        } catch {
          // ignore
        }
      }

      for (const c of circles) {
        const p = getNext() || c
        const cm = new AMap.CircleMarker({
          center: [p.lng, p.lat],
          radius: c.radius,
          strokeColor: 'rgba(45, 212, 191, 0.9)',
          strokeWeight: 2,
          fillColor: 'rgba(45, 212, 191, 0.35)',
          fillOpacity: 1,
          cursor: 'pointer',
          zIndex: 10,
        })
        cm.setMap(map)
        if (c.popup) {
          cm.on('click', () => {
            infoRef.current?.setContent(c.popup)
            infoRef.current?.open(map, cm.getCenter())
          })
        }
      }
    }

    init()
    return () => {
      cancelled = true
      try {
        infoRef.current?.close()
      } catch {
        // ignore
      }
      try {
        mapRef.current?.destroy()
      } catch {
        // ignore
      }
      mapRef.current = null
      infoRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [env.key])

  // Re-render overlays when data changes.
  useEffect(() => {
    let cancelled = false
    async function rerender() {
      if (!mapRef.current) return
      try {
        const AMap = await loadAmap()
        if (cancelled) return

        // Duplicate renderOverlays logic (kept local to avoid exposing refs).
        const map = mapRef.current
        try {
          map.clearMap()
        } catch {
          // ignore
        }

        const points: LatLng[] = []
        if (marker) points.push({ lat: marker.lat, lng: marker.lng })
        for (const c of circles) points.push({ lat: c.lat, lng: c.lng })
        const converted = env.coordMode === 'gps' ? await convertGpsToGcj(AMap, points) : points
        if (cancelled) return

        let idx = 0
        const getNext = () => converted[idx++]

        if (marker) {
          const p = getNext() || marker
          const m = new AMap.Marker({ position: [p.lng, p.lat] })
          m.setMap(map)
          if (marker.popup) {
            m.on('click', () => {
              infoRef.current?.setContent(marker.popup)
              infoRef.current?.open(map, m.getPosition())
            })
          }
          try {
            map.setCenter([p.lng, p.lat])
            map.setZoom(zoom)
          } catch {
            // ignore
          }
        }

        for (const c of circles) {
          const p = getNext() || c
          const cm = new AMap.CircleMarker({
            center: [p.lng, p.lat],
            radius: c.radius,
            strokeColor: 'rgba(45, 212, 191, 0.9)',
            strokeWeight: 2,
            fillColor: 'rgba(45, 212, 191, 0.35)',
            fillOpacity: 1,
            cursor: 'pointer',
            zIndex: 10,
          })
          cm.setMap(map)
          if (c.popup) {
            cm.on('click', () => {
              infoRef.current?.setContent(c.popup)
              infoRef.current?.open(map, cm.getCenter())
            })
          }
        }
      } catch (e) {
        if (cancelled) return
        setError(e instanceof Error ? e.message : String(e))
      }
    }
    rerender()
    return () => {
      cancelled = true
    }
  }, [circles, marker, zoom, env.coordMode])

  return (
    <div
      style={{
        height,
        borderRadius: 14,
        overflow: 'hidden',
        border: '1px solid rgba(255, 255, 255, 0.12)',
        background: 'rgba(0,0,0,0.14)',
        position: 'relative',
      }}
    >
      {error ? (
        <div
          style={{
            position: 'absolute',
            inset: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: 14,
            color: 'rgba(255,255,255,0.75)',
            fontSize: 12,
            textAlign: 'center',
            whiteSpace: 'pre-wrap',
          }}
        >
          {error}
          {'\n'}
          {'\n'}
          {TEXT.amap.hint}
        </div>
      ) : null}
      <div ref={elRef} style={{ height: '100%', width: '100%', opacity: error ? 0.25 : 1 }} />
    </div>
  )
}
