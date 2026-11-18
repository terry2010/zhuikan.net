#!/usr/bin/env node
/**
 * news-update.zhuikan.net — 更新检查端点（零依赖，Node >=22）
 *
 * 路由：
 *   POST /v1/check    上报 {pkg, version, companions} → 落 reports.jsonl → 回 latest.json
 *   GET  /v1/check    直接回 latest.json（浏览器/脚本可读）
 *   GET  /v1/versions 官网版本徽标：npm dist-tags 轮询缓存（daily 以 latest.json 兜底）
 *   GET  /healthz     探活
 *
 * 设计口径：
 *  - latest.json 是唯一事实源——改文件即发公告，每请求重读，无需重启。
 *  - 上报最小化落盘：IP 截断（v4 /24，v6 /48）、UA 截 120 字符、body 上限 8KB。
 *  - 内存令牌桶限流：每 IP 每小时 60 次（防刷，不靠精确）。
 *  - 容器内监听 0.0.0.0:8788，公网入口交给 webnet 内 nginx 反代（TLS 在反代层终止）。
 */
import { createServer } from 'node:http'
import { appendFileSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const DIR = dirname(fileURLToPath(import.meta.url))
const HOST = process.env.UPDATE_HOST || '127.0.0.1'
const PORT = Number(process.env.UPDATE_PORT || 8788)
const LATEST_FILE = process.env.LATEST_FILE || join(DIR, 'latest.json')
const REPORTS_FILE = process.env.REPORTS_FILE || join(DIR, 'reports.jsonl')
const BODY_CAP = 8 * 1024
const RATE_MAX = 60
const RATE_WINDOW_MS = 3600_000

const buckets = new Map()
const rateLimited = (ip) => {
  const now = Date.now()
  let b = buckets.get(ip)
  if (!b || now > b.reset) { b = { n: 0, reset: now + RATE_WINDOW_MS }; buckets.set(ip, b) }
  if (++b.n > RATE_MAX) return true
  if (buckets.size > 10_000) buckets.clear() // 防内存膨胀的粗兜底
  return false
}

/** IP 截断：v4 → a.b.c.0，v6 → 前 3 组 + ::。落盘数据不含精确地址。 */
const maskIp = (ip) => {
  if (!ip) return ''
  if (ip.startsWith('::ffff:')) ip = ip.slice(7)
  if (ip.includes('.')) return ip.split('.').slice(0, 3).join('.') + '.0'
  if (ip.includes(':')) return ip.split(':').slice(0, 3).join(':') + '::'
  return ''
}

const sendJson = (res, status, obj, extraHeaders = {}) => {
  const body = JSON.stringify(obj)
  res.writeHead(status, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store', ...extraHeaders })
  res.end(body)
}

/* ---- /v1/versions：npm dist-tags 每小时轮询缓存 ---- */
const PKGS = ['dsh-rss-news-daily', 'dsh-rss-news-zhuikan', 'dsh-rss-news-flash-kuaixun', 'dsh-rss-news-browser-fetch']
const VERSIONS_TTL_MS = 3600_000
const SITE_ORIGIN = 'https://zhuikan.net'
let versionsCache = { at: 0, map: {} }

const npmLatest = async (pkg) => {
  const r = await fetch(`https://registry.npmjs.org/-/package/${pkg}/dist-tags`, { signal: AbortSignal.timeout(8000) })
  if (!r.ok) return null
  const d = await r.json()
  return typeof d?.latest === 'string' && d.latest.length <= 40 ? d.latest : null
}

const refreshVersions = async () => {
  const map = { ...versionsCache.map }
  await Promise.all(PKGS.map(async (pkg) => {
    try { const v = await npmLatest(pkg); if (v) map[pkg] = v } catch { /* 保留旧值 */ }
  }))
  versionsCache = { at: Date.now(), map }
}

const versionsPayload = async () => {
  if (Date.now() - versionsCache.at > VERSIONS_TTL_MS) await refreshVersions()
  const map = { ...versionsCache.map }
  map['dsh-rss-news-daily'] ??= latestPayload()?.latest // npm 不可达时 latest.json 兜底
  return Object.keys(map).length
    ? { versions: map, at: new Date(versionsCache.at).toISOString() }
    : null
}

refreshVersions().catch(() => {})

/** 每请求重读 latest.json——改文件即发公告。读不出 = 503。 */
const latestPayload = () => {
  try {
    const v = JSON.parse(readFileSync(LATEST_FILE, 'utf8'))
    return typeof v?.latest === 'string' ? v : null
  } catch { return null }
}

const readBody = (req) => new Promise((resolve, reject) => {
  const chunks = []
  let size = 0
  req.on('data', (c) => {
    size += c.length
    if (size > BODY_CAP) { reject(new Error('body too large')); req.destroy(); return }
    chunks.push(c)
  })
  req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')))
  req.on('error', reject)
})

/** 上报字段白名单消毒：只留 pkg/version/companions，长度全截断。 */
const sanitizeReport = (v) => {
  if (typeof v !== 'object' || v === null) return null
  const pkg = typeof v.pkg === 'string' ? v.pkg.slice(0, 100) : ''
  const version = typeof v.version === 'string' ? v.version.slice(0, 40) : ''
  if (!pkg || !version) return null
  const companions = {}
  if (typeof v.companions === 'object' && v.companions !== null) {
    for (const [k, val] of Object.entries(v.companions).slice(0, 50)) {
      if (typeof val === 'string') companions[k.slice(0, 100)] = val.slice(0, 40)
    }
  }
  return { pkg, version, companions }
}

const server = createServer(async (req, res) => {
  const u = new URL(req.url || '/', 'http://x')
  const ip = maskIp((req.headers['x-forwarded-for'] || '').split(',')[0]?.trim() || req.socket.remoteAddress || '')

  if (u.pathname === '/healthz') return sendJson(res, 200, { ok: true })

  if (u.pathname === '/v1/versions') {
    const cors = { 'access-control-allow-origin': SITE_ORIGIN, 'vary': 'origin' }
    if (req.method === 'OPTIONS') { res.writeHead(204, { ...cors, 'access-control-allow-methods': 'GET' }); return res.end() }
    if (req.method !== 'GET') { res.writeHead(405); return res.end() }
    const v = await versionsPayload()
    return v ? sendJson(res, 200, v, cors) : sendJson(res, 503, { error: 'versions unavailable' }, cors)
  }

  if (u.pathname !== '/v1/check') { res.writeHead(404); return res.end('not found') }
  if (rateLimited(ip)) return sendJson(res, 429, { error: 'rate limited' })

  const latest = latestPayload()
  if (!latest) return sendJson(res, 503, { error: 'latest.json unreadable' })

  if (req.method === 'GET') return sendJson(res, 200, latest)
  if (req.method !== 'POST') { res.writeHead(405); return res.end() }

  try {
    const report = sanitizeReport(JSON.parse((await readBody(req)) || '{}'))
    if (report) {
      appendFileSync(REPORTS_FILE, JSON.stringify({ ts: new Date().toISOString(), ip, ua: String(req.headers['user-agent'] || '').slice(0, 120), ...report }) + '\n')
    }
  } catch { /* 坏 body 也照常回最新信息——检查通道不为上报格式所累 */ }
  return sendJson(res, 200, latest)
})

server.listen(PORT, HOST, () => console.log(`news-update listening on ${HOST}:${PORT}`))
