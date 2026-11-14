/**
 * The documentation shell — deliberately not the dashboard's.
 *
 * Docs are a different mode of use from operating a gateway: you arrive to
 * read rather than to act, often from a link, and often before you have any
 * gateway at all. Wrapping them in the operational sidebar puts twenty-eight
 * links you are not going to click beside the one thing you came for, and
 * makes the reading column narrow enough to hurt.
 *
 * So this is a reading layout: a centred measure, its own quiet header, and a
 * single way back to the dashboard. The header is sticky and everything else
 * scrolls, including the contents rail, which has its own overflow for the
 * same reason the dashboard's does.
 */

import { Link } from '@inertiajs/react'
import type { ReactNode } from 'react'
import { useTheme } from '@/js/hooks'
import { LogoMark } from '@/views/ui/Logo'
import { IconChevronRight, IconMoon, IconSun } from '@/views/ui/icons'

export function DocsLayout({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useTheme()

  return (
    <div className="min-h-screen bg-canvas text-[13px] text-ink-muted">
      <header className="sticky top-0 z-30 border-b border-line bg-surface/85 backdrop-blur">
        <div className="mx-auto flex h-14 w-full max-w-[1400px] items-center gap-3 px-4 lg:px-8">
          <Link href="/docs" className="flex items-center gap-2">
            <LogoMark className="h-[18px] w-[18px] text-brand" />
            <span className="text-[14px] font-semibold tracking-tight text-ink">Janus</span>
            <span className="text-[13px] text-ink-faint">docs</span>
          </Link>

          <div className="ml-auto flex items-center gap-1">
            <button
              type="button"
              onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
              aria-label={theme === 'dark' ? 'Light theme' : 'Dark theme'}
              className="ring-focus rounded-full p-2 text-ink-faint transition hover:bg-sunken hover:text-ink"
            >
              {theme === 'dark' ? (
                <IconSun className="h-4 w-4" />
              ) : (
                <IconMoon className="h-4 w-4" />
              )}
            </button>
            {/* The one way back. A docs reader who is signed in wants the
                dashboard; one who is not gets sent to the login page by the
                route guard, which is the right answer either way. */}
            <Link
              href="/"
              className="ring-focus ml-1 inline-flex items-center gap-1.5 rounded-full border border-line px-3 py-1.5 text-[12.5px] font-medium text-ink transition hover:bg-sunken"
            >
              Dashboard
              <IconChevronRight className="h-3.5 w-3.5 text-ink-faint" />
            </Link>
          </div>
        </div>
      </header>

      <div className="mx-auto w-full max-w-[1400px] px-4 py-8 lg:px-8">{children}</div>

      <footer className="border-t border-line">
        <div className="mx-auto flex w-full max-w-[1400px] flex-wrap items-center gap-x-4 gap-y-2 px-4 py-6 text-[12px] text-ink-faint lg:px-8">
          <span>Janus — API gateway control plane</span>
          <a
            href="https://caddyserver.com"
            target="_blank"
            rel="noreferrer"
            className="hover:text-brand"
          >
            Caddy
          </a>
          <a
            href="https://sillo.build"
            target="_blank"
            rel="noreferrer"
            className="hover:text-brand"
          >
            Sillo
          </a>
          <Link href="/docs" className="ml-auto hover:text-brand">
            All documentation
          </Link>
        </div>
      </footer>
    </div>
  )
}
