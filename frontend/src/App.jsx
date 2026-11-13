import React, { useState } from 'react'
import UserInterface from './components/UserInterface'
import AgentDashboard from './components/AgentDashboard'
import AuthScreen from './components/AuthScreen'
import { Mic, LayoutDashboard, ShieldCheck } from 'lucide-react'

function App() {
  const [view, setView] = useState('user') // 'user' | 'agent' | 'account'

  return (
    <div className="min-h-screen bg-slate-50">
      {/* Top Navigation */}
      <nav className="bg-white/90 backdrop-blur border-b border-slate-200 sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex justify-between h-16 items-center gap-4">
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 bg-gradient-to-br from-primary-500 to-primary-700 rounded-xl flex items-center justify-center shadow-sm">
                <Mic className="w-5 h-5 text-white" />
              </div>
              <div>
                <h1 className="text-lg font-bold text-slate-900 leading-tight">VoiceKYC</h1>
                <p className="text-[11px] text-slate-500 -mt-0.5">Voice-first microfinance intake</p>
              </div>
            </div>

            <div className="flex items-center gap-1 bg-slate-100/80 p-1 rounded-xl">
              {[
                { key: 'user', label: 'Applicant', icon: Mic },
                { key: 'agent', label: 'Agent', icon: LayoutDashboard },
                { key: 'account', label: 'Account', icon: ShieldCheck },
              ].map(({ key, label, icon: Icon }) => (
                <button
                  key={key}
                  onClick={() => setView(key)}
                  className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm font-medium transition-all ${
                    view === key
                      ? 'bg-white text-primary-700 shadow-sm'
                      : 'text-slate-600 hover:text-slate-900 hover:bg-white/60'
                  }`}
                >
                  <Icon className="w-4 h-4" />
                  <span className="hidden sm:inline">{label}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      </nav>

      {/* Main Content */}
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        {view === 'user' ? <UserInterface /> : view === 'agent' ? <AgentDashboard /> : <AuthScreen />}
      </main>
    </div>
  )
}

export default App
