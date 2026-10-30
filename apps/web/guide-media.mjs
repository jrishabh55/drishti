import { createReadStream } from 'node:fs'
import { realpath, stat } from 'node:fs/promises'
import { extname, resolve, sep } from 'node:path'
import { Readable } from 'node:stream'

const MIME = { '.mp4': 'video/mp4', '.vtt': 'text/vtt; charset=utf-8', '.json': 'application/json; charset=utf-8', '.jpg': 'image/jpeg', '.png': 'image/png' }
/** Public, non-personal guide assets only. Does not intercept API or SSR routes. */
export async function serveGuideMedia(request, root) {
  const pathname = new URL(request.url).pathname
  if (!pathname.startsWith('/guide-media/')) return null
  if (!['GET', 'HEAD'].includes(request.method)) return new Response(null, { status: 405, headers: { allow: 'GET, HEAD' } })
  if (!root) return new Response(null, { status: 404 })
  let relative
  try { relative = decodeURIComponent(pathname.slice('/guide-media/'.length)) } catch { return new Response(null, { status: 400 }) }
  if (!relative || relative.split('/').some(part => !/^[\w.-]+$/.test(part) || part === '.' || part === '..')) return new Response(null, { status: 400 })
  try {
    const base = await realpath(root)
    const file = await realpath(resolve(base, relative))
    if (!file.startsWith(base + sep) || !MIME[extname(file)]) return new Response(null, { status: 404 })
    const info = await stat(file)
    if (!info.isFile()) return new Response(null, { status: 404 })
    let start = 0, end = info.size - 1, status = 200
    const headers = new Headers({ 'content-type': MIME[extname(file)], 'accept-ranges': 'bytes', 'cache-control': relative === 'manifest.json' ? 'no-store' : 'public, max-age=31536000, immutable', 'x-content-type-options': 'nosniff' })
    const range = request.headers.get('range')
    if (range) {
      const match = /^bytes=(\d*)-(\d*)$/.exec(range)
      if (match && (match[1] || match[2])) {
        start = match[1] ? Number(match[1]) : Math.max(0, info.size - Number(match[2]))
        end = match[1] && match[2] ? Math.min(Number(match[2]), info.size - 1) : info.size - 1
      } else start = -1
      if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start < 0 || start >= info.size || end < start) return new Response(null, { status: 416, headers: { 'content-range': `bytes */${info.size}`, 'accept-ranges': 'bytes' } })
      status = 206; headers.set('content-range', `bytes ${start}-${end}/${info.size}`)
    }
    headers.set('content-length', String(Math.max(0, end - start + 1)))
    const body = request.method === 'HEAD' || !info.size ? null : Readable.toWeb(createReadStream(file, { start, end }))
    return new Response(body, { status, headers })
  } catch { return new Response(null, { status: 404 }) }
}
