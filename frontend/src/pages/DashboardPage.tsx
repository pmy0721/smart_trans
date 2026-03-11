import ReactECharts from 'echarts-for-react'
import { useEffect, useMemo, useState } from 'react'
import AmapView from '../map/AmapView'
import { getBySeverity, getByType, getGeoBuckets, getSummary, getTimeline } from '../api/client'
import type { BucketCount, GeoBucket, SummaryStats, TimelinePoint } from '../api/types'
import { TEXT } from '../ui/textZh'

export default function DashboardPage() {
  const [summary, setSummary] = useState<SummaryStats | null>(null)
  const [byType, setByType] = useState<BucketCount[]>([])
  const [bySeverity, setBySeverity] = useState<BucketCount[]>([])
  const [timeline, setTimeline] = useState<TimelinePoint[]>([])
  const [geo, setGeo] = useState<GeoBucket[]>([])
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    let inFlight = false

    const fetchAll = () => {
      if (cancelled) return
      if (document.visibilityState !== 'visible') return
      if (inFlight) return

      inFlight = true
      Promise.all([getSummary(), getByType(), getBySeverity(), getTimeline(30), getGeoBuckets({ precision: 2, limit: 300 })])
        .then(([s, t, sev, tl, g]) => {
          if (cancelled) return
          setSummary(s)
          setByType(t)
          setBySeverity(sev)
          setTimeline(tl)
          setGeo(g)
          setErr(null)
        })
        .catch((e) => {
          if (cancelled) return
          setErr(e instanceof Error ? e.message : String(e))
        })
        .finally(() => {
          inFlight = false
        })
    }

    const onVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        fetchAll()
      }
    }

    fetchAll()
    const timer = window.setInterval(fetchAll, 10000)
    document.addEventListener('visibilitychange', onVisibilityChange)

    return () => {
      cancelled = true
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }, [])

  const geoTotal = useMemo(() => geo.reduce((acc, x) => acc + (x.count || 0), 0), [geo])

  function radiusForCount(c: number) {
    const r = 6 + Math.log2(Math.max(1, c) + 1) * 6
    return Math.max(6, Math.min(22, r))
  }

  const typeOption = useMemo(() => {
    const top = byType.slice(0, 10)
    return {
      grid: { left: 20, right: 20, top: 30, bottom: 20, containLabel: true },
      xAxis: { type: 'value', axisLabel: { color: 'rgba(255,255,255,0.6)' } },
      yAxis: {
        type: 'category',
        data: top.map((x) => x.key).reverse(),
        axisLabel: { color: 'rgba(255,255,255,0.7)' },
      },
      series: [
        {
          type: 'bar',
          data: top
            .map((x) => x.count)
            .reverse(),
          barWidth: 12,
          itemStyle: {
            color: 'rgba(45, 212, 191, 0.85)',
            borderRadius: [8, 8, 8, 8],
          },
        },
      ],
    }
  }, [byType])

  const severityOption = useMemo(() => {
    const data = bySeverity.map((x) => ({ name: x.key, value: x.count }))
    return {
      tooltip: { trigger: 'item' },
      series: [
        {
          type: 'pie',
          radius: ['45%', '70%'],
          avoidLabelOverlap: true,
          itemStyle: {
            borderColor: 'rgba(0,0,0,0.2)',
            borderWidth: 1,
          },
          label: { color: 'rgba(255,255,255,0.75)' },
          data,
        },
      ],
      color: ['rgba(45, 212, 191, 0.85)', 'rgba(245, 158, 11, 0.85)', 'rgba(244, 63, 94, 0.85)'],
    }
  }, [bySeverity])

  const timelineOption = useMemo(() => {
    return {
      grid: { left: 20, right: 20, top: 30, bottom: 30, containLabel: true },
      xAxis: {
        type: 'category',
        data: timeline.map((p) => p.date.slice(5)),
        axisLabel: { color: 'rgba(255,255,255,0.55)', interval: 5 },
      },
      yAxis: { type: 'value', axisLabel: { color: 'rgba(255,255,255,0.6)' } },
      series: [
        {
          type: 'line',
          data: timeline.map((p) => p.count),
          smooth: true,
          showSymbol: false,
          lineStyle: { color: 'rgba(245, 158, 11, 0.85)', width: 2 },
          areaStyle: { color: 'rgba(245, 158, 11, 0.16)' },
        },
      ],
    }
  }, [timeline])

  return (
    <div>
      <h1 className="pageTitle">{TEXT.dashboard.title}</h1>
      {err ? (
        <div className="card">
          <div className="cardInner">
            <div style={{ fontWeight: 650, marginBottom: 6 }}>{TEXT.dashboard.apiErrorTitle}</div>
            <div className="muted" style={{ whiteSpace: 'pre-wrap' }}>
              {err}
            </div>
            <div className="muted" style={{ marginTop: 8 }}>{TEXT.dashboard.apiErrorTip}</div>
          </div>
        </div>
      ) : null}

      <div className="grid" style={{ marginBottom: 14 }}>
        <div className="card" style={{ gridColumn: 'span 4' }}>
          <div className="cardInner">
            <div className="kpi">
              <div>
                <div className="kpiValue">{summary ? summary.total : '—'}</div>
                <div className="kpiLabel">{TEXT.dashboard.kpiTotal}</div>
              </div>
              <div className="pill teal">{TEXT.dashboard.pillLive}</div>
            </div>
          </div>
        </div>
        <div className="card" style={{ gridColumn: 'span 4' }}>
          <div className="cardInner">
            <div className="kpi">
              <div>
                <div className="kpiValue">{summary ? summary.last_7d : '—'}</div>
                <div className="kpiLabel">{TEXT.dashboard.kpiLast7d}</div>
              </div>
              <div className="pill">{TEXT.dashboard.pillRolling}</div>
            </div>
          </div>
        </div>
        <div className="card" style={{ gridColumn: 'span 4' }}>
          <div className="cardInner">
            <div className="kpi">
              <div>
                <div className="kpiValue">{summary ? summary.severe : '—'}</div>
                <div className="kpiLabel">{TEXT.dashboard.kpiSevere}</div>
              </div>
              <div className="pill amber">
                {summary ? `${Math.round(summary.severe_ratio * 100)}%` : '—'}
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="grid">
        <div className="card" style={{ gridColumn: 'span 12' }}>
          <div className="cardInner">
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div style={{ fontWeight: 650 }}>{TEXT.dashboard.regionTitle}</div>
              <div className="muted" style={{ fontSize: 12 }}>
                {geo.length ? TEXT.dashboard.regionMetaFmt(geoTotal, geo.length) : TEXT.dashboard.regionMetaNone}
              </div>
            </div>
            <div
              style={{
                height: 340,
                marginTop: 10,
              }}
            >
              <AmapView
                height="100%"
                center={{ lat: 35.8617, lng: 104.1954 }}
                zoom={4}
                scrollWheel={false}
                circles={geo.map((b) => ({
                  lat: b.lat,
                  lng: b.lng,
                  radius: radiusForCount(b.count),
                  popup: TEXT.dashboard.mapPopupFmt(b.lat.toFixed(2), b.lng.toFixed(2), b.count),
                }))}
              />
            </div>
          </div>
        </div>

        <div className="card" style={{ gridColumn: 'span 7' }}>
          <div className="cardInner">
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div style={{ fontWeight: 650 }}>{TEXT.dashboard.byTypeTitle}</div>
              <div className="muted" style={{ fontSize: 12 }}>
                {TEXT.dashboard.byTypeMeta}
              </div>
            </div>
            <div style={{ height: 280, marginTop: 10 }}>
              <ReactECharts option={typeOption as any} style={{ height: '100%' }} />
            </div>
          </div>
        </div>
        <div className="card" style={{ gridColumn: 'span 5' }}>
          <div className="cardInner">
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div style={{ fontWeight: 650 }}>{TEXT.dashboard.severityTitle}</div>
              <div className="muted" style={{ fontSize: 12 }}>
                {TEXT.dashboard.severityMeta}
              </div>
            </div>
            <div style={{ height: 280, marginTop: 10 }}>
              <ReactECharts option={severityOption as any} style={{ height: '100%' }} />
            </div>
          </div>
        </div>
        <div className="card" style={{ gridColumn: 'span 12' }}>
          <div className="cardInner">
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div style={{ fontWeight: 650 }}>{TEXT.dashboard.timelineTitle}</div>
              <div className="muted" style={{ fontSize: 12 }}>
                {TEXT.dashboard.timelineMeta}
              </div>
            </div>
            <div style={{ height: 280, marginTop: 10 }}>
              <ReactECharts option={timelineOption as any} style={{ height: '100%' }} />
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
