import { defineConfig } from 'vite'
import { devtools } from '@tanstack/devtools-vite'

import { tanstackStart } from '@tanstack/react-start/plugin/vite'

import viteReact from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { cloudflare } from '@cloudflare/vite-plugin'
import { Readable } from 'node:stream'
import { resolve } from 'node:path'
import { serveGuideMedia } from './guide-media.mjs'

/* WEBAPP1_API_BASE (read in src/routes/api.$.tsx): where the app's /api/*
   catch-all server route forwards requests — the FastAPI backend. Same-origin
   for the browser, so cookies flow and EventSource works without CORS. */

/* VITE_ALLOWED_HOSTS: comma-separated public hostnames this server may answer
   for. Vite rejects requests whose Host header it doesn't recognise ("Blocked
   request. This host is not allowed."), which is a DNS-rebinding guard — good
   default, but it means a reverse proxy forwarding a real domain (Dokploy →
   Traefik → this container) gets blocked until the domain is listed here.
   Unset leaves Vite's own defaults untouched, so local dev is unaffected. */
const ALLOWED_HOSTS = process.env.VITE_ALLOWED_HOSTS?.split(',')
  .map((h) => h.trim())
  .filter(Boolean)

/* WEB_TARGET=node builds/serves WITHOUT the Cloudflare plugin: TanStack
   Start's default Node server target. Used by the Docker prod image
   (apps/web/Dockerfile) — running `vite dev` + workerd in production left
   live HMR sockets in users' tabs, and every redeploy hot-patched a running
   page into a corrupted module graph. Local dev and the Cloudflare Workers
   deploy path keep the plugin (the default). */
const NODE_TARGET = process.env.WEB_TARGET === 'node'

const config = defineConfig({
  resolve: { tsconfigPaths: true },
  server: {
    port: 3000,
    ...(ALLOWED_HOSTS?.length ? { allowedHosts: ALLOWED_HOSTS } : {}),
    /* No vite-level /api proxy: /api/* is served by the app's own catch-all
       server route (src/routes/api.$.tsx), which forwards to WEBAPP1_API_BASE
       and attaches the Clerk token server-side. A vite proxy here would win
       over the server route and silently bypass auth — dev must flow through
       the same path as prod. */
  },
  plugins: [
    { name: 'drishti-guide-media', configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (!req.url?.startsWith('/guide-media/')) return next()
        const request = new Request(`http://localhost${req.url}`, { method: req.method, headers: req.headers as Record<string, string> })
        void serveGuideMedia(request, process.env.GUIDE_MEDIA_DIR ?? resolve(process.cwd(), '../../output/guide-video/published')).then(response => {
          if (!response) return next()
          res.statusCode = response.status
          response.headers.forEach((value, key) => res.setHeader(key, value))
          if (response.body) Readable.fromWeb(response.body as Parameters<typeof Readable.fromWeb>[0]).pipe(res)
          else res.end()
        }).catch(next)
      })
    } },
    devtools(),
    ...(NODE_TARGET ? [] : [cloudflare({ viteEnvironment: { name: 'ssr' } })]),
    tailwindcss(),
    tanstackStart(),
    viteReact(),
  ],
})

export default config
