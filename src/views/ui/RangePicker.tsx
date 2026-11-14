/**
 * The time-range control.
 *
 * A segmented set of the six standard ranges plus a custom pair. It navigates
 * with a GET and `preserveState`, so switching range keeps scroll position and
 * any open panel — an operator comparing 1h against 24h should not be sent
 * back to the top of the page each time.
 */

import { router, usePage } from '@inertiajs/react'
import { useState } from 'react'
import { cx } from '@/js/hooks'

const RANGES: { key: string; label: string }[] = [
  { key: '15m', label: '15m' },
  { key: '1h', label: '1h' },
  { key: '6h', label: '6h' },
  { key: '24h', label: '24h' },
  { key: '7d', label: '7d' },
  { key: '30d', label: '30d' },
]

export function RangePicker({ current }: { current: string }) {
  const { url } = usePage()
  const [custom, setCustom] = useState(false)

  const go = (params: Record<string, string>) => {
    const [path] = url.split('?')
    router.get(path, params, { preserveState: true, preserveScroll: true, replace: true })
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <div className="inline-flex rounded-md border border-line bg-surface p-0.5">
        {RANGES.map((range) => (
          <button
            key={range.key}
            type="button"
            onClick={() => {
              setCustom(false)
              go({ range: range.key })
            }}
            className={cx(
              'rounded-sm px-2.5 py-1 text-[12px] font-medium transition',
              current === range.key
                ? 'bg-brand-soft text-brand'
                : 'text-ink-muted hover:text-ink',
            )}
          >
            {range.label}
          </button>
        ))}
        <button
          type="button"
          onClick={() => setCustom((open) => !open)}
          className={cx(
            'rounded-sm px-2.5 py-1 text-[12px] font-medium transition',
            current === 'custom' ? 'bg-brand-soft text-brand' : 'text-ink-muted hover:text-ink',
          )}
        >
          Custom
        </button>
      </div>

      {custom && (
        <form
          className="flex items-center gap-1.5"
          onSubmit={(event) => {
            event.preventDefault()
            const data = new FormData(event.currentTarget)
            go({
              range: 'custom',
              start: String(data.get('start') ?? ''),
              end: String(data.get('end') ?? ''),
            })
          }}
        >
          <input
            type="datetime-local"
            name="start"
            required
            className="h-8 rounded-md border border-line bg-surface px-2 text-[12px]"
          />
          <span className="text-ink-faint">–</span>
          <input
            type="datetime-local"
            name="end"
            required
            className="h-8 rounded-md border border-line bg-surface px-2 text-[12px]"
          />
          <button
            type="submit"
            className="h-8 rounded-full bg-primary px-3 text-[12px] font-medium text-on-primary"
          >
            Apply
          </button>
        </form>
      )}
    </div>
  )
}
