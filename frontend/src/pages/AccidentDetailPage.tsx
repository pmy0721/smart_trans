import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { getAccident } from '../api/client'
import type { AccidentRead } from '../api/types'
import AmapView from '../map/AmapView'
import { TEXT } from '../ui/textZh'

function fmtDate(iso: string) {
  try {
    const d = new Date(iso)
    return new Intl.DateTimeFormat('zh-CN', {
      timeZone: 'Asia/Shanghai',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    }).format(d)
  } catch {
    return iso
  }
}

function fmtDateTimeLine(iso: string) {
  try {
    const d = new Date(iso)
    const parts = new Intl.DateTimeFormat('zh-CN', {
      timeZone: 'Asia/Shanghai',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false,
    }).formatToParts(d)

    const get = (type: Intl.DateTimeFormatPartTypes) => parts.find((p) => p.type === type)?.value
    const y = get('year')
    const mo = get('month')
    const da = get('day')
    const h = get('hour')
    const mi = get('minute')
    const s = get('second')
    if (y && mo && da && h && mi && s) return `${y}/${mo}/${da} ${h}:${mi}:${s}`
    return fmtDate(iso)
  } catch {
    return fmtDate(iso)
  }
}

function fmtCoord(n: number) {
  const s = n.toFixed(6)
  return s.replace(/\.0+$/, '').replace(/(\.\d*?)0+$/, '$1')
}

export default function AccidentDetailPage() {
  const { id } = useParams()
  const [item, setItem] = useState<AccidentRead | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    if (!id) return
    let cancelled = false
    setErr(null)
    getAccident(id)
      .then((r) => {
        if (cancelled) return
        setItem(r)
      })
      .catch((e) => {
        if (cancelled) return
        setErr(e instanceof Error ? e.message : String(e))
      })
    return () => {
      cancelled = true
    }
  }, [id])

  async function copyInfo() {
    if (!item) return

    const t0 = fmtDateTimeLine(item.created_at)
    const t1 = (item.description || '').trim() || TEXT.common.none

    let loc = TEXT.common.none
    if (item.location_text && item.location_text.trim()) {
      loc = item.location_text.replace(/\s+/g, ' ').trim()
    } else if (item.lat != null && item.lng != null) {
      loc = `Lat ${fmtCoord(item.lat)}, Lng ${fmtCoord(item.lng)}`
    }

    const cause = ((item as any).cause as string | undefined | null) || ''
    const t2 = cause.trim() ? `原因：${cause.trim()}` : ''

    const parts = [t0, t1, loc]
    if (t2) parts.push(t2)
    await navigator.clipboard.writeText(parts.join('\n'))
  }

  return (
    <div>
      <h1 className="pageTitle">{TEXT.detail.title}</h1>

      {err ? (
        <div className="card">
          <div className="cardInner">
            <div style={{ fontWeight: 650, marginBottom: 6 }}>{TEXT.detail.errorTitle}</div>
            <div className="muted" style={{ whiteSpace: 'pre-wrap' }}>
              {err}
            </div>
          </div>
        </div>
      ) : null}

      {item ? (
        <div className="twoCol">
          <div className="card">
            <div className="cardInner">
              <div style={{ fontWeight: 650, marginBottom: 10 }}>{TEXT.detail.imageTitle}</div>

              {(() => {
                const frames = ((item as any).frames as any[] | undefined | null) || []
                const order: Record<string, number> = { t0: 0, 't-1s': 1, 't-3s': 2 }
                const sorted = [...frames]
                  .filter((f) => f && typeof f === 'object')
                  .sort((a, b) => (order[String(a.key)] ?? 99) - (order[String(b.key)] ?? 99))

                const label = (k: string) => (k === 't0' ? TEXT.detail.frameT0 : k === 't-1s' ? TEXT.detail.frameT1 : k === 't-3s' ? TEXT.detail.frameT3 : k)

                if (sorted.length) {
                  return (
                    <div style={{ display: 'grid', gap: 12 }}>
                      {sorted.map((f, idx) => (
                        <div key={`${f.key || 'frame'}-${idx}`}>
                          <div className="muted" style={{ fontSize: 12, marginBottom: 6 }}>
                            {label(String(f.key || ''))}
                          </div>
                          {f.image_url ? (
                            <img className="img" src={String(f.image_url)} alt={`accident-${item.id}-${String(f.key || idx)}`} />
                          ) : (
                            <div className="muted">{TEXT.detail.imageNone}</div>
                          )}
                        </div>
                      ))}
                    </div>
                  )
                }

                return item.image_url ? (
                  <img className="img" src={item.image_url} alt={`accident-${item.id}`} />
                ) : (
                  <div className="muted">{TEXT.detail.imageNone}</div>
                )
              })()}
            </div>
          </div>

          <div className="card">
            <div className="cardInner">
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <div style={{ fontWeight: 650 }}>{TEXT.detail.recordTitle}</div>
                <button className="btn" onClick={copyInfo}>
                  {TEXT.detail.copyInfo}
                </button>
              </div>

              <div style={{ marginTop: 12 }}>
                <div className="muted" style={{ fontSize: 12 }}>
                  {TEXT.detail.id}
                </div>
                <div style={{ fontWeight: 650 }}>{item.id}</div>
              </div>

              <div style={{ marginTop: 10 }}>
                <div className="muted" style={{ fontSize: 12 }}>
                  {TEXT.detail.createdAt}
                </div>
                <div>{fmtDate(item.created_at)}</div>
              </div>

              <div style={{ marginTop: 10, display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                <span className={item.has_accident ? 'pill amber' : 'pill teal'}>
                  {item.has_accident ? TEXT.detail.hasAccidentYes : TEXT.detail.hasAccidentNo}
                </span>
                <span className="pill">{item.accident_type}</span>
                <span className="pill">{item.severity}</span>
                <span className="pill">{Math.round(item.confidence * 100)}%</span>
              </div>

              <div style={{ marginTop: 12 }}>
                <div className="muted" style={{ fontSize: 12 }}>
                  {TEXT.detail.description}
                </div>
                <div className="muted" style={{ whiteSpace: 'pre-wrap', marginTop: 4 }}>
                  {item.description || TEXT.common.none}
                </div>
              </div>

              <div style={{ marginTop: 12 }}>
                <div className="muted" style={{ fontSize: 12 }}>
                  {TEXT.detail.cause}
                </div>
                <div className="muted" style={{ whiteSpace: 'pre-wrap', marginTop: 4 }}>
                  {((item as any).cause as string | undefined | null) || TEXT.common.none}
                </div>
              </div>

              <div style={{ marginTop: 12 }}>
                <div className="muted" style={{ fontSize: 12 }}>
                  {TEXT.detail.legalQualitative}
                </div>
                <div className="muted" style={{ whiteSpace: 'pre-wrap', marginTop: 4 }}>
                  {((item as any).legal_qualitative as string | undefined | null) || TEXT.common.none}
                </div>
              </div>

              {Array.isArray((item as any).law_refs) && (item as any).law_refs.length ? (
                <div style={{ marginTop: 12 }}>
                  <div className="muted" style={{ fontSize: 12 }}>
                    {TEXT.detail.lawRefs}
                  </div>
                  <div style={{ marginTop: 6, display: 'grid', gap: 8 }}>
                    {((item as any).law_refs as any[]).slice(0, 6).map((r, idx) => (
                      <div key={idx} className="card" style={{ background: 'rgba(255,255,255,0.04)' }}>
                        <div className="cardInner" style={{ padding: 10 }}>
                          <div style={{ fontWeight: 650, marginBottom: 6, fontSize: 12 }}>
                            {`${r?.source || ''}${r?.title ? `｜${r.title}` : ''}`}
                          </div>
                          <div className="muted" style={{ whiteSpace: 'pre-wrap' }}>{r?.quote || TEXT.common.none}</div>
                          {r?.relevance ? (
                            <div className="muted" style={{ marginTop: 6, whiteSpace: 'pre-wrap' }}>{r.relevance}</div>
                          ) : null}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}

              {item.hint ? (
                <div style={{ marginTop: 12 }}>
                  <div className="muted" style={{ fontSize: 12 }}>
                    {TEXT.detail.hint}
                  </div>
                  <div className="muted" style={{ whiteSpace: 'pre-wrap', marginTop: 4 }}>
                    {item.hint}
                  </div>
                </div>
              ) : null}

              <div style={{ marginTop: 12 }}>
                <div className="muted" style={{ fontSize: 12 }}>
                  {TEXT.detail.location}
                </div>
                {item.lat != null && item.lng != null ? (
                  <div className="muted" style={{ marginTop: 4 }}>
                    {`纬度 ${fmtCoord(item.lat)}，经度 ${fmtCoord(item.lng)}`}
                  </div>
                ) : (
                  <div className="muted" style={{ marginTop: 4 }}>{TEXT.detail.noCoordinates}</div>
                )}
                {item.location_text ? (
                  <div className="muted" style={{ marginTop: 6, whiteSpace: 'pre-wrap' }}>
                    {item.location_text}
                  </div>
                ) : null}
              </div>
            </div>
          </div>

          <div className="card" style={{ gridColumn: '1 / -1' }}>
            <div className="cardInner">
              <div style={{ fontWeight: 650, marginBottom: 10 }}>{TEXT.detail.mapTitle}</div>
              {item.lat != null && item.lng != null ? (
                <AmapView
                  height={320}
                  center={{ lat: item.lat, lng: item.lng }}
                  zoom={15}
                  scrollWheel={false}
                  marker={{
                    lat: item.lat,
                    lng: item.lng,
                    popup: TEXT.detail.mapPopupFmt(item.id, fmtCoord(item.lat), fmtCoord(item.lng)),
                  }}
                />
              ) : (
                <div className="muted">{TEXT.detail.mapNone}</div>
              )}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  )
}
