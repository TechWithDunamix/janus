/**
 * The client entry.
 *
 * Resolves a page component by the name the server sent and wraps it in the
 * right chrome. Which chrome a page gets is decided *here* rather than inside
 * each page, so a new screen cannot forget to have one:
 *
 * - `auth/*`, `errors/*` → the bare centred column
 * - `docs/*`             → the reading layout, outside the dashboard shell
 * - anything else        → the control plane shell
 */

import { createInertiaApp } from '@inertiajs/react'
import type { ComponentType, ReactNode } from 'react'
import { createRoot, hydrateRoot } from 'react-dom/client'
import { AppLayout } from '@/views/layouts/AppLayout'
import { AuthLayout } from '@/views/layouts/AuthLayout'
import { DocsLayout } from '@/views/layouts/DocsLayout'
import './app.css'

const APP_NAME = 'Janus'

type PageModule = {
  default: ComponentType<Record<string, unknown>> & {
    layout?: (children: ReactNode) => ReactNode
  }
}

function chromeFor(name: string): (children: ReactNode) => ReactNode {
  if (name.startsWith('auth/') || name.startsWith('errors/')) {
    return (children) => <AuthLayout>{children}</AuthLayout>
  }
  // Docs are read, not operated. The dashboard's twenty-eight operational
  // links beside a page of prose is navigation nobody is going to use, and it
  // narrows the reading column to where it hurts.
  if (name.startsWith('docs/')) {
    return (children) => <DocsLayout>{children}</DocsLayout>
  }
  return (children) => <AppLayout>{children}</AppLayout>
}

createInertiaApp({
  id: 'app',

  title: (title) => (title ? `${title} · ${APP_NAME}` : APP_NAME),

  resolve: async (name) => {
    // Lazy, so Vite code-splits one chunk per page. Eager would put all forty
    // screens and the chart library into the bundle downloaded to render the
    // login form. The cost is one small request on a first visit to a screen;
    // React, Inertia and the UI kit are in the shared chunk and load once.
    const pages = import.meta.glob<PageModule>('../views/pages/**/*.tsx')
    const loader = pages[`../views/pages/${name}.tsx`]
    if (!loader) {
      throw new Error(
        `No page component for "${name}". Expected views/pages/${name}.tsx — ` +
          `the name comes from the server's render() call.`,
      )
    }
    const page = await loader()
    // `??=` so a page that declared its own layout keeps it.
    page.default.layout ??= chromeFor(name)
    // The component itself, not the module: Inertia's resolver accepts a
    // promise of a component, and returning the module fails to type-check.
    return page.default
  },

  setup({ el, App, props }) {
    if (el.hasChildNodes()) {
      hydrateRoot(el, <App {...props} />)
    } else {
      createRoot(el).render(<App {...props} />)
    }
  },

  // The thin bar during a navigation. 250ms delay, so a fast transition shows
  // nothing at all rather than a flash of bar.
  progress: {
    color: 'oklch(0.55 0.16 250)',
    delay: 250,
    showSpinner: false,
  },
})
