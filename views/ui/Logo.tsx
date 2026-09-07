/**
 * The Janus mark.
 *
 * A doorway with two thresholds — the god of gates faces both ways, and an API
 * gateway is a door with an inside and an outside. Drawn rather than
 * illustrated: at 20px in a sidebar, anything more detailed becomes a smudge.
 */

export function LogoMark({ className = 'h-5 w-5' }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M3 21V6.5A2.5 2.5 0 0 1 5.5 4h13A2.5 2.5 0 0 1 21 6.5V21"
        stroke="currentColor"
        strokeWidth={2}
        strokeLinecap="round"
      />
      <path d="M1.5 21h21" stroke="currentColor" strokeWidth={2} strokeLinecap="round" />
      {/* The two faces: an arch on each side of the threshold. */}
      <path
        d="M8.25 21v-6a3.75 3.75 0 0 1 7.5 0v6"
        stroke="currentColor"
        strokeWidth={2}
        strokeLinecap="round"
        opacity={0.45}
      />
    </svg>
  )
}

export function Wordmark({ className = 'h-6' }: { className?: string }) {
  return (
    <span className={`inline-flex items-center gap-2 ${className}`}>
      <LogoMark className="h-[1.15em] w-[1.15em] text-brand" />
      <span className="text-[1.05em] font-semibold tracking-tight text-ink">Janus</span>
    </span>
  )
}
