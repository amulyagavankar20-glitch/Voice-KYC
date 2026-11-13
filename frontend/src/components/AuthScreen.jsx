import React, { useEffect, useState } from 'react'
import { api } from '../api/client'
import { LogIn, UserPlus, Upload, FileText, Trash2, Loader2, ShieldCheck, AlertTriangle } from 'lucide-react'

const TOKEN_KEY = 'voicekyc.token.v1'

export function getToken() {
  try { return localStorage.getItem(TOKEN_KEY) } catch { return null }
}
export function setToken(t) {
  try { if (t) localStorage.setItem(TOKEN_KEY, t); else localStorage.removeItem(TOKEN_KEY) } catch { /* ignore */ }
}

export default function AuthScreen() {
  const [mode, setMode] = useState('login') // login | register
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [fullName, setFullName] = useState('')
  const [email, setEmail] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [user, setUser] = useState(null)
  const [docs, setDocs] = useState([])
  const [docType, setDocType] = useState('pan')
  const [docBusy, setDocBusy] = useState(false)
  const [docNote, setDocNote] = useState('')

  const token = getToken()

  const loadMe = async () => {
    const t = getToken()
    if (!t) { setUser(null); return }
    try {
      const me = await api.me()
      setUser(me)
      const d = await api.listUserDocuments()
      setDocs(d)
    } catch (err) {
      setToken(null)
      setUser(null)
      setError(err.message)
    }
  }

  useEffect(() => { if (token) loadMe() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      let res
      if (mode === 'register') {
        res = await api.register(username.trim(), password, fullName.trim(), email.trim() || null)
      } else {
        res = await api.login(username.trim(), password)
      }
      setToken(res.access_token)
      setUser(res.user)
      const d = await api.listUserDocuments()
      setDocs(d)
      setUsername(''); setPassword(''); setFullName(''); setEmail('')
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const logout = () => {
    setToken(null)
    setUser(null)
    setDocs([])
  }

  const uploadDoc = async () => {
    const input = document.getElementById('auth-doc-file')
    const file = input?.files?.[0]
    if (!file) { setDocNote('Choose a file first.'); return }
    setDocBusy(true)
    setDocNote('')
    try {
      const d = await api.uploadUserDocument(docType, file)
      setDocs(prev => [d, ...prev])
      setDocNote(`${docType.toUpperCase()} uploaded (${d.size_bytes} bytes). Verification status: ${d.verification_status}.`)
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

  if (!user) {
    return (
      <div className="max-w-md mx-auto mt-10 bg-white rounded-2xl shadow-sm border border-slate-200 p-6">
        <div className="flex items-center gap-2 mb-4">
          <ShieldCheck className="text-primary-600" size={22} />
          <h2 className="font-semibold text-lg">{mode === 'login' ? 'Sign in' : 'Create account'}</h2>
        </div>
        <p className="text-xs text-slate-500 mb-4">
          Demo accounts use password <code className="bg-slate-100 px-1 rounded">Demo@1234</code> (e.g. ramesh.kumar).
        </p>
        <form onSubmit={submit} className="space-y-3">
          {mode === 'register' && (
            <input value={fullName} onChange={e => setFullName(e.target.value)} placeholder="Full name" required
              className="w-full px-3 py-2 border rounded-lg text-sm" />
          )}
          <input value={username} onChange={e => setUsername(e.target.value)} placeholder="User ID" required
            className="w-full px-3 py-2 border rounded-lg text-sm" />
          <input value={password} onChange={e => setPassword(e.target.value)} type="password" placeholder="Password" required
            className="w-full px-3 py-2 border rounded-lg text-sm" />
          {mode === 'register' && (
            <input value={email} onChange={e => setEmail(e.target.value)} type="email" placeholder="Email (optional)"
              className="w-full px-3 py-2 border rounded-lg text-sm" />
          )}
          <button type="submit" disabled={busy}
            className="w-full py-2.5 bg-primary-600 text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center justify-center gap-2">
            {busy ? <Loader2 size={16} className="animate-spin" /> : (mode === 'login' ? <LogIn size={16} /> : <UserPlus size={16} />)}
            {mode === 'login' ? 'Sign in' : 'Create account'}
          </button>
        </form>
        {error && (
          <div className="mt-3 p-2.5 rounded-lg bg-red-50 text-red-700 text-xs flex gap-2">
            <AlertTriangle size={14} className="shrink-0 mt-0.5" /> {error}
          </div>
        )}
        <button onClick={() => { setMode(mode === 'login' ? 'register' : 'login'); setError('') }}
          className="mt-4 text-xs text-primary-700 hover:underline">
          {mode === 'login' ? "Don't have an account? Register" : 'Already registered? Sign in'}
        </button>
      </div>
    )
  }

  return (
    <div className="max-w-2xl mx-auto mt-6 bg-white rounded-2xl shadow-sm border border-slate-200 p-6">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <ShieldCheck className="text-primary-600" size={22} />
          <h2 className="font-semibold text-lg">Account: {user.full_name}</h2>
        </div>
        <button onClick={logout} className="text-xs px-3 py-1.5 rounded-lg bg-slate-100 hover:bg-slate-200">Sign out</button>
      </div>
      <p className="text-xs text-slate-500 mb-4">User ID: <span className="font-mono">{user.username}</span> · {user.email || 'no email'}</p>

      <h3 className="text-sm font-semibold mb-2 flex items-center gap-2"><FileText size={15} /> Verification documents</h3>
      <p className="text-[11px] text-slate-500 mb-3">
        Upload an identity document for review. Documents are stored; verification status is recorded by an authorized reviewer. Uploading is not verification.
      </p>
      <div className="flex flex-wrap gap-2 items-center mb-3">
        <select value={docType} onChange={e => setDocType(e.target.value)} className="border rounded-lg px-2 py-1.5 text-xs">
          <option value="pan">PAN</option>
          <option value="aadhaar">Aadhaar</option>
          <option value="passport">Passport</option>
          <option value="voter_id">Voter ID</option>
          <option value="other">Other</option>
        </select>
        <input id="auth-doc-file" type="file" className="text-xs" />
        <button onClick={uploadDoc} disabled={docBusy}
          className="flex items-center gap-2 px-3 py-1.5 bg-slate-800 text-white rounded-lg text-xs disabled:opacity-50">
          {docBusy ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />} Upload
        </button>
      </div>
      {docNote && <p className="text-[11px] text-slate-600 mb-3">{docNote}</p>}
      <div className="space-y-2">
        {docs.length === 0 && <p className="text-xs text-slate-400 bg-slate-50 rounded-lg p-3">No documents uploaded.</p>}
        {docs.map(d => (
          <div key={d.id} className="flex items-center justify-between bg-slate-50 rounded-lg p-2.5 text-xs">
            <div>
              <span className="font-medium uppercase">{d.doc_type}</span> · {d.filename || 'file'} · {d.size_bytes} bytes
              <div className="text-[10px] text-slate-500 mt-0.5">Status: {d.verification_status}</div>
            </div>
            <button onClick={() => removeDoc(d.id)} className="p-1.5 hover:bg-red-50 text-red-600 rounded" title="Delete">
              <Trash2 size={14} />
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}
