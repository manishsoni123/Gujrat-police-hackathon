// Capture one frame from selected relay paths every N minutes so the sandbox loop phase
// (burnt-in footage clock) can be read off later. Detached helper; no credentials involved
// (it reads our own MediaMTX relay on the compose network).
// Usage: node scripts/loopcheck.mjs [minutes=15] [hours=26] [cams=61,70]
import { spawnSync } from 'node:child_process'
import { mkdirSync, appendFileSync } from 'node:fs'
import { resolve } from 'node:path'

const [minutes = '15', hours = '26', cams = '61,70'] = process.argv.slice(2)
const outDir = resolve(process.cwd(), 'media', 'loopcheck')
mkdirSync(outDir, { recursive: true })
const net = 'sentinel_default'
const image = 'sentinel-anpr:cpu'
const camList = cams.split(',').map(s => s.trim()).filter(Boolean)
const iterations = Math.ceil((Number(hours) * 60) / Number(minutes))
const pad = n => String(n).padStart(2, '0')
const stamp = () => { const d = new Date(); return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}` }
const sleep = ms => new Promise(r => setTimeout(r, ms))

for (let i = 0; i < iterations; i++) {
  const ts = stamp()
  for (const cam of camList) {
    const r = spawnSync('docker', ['run', '--rm', '--network', net, '-v', `${outDir}:/out`, image,
      'ffmpeg', '-nostdin', '-loglevel', 'error', '-rtsp_transport', 'tcp', '-timeout', '40000000',
      '-i', `rtsp://mediamtx:8554/cam_${cam}`, '-frames:v', '1', '-q:v', '4', '-y', `/out/${ts}_cam${cam}.jpg`],
      { encoding: 'utf8', timeout: 90000, env: { ...process.env, MSYS_NO_PATHCONV: '1' } })
    if (r.status !== 0) appendFileSync(resolve(outDir, 'failures.log'), `${ts} cam${cam} status=${r.status} ${(r.stderr || '').trim().slice(0, 200)}\n`)
  }
  appendFileSync(resolve(outDir, 'runs.log'), `${ts} iteration ${i + 1}/${iterations}\n`)
  await sleep(Number(minutes) * 60 * 1000)
}
