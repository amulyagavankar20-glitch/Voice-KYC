import React, { useEffect, useState } from 'react'
import { api, setToken, setStoredUser, clearAuth } from '../api/client'
import {
  LogIn, UserPlus, Upload, FileText, Trash2, Loader2, ShieldCheck, AlertTriangle,
  Mic, Sparkles, ChevronRight,
} from 'lucide-react'

export default function AuthScreen({ onAuthed, embedded = false, user: initialUser = null, onLogout }) {
  const [mode, setMode] = useState('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [fullName, setFullName] = useState('')
  const [email, setEmail] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [user, setUser] = useState(initialUser)
  const [docs, setDocs] = useState([])
  const [docType, setDocType] = useState('pan')
  const [docBusy, setDocBusy] = useState(false)
  const [docNote, setDocNote] = useState('')

  const loadMeAndDocs = async () => {
    try {
      const me = await api.me()
      setUser(me)
      setStoredUser(me)
      const d = await api.listUserDocuments().catch(() => [])
      setDocs(d)
      return me
    } catch (err) {
      clearAuth()
      setUser(null)
      throw err
    }
  }

  useEffect(() => {
    if (embedded && initialUser) {
      loadMeAndDocs().catch(() => {})
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [embedded])

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      let res
      if (mode === 'register') {
        if (!fullName.trim()) throw new Error('Please enter your full name.')
        res = await api.register(username.trim(), password, fullName.trim(), email.trim() || null)
      } else {
        res = await api.login(username.trim(), password)
      }
      setToken(res.access_token)
      setStoredUser(res.user)
      setUser(res.user)
      const d = await api.listUserDocuments().catch(() => [])
      setDocs(d)
      setUsername(''); setPassword(''); setFullName(''); setEmail('')
      onAuthed?.(res.user)
    } catch (err) {
      setError(err.message || 'Authentication failed.')
    } finally {
      setBusy(false)
    }
  }

  const uploadDoc = async () => {
    const input = document.getElementById('auth-doc-file')
    const file = input?.files?.[0]
    if (!file) { setDocNote('Choose a file to upload (optional).'); return }
    setDocBusy(true)
    setDocNote('')
    try {
      const d = await api.uploadUserDocument(docType, file)
      setDocs(prev => [d, ...prev])
      setDocNote(`${docType.toUpperCase()} stored · status: ${d.verification_status} (upload is not verification).`)
      if (input) input.value = ''
    } catch (err) {
      setDocNote(err.message)
    } finally {
      setDocBusy(false)
    }
  }

  const removeDoc = async (id) => {
    try {
      await api.deleteUserDocument(id)
      setDocs(prev => prev.filter(d => d.id !== id))
    } catch (err) {
      setDocNote(err.message)
    }
  }

  // Embedded account view (logged in)
  if (embedded && user) {
    return (
      <div className="max-w-3xl mx-auto space-y-4">
        <div className="bg-white/80 backdrop-blur rounded-2xl border border-slate-200 shadow-sm p-6">
          <div className="flex items-start justify-between gap-4">
            <div className="flex items-center gap-3">
              <div className="w-11 h-11 rounded-xl bg-gradient-to-br from-primary-500 to-indigo-600 flex items-center justify-center text-white shadow">
                <ShieldCheck size={20} />
              </div>
              <div>
                <h2 className="font-semibold text-lg text-slate-900">{user.full_name}</h2>
                <p className="text-xs text-slate-500">
                  User ID: <span className="font-mono text-slate-700">{user.username}</span>
                  {user.email ? ` · ${user.email}` : ''} · {user.is_agent ? 'Review Agent' : 'Applicant'}
                </p>
              </div>
            </div>
            {onLogout && (
              <button onClick={onLogout} className="text-xs px-3 py-1.5 rounded-lg bg-slate-100 hover:bg-slate-200">Sign out</button>
            )}
          </div>
        </div>

        <div className="bg-white/80 backdrop-blur rounded-2xl border border-slate-200 shadow-sm p-6">
          <h3 className="text-sm font-semibold mb-1 flex items-center gap-2"><FileText size={15} /> Identity documents <span className="text-[10px] font-normal text-slate-400">(optional)</span></h3>
          <p className="text-[11px] text-slate-500 mb-4">
            You can upload identity documents for review. Uploads are stored securely and reviewed by an authorized agent — uploading is <strong>not</strong> identity verification. You do not need to upload documents to use the app.
          </p>
          <div className="flex flex-wrap gap-2 items-center mb-3">
            <select value={docType} onChange={e => setDocType(e.target.value)} className="border border-slate-200 rounded-lg px-3 py-2 text-xs bg-white">
              <option value="pan">PAN</option>
              <option value="aadhaar">Aadhaar</option>
              <option value="passport">Passport</option>
              <option value="voter_id">Voter ID</option>
              <option value="other">Other</option>
            </select>
            <input id="auth-doc-file" type="file" className="text-xs flex-1 min-w-[180px]" />
            <button onClick={uploadDoc} disabled={docBusy}
              className="flex items-center gap-2 px-4 py-2 bg-slate-900 text-white rounded-lg text-xs disabled:opacity-50">
              {docBusy ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />} Upload
            </button>
          </div>
          {docNote && <p className="text-[11px] text-slate-600 mb-3">{docNote}</p>}
          <div className="space-y-2">
            {docs.length === 0 && (
              <div className="text-xs text-slate-400 bg-slate-50 rounded-xl p-4 border border-dashed border-slate-200">
                No documents uploaded yet. This is optional.
              </div>
            )}
            {docs.map(d => (
              <div key={d.id} className="flex items-center justify-between bg-slate-50 rounded-xl p-3 text-xs border border-slate-100">
                <div>
                  <span className="font-semibold uppercase text-slate-800">{d.doc_type}</span>
                  <span className="text-slate-500"> · {d.filename || 'file'} · {(d.size_bytes / 1024).toFixed(1)} KB</span>
                  <div className="text-[10px] text-slate-500 mt-0.5">Status: <span className="font-medium">{d.verification_status}</span></div>
                </div>
                <button onClick={() => removeDoc(d.id)} className="p-2 hover:bg-red-50 text-red-600 rounded-lg" title="Delete">
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
          </div>
        </div>
      </div>
    )
  }

  // Standalone auth gate
  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-950 via-slate-900 to-primary-950 flex items-center justify-center p-4">
      <div className="w-full max-w-5xl grid grid-cols-1 lg:grid-cols-2 gap-6 items-center">
        <div className="text-white space-y-6 px-2 lg:px-6">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-white/10 border border-white/10 text-xs text-primary-200">
            <Sparkles size={12} /> Microfinance · Voice-first KYC
          </div>
          <div className="flex items-center gap-3">
            <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-primary-400 to-indigo-500 flex items-center justify-center shadow-lg shadow-primary-500/30">
              <Mic className="w-6 h-6 text-white" />
            </div>
            <div>
              <h1 className="text-3xl font-bold tracking-tight">VoiceKYC</h1>
              <p className="text-sm text-slate-300">Collect KYC details by voice or text, reviewed by humans.</p>
            </div>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm">
            {[
              { t: 'Speak or type', d: 'Hinglish & Marathi intake with clear recording feedback.' },
              { t: 'Microfinance-ready', d: 'Step-by-step KYC fields and document cross-checks.' },
              { t: 'Human review', d: 'No auto-approval. Agents verify every application.' },
              { t: 'Private by account', d: 'Each user sees only their own applications.' },
            ].map(x => (
              <div key={x.t} className="rounded-xl bg-white/5 border border-white/10 p-3">
                <div className="font-medium text-white">{x.t}</div>
                <div className="text-xs text-slate-300 mt-1">{x.d}</div>
              </div>
            ))}
          </div>
          <p className="text-[11px] text-slate-400">
            Demo accounts: <code className="bg-white/10 px-1.5 py-0.5 rounded">ramesh.kumar</code> … <code className="bg-white/10 px-1.5 py-0.5 rounded">priya.desai</code> or <code className="bg-white/10 px-1.5 py-0.5 rounded">agent.review</code> · password <code className="bg-white/10 px-1.5 py-0.5 rounded">Demo@1234</code>
          </p>
        </div>

        <div className="bg-white/95 backdrop-blur rounded-3xl shadow-2xl border border-white/60 p-6 sm:p-8">
          <div className="flex items-center gap-2 mb-1">
            <ShieldCheck className="text-primary-600" size={20} />
            <h2 className="font-semibold text-xl text-slate-900">{mode === 'login' ? 'Sign in' : 'Create your account'}</h2>
          </div>
          <p className="text-xs text-slate-500 mb-5">
            {mode === 'login' ? 'Access your microfinance KYC applications.' : 'Register to start your KYC intake. Document upload is optional.'}
          </p>

          <form onSubmit={submit} className="space-y-3">
            {mode === 'register' && (
              <div>
                <label className="text-xs font-medium text-slate-700">Full name</label>
                <input value={fullName} onChange={e => setFullName(e.target.value)} placeholder="e.g. Ramesh Kumar" required
                  className="mt-1 w-full px-3.5 py-2.5 border border-slate-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-primary-500" />
              </div>
            )}
            <div>
              <label className="text-xs font-medium text-slate-700">User ID</label>
              <input value={username} onChange={e => setUsername(e.target.value)} placeholder="Choose a user ID" required autoComplete="username"
                className="mt-1 w-full px-3.5 py-2.5 border border-slate-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-primary-500" />
            </div>
            <div>
              <label className="text-xs font-medium text-slate-700">Password</label>
              <input value={password} onChange={e => setPassword(e.target.value)} type="password" placeholder="••••••••" required autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                className="mt-1 w-full px-3.5 py-2.5 border border-slate-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-primary-500" />
              {mode === 'register' && <p className="text-[10px] text-slate-400 mt-1">Min 8 characters, include a letter and a number.</p>}
            </div>
            {mode === 'register' && (
              <div>
                <label className="text-xs font-medium text-slate-700">Email <span className="text-slate-400 font-normal">(optional)</span></label>
                <input value={email} onChange={e => setEmail(e.target.value)} type="email" placeholder="you@example.com"
                  className="mt-1 w-full px-3.5 py-2.5 border border-slate-200 rounded-xl text-sm focus:outline-none focus:ring-2 focus:ring-primary-500" />
              </div>
            )}
            <button type="submit" disabled={busy}
              className="w-full mt-2 py-2.5 bg-gradient-to-r from-primary-600 to-indigo-600 text-white rounded-xl text-sm font-medium disabled:opacity-50 flex items-center justify-center gap-2 shadow-lg shadow-primary-500/20">
              {busy ? <Loader2 size={16} className="animate-spin" /> : (mode === 'login' ? <LogIn size={16} /> : <UserPlus size={16} />)}
              {mode === 'login' ? 'Sign in' : 'Create account'}
              {!busy && <ChevronRight size={14} />}
            </button>
          </form>

          {error && (
            <div className="mt-3 p-3 rounded-xl bg-red-50 text-red-700 text-xs flex gap-2 border border-red-100">
              <AlertTriangle size={14} className="shrink-0 mt-0.5" /> {error}
            </div>
          )}

          <div className="mt-5 pt-4 border-t border-slate-100 text-center">
            <button onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setError('') }}
              className="text-xs text-primary-700 hover:text-primary-800 font-medium">
              {mode === 'login' ? "New here? Create an account" : 'Already have an account? Sign in'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
