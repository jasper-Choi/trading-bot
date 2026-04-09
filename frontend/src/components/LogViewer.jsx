/** 실시간 로그 뷰어 — 새 줄이 오면 자동 스크롤 */
import { useEffect, useRef } from 'react'

export default function LogViewer({ lines, t }) {
  const bottomRef = useRef(null)

  // 새 로그가 들어올 때마다 맨 아래로 스크롤
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
            <div key={i} className="log-line">{line || '\u00A0'}</div>
          ))
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}
