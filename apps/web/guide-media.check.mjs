import { test, before, after } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, mkdir, writeFile, symlink, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { serveGuideMedia } from './guide-media.mjs'
let root
before(async () => { root = await mkdtemp(join(tmpdir(), 'guide-media-')); await mkdir(join(root, 'v1')); await writeFile(join(root, 'v1', 'intro.mp4'), '0123456789'); await writeFile(join(root, 'manifest.json'), '{}'); await symlink('/etc/passwd', join(root, 'v1', 'outside.txt')) })
after(async () => { await rm(root, { recursive: true, force: true }) })
const request = (path, init) => new Request(`http://localhost${path}`, init)
test('streams full MP4 and HEAD metadata with correct MIME', async () => {
  const response = await serveGuideMedia(request('/guide-media/v1/intro.mp4'), root)
  assert.equal(response.status, 200); assert.equal(response.headers.get('content-type'), 'video/mp4'); assert.equal(await response.text(), '0123456789')
  const head = await serveGuideMedia(request('/guide-media/v1/intro.mp4', { method: 'HEAD' }), root)
  assert.equal(head.headers.get('content-length'), '10'); assert.equal(await head.text(), '')
})
test('supports open-ended and suffix ranges and rejects invalid/multiple ranges', async () => {
  for (const [range, text, contentRange] of [['bytes=2-4','234','bytes 2-4/10'],['bytes=7-','789','bytes 7-9/10'],['bytes=-2','89','bytes 8-9/10']]) {
    const r = await serveGuideMedia(request('/guide-media/v1/intro.mp4', { headers: { range } }), root)
    assert.equal(r.status, 206); assert.equal(r.headers.get('content-range'), contentRange); assert.equal(await r.text(), text)
  }
  for (const range of ['bytes=99-','bytes=3-2','bytes=-0','bytes=0-1,4-5','invalid']) {
    const r = await serveGuideMedia(request('/guide-media/v1/intro.mp4', { headers: { range } }), root)
    assert.equal(r.status, 416); assert.equal(r.headers.get('content-range'), 'bytes */10')
  }
})
test('guards symlinks, encoded separators and missing media while leaving other routes alone', async () => {
  assert.equal((await serveGuideMedia(request('/guide-media/v1/outside.txt'), root)).status, 404)
  assert.equal((await serveGuideMedia(request('/guide-media/v1/%2e%2e%2fmanifest.json'), root)).status, 400)
  assert.equal((await serveGuideMedia(request('/guide-media/v1/missing.mp4'), root)).status, 404)
  assert.equal((await serveGuideMedia(request('/guide-media/manifest.json'), undefined)).status, 404)
  assert.equal(await serveGuideMedia(request('/api/me'), root), null)
  const manifest = await serveGuideMedia(request('/guide-media/manifest.json'), root)
  assert.equal(manifest.headers.get('cache-control'), 'no-store')
})
