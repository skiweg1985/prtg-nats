import { useEffect, useMemo, useState } from 'react'

import { Button } from './primitives'

/** A command in a box with the one button such a box needs. */
export function CopyBlock({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!copied) return
    const timer = window.setTimeout(() => setCopied(false), 2000)
    return () => window.clearTimeout(timer)
  }, [copied])

  return (
    <div className="relative">
      <pre className="border-rule-2 bg-surface-2 text-ink overflow-x-auto rounded-card border p-3 font-mono text-xs leading-relaxed">
        {value}
      </pre>
      <Button
        size="sm"
        className="absolute top-2 right-2"
        onClick={() => {
          void navigator.clipboard.writeText(value).then(() => setCopied(true))
        }}
      >
        {copied ? '✓' : label}
      </Button>
    </div>
  )
}

/** Time left, as a string, or null once it has run out. */
export function useCountdown(deadline: string): string | null {
  const target = useMemo(() => new Date(deadline).getTime(), [deadline])
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [])

  const seconds = Math.floor((target - now) / 1000)
  if (seconds <= 0) return null
  const minutes = Math.floor(seconds / 60)
  return minutes > 0
    ? `${minutes} min ${String(seconds % 60).padStart(2, '0')} s`
    : `${seconds} s`
}
