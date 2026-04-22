/** Runtime log viewer that auto-scrolls as new lines arrive. */
import { useEffect, useRef } from 'react'

export default function LogViewer({ lines, t }) {
  const bottomRef = useRef(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [lines])

  return (
    <div className="panel">
      <div className="panel-title">{t.liveLog}</div>
      <div className="log-body">
        {lines.length === 0 ? (
          <span className="c-muted">{t.noLog}</span>
        ) : (
          lines.map((line, i) => (
            <div key={`${i}-${line}`} className="log-line">{line || '\u00A0'}</div>
          ))
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}
