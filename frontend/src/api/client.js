const API_BASE = import.meta.env.VITE_API_BASE_URL || '/api/v1'
const DEFAULT_TIMEOUT_MS = 30000

function errorMessage(payload, status) {
  if (typeof payload === 'string' && payload.trim()) return payload
  const detail = payload?.detail ?? payload?.message
  if (typeof detail === 'string' && detail.trim()) return detail
  if (detail && typeof detail === 'object' && typeof detail.message === 'string') return detail.message
  if (Array.isArray(detail) && detail.length) {
    return detail.map(item => item?.msg || item?.message || 'Invalid request').join('; ')
  }
  return `Request failed (${status})`
}

// Network failures and timeouts are safe to retry because user messages carry a
// client_message_id. Validation/4xx failures are not retried.
function retryableError(message, status) {
  const error = new Error(message)
  error.retryable = status == null || status === 408 || status === 429 || status >= 500
  if (status != null) error.status = status
  return error
}

async function request(path, options = {}, timeoutMs = DEFAULT_TIMEOUT_MS) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    let response
    try {
      response = await fetch(`${API_BASE}${path}`, { ...options, signal: controller.signal })
    } catch (error) {
      if (error.name === 'AbortError') {
        throw retryableError('The request took too long. It will be retried when the connection recovers.', null)
      }
      throw retryableError('Could not reach the server. Check that the backend is running and try again.', null)
    }

    const contentType = (response.headers.get('content-type') || '').toLowerCase()
    let payload = null
    if (response.status !== 204) {
      if (contentType.includes('application/json')) {
        try { payload = await response.json() } catch { payload = null }
      } else if (contentType.startsWith('audio/')) {
        payload = await response.blob()
      } else {
        payload = await response.text()
      }
    }

    if (!response.ok) throw retryableError(errorMessage(payload, response.status), response.status)
    return payload
  } finally {
    clearTimeout(timer)
  }
}

const json = body => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

function absoluteBase() {
  if (/^https?:\/\//i.test(API_BASE)) return API_BASE.replace(/\/$/, '')
  const path = API_BASE.startsWith('/') ? API_BASE : `/${API_BASE}`
  return `${window.location.origin}${path}`.replace(/\/$/, '')
}

export const api = {
  startApplication: (language, consent) => request('/applications', json({ language, consent })),
  sendMessage: (id, message, clientMessageId) =>
    request(`/applications/${id}/messages`, json({ message, client_message_id: clientMessageId || null })),
  listApplications: () => request('/applications'),
  getApplication: (id) => request(`/applications/${id}`),
  getAssessment: (id) => request(`/applications/${id}/assessment`),
  decide: (id, decision, note) => request(`/applications/${id}/decision`, json({ decision, note: note || null })),
  reviewField: (id, field, status, note) =>
    request(`/applications/${id}/fields/${encodeURIComponent(field)}/review`, json({ status, note: note || null })),
  followUp: (id, field, send) => request(`/applications/${id}/follow-up`, json({ field, send: Boolean(send) })),

  listDocuments: (id) => request(`/applications/${id}/documents`),
  uploadDocument: (id, { docType, text, file }) => {
    const form = new FormData()
    form.append('doc_type', docType)
    if (text) form.append('text', text)
    if (file) form.append('file', file)
    return request(`/applications/${id}/documents`, { method: 'POST', body: form }, 120000)
  },
  deleteDocument: (id, documentId) =>
    request(`/applications/${id}/documents/${documentId}`, { method: 'DELETE' }),

  listAudio: (id) => request(`/applications/${id}/audio`),
  uploadAudio: (id, file, messageId) => {
    const form = new FormData()
    form.append('file', file)
    if (messageId != null) form.append('message_id', String(messageId))
    return request(`/applications/${id}/audio`, { method: 'POST', body: form }, 120000)
  },
  audioUrl: (id, audioId) => `${API_BASE}/applications/${id}/audio/${audioId}`,
  initAudio: (id, { filename, contentType, messageId }) => {
    const form = new FormData()
    form.append('filename', filename || 'recording.wav')
    form.append('content_type', contentType || 'application/octet-stream')
    if (messageId != null) form.append('message_id', String(messageId))
    return request(`/applications/${id}/audio/init`, { method: 'POST', body: form })
  },
  uploadAudioChunk: (id, uploadId, offset, blob) => {
    const form = new FormData()
    form.append('offset', String(offset))
    form.append('file', blob, 'chunk.bin')
    return request(`/applications/${id}/audio/${uploadId}/chunk`, { method: 'POST', body: form }, 120000)
  },
  completeAudio: (id, uploadId) =>
    request(`/applications/${id}/audio/${uploadId}/complete`, { method: 'POST' }),

  transcribe: (file, language) => {
    const form = new FormData()
    form.append('file', file)
    form.append('language', language)
    return request('/voice/transcribe', { method: 'POST', body: form }, 120000)
  },
  synthesize: (text, language) =>
    request('/voice/synthesize', json({ text, language }), 60000),
  voiceStreamUrl: (language) =>
    `${absoluteBase().replace(/^http/i, 'ws')}/voice/stream?language=${encodeURIComponent(language)}`,
  capabilities: () => request('/capabilities'),
  metrics: () => request('/metrics'),
}

export { API_BASE }
