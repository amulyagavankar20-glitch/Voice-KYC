import React, { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import {
  Send, Mic, Square, RotateCcw, CheckCircle2, Loader2, Volume2, AlertTriangle,
  Upload, FileText, Languages, Sparkles, Info, X, BadgeCheck,
} from 'lucide-react'

const MAX_RECORDING_SECONDS = 29
const TARGET_RATE = 16000
const OUTBOX_KEY = 'voicekyc.outbox.v1'
const APPLICATION_KEY = 'voicekyc.application.v1'
const REQUIRED_FIELDS = [
  'full_name', 'father_name', 'date_of_birth', 'address',
  'occupation', 'monthly_income', 'loan_purpose', 'phone',
]
const FIELD_LABELS = {
  full_name: 'Full name', father_name: "Father's name", date_of_birth: 'Date of birth',
  address: 'Address', occupation: 'Occupation', monthly_income: 'Monthly income',
  loan_purpose: 'Loan purpose', phone: 'Mobile number',
}
const STATUS_LABEL = {
  in_progress: 'Collecting KYC details',
  pending_review: 'Pending human review',
  request_info: 'Agent requested more information',
  approve: 'Reviewed: approved',
  reject: 'Reviewed: rejected',
}

function readOutbox() {
  try { const p = JSON.parse(localStorage.getItem(OUTBOX_KEY) || '[]'); return Array.isArray(p) ? p : [] } catch { return [] }
}
function writeOutbox(items) {
  try { localStorage.setItem(OUTBOX_KEY, JSON.stringify(items)) } catch {}
}
function makeClientId() {
  if (window.crypto?.randomUUID) return window.crypto.randomUUID()
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function downsampleBuffer(buffer, inputRate, targetRate) {
  if (inputRate <= targetRate) return buffer
  const ratio = inputRate / targetRate
  const length = Math.floor(buffer.length / ratio)
  const output = new Float32Array(length)
  for (let i = 0; i < length; i++) {
    const start = Math.floor(i * ratio)
    const end = Math.min(buffer.length, Math.floor((i + 1) * ratio))
    let sum = 0
    for (let j = start; j < end; j++) sum += buffer[j]
    output[i] = sum / Math.max(1, end - start)
  }
  return output
}
function floatTo16BitPCM(samples) {
  const out = new Int16Array(samples.length)
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]))
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff
  }
  return out
}
function pcmToWav(chunks, sampleRate) {
  const total = chunks.reduce((s, c) => s + c.byteLength, 0)
  const buffer = new ArrayBuffer(44 + total)
  const view = new DataView(buffer)
  const wa = (o, t) => { for (let i = 0; i < t.length; i++) view.setUint8(o + i, t.charCodeAt(i)) }
  wa(0, 'RIFF'); view.setUint32(4, 36 + total, true); wa(8, 'WAVE'); wa(12, 'fmt ')
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true)
  view.setUint32(24, sampleRate, true); view.setUint32(28, sampleRate * 2, true)
  view.setUint16(32, 2, true); view.setUint16(34, 16, true); wa(36, 'data'); view.setUint32(40, total, true)
  let offset = 44
  chunks.forEach(c => { const bytes = new Uint8Array(c.buffer, c.byteOffset, c.byteLength); new Uint8Array(buffer, offset, bytes.byteLength).set(bytes); offset += bytes.byteLength })
  return new Blob([buffer], { type: 'audio/wav' })
}
function waitForStreamDone(ws, fallbackText) {
  return new Promise(resolve => {
    let settled = false, timer = null
    const cleanup = () => { clearTimeout(timer); ws.removeEventListener('message', onMsg); ws.removeEventListener('close', onClose); ws.removeEventListener('error', onClose) }
    const finish = (text, err) => { if (settled) return; settled = true; cleanup(); resolve({ text: (text || '').trim(), err }) }
    const onMsg = (event) => {
      let p; try { p = JSON.parse(event.data) } catch { return }
      if (p.type === 'done') finish(p.text || fallbackText)
      else if (p.type === 'error') finish(fallbackText, p.detail || 'Voice service error')
      else if (p.type === 'final') finish(p.text_so_far || p.text || fallbackText)
    }
    const onClose = () => finish(fallbackText)
    ws.addEventListener('message', onMsg); ws.addEventListener('close', onClose); ws.addEventListener('error', onClose)
    timer = setTimeout(() => finish(fallbackText), 7000)
    try { ws.send(JSON.stringify({ type: 'stop' })) } catch { finish(fallbackText) }
  })
}

export default function UserInterface() {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [applicationId, setApplicationId] = useState(null)
  const [status, setStatus] = useState(null)
  const [loading, setLoading] = useState(false)
  const [progress, setProgress] = useState(0)
  const [isComplete, setIsComplete] = useState(false)
  const [language, setLanguage] = useState('hinglish')
  const [extracted, setExtracted] = useState({})
  const [documents, setDocuments] = useState([])
  const [error, setError] = useState('')
  const [recording, setRecording] = useState(false)
  const [voiceBusy, setVoiceBusy] = useState(false)
  const [voicePhase, setVoicePhase] = useState('') // recording | transcribing | speaking
  const [partialText, setPartialText] = useState('')
  const [outboxCount, setOutboxCount] = useState(0)
  const [docType, setDocType] = useState('pan')
  const [docText, setDocText] = useState('')
  const [docNote, setDocNote] = useState('')
  const [docBusy, setDocBusy] = useState(false)
  const [showConsent, setShowConsent] = useState(false)
  const [consentChecked, setConsentChecked] = useState(false)
  const [caps, setCaps] = useState(null)

  const applicationIdRef = useRef(null)
  const loadingRef = useRef(false)
  const disposedRef = useRef(false)
  const recordingIdRef = useRef(0)
  const recordingActiveRef = useRef(false)
  const streamRef = useRef(null)
  const audioCtxRef = useRef(null)
  const sourceRef = useRef(null)
  const processorRef = useRef(null)
  const pcmRef = useRef([])
  const pendingSendRef = useRef([])
  const partialRef = useRef('')
  const wsRef = useRef(null)
  const stopTimerRef = useRef(null)
  const audioRef = useRef(null)
  const ttsUrlRef = useRef(null)
  const docFileRef = useRef(null)
  const bottomRef = useRef(null)

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages, loading, partialText])

  const refreshApplication = useCallback(async id => {
    const target = id || applicationIdRef.current
    if (!target) return null
    try {
      const [detail, docs] = await Promise.all([
        api.getApplication(target),
        api.listDocuments(target).catch(() => []),
      ])
      if (disposedRef.current) return null
      setMessages((detail.conversation || []).map(m => ({ role: m.role, content: m.content, channel: m.channel, audioId: m.audio_id })))
      const fields = detail.extracted || {}
      setExtracted(fields)
      setStatus(detail.status)
      const filled = REQUIRED_FIELDS.filter(f => fields[f]?.value).length
      const hasDoc = (docs || []).length > 0
      const totalSteps = REQUIRED_FIELDS.length + 1 // +1 for document step
      const done = filled + (hasDoc ? 1 : 0)
      setProgress(Math.round((done / totalSteps) * 100))
      setIsComplete(['pending_review', 'approve', 'reject'].includes(detail.status))
      setDocuments(docs || [])
      return detail
    } catch (err) {
      if (!disposedRef.current && err.status !== 404) setError(err.message)
      if (err.status === 404) {
        // stale stored app
        try { localStorage.removeItem(APPLICATION_KEY) } catch {}
        applicationIdRef.current = null
        setApplicationId(null)
      }
      return null
    }
  }, [])

  const flushOutbox = useCallback(async () => {
    const items = readOutbox()
    if (!items.length) { setOutboxCount(0); return }
    const remaining = []
    let changed = false
    for (const item of items) {
      try {
        await api.sendMessage(item.applicationId, item.message, item.clientMessageId)
        changed = true
      } catch (err) {
        if (err.retryable) remaining.push(item)
        else changed = true
      }
    }
    writeOutbox(remaining)
    setOutboxCount(remaining.length)
    if (changed && applicationIdRef.current) {
      await refreshApplication(applicationIdRef.current)
      if (!remaining.length) setError('')
    }
  }, [refreshApplication])

  const flushRef = useRef(flushOutbox)
  useEffect(() => { flushRef.current = flushOutbox }, [flushOutbox])

  useEffect(() => {
    api.capabilities().then(setCaps).catch(() => {})
  }, [])

  useEffect(() => {
    const stored = localStorage.getItem(APPLICATION_KEY)
    if (stored) {
      applicationIdRef.current = stored
      setApplicationId(stored)
      refreshApplication(stored)
    }
    setOutboxCount(readOutbox().length)
    const onOnline = () => flushRef.current()
    window.addEventListener('online', onOnline)
    const timer = setInterval(() => { if (navigator.onLine) flushRef.current() }, 15000)
    return () => { window.removeEventListener('online', onOnline); clearInterval(timer) }
  }, [refreshApplication])

  useEffect(() => {
    if (!applicationId) return undefined
    const timer = setInterval(() => {
      if (!loadingRef.current && !recordingActiveRef.current && !voiceBusy) refreshApplication(applicationId)
    }, 8000)
    return () => clearInterval(timer)
  }, [applicationId, voiceBusy, refreshApplication])

  const stopCapture = useCallback(() => {
    clearTimeout(stopTimerRef.current)
    recordingActiveRef.current = false
    try { processorRef.current?.disconnect() } catch {}
    try { sourceRef.current?.disconnect() } catch {}
    processorRef.current = null; sourceRef.current = null
    try { streamRef.current?.getTracks().forEach(t => t.stop()) } catch {}
    streamRef.current = null
    const ctx = audioCtxRef.current; audioCtxRef.current = null
    if (ctx) ctx.close().catch(() => {})
  }, [])
  const closeSocket = useCallback(() => {
    const s = wsRef.current; wsRef.current = null
    if (s) { try { s.close() } catch {} }
    pendingSendRef.current = []
  }, [])
  useEffect(() => () => {
    disposedRef.current = true; recordingIdRef.current += 1
    stopCapture(); closeSocket()
    audioRef.current?.pause(); audioRef.current = null
    if (ttsUrlRef.current) { URL.revokeObjectURL(ttsUrlRef.current); ttsUrlRef.current = null }
  }, [closeSocket, stopCapture])

  const startConversation = async () => {
    if (!consentChecked) { setError('Please provide consent to continue.'); return }
    setShowConsent(false)
    recordingIdRef.current += 1
    if (recordingActiveRef.current) stopCapture()
    closeSocket()
    setLoading(true); loadingRef.current = true
    setError(''); setMessages([]); setExtracted({}); setDocuments([])
    setProgress(0); setIsComplete(false); setStatus(null)
    setApplicationId(null); applicationIdRef.current = null
    setInput(''); setPartialText('')
    try {
      const data = await api.startApplication(language, true)
      applicationIdRef.current = data.application_id
      setApplicationId(data.application_id)
      setStatus(data.status)
      setMessages([{ role: 'assistant', content: data.reply }])
      try { localStorage.setItem(APPLICATION_KEY, data.application_id) } catch {}
    } catch (err) {
      setError(err.message)
    } finally {
      loadingRef.current = false; setLoading(false)
    }
  }

  const queueMessage = useCallback(message => {
    const target = applicationIdRef.current
    if (!target) return
    const entry = { applicationId: target, message, clientMessageId: makeClientId(), queuedAt: Date.now() }
    const items = [...readOutbox(), entry]
    writeOutbox(items); setOutboxCount(items.length)
  }, [])

  const sendMessage = async (text = input) => {
    const userMsg = (text || '').trim()
    if (!userMsg || loading || isComplete || !applicationId || recording) return
    setInput(''); setError('')
    if (readOutbox().some(i => i.applicationId === applicationId)) {
      setMessages(prev => [...prev, { role: 'user', content: userMsg }])
      queueMessage(userMsg)
      setError('Saved on this device. It will be sent automatically when the connection returns.')
      return
    }
    const clientMessageId = makeClientId()
    setMessages(prev => [...prev, { role: 'user', content: userMsg }])
    setLoading(true); loadingRef.current = true
    try {
      await api.sendMessage(applicationId, userMsg, clientMessageId)
      await refreshApplication(applicationId)
    } catch (err) {
      if (err.retryable) {
        queueMessage(userMsg)
        setError('Connection issue. Your answer is saved and will sync automatically.')
      } else {
        setError(err.message)
        setMessages(prev => prev.slice(0, -1))
        setInput(current => current || userMsg)
      }
    } finally {
      loadingRef.current = false; setLoading(false)
    }
  }

  const startRecording = async () => {
    setError(''); setPartialText(''); partialRef.current = ''
    if (!applicationId) { setError('Start a new application before recording.'); return }
    if (recordingActiveRef.current || voiceBusy) return
    const AudioCtx = window.AudioContext || window.webkitAudioContext
    if (!navigator.mediaDevices?.getUserMedia || !AudioCtx) {
      setError('Audio recording is not supported in this browser. Please type your response.')
      return
    }
    try {
      const mic = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } })
      if (disposedRef.current) { mic.getTracks().forEach(t => t.stop()); return }
      const recordingId = ++recordingIdRef.current
      const ctx = new AudioCtx()
      await ctx.resume().catch(() => {})
      const source = ctx.createMediaStreamSource(mic)
      const processor = ctx.createScriptProcessor(4096, 1, 1)
      pcmRef.current = []; pendingSendRef.current = []; partialRef.current = ''
      recordingActiveRef.current = true
      streamRef.current = mic; audioCtxRef.current = ctx; processorRef.current = processor; sourceRef.current = source

      // Best-effort live streaming transcription (requires Prisma configured server-side)
      try {
        const socket = new WebSocket(api.voiceStreamUrl(language))
        socket.binaryType = 'arraybuffer'
        wsRef.current = socket
        socket.onopen = () => {
          if (wsRef.current !== socket || recordingId !== recordingIdRef.current) return
          try { socket.send(JSON.stringify({ type: 'start', sampleRate: TARGET_RATE })) } catch {}
          const queued = pendingSendRef.current; pendingSendRef.current = []
          queued.forEach(chunk => { try { socket.send(chunk) } catch {} })
        }
        socket.onmessage = event => {
          if (wsRef.current !== socket) return
          let p; try { p = JSON.parse(event.data) } catch { return }
          if (p.type === 'partial' || p.type === 'final') {
            partialRef.current = p.text_so_far || p.text || partialRef.current
            setPartialText(partialRef.current)
          } else if (p.type === 'error') {
            // keep recording; batch fallback will try at stop
          }
        }
        socket.onerror = () => {}
        socket.onclose = () => {}
      } catch { wsRef.current = null }

      processor.onaudioprocess = event => {
        if (!recordingActiveRef.current || recordingId !== recordingIdRef.current) return
        const channel = event.inputBuffer.getChannelData(0)
        const resampled = downsampleBuffer(channel, ctx.sampleRate, TARGET_RATE)
        const pcm = floatTo16BitPCM(resampled)
        pcmRef.current.push(pcm)
        const bytes = new Uint8Array(pcm.buffer.slice(0))
        const socket = wsRef.current
        if (socket && socket.readyState === WebSocket.OPEN) { try { socket.send(bytes) } catch {} }
        else pendingSendRef.current.push(bytes)
      }
      source.connect(processor)
      processor.connect(ctx.destination)
      setRecording(true); setVoicePhase('recording')
      stopTimerRef.current = setTimeout(() => { stopRecording() }, MAX_RECORDING_SECONDS * 1000)
    } catch (err) {
      stopCapture(); closeSocket()
      if (!disposedRef.current) {
        setError(err?.name === 'NotAllowedError'
          ? 'Microphone permission denied. Please allow mic access or type your response.'
          : (err.message || 'Could not start recording. Please type your response.'))
      }
    }
  }

  const stopRecording = async () => {
    if (!recordingActiveRef.current) return
    clearTimeout(stopTimerRef.current)
    const recordingId = recordingIdRef.current
    const pcmChunks = pcmRef.current
    stopCapture()
    setRecording(false); setVoicePhase('transcribing'); setVoiceBusy(true)

    let captured = ''
    const socket = wsRef.current
    if (socket && socket.readyState === WebSocket.OPEN) {
      const res = await waitForStreamDone(socket, partialRef.current)
      wsRef.current = null
      try { socket.close() } catch {}
      if (disposedRef.current || recordingId !== recordingIdRef.current) return
      if (res.text) captured = res.text
    } else {
      closeSocket()
    }

    if (!captured) {
      const total = pcmChunks.reduce((s, c) => s + c.byteLength, 0)
      pcmRef.current = []
      if (!total) {
        setVoiceBusy(false); setVoicePhase(''); setPartialText('')
        setError('No speech detected. Please try again or type your response.')
        return
      }
      if (disposedRef.current || recordingId !== recordingIdRef.current) return
      try {
        const wav = pcmToWav(pcmChunks, TARGET_RATE)
        const result = await api.transcribe(new File([wav], 'voice-response.wav', { type: 'audio/wav' }), language)
        if (disposedRef.current) return
        if (result?.text) captured = result.text
      } catch (err) {
        if (!disposedRef.current) {
          const msg = err?.status === 503
            ? 'Voice transcription is not configured on the server. Please type your response.'
            : (err.message || 'Could not transcribe recording. Please type your response.')
          setError(msg)
        }
      }
    } else {
      pcmRef.current = []
    }

    if (!disposedRef.current) {
      if (captured) {
        setInput(current => (current ? `${current} ${captured}` : captured))
        setError('')
      } else if (!error) {
        setError('No speech detected. Please try again or type your response.')
      }
      setVoiceBusy(false); setVoicePhase(''); setPartialText(''); partialRef.current = ''
    }
  }

  const speak = async text => {
    setError(''); setVoiceBusy(true); setVoicePhase('speaking')
    try {
      audioRef.current?.pause()
      if (ttsUrlRef.current) { URL.revokeObjectURL(ttsUrlRef.current); ttsUrlRef.current = null }
      const blob = await api.synthesize(text, language)
      if (!(blob instanceof Blob)) throw new Error('Voice service returned an invalid response.')
      const url = URL.createObjectURL(blob)
      ttsUrlRef.current = url
      const audio = new Audio(url)
      audioRef.current = audio
      const release = () => { URL.revokeObjectURL(url); if (ttsUrlRef.current === url) ttsUrlRef.current = null; if (audioRef.current === audio) audioRef.current = null }
      audio.onended = release; audio.onerror = release
      try { await audio.play() } catch (e) { release(); throw e }
    } catch (err) {
      if (!disposedRef.current) {
        setError(err?.status === 503 ? 'Voice playback is not configured on the server.' : (err.message || 'Could not play voice.'))
      }
    } finally {
      if (!disposedRef.current) { setVoiceBusy(false); setVoicePhase('') }
    }
  }

  const uploadDocument = async () => {
    if (!applicationId) return
    const file = docFileRef.current?.files?.[0] || null
    if (!docText.trim() && !file) { setDocNote('Paste document text or choose a file (PAN/Aadhaar).'); return }
    setDocBusy(true); setDocNote('')
    try {
      const document = await api.uploadDocument(applicationId, { docType, text: docText.trim() || null, file })
      const check = document.cross_check || {}
      const flag = check.review_flag ? ' Review flag raised for agent.' : ''
      setDocNote(`${docType.toUpperCase()} stored. Cross-check: ${check.overall || 'not checked'}.${flag}`)
      setDocText('')
      if (docFileRef.current) docFileRef.current.value = ''
      await refreshApplication(applicationId)
    } catch (err) {
      setDocNote(err.message)
    } finally {
      setDocBusy(false)
    }
  }

  const handleKeyDown = e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage() }
  }

  const canSend = !!applicationId && !loading && !recording && !isComplete && !!input.trim()

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
      {/* Consent Modal */}
      {showConsent && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-slate-900/60 backdrop-blur-sm" onClick={() => setShowConsent(false)} />
          <div className="relative w-full max-w-lg bg-white rounded-3xl shadow-2xl border border-slate-200 p-6">
            <button onClick={() => setShowConsent(false)} className="absolute top-4 right-4 p-1.5 rounded-lg hover:bg-slate-100 text-slate-500"><X size={16} /></button>
            <div className="flex items-center gap-3 mb-3">
              <div className="w-10 h-10 rounded-xl bg-primary-50 text-primary-700 flex items-center justify-center"><ShieldIcon /></div>
              <div>
                <h3 className="font-semibold text-lg text-slate-900">Start microfinance KYC intake</h3>
                <p className="text-xs text-slate-500">Consent required before collecting information</p>
              </div>
            </div>
            <div className="bg-slate-50 rounded-xl p-4 text-sm text-slate-700 space-y-2 border border-slate-100">
              <p>We will collect your KYC details (name, DOB, address, occupation, income, loan purpose, phone) by voice or text for <strong>microfinance application review</strong>.</p>
              <ul className="list-disc pl-5 text-xs text-slate-600 space-y-1">
                <li>Your responses are applicant-provided statements and will be reviewed by a human agent.</li>
                <li>This is <strong>not</strong> identity verification and <strong>not</strong> loan approval.</li>
                <li>You can type or speak in Hinglish or Marathi.</li>
              </ul>
            </div>
            <label className="flex gap-3 items-start mt-4 cursor-pointer">
              <input type="checkbox" checked={consentChecked} onChange={e => setConsentChecked(e.target.checked)} className="mt-1 h-4 w-4 accent-primary-600" />
              <span className="text-sm text-slate-800">I consent to collect and process the information I provide for this microfinance KYC intake demo.</span>
            </label>
            {error && <p className="text-xs text-red-600 mt-2">{error}</p>}
            <div className="flex gap-2 mt-5">
              <button onClick={() => setShowConsent(false)} className="flex-1 py-2.5 rounded-xl border border-slate-200 text-sm text-slate-700 hover:bg-slate-50">Cancel</button>
              <button onClick={startConversation} disabled={!consentChecked || loading} className="flex-1 py-2.5 rounded-xl bg-gradient-to-r from-primary-600 to-indigo-600 text-white text-sm font-medium disabled:opacity-50 flex items-center justify-center gap-2">
                {loading ? <Loader2 size={16} className="animate-spin" /> : <Sparkles size={16} />} Start application
              </button>
            </div>
          </div>
        </div>
      )}

      <section className="lg:col-span-2 bg-white/80 backdrop-blur rounded-3xl shadow-sm border border-slate-200/80 flex flex-col min-h-[72vh] overflow-hidden">
        <header className="px-5 py-4 border-b border-slate-100 flex flex-wrap items-center justify-between gap-3 bg-gradient-to-r from-white to-primary-50/30">
          <div>
            <div className="flex items-center gap-2">
              <h2 className="font-semibold text-slate-900">Microfinance KYC Intake</h2>
              {caps?.llm_configured ? (
                <span className="text-[10px] px-2 py-0.5 rounded-full bg-indigo-50 text-indigo-700 border border-indigo-100">Groq extraction</span>
              ) : (
                <span className="text-[10px] px-2 py-0.5 rounded-full bg-slate-100 text-slate-600">Rules extraction</span>
              )}
            </div>
            <p className="text-xs text-slate-500">Step-by-step details & documents · speak or type</p>
          </div>
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-xl border border-slate-200 bg-white">
              <Languages size={14} className="text-slate-500" />
              <select
                value={language}
                onChange={e => setLanguage(e.target.value)}
                disabled={!!applicationId}
                className="text-sm bg-transparent focus:outline-none disabled:opacity-60"
                title={applicationId ? 'Language locked for this application' : 'Choose language'}
              >
                <option value="hinglish">Hinglish</option>
                <option value="marathi">मराठी (Marathi)</option>
              </select>
            </div>
            <button
              onClick={() => { setError(''); setConsentChecked(false); setShowConsent(true) }}
              disabled={loading}
              className="flex items-center gap-2 px-3.5 py-2 bg-slate-900 text-white rounded-xl text-sm disabled:opacity-50 hover:bg-slate-800"
            >
              <RotateCcw size={14} /> New application
            </button>
          </div>
        </header>

        {applicationId && (
          <div className="px-5 py-3 bg-slate-50/80 border-b border-slate-100">
            <div className="flex justify-between items-center text-xs mb-1.5">
              <span className="font-medium text-slate-700">{STATUS_LABEL[status] || 'Information collection progress'}</span>
              <span className="text-slate-600">{progress}% complete</span>
            </div>
            <div className="h-2 bg-slate-200/80 rounded-full overflow-hidden">
              <div className="h-2 bg-gradient-to-r from-primary-500 to-indigo-500 rounded-full transition-all duration-500" style={{ width: `${progress}%` }} />
            </div>
            <p className="text-[10px] text-slate-400 mt-1.5">Progress includes 8 KYC fields + 1 document upload step.</p>
          </div>
        )}

        <div className="flex-1 overflow-y-auto p-4 sm:p-5 space-y-4">
          {!messages.length && (
            <div className="h-full min-h-72 flex flex-col items-center justify-center text-center px-6">
              <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-primary-500 to-indigo-600 text-white flex items-center justify-center shadow-lg shadow-primary-500/20 mb-4">
                <Mic size={28} />
              </div>
              <p className="font-semibold text-slate-900 text-lg">Start your microfinance KYC</p>
              <p className="text-sm text-slate-500 mt-1 max-w-md">
                We’ll guide you step-by-step to provide your details and KYC documents (PAN/Aadhaar) for agent review. You can speak or type in Hinglish or Marathi.
              </p>
              <button onClick={() => { setError(''); setConsentChecked(false); setShowConsent(true) }} className="mt-5 px-5 py-2.5 rounded-xl bg-gradient-to-r from-primary-600 to-indigo-600 text-white text-sm font-medium shadow-lg shadow-primary-500/20">
                Begin application
              </button>
            </div>
          )}

          {messages.map((message, index) => (
            <div key={`${index}-${message.role}`} className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}>
              <div className={`max-w-[88%] px-4 py-3 rounded-2xl text-sm whitespace-pre-wrap shadow-sm ${message.role === 'user' ? 'bg-gradient-to-br from-primary-600 to-indigo-600 text-white rounded-br-md' : 'bg-white border border-slate-200 text-slate-800 rounded-bl-md'}`}>
                <div>{message.content}</div>
                {message.channel === 'follow_up' && (
                  <div className="text-[10px] mt-2 inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-amber-100 text-amber-800">Agent follow-up</div>
                )}
                {message.role === 'assistant' && (
                  <button onClick={() => speak(message.content)} disabled={voiceBusy || isComplete} className="mt-2 text-xs inline-flex items-center gap-1.5 text-primary-700 hover:text-primary-800 disabled:opacity-40">
                    <Volume2 size={13} /> Play voice
                  </button>
                )}
              </div>
            </div>
          ))}

          {(recording || voicePhase === 'transcribing') && (
            <div className="rounded-2xl border border-primary-200 bg-primary-50/60 p-4">
              <div className="flex items-center gap-3">
                <div className={`w-10 h-10 rounded-xl flex items-center justify-center ${recording ? 'bg-red-500 text-white animate-pulse' : 'bg-primary-600 text-white'}`}>
                  {recording ? <Mic size={18} /> : <Loader2 size={18} className="animate-spin" />}
                </div>
                <div className="flex-1">
                  <div className="text-sm font-medium text-slate-900">
                    {recording ? 'Recording… speak now' : 'Transcribing your voice…'}
                  </div>
                  <div className="text-xs text-slate-600">
                    {recording ? `Listening (${language === 'marathi' ? 'Marathi' : 'Hinglish'}). Tap stop when done.` : 'Converting speech to text. You can also type instead.'}
                  </div>
                </div>
                {recording && (
                  <div className="flex items-end gap-0.5 h-6">
                    {[8,14,10,18,12].map((h,i) => <span key={i} className="w-1.5 bg-primary-500 rounded-full animate-pulse" style={{height: h, animationDelay: `${i*80}ms`}} />)}
                  </div>
                )}
              </div>
              {partialText && (
                <div className="mt-3 text-sm bg-white border border-primary-100 rounded-xl p-3 text-slate-800">
                  <span className="text-[10px] uppercase tracking-wide text-primary-600 font-semibold">Live transcript</span>
                  <div className="mt-1">{partialText}</div>
                </div>
              )}
            </div>
          )}

          {loading && (
            <div className="flex gap-2 items-center text-sm text-slate-500">
              <Loader2 className="animate-spin" size={16} /> Processing your response…
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        {outboxCount > 0 && (
          <div className="mx-4 mb-2 p-2.5 rounded-xl bg-blue-50 text-blue-800 text-xs flex gap-2 border border-blue-100">
            <AlertTriangle size={14} className="shrink-0 mt-0.5" />
            {outboxCount} answer{outboxCount === 1 ? '' : 's'} saved on this device and will sync automatically.
          </div>
        )}
        {error && (
          <div className="mx-4 mb-3 p-3 rounded-xl bg-red-50 text-red-700 text-sm flex gap-2 border border-red-100">
            <AlertTriangle size={16} className="shrink-0 mt-0.5" />
            <div className="flex-1">{error}</div>
            <button onClick={() => setError('')} className="text-red-500 hover:text-red-700"><X size={14} /></button>
          </div>
        )}

        <footer className="p-4 border-t border-slate-100 bg-white">
          {isComplete ? (
            <div className="flex items-center justify-center gap-2 text-emerald-800 bg-emerald-50 py-3 rounded-xl text-sm border border-emerald-100">
              <CheckCircle2 size={18} /> KYC details collected. Pending human review — not identity verification or loan approval.
            </div>
          ) : (
            <>
              <div className="flex gap-2 items-end">
                <button
                  onClick={recording ? stopRecording : startRecording}
                  disabled={!applicationId || loading || (voiceBusy && !recording)}
                  title={recording ? 'Stop recording' : 'Record voice response'}
                  className={`relative h-11 w-11 shrink-0 rounded-xl border flex items-center justify-center transition ${
                    recording ? 'bg-red-500 text-white border-red-500 shadow-lg shadow-red-500/30' : 'bg-white text-slate-700 border-slate-200 hover:border-primary-300 hover:text-primary-700'
                  } disabled:opacity-40`}
                >
                  {recording ? <Square size={16} /> : (voiceBusy ? <Loader2 size={16} className="animate-spin" /> : <Mic size={18} />)}
                  {recording && <span className="absolute -top-1 -right-1 w-3 h-3 bg-red-500 rounded-full border-2 border-white animate-ping" />}
                </button>
                <textarea
                  value={input}
                  onChange={e => setInput(e.target.value)}
                  onKeyDown={handleKeyDown}
                  disabled={!applicationId || recording}
                  rows={1}
                  placeholder={!applicationId ? 'Start a new application to respond…' : (recording ? 'Recording… stop to transcribe' : 'Type your response, or tap mic to speak…')}
                  className="flex-1 min-w-0 px-4 py-2.5 border border-slate-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-primary-500 disabled:bg-slate-50 resize-none"
                />
                <button onClick={() => sendMessage()} disabled={!canSend} className="h-11 w-11 shrink-0 rounded-xl bg-gradient-to-r from-primary-600 to-indigo-600 text-white flex items-center justify-center disabled:opacity-40 shadow-sm">
                  {loading ? <Loader2 size={16} className="animate-spin" /> : <Send size={16} />}
                </button>
              </div>
              <div className="flex items-center justify-between mt-2 text-[11px] text-slate-400">
                <span>{recording ? 'Recording live — transcription appears after stop (or live if available).' : 'Press Enter to send. Voice & text both work.'}</span>
                <span>{language === 'marathi' ? 'मराठी' : 'Hinglish'}</span>
              </div>
            </>
          )}
        </footer>
      </section>

      <aside className="space-y-4">
        <div className="bg-white/80 backdrop-blur rounded-3xl shadow-sm border border-slate-200/80 p-5">
          <h3 className="font-semibold text-slate-900 flex items-center gap-2"><BadgeCheck size={17} className="text-primary-600" /> Collected KYC details</h3>
          <p className="text-[11px] text-slate-500 mt-1">Applicant-provided · requires human verification</p>

          {Object.keys(extracted).length ? (
            <div className="mt-4 space-y-2">
              {REQUIRED_FIELDS.map(key => {
                const field = extracted[key]
                if (!field?.value) return null
                return (
                  <div key={key} className="bg-slate-50 rounded-xl p-3 border border-slate-100">
                    <div className="text-[10px] uppercase tracking-wide text-slate-500">{FIELD_LABELS[key] || key}</div>
                    <div className="text-sm font-medium text-slate-900 mt-0.5 break-words">{field.value}</div>
                    <div className="flex items-center gap-2 mt-1">
                      <span className="text-[10px] text-amber-700">Not verified</span>
                      {field.extraction_source && (
                        <span className={`text-[10px] px-1.5 py-0.5 rounded ${field.extraction_source === 'llm' ? 'bg-indigo-50 text-indigo-700' : 'bg-slate-200 text-slate-600'}`}>
                          {field.extraction_source === 'llm' ? 'AI' : 'Rules'}
                        </span>
                      )}
                    </div>
                  </div>
                )
              })}
              {REQUIRED_FIELDS.filter(k => !extracted[k]?.value).length > 0 && (
                <p className="text-xs text-slate-400 pt-1">Remaining: {REQUIRED_FIELDS.filter(k => !extracted[k]?.value).map(k => FIELD_LABELS[k]).join(', ')}</p>
              )}
            </div>
          ) : (
            <div className="mt-8 text-center text-sm text-slate-400">
              <Info size={22} className="mx-auto mb-2 opacity-40" />
              Your answers will appear here as they are collected.
            </div>
          )}
        </div>

        <div className="bg-white/80 backdrop-blur rounded-3xl shadow-sm border border-slate-200/80 p-5">
          <h4 className="text-sm font-semibold flex items-center gap-2"><FileText size={15} className="text-primary-600" /> KYC document <span className="text-[10px] font-normal text-slate-400">(optional but recommended)</span></h4>
          <p className="text-[11px] text-slate-500 mt-1">
            Upload PAN or Aadhaar for cross-check against your stated details. A match is <strong>not</strong> identity verification.
          </p>
          {applicationId ? (
            <>
              <div className="flex gap-2 mt-3">
                <select value={docType} onChange={e => setDocType(e.target.value)} className="border border-slate-200 rounded-lg px-2.5 py-2 text-xs bg-white">
                  <option value="pan">PAN</option>
                  <option value="aadhaar">Aadhaar</option>
                </select>
                <input ref={docFileRef} type="file" accept="image/*,text/plain" className="text-[11px] min-w-0 flex-1" />
              </div>
              <textarea
                value={docText}
                onChange={e => setDocText(e.target.value)}
                rows={2}
                placeholder="Or paste document text (name, DOB, number)"
                className="w-full mt-2 p-2.5 border border-slate-200 rounded-xl text-xs resize-none focus:outline-none focus:ring-2 focus:ring-primary-500"
              />
              <button onClick={uploadDocument} disabled={docBusy} className="mt-2 w-full flex items-center justify-center gap-2 py-2.5 bg-slate-900 text-white rounded-xl text-xs disabled:opacity-50 hover:bg-slate-800">
                {docBusy ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />} Upload & cross-check
              </button>
              {docNote && <p className="text-[11px] text-slate-600 mt-2 bg-slate-50 p-2 rounded-lg">{docNote}</p>}
              {documents.length > 0 && (
                <div className="mt-3 space-y-2">
                  {documents.map(d => (
                    <div key={d.id} className="bg-slate-50 rounded-xl p-2.5 text-[11px] border border-slate-100 flex items-center justify-between gap-2">
                      <div>
                        <span className="font-semibold uppercase">{d.doc_type}</span>
                        <div className="text-slate-500 mt-0.5">{d.extracted_fields?.number_masked || 'Number not extracted'} · {d.extraction_method}</div>
                      </div>
                      <span className={`px-2 py-0.5 rounded-full text-[10px] ${d.cross_check?.review_flag ? 'bg-red-100 text-red-700' : 'bg-emerald-100 text-emerald-700'}`}>
                        {d.cross_check?.overall || 'checked'}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </>
          ) : (
            <p className="text-xs text-slate-400 mt-3">Start an application to attach KYC documents.</p>
          )}
        </div>

        {applicationId && (
          <div className="text-[10px] text-slate-400 px-2">
            Application ref: <span className="font-mono text-slate-500">{applicationId}</span>
          </div>
        )}
      </aside>
    </div>
  )
}

function ShieldIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
      <path d="m9 12 2 2 4-4" />
    </svg>
  )
}
