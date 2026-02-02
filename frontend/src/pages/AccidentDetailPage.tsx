import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { getAccident } from '../api/client'
import type { AccidentRead } from '../api/types'
import { MapContainer, Marker, Popup, TileLayer } from 'react-leaflet'

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

  async function copyJson() {
    if (!item) return
    await navigator.clipboard.writeText(JSON.stringify(item, null, 2))
  }

  return (
    <div>
      <h1 className="pageTitle">Accident Detail</h1>

      {err ? (
        <div className="card">
          <div className="cardInner">
            <div style={{ fontWeight: 650, marginBottom: 6 }}>Error</div>
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
              <div style={{ fontWeight: 650, marginBottom: 10 }}>Image</div>
              {item.image_url ? (
                <img className="img" src={item.image_url} alt={`accident-${item.id}`} />
              ) : (
                <div className="muted">No image attached.</div>
              )}
            </div>
          </div>

          <div className="card">
            <div className="cardInner">
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <div style={{ fontWeight: 650 }}>Record</div>
                <button className="btn" onClick={copyJson}>
                  Copy JSON
                </button>
              </div>

              <div style={{ marginTop: 12 }}>
                <div className="muted" style={{ fontSize: 12 }}>
                  ID
                </div>
                <div style={{ fontWeight: 650 }}>{item.id}</div>
              </div>

              <div style={{ marginTop: 10 }}>
                <div className="muted" style={{ fontSize: 12 }}>
                  Created at
                </div>
                <div>{fmtDate(item.created_at)}</div>
              </div>

              <div style={{ marginTop: 10, display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                <span className={item.has_accident ? 'pill amber' : 'pill teal'}>
                  {item.has_accident ? 'Accident' : 'No accident'}
                </span>
                <span className="pill">{item.accident_type}</span>
                <span className="pill">{item.severity}</span>
                <span className="pill">{Math.round(item.confidence * 100)}%</span>
              </div>

              <div style={{ marginTop: 12 }}>
                <div className="muted" style={{ fontSize: 12 }}>
                  Description
                </div>
                <div className="muted" style={{ whiteSpace: 'pre-wrap', marginTop: 4 }}>
                  {item.description || '—'}
                </div>
              </div>

              {item.hint ? (
                <div style={{ marginTop: 12 }}>
                  <div className="muted" style={{ fontSize: 12 }}>
                    Hint
                  </div>
                  <div className="muted" style={{ whiteSpace: 'pre-wrap', marginTop: 4 }}>
                    {item.hint}
                  </div>
                </div>
              ) : null}

              <div style={{ marginTop: 12 }}>
                <div className="muted" style={{ fontSize: 12 }}>
                  Location
                </div>
                {item.lat != null && item.lng != null ? (
                  <div className="muted" style={{ marginTop: 4 }}>
                    Lat {fmtCoord(item.lat)}, Lng {fmtCoord(item.lng)}
                  </div>
                ) : (
                  <div className="muted" style={{ marginTop: 4 }}>
                    No coordinates.
                  </div>
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
              <div style={{ fontWeight: 650, marginBottom: 10 }}>Map</div>
              {item.lat != null && item.lng != null ? (
                <div style={{ height: 320, borderRadius: 14, overflow: 'hidden', border: '1px solid rgba(255, 255, 255, 0.12)' }}>
                  <MapContainer
                    center={[item.lat, item.lng]}
                    zoom={15}
                    scrollWheelZoom={false}
                    style={{ height: '100%', width: '100%' }}
                  >
                    <TileLayer
                      attribution='&copy; OpenStreetMap contributors'
                      url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
                    />
                    <Marker position={[item.lat, item.lng]}>
                      <Popup>
                        ID {item.id}
                        <br />
                        Lat {fmtCoord(item.lat)}, Lng {fmtCoord(item.lng)}
                      </Popup>
                    </Marker>
                  </MapContainer>
                </div>
              ) : (
                <div className="muted">No location data to display.</div>
              )}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  )
}
