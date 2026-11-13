import React, { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import { Send, Mic, Square, RotateCcw, CheckCircle2, Loader2, Volume2, AlertTriangle, Upload, FileText } from 'lucide-react'

// Prisma's batch endpoint accepts clips up to 30 seconds. The live WebSocket
// flushes short windows, but the batch fallback still truncates at 29 seconds.
const MAX_RECORDING_SECONDS = 29
const TARGET_RATE = 16000
const OUTBOX_KEY = 'voicekyc.outbox.v1'
const APPLICATION_KEY = 'voicekyc.application.v1'
const REQUIRED_FIELDS = [
  'full_name', 'father_name', 'date_of_birth', 'address',
  'occupation', 'monthly_income', 'loan_purpose', 'phone',
]

const STATUS_LABEL = {
  in_progress: 'Collecting information',
  pending_review: 'Pending human review',
  request_info: 'Agent requested more information',
  approve: 'Reviewed: approved',
  reject: 'Reviewed: rejected',
}

function readOutbox() {
  try {
    const parsed = JSON.parse(localStorage.getItem(OUTBOX_KEY) || '[]')
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

function writeOutbox(items) {
  try { localStorage.setItem(OUTBOX_KEY, JSON.stringify(items)) } catch { /* storage may be unavailable */ }
}

function makeClientId() {
  if (window.crypto?.randomUUID) return window.crypto.randomUUID()
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`
}

// Average buckets down to 16 kHz. Prisma expects a normalised mono signal.
function downsampleBuffer(buffer, inputRate, targetRate) {
  if (inputRate <= targetRate) return buffer
  const ratio = inputRate / targetRate
  const length = Math.floor(buffer.length / ratio)
  const output = new Float32Array(length)
  for (let i = 0; i < length; i += 1) {
    const start = Math.floor(i * ratio)
    const end = Math.min(buffer.length, Math.floor((i + 1) * ratio))
    let sum = 0
    for (let j = start; j < end; j += 1) sum += buffer[j]
    output[i] = sum / Math.max(1, end - start)
  }
  return output
}

function floatTo16BitPCM(samples) {
  const output = new Int16Array(samples.length)
  for (let i = 0; i < samples.length; i += 1) {
    const sample = Math.max(-1, Math.min(1, samples[i]))
    output[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff
  }
  return output
}

function pcmToWav(chunks, sampleRate) {
  const total = chunks.reduce((sum, chunk) => sum + chunk.byteLength, 0)
  const buffer = new ArrayBuffer(44 + total)
  const view = new DataView(buffer)
  const writeAscii = (offset, text) => {
    for (let i = 0; i < text.length; i += 1) view.setUint8(offset + i, text.charCodeAt(i))
  }
  writeAscii(0, 'RIFF')
  view.setUint32(4, 36 + total, true)
  writeAscii(8, 'WAVE')
  writeAscii(12, 'fmt ')
  view.setUint32(16, 16, true)
  view.setUint16(20, 1, true)
  view.setUint16(22, 1, true)
  view.setUint32(24, sampleRate, true)
  view.setUint32(28, sampleRate * 2, true)
  view.setUint16(32, 2, true)
  view.setUint16(34, 16, true)
  writeAscii(36, 'data')
  view.setUint32(40, total, true)
  let offset = 44
  chunks.forEach(chunk => {
    const bytes = new Uint8Array(chunk.buffer, chunk.byteOffset, chunk.byteLength)
    new Uint8Array(buffer, offset, bytes.byteLength).set(bytes)
    offset += bytes.byteLength
  })
  return new Blob([buffer], { type: 'audio/wav' })
}

// Resolve when the stream sends `done`, or with the partial text already
// received if the socket closes, errors, or the provider stalls.
function waitForStreamDone(ws, fallbackText) {
  return new Promise(resolve => {
    let settled = false
    let timer = null

    function cleanup() {
      clearTimeout(timer)
      ws.removeEventListener('message', onMessage)
      ws.removeEventListener('close', onClose)
      ws.removeEventListener('error', onClose)
    }

    function finish(text) {
      if (settled) return
      settled = true
      cleanup()
      resolve((text || '').trim())
    }

    function onMessage(event) {
      let payload
      try { payload = JSON.parse(event.data) } catch { return }
      if (payload.type === 'done') finish(payload.text || fallbackText)
      else if (payload.type === 'error') finish(fallbackText)
    }

    function onClose() { finish(fallbackText) }

    ws.addEventListener('message', onMessage)
    ws.addEventListener('close', onClose)
    ws.addEventListener('error', onClose)
    timer = setTimeout(() => finish(fallbackText), 6000)
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
  const [consent, setConsent] = useState(false)
  const [error, setError] = useState('')
  const [recording, setRecording] = useState(false)
  const [voiceBusy, setVoiceBusy] = useState(false)
  const [partialText, setPartialText] = useState('')
  const [outboxCount, setOutboxCount] = useState(0)
  const [docType, setDocType] = useState('pan')
  const [docText, setDocText] = useState('')
  const [docNote, setDocNote] = useState('')
  const [docBusy, setDocBusy] = useState(false)

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

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages, loading])

  const refreshApplication = useCallback(async id => {
    const target = id || applicationIdRef.current
    if (!target) return null
    try {
      const [detail, docs] = await Promise.all([
        api.getApplication(target),
        api.listDocuments(target).catch(() => []),
      ])
      if (disposedRef.current) return null
      setMessages((detail.conversation || []).map(message => ({
        role: message.role,
        content: message.content,
        channel: message.channel,
        audioId: message.audio_id,
      })))
      const fields = detail.extracted || {}
      setExtracted(fields)
      setStatus(detail.status)
      setProgress(Math.round(
        REQUIRED_FIELDS.filter(field => fields[field]?.value).length / REQUIRED_FIELDS.length * 100,
      ))
      setIsComplete(['pending_review', 'approve', 'reject'].includes(detail.status))
      setDocuments(docs || [])
      return detail
    } catch (err) {
      if (!disposedRef.current && !err.retryable) setError(err.message)
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

  // Resume a stored application after a reload, retry queued answers, and poll
  // for agent follow-ups. Local storage is device-local; it is not identity data.
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
    const timer = setInterval(() => {
      if (navigator.onLine) flushRef.current()
    }, 15000)
    return () => {
      window.removeEventListener('online', onOnline)
      clearInterval(timer)
    }
  }, [refreshApplication])

  useEffect(() => {
    if (!applicationId) return undefined
    const timer = setInterval(() => {
      if (!loadingRef.current && !recordingActiveRef.current && !voiceBusy) {
        refreshApplication(applicationId)
      }
    }, 8000)
    return () => clearInterval(timer)
  }, [applicationId, voiceBusy, refreshApplication])

  const stopCapture = useCallback(() => {
    clearTimeout(stopTimerRef.current)
    recordingActiveRef.current = false
    try { processorRef.current?.disconnect() } catch { /* already disconnected */ }
    try { sourceRef.current?.disconnect() } catch { /* already disconnected */ }
    processorRef.current = null
    sourceRef.current = null
    try { streamRef.current?.getTracks().forEach(track => track.stop()) } catch { /* already stopped */ }
    streamRef.current = null
    const ctx = audioCtxRef.current
    audioCtxRef.current = null
    if (ctx) ctx.close().catch(() => {})
  }, [])

  const closeSocket = useCallback(() => {
    const socket = wsRef.current
    wsRef.current = null
    if (socket) {
      try { socket.close() } catch { /* already closed */ }
    }
    pendingSendRef.current = []
  }, [])

  // Release the microphone, stream, and audio URL if the user navigates away.
  useEffect(() => {
    return () => {
      disposedRef.current = true
      recordingIdRef.current += 1
      stopCapture()
      closeSocket()
      audioRef.current?.pause()
      audioRef.current = null
      if (ttsUrlRef.current) { URL.revokeObjectURL(ttsUrlRef.current); ttsUrlRef.current = null }
    }
  }, [closeSocket, stopCapture])

  const startConversation = async () => {
    if (!consent) { setError('Please obtain applicant consent before starting.'); return }
    recordingIdRef.current += 1
    if (recordingActiveRef.current) stopCapture()
    closeSocket()
    setLoading(true)
    loadingRef.current = true
    setError('')
    setMessages([])
    setExtracted({})
    setDocuments([])
    setProgress(0)
    setIsComplete(false)
    setStatus(null)
    setApplicationId(null)
    applicationIdRef.current = null
    setInput('')
    setPartialText('')
    try {
      const data = await api.startApplication(language, true)
      applicationIdRef.current = data.application_id
      setApplicationId(data.application_id)
      setStatus(data.status)
      setMessages([{ role: 'assistant', content: data.reply }])
      try { localStorage.setItem(APPLICATION_KEY, data.application_id) } catch { /* storage may be unavailable */ }
    } catch (err) {
      setError(err.message)
    } finally {
      loadingRef.current = false
      setLoading(false)
    }
  }

  const queueMessage = useCallback(message => {
    const target = applicationIdRef.current
    if (!target) return
    const entry = {
      applicationId: target,
      message,
      clientMessageId: makeClientId(),
      queuedAt: Date.now(),
    }
    const items = [...readOutbox(), entry]
    writeOutbox(items)
    setOutboxCount(items.length)
  }, [])

  const sendMessage = async (text = input) => {
    const userMsg = (text || '').trim()
    if (!userMsg || loading || isComplete || !applicationId || recording || voiceBusy) return
    setInput('')
    setError('')

    // Keep ordering strict: while any answer for this application is queued,
    // new answers join the same queue instead of overtaking it.
    if (readOutbox().some(item => item.applicationId === applicationId)) {
      setMessages(prev => [...prev, { role: 'user', content: userMsg }])
      queueMessage(userMsg)
      setError('Saved on this device. It will be sent automatically when the connection returns.')
      return
    }

    const clientMessageId = makeClientId()
    const optimistic = { role: 'user', content: userMsg }
    setMessages(prev => [...prev, optimistic])
    setLoading(true)
    loadingRef.current = true
    try {
      await api.sendMessage(applicationId, userMsg, clientMessageId)
      await refreshApplication(applicationId)
    } catch (err) {
      if (err.retryable) {
        queueMessage(userMsg)
        setError('You appear to be offline. Your answer is saved on this device and will be sent automatically.')
      } else {
        setError(err.message)
        setMessages(prev => prev.slice(0, -1))
        setInput(current => current || userMsg)
      }
    } finally {
      loadingRef.current = false
      setLoading(false)
    }
  }

  const startRecording = async () => {
    setError('')
    setPartialText('')
    if (!applicationId) { setError('Start an application before recording.'); return }
    if (recordingActiveRef.current || voiceBusy) return
    const AudioCtx = window.AudioContext || window.webkitAudioContext
    if (!navigator.mediaDevices?.getUserMedia || !AudioCtx) {
      setError('Audio recording is not supported in this browser.')
      return
    }
    try {
      const mic = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      })
      if (disposedRef.current) {
        mic.getTracks().forEach(track => track.stop())
        return
      }
      const recordingId = ++recordingIdRef.current
      const ctx = new AudioCtx()
      await ctx.resume().catch(() => {})
      const source = ctx.createMediaStreamSource(mic)
      const processor = ctx.createScriptProcessor(4096, 1, 1)
      pcmRef.current = []
      pendingSendRef.current = []
      partialRef.current = ''
      recordingActiveRef.current = true
      streamRef.current = mic
      audioCtxRef.current = ctx
      processorRef.current = processor
      sourceRef.current = source

      // Best-effort live transcription. If the socket cannot connect, the
      // recording is still kept for the batch fallback at stop.
      try {
        const socket = new WebSocket(api.voiceStreamUrl(language))
        socket.binaryType = 'arraybuffer'
        wsRef.current = socket
        socket.onopen = () => {
          if (wsRef.current !== socket || recordingId !== recordingIdRef.current) return
          try { socket.send(JSON.stringify({ type: 'start', sampleRate: TARGET_RATE })) } catch { /* ignore */ }
          const queued = pendingSendRef.current
          pendingSendRef.current = []
          queued.forEach(chunk => {
            try { socket.send(chunk) } catch { /* ignore a closed socket */ }
          })
        }
        socket.onmessage = event => {
          if (wsRef.current !== socket) return
          let payload
          try { payload = JSON.parse(event.data) } catch { return }
          if (payload.type === 'partial' || payload.type === 'final') {
            partialRef.current = payload.text_so_far || payload.text || partialRef.current
            setPartialText(partialRef.current)
          }
        }
        socket.onerror = () => { /* batch fallback at stop */ }
        socket.onclose = () => { /* batch fallback at stop */ }
      } catch {
        wsRef.current = null
      }

      processor.onaudioprocess = event => {
        if (!recordingActiveRef.current || recordingId !== recordingIdRef.current) return
        const channel = event.inputBuffer.getChannelData(0)
        const resampled = downsampleBuffer(channel, ctx.sampleRate, TARGET_RATE)
        const pcm = floatTo16BitPCM(resampled)
        pcmRef.current.push(pcm)
        const bytes = new Uint8Array(pcm.buffer.slice(0))
        const socket = wsRef.current
        if (socket && socket.readyState === WebSocket.OPEN) {
          try { socket.send(bytes) } catch { /* batch fallback keeps the PCM */ }
        } else {
          pendingSendRef.current.push(bytes)
        }
      }
      source.connect(processor)
      processor.connect(ctx.destination)
      setRecording(true)
      stopTimerRef.current = setTimeout(() => { stopRecording() }, MAX_RECORDING_SECONDS * 1000)
    } catch (err) {
      stopCapture()
      closeSocket()
      if (!disposedRef.current) setError(err.message || 'Microphone permission was denied.')
    }
  }

  const stopRecording = async () => {
    if (!recordingActiveRef.current) return
    clearTimeout(stopTimerRef.current)
    const recordingId = recordingIdRef.current
    const pcmChunks = pcmRef.current
    stopCapture()
    setRecording(false)

    const socket = wsRef.current
    if (socket && socket.readyState === WebSocket.OPEN) {
      setVoiceBusy(true)
      const text = await waitForStreamDone(socket, partialRef.current)
      wsRef.current = null
      try { socket.close() } catch { /* already closed */ }
      if (disposedRef.current || recordingId !== recordingIdRef.current) return
      if (text) {
        setInput(current => (current ? `${current} ${text}` : text))
        setPartialText('')
        partialRef.current = ''
        setVoiceBusy(false)
        pcmRef.current = []
        return
      }
      setVoiceBusy(false)
    } else {
      closeSocket()
    }

    const total = pcmChunks.reduce((sum, chunk) => sum + chunk.byteLength, 0)
    pcmRef.current = []
    if (!total) {
      setPartialText('')
      setError('No speech was detected. Please try again or type your response.')
      return
    }
    if (disposedRef.current || recordingId !== recordingIdRef.current) return
    setVoiceBusy(true)
    try {
      const wav = pcmToWav(pcmChunks, TARGET_RATE)
      const result = await api.transcribe(new File([wav], 'voice-response.wav', { type: 'audio/wav' }), language)
      if (disposedRef.current) return
      if (result?.text) setInput(current => (current ? `${current} ${result.text}` : result.text))
      else setError('No speech was detected. Please try again or type your response.')
    } catch (err) {
      if (!disposedRef.current) {
        setError(err.message || 'Could not process the recording. Please type your response instead.')
      }
    } finally {
      if (!disposedRef.current) {
        setVoiceBusy(false)
        setPartialText('')
        partialRef.current = ''
      }
    }
  }

  const speak = async text => {
    setError('')
    setVoiceBusy(true)
    try {
      audioRef.current?.pause()
      if (ttsUrlRef.current) { URL.revokeObjectURL(ttsUrlRef.current); ttsUrlRef.current = null }
      const blob = await api.synthesize(text, language)
      if (!(blob instanceof Blob)) throw new Error('The voice service returned an invalid response.')
      const url = URL.createObjectURL(blob)
      ttsUrlRef.current = url
      const audio = new Audio(url)
      audioRef.current = audio
      const release = () => {
        URL.revokeObjectURL(url)
        if (ttsUrlRef.current === url) ttsUrlRef.current = null
        if (audioRef.current === audio) audioRef.current = null
      }
      audio.onended = release
      audio.onerror = release
      try { await audio.play() } catch (err) { release(); throw err }
    } catch (err) {
      if (!disposedRef.current) setError(err.message || 'Could not play the voice response.')
    } finally {
      setVoiceBusy(false)
    }
  }

  const uploadDocument = async () => {
    if (!applicationId) return
    const file = docFileRef.current?.files?.[0] || null
    if (!docText.trim() && !file) {
      setDocNote('Enter the document text or choose a text/image file.')
      return
    }
    setDocBusy(true)
    setDocNote('')
    try {
      const document = await api.uploadDocument(applicationId, {
        docType,
        text: docText.trim() || null,
        file,
      })
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

  const handleKeyDown = event => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      sendMessage()
    }
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
      <section className="lg:col-span-2 bg-white rounded-2xl shadow-sm border border-slate-200 flex flex-col min-h-[68vh]">
        <header className="px-5 py-4 border-b border-slate-100 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="font-semibold text-slate-900">Applicant onboarding</h2>
            <p className="text-xs text-slate-500">Conversational information collection · Hinglish / Marathi</p>
          </div>
          <div className="flex items-center gap-2">
            <select value={language} onChange={event => setLanguage(event.target.value)} disabled={!!applicationId} className="border rounded-lg px-3 py-2 text-sm">
              <option value="hinglish">Hinglish</option>
              <option value="marathi">मराठी</option>
            </select>
            <button onClick={startConversation} disabled={loading} className="flex items-center gap-2 px-3 py-2 bg-primary-600 text-white rounded-lg text-sm disabled:opacity-50">
              <RotateCcw size={15} /> New application
            </button>
          </div>
        </header>

        {!applicationId && (
          <div className="mx-5 mt-4 rounded-xl bg-amber-50 border border-amber-200 p-3 text-sm text-amber-900">
            <label className="flex gap-2 items-start">
              <input type="checkbox" checked={consent} onChange={event => setConsent(event.target.checked)} className="mt-1" />
              <span>
                I consent to the collection and processing of the information I provide for this demonstration.
                I understand this is not identity verification or a loan approval.
              </span>
            </label>
          </div>
        )}

        {applicationId && (
          <div className="px-5 py-3 bg-slate-50 border-b">
            <div className="flex justify-between text-xs mb-1">
              <span>{STATUS_LABEL[status] || 'Information collection progress'}</span>
              <span>{progress}%</span>
            </div>
            <div className="h-1.5 bg-slate-200 rounded-full">
              <div className="h-1.5 bg-primary-500 rounded-full transition-all" style={{ width: `${progress}%` }} />
            </div>
          </div>
        )}

        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {!messages.length && (
            <div className="h-full min-h-64 flex flex-col items-center justify-center text-center text-slate-400">
              <Mic size={38} className="mb-3 text-primary-500" />
              <p className="font-medium text-slate-700">Start a new onboarding session</p>
              <p className="text-sm mt-1">Applicant consent is required before collecting information.</p>
            </div>
          )}
          {messages.map((message, index) => (
            <div key={`${index}-${message.role}`} className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}>
              <div className={`max-w-[85%] px-4 py-3 rounded-2xl text-sm whitespace-pre-wrap ${message.role === 'user' ? 'bg-primary-600 text-white rounded-br-md' : 'bg-slate-100 text-slate-800 rounded-bl-md'}`}>
                <div>{message.content}</div>
                {message.channel === 'follow_up' && (
                  <div className="text-[10px] mt-1 text-amber-700">Follow-up from review agent</div>
                )}
                {message.role === 'assistant' && (
                  <button onClick={() => speak(message.content)} disabled={voiceBusy} className="mt-2 text-xs flex items-center gap-1 text-primary-700 disabled:opacity-40">
                    <Volume2 size={13} /> Play voice
                  </button>
                )}
              </div>
            </div>
          ))}
          {loading && (
            <div className="flex gap-2 items-center text-sm text-slate-500">
              <Loader2 className="animate-spin" size={16} /> Processing response…
            </div>
          )}
          {recording && (
            <div className="flex gap-2 items-center text-sm text-primary-700">
              <span className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
              Listening… {partialText ? <span className="text-slate-600">{partialText}</span> : <span className="text-slate-400">speak now</span>}
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        {outboxCount > 0 && (
          <div className="mx-4 mb-2 p-2.5 rounded-lg bg-blue-50 text-blue-800 text-xs flex gap-2">
            <AlertTriangle size={14} className="shrink-0 mt-0.5" />
            {outboxCount} answer{outboxCount === 1 ? '' : 's'} saved on this device and waiting to sync.
          </div>
        )}
        {error && (
          <div className="mx-4 mb-3 p-3 rounded-lg bg-red-50 text-red-700 text-sm flex gap-2">
            <AlertTriangle size={16} className="shrink-0 mt-0.5" />
            {error}
          </div>
        )}

        <footer className="p-4 border-t border-slate-100">
          {isComplete ? (
            <div className="flex items-center justify-center gap-2 text-green-700 bg-green-50 py-3 rounded-xl text-sm">
              <CheckCircle2 size={18} /> Information collected. Pending human review.
            </div>
          ) : (
            <div className="flex gap-2">
              <button
                onClick={recording ? stopRecording : startRecording}
                disabled={!applicationId || loading || voiceBusy}
                title={recording ? 'Stop recording' : 'Record response'}
                className={`px-3 rounded-xl border ${recording ? 'bg-red-50 text-red-600 border-red-200' : 'text-slate-600 border-slate-200'} disabled:opacity-40`}
              >
                {recording ? <Square size={18} /> : <Mic size={18} />}
              </button>
              <input
                value={input}
                onChange={event => setInput(event.target.value)}
                onKeyDown={handleKeyDown}
                disabled={!applicationId || loading || recording}
                placeholder={applicationId ? 'Type a response or record your voice…' : 'Start a new application first'}
                className="flex-1 min-w-0 px-4 py-2.5 border border-slate-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-primary-500 disabled:bg-slate-50"
              />
              <button onClick={() => sendMessage()} disabled={!input.trim() || loading || !applicationId} className="px-4 bg-primary-600 text-white rounded-xl disabled:bg-slate-300">
                <Send size={17} />
              </button>
            </div>
          )}
          {voiceBusy && <p className="text-xs text-slate-500 mt-2">Communicating with the configured voice service…</p>}
          {!isComplete && <p className="text-[11px] text-slate-400 mt-2">Live transcription streams short audio windows. A failed stream falls back to one batch recording, and typing always works.</p>}
        </footer>
      </section>

      <aside className="bg-white rounded-2xl shadow-sm border border-slate-200 p-5 min-h-[68vh]">
        <h3 className="font-semibold text-slate-900">Applicant information</h3>
        <p className="text-xs text-slate-500 mt-1 mb-4">Applicant-provided information · Agent verification required</p>

        {Object.keys(extracted).length ? (
          <div className="space-y-3">
            {Object.entries(extracted).map(([key, field]) => (
              <div key={key} className="bg-slate-50 rounded-lg p-3">
                <div className="text-xs uppercase tracking-wide text-slate-500">{key.replace(/_/g, ' ')}</div>
                <div className="text-sm font-medium text-slate-900 mt-1 break-words">{field.value || '—'}</div>
                <div className="text-[11px] text-amber-700 mt-1">
                  {field.confidence == null ? 'Not independently assessed · Verify' : `Confidence: ${Math.round(field.confidence * 100)}%`}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-slate-400 mt-10 text-center">Collected information will appear here.</p>
        )}

        {applicationId && (
          <div className="mt-6 pt-4 border-t">
            <h4 className="text-sm font-semibold flex items-center gap-2"><FileText size={15} /> Identity document (optional)</h4>
            <p className="text-[11px] text-slate-500 mt-1">
              PAN/Aadhaar is cross-checked against what you said. Consistent fields are not identity verification.
            </p>
            <div className="flex gap-2 mt-3">
              <select value={docType} onChange={event => setDocType(event.target.value)} className="border rounded-lg px-2 py-1.5 text-xs">
                <option value="pan">PAN</option>
                <option value="aadhaar">Aadhaar</option>
              </select>
              <input ref={docFileRef} type="file" accept="image/*,text/plain" className="text-[11px] min-w-0 flex-1" />
            </div>
            <textarea
              value={docText}
              onChange={event => setDocText(event.target.value)}
              rows={3}
              placeholder="Or paste document text (name, DOB, document number)"
              className="w-full mt-2 p-2 border rounded-lg text-xs resize-none"
            />
            <button onClick={uploadDocument} disabled={docBusy} className="mt-2 w-full flex items-center justify-center gap-2 py-2 bg-slate-800 text-white rounded-lg text-xs disabled:opacity-50">
              {docBusy ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />} Upload and cross-check
            </button>
            {docNote && <p className="text-[11px] text-slate-600 mt-2">{docNote}</p>}
            {documents.length > 0 && (
              <div className="mt-3 space-y-2">
                {documents.map(document => (
                  <div key={document.id} className="bg-slate-50 rounded-lg p-2 text-[11px]">
                    <div className="flex justify-between gap-2">
                      <span className="font-medium uppercase">{document.doc_type}</span>
                      <span className={document.cross_check?.review_flag ? 'text-red-600' : 'text-slate-500'}>
                        {document.cross_check?.overall || 'not checked'}
                      </span>
                    </div>
                    <div className="text-slate-500 mt-0.5">
                      {document.extracted_fields?.number_masked || 'Number not extracted'} · {document.extraction_method}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {applicationId && (
          <div className="mt-6 pt-4 border-t text-xs">
            <div className="text-slate-500">Application reference</div>
            <div className="font-mono break-all mt-1">{applicationId}</div>
          </div>
        )}
      </aside>
    </div>
  )
}
