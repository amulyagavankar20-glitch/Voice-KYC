import React, { useEffect, useState } from 'react'
import UserInterface from './components/UserInterface'
import AgentDashboard from './components/AgentDashboard'
import AuthScreen from './components/AuthScreen'
import { api, getToken, getStoredUser, setStoredUser, clearAuth } from './api/client'
import { Mic, LayoutDashboard, LogOut, Shield, Sparkles } from 'lucide-react'

function App() {
  const [user, setUser] = useState(getStoredUser())
  const [checking, setChecking] = useState(!!getToken())
  const [view, setView] = useState(() => (getStoredUser()?.is_agent ? 'agent' : 'user'))

  useEffect(() => {
    let cancelled = false
    async function verify() {
      if (!getToken()) { setChecking(false); return }
      try {
        const me = await api.me()
        if (cancelled) return
        setUser(me)
        setStoredUser(me)
        setView(me.is_agent ? 'agent' : 'user')
      } catch {
        if (cancelled) return
        clearAuth()
        setUser(null)
      } finally {
        if (!cancelled) setChecking(false)
      }
    }
    verify()
    return () => { cancelled = true }
  }, [])

  const onAuthed = (u) => {
    setUser(u)
    setStoredUser(u)
    setView(u.is_agent ? 'agent' : 'user')
  }

  const logout = () => {
    clearAuth()
    setUser(null)
  }

  if (checking) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-950 via-slate-900 to-primary-950 flex items-center justify-center">
        <div className="text-white/80 text-sm flex items-center gap-3">
          <span className="w-2 h-2 rounded-full bg-primary-400 animate-pulse" />
          Loading VoiceKYC…
        </div>
      </div>
    )
  }

  if (!user) {
    return <AuthScreen onAuthed={onAuthed} />
  }

  const isAgent = !!user.is_agent

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-100 via-slate-50 to-primary-50/40">
      <nav className="sticky top-0 z-50 border-b border-white/60 bg-white/70 backdrop-blur-xl">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex h-16 items-center justify-between gap-3">
            <div className="flex items-center gap-3 min-w-0">
              <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-primary-500 to-indigo-600 flex items-center justify-center shadow-lg shadow-primary-500/20">
                <Mic className="w-5 h-5 text-white" />
              </div>
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <h1 className="text-base font-bold text-slate-900 leading-none">VoiceKYC</h1>
                  <span className="hidden sm:inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full bg-primary-50 text-primary-700 border border-primary-100">
                    <Sparkles size={10} /> Microfinance
                  </span>
                </div>
                <p className="text-[11px] text-slate-500 truncate">Voice-first KYC intake · Human review</p>
              </div>
            </div>

            <div className="flex items-center gap-2">
              <div className="flex items-center gap-1 bg-slate-900/5 p-1 rounded-xl border border-slate-200/70">
                {!isAgent && (
                  <button
                    onClick={() => setView('user')}
                    className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm font-medium transition ${
                      view === 'user' ? 'bg-white text-primary-700 shadow-sm' : 'text-slate-600 hover:text-slate-900'
                    }`}
                  >
                    <Mic size={15} /> <span className="hidden sm:inline">My Application</span>
                  </button>
                )}
                {isAgent && (
                  <button
                    onClick={() => setView('agent')}
                    className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm font-medium transition ${
                      view === 'agent' ? 'bg-white text-primary-700 shadow-sm' : 'text-slate-600 hover:text-slate-900'
                    }`}
                  >
                    <LayoutDashboard size={15} /> <span className="hidden sm:inline">Review Queue</span>
                  </button>
                )}
                <button
                  onClick={() => setView('account')}
                  className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm font-medium transition ${
                    view === 'account' ? 'bg-white text-primary-700 shadow-sm' : 'text-slate-600 hover:text-slate-900'
                  }`}
                >
                  <Shield size={15} /> <span className="hidden sm:inline">Account</span>
                </button>
              </div>

              <div className="hidden md:flex flex-col items-end leading-tight px-2">
                <span className="text-xs font-medium text-slate-800">{user.full_name}</span>
                <span className="text-[10px] text-slate-500">{isAgent ? 'Review Agent' : 'Applicant'} · {user.username}</span>
              </div>
              <button
                onClick={logout}
                title="Sign out"
                className="p-2 rounded-lg text-slate-500 hover:text-slate-900 hover:bg-white border border-transparent hover:border-slate-200"
              >
                <LogOut size={16} />
              </button>
            </div>
          </div>
        </div>
      </nav>

      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        {view === 'account' ? (
          <AuthScreen embedded user={user} onLogout={logout} />
        ) : isAgent ? (
          <AgentDashboard />
        ) : (
          <UserInterface />
        )}
      </main>
    </div>
  )
}

export default App
