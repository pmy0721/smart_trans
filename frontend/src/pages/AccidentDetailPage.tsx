import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { getAccident } from '../api/client'
import type { AccidentRead } from '../api/types'

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
            </div>
          </div>
        </div>
      ) : null}
    </div>
  )
}
