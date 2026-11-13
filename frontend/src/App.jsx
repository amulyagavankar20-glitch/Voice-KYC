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
      <nav className="bg-white border-b border-slate-200 sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex justify-between h-16 items-center">
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 bg-primary-600 rounded-lg flex items-center justify-center">
                <Mic className="w-5 h-5 text-white" />
              </div>
              <div>
                <h1 className="text-lg font-bold text-slate-900">VoiceKYC</h1>
                <p className="text-xs text-slate-500 -mt-0.5">Gnani AI × Microfinance</p>
              </div>
            </div>

            <div className="flex items-center gap-2 bg-slate-100 p-1 rounded-lg">
              <button
                onClick={() => setView('user')}
                className={`flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-all ${
                  view === 'user'
                    ? 'bg-white text-primary-700 shadow-sm'
                    : 'text-slate-600 hover:text-slate-900'
                }`}
              >
                <Mic className="w-4 h-4" />
                User Voice Interface
              </button>
              <button
                onClick={() => setView('agent')}
                className={`flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-all ${
                  view === 'agent'
                    ? 'bg-white text-primary-700 shadow-sm'
                    : 'text-slate-600 hover:text-slate-900'
                }`}
              >
                <LayoutDashboard className="w-4 h-4" />
                Agent Dashboard
              </button>
              <button
                onClick={() => setView('account')}
                className={`flex items-center gap-2 px-4 py-2 rounded-md text-sm font-medium transition-all ${
                  view === 'account'
                    ? 'bg-white text-primary-700 shadow-sm'
                    : 'text-slate-600 hover:text-slate-900'
                }`}
              >
                <ShieldCheck className="w-4 h-4" />
                Account
              </button>
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
