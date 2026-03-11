import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { listAccidents } from '../api/client'
import type { AccidentRead } from '../api/types'
import { TEXT } from '../ui/textZh'

const SEVERITIES = ['', '轻微', '中等', '严重'] as const
const HAS_ACCIDENT = ['', 'true', 'false'] as const

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

export default function AccidentsPage() {
  const nav = useNavigate()
  const [items, setItems] = useState<AccidentRead[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [severity, setSeverity] = useState<string>('')
  const [hasAccident, setHasAccident] = useState<string>('')
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const totalPages = useMemo(() => Math.max(1, Math.ceil(total / pageSize)), [total, pageSize])

  useEffect(() => {
    let cancelled = false
    let inFlight = false

    const fetchList = () => {
      if (cancelled) return
      if (document.visibilityState !== 'visible') return
      if (inFlight) return

      inFlight = true
      setLoading(true)
      listAccidents({ page, page_size: pageSize, severity: severity || undefined, has_accident: hasAccident || undefined })
        .then((r) => {
          if (cancelled) return
          setItems(r.items)
          setTotal(r.total)
          setErr(null)
        })
        .catch((e) => {
          if (cancelled) return
          setErr(e instanceof Error ? e.message : String(e))
        })
        .finally(() => {
          inFlight = false
          if (cancelled) return
          setLoading(false)
        })
    }

    const onVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        fetchList()
      }
    }

    fetchList()
    const timer = window.setInterval(fetchList, 5000)
    document.addEventListener('visibilitychange', onVisibilityChange)

    return () => {
      cancelled = true
      window.clearInterval(timer)
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }, [page, pageSize, severity, hasAccident])

  return (
    <div>
      <h1 className="pageTitle">{TEXT.accidents.title}</h1>

      <div className="card" style={{ marginBottom: 14 }}>
        <div className="cardInner">
          <div className="controls">
            <div className="control">
              <label className="muted" style={{ fontSize: 12, marginRight: 6 }}>
                {TEXT.accidents.filterSeverity}
              </label>
              <select value={severity} onChange={(e) => setSeverity(e.target.value)}>
                {SEVERITIES.map((s) => (
                  <option key={s} value={s}>
                    {s || TEXT.common.all}
                  </option>
                ))}
              </select>
            </div>
            <div className="control">
              <label className="muted" style={{ fontSize: 12, marginRight: 6 }}>
                {TEXT.accidents.filterHasAccident}
              </label>
              <select value={hasAccident} onChange={(e) => setHasAccident(e.target.value)}>
                {HAS_ACCIDENT.map((s) => (
                  <option key={s} value={s}>
                    {s === '' ? TEXT.common.all : s === 'true' ? TEXT.common.yes : TEXT.common.no}
                  </option>
                ))}
              </select>
            </div>
            <div className="control">
              <label className="muted" style={{ fontSize: 12, marginRight: 6 }}>
                {TEXT.accidents.filterPageSize}
              </label>
              <select value={pageSize} onChange={(e) => setPageSize(Number(e.target.value))}>
                {[10, 20, 50, 100].map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            </div>
            <div className="muted" style={{ fontSize: 12 }}>
              {loading ? TEXT.common.loading : TEXT.accidents.recordsFmt(total)}
            </div>
          </div>
          {err ? (
            <div className="muted" style={{ marginTop: 10, whiteSpace: 'pre-wrap' }}>
              {err}
            </div>
          ) : null}
        </div>
      </div>

      <div className="card">
        <div className="cardInner" style={{ padding: 0 }}>
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: 190 }}>{TEXT.accidents.thTime}</th>
                <th style={{ width: 90 }}>{TEXT.accidents.thSeverity}</th>
                <th style={{ width: 120 }}>{TEXT.accidents.thType}</th>
                <th style={{ width: 90 }}>{TEXT.accidents.thConfidence}</th>
                <th>{TEXT.accidents.thDescription}</th>
              </tr>
            </thead>
            <tbody>
              {items.map((it) => (
                <tr key={it.id} className="rowLink" onClick={() => nav(`/accidents/${it.id}`)}>
                  <td className="muted">{fmtDate(it.created_at)}</td>
                  <td>
                    <span
                      className={
                        it.severity === '严重' ? 'pill amber' : it.severity === '中等' ? 'pill' : 'pill teal'
                      }
                    >
                      {it.severity}
                    </span>
                  </td>
                  <td>{it.accident_type}</td>
                  <td className="muted">{Math.round(it.confidence * 100)}%</td>
                  <td className="muted">{(it.description || '—').slice(0, 120)}{it.description && it.description.length > 120 ? '…' : ''}</td>
                </tr>
              ))}
              {items.length === 0 && !loading ? (
                <tr>
                  <td colSpan={5} className="muted" style={{ padding: 14 }}>
                    {TEXT.accidents.empty}
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </div>

      <div className="controls" style={{ marginTop: 14, justifyContent: 'space-between' }}>
        <div className="muted" style={{ fontSize: 12 }}>
          {TEXT.accidents.pageFmt(page, totalPages)}
        </div>
        <div style={{ display: 'flex', gap: 10 }}>
          <button className="btn" onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page <= 1}>
            {TEXT.accidents.prev}
          </button>
          <button
            className="btn"
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            disabled={page >= totalPages}
          >
            {TEXT.accidents.next}
          </button>
        </div>
      </div>
    </div>
  )
}
