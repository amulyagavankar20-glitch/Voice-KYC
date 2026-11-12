import React, { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import {
  RefreshCw, CheckCircle, XCircle, AlertCircle, User, Clock, ChevronRight,
  FileText, Shield, AlertTriangle, Activity, Send, Flag,
} from 'lucide-react'

const STATUS = {
  in_progress: 'In progress',
  pending_review: 'Pending review',
  request_info: 'Information requested',
  approve: 'Approved',
  reject: 'Rejected',
}
const STATUS_STYLE = {
  in_progress: 'bg-blue-100 text-blue-700',
  pending_review: 'bg-amber-100 text-amber-700',
  request_info: 'bg-orange-100 text-orange-700',
  approve: 'bg-green-100 text-green-700',
  reject: 'bg-red-100 text-red-700',
}
const REVIEW_STYLE = {
  verified: 'bg-green-100 text-green-700',
  flagged: 'bg-red-100 text-red-700',
}
const REQUIRED_FIELDS = [
  'full_name', 'father_name', 'date_of_birth', 'address',
  'occupation', 'monthly_income', 'loan_purpose', 'phone',
]
const FIELD_LABELS = {
  full_name: 'Full name',
  father_name: "Father's name",
  date_of_birth: 'Date of birth',
  address: 'Address',
  occupation: 'Occupation',
  monthly_income: 'Monthly income',
  loan_purpose: 'Loan purpose',
  phone: 'Mobile number',
}

const pretty = value => (value || '').replace(/_/g, ' ')

function CrossCheckBadge({ check }) {
  const overall = check?.overall || 'not_checked'
  const style = overall === 'inconsistent'
    ? 'bg-red-100 text-red-700'
    : overall === 'consistent'
      ? 'bg-green-100 text-green-700'
      : 'bg-slate-100 text-slate-600'
  return <span className={`text-[10px] px-2 py-0.5 rounded-full ${style}`}>{pretty(overall)}</span>
}

export default function AgentDashboard() {
  const [applications, setApplications] = useState([])
  const [selected, setSelected] = useState(null)
  const [detail, setDetail] = useState(null)
  const [documents, setDocuments] = useState([])
  const [audio, setAudio] = useState([])
  const [loading, setLoading] = useState(false)
  const [note, setNote] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [reviewBusy, setReviewBusy] = useState('')
  const [followField, setFollowField] = useState('')
  const [followDraft, setFollowDraft] = useState(null)
  const [followBusy, setFollowBusy] = useState(false)
  const [error, setError] = useState('')
  const selectedRef = useRef(null)
  const submittingRef = useRef(false)

  useEffect(() => { selectedRef.current = selected }, [selected])
  useEffect(() => { submittingRef.current = submitting }, [submitting])

  const fetchApplications = useCallback(async (silent = false) => {
    if (!silent) setLoading(true)
    if (!silent) setError('')
    try {
      const rows = await api.listApplications()
      setApplications(rows)
    } catch (err) {
      if (!silent) setError(err.message)
    } finally {
      if (!silent) setLoading(false)
    }
  }, [])

  const fetchDetail = useCallback(async (id, silent = false) => {
    try {
      const [row, docs, uploads, assessment] = await Promise.all([
        api.getApplication(id),
        api.listDocuments(id).catch(() => []),
        api.listAudio(id).catch(() => []),
        api.getAssessment(id).catch(() => null),
      ])
      // Ignore a slow response if the agent has already selected another record.
      if (selectedRef.current !== id) return
      setDetail({ ...row, assessment: assessment || row.assessment || {} })
      setDocuments(docs || [])
      setAudio(uploads || [])
      if (!silent) fetchApplications(true)
    } catch (err) {
      if (selectedRef.current === id && !silent) {
        setError(err.message)
        setDetail(null)
      }
    }
  }, [fetchApplications])

  useEffect(() => { fetchApplications() }, [fetchApplications])

  useEffect(() => {
    setNote('')
    setFollowField('')
    setFollowDraft(null)
    if (selected) fetchDetail(selected)
    else {
      setDetail(null)
      setDocuments([])
      setAudio([])
    }
  }, [selected, fetchDetail])

  // Light polling keeps the floor view current without a manual refresh.
  useEffect(() => {
    const timer = setInterval(() => fetchApplications(true), 8000)
    return () => clearInterval(timer)
  }, [fetchApplications])

  useEffect(() => {
    if (!selected) return undefined
    const timer = setInterval(() => {
      if (!submittingRef.current) fetchDetail(selected, true)
    }, 8000)
    return () => clearInterval(timer)
  }, [selected, fetchDetail])

  const handleDecision = async decision => {
    if (!selected) return
    setSubmitting(true)
    setError('')
    try {
      await api.decide(selected, decision, note)
      setNote('')
      await fetchApplications(true)
      await fetchDetail(selected, true)
    } catch (err) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  const handleReview = async (field, reviewStatus) => {
    if (!selected) return
    setReviewBusy(`${field}:${reviewStatus}`)
    setError('')
    try {
      await api.reviewField(selected, field, reviewStatus)
      await fetchDetail(selected, true)
    } catch (err) {
      setError(err.message)
    } finally {
      setReviewBusy('')
    }
  }

  const generateFollowUp = async () => {
    if (!selected) return
    setFollowBusy(true)
    setError('')
    try {
      const draft = await api.followUp(selected, followField || null, false)
      setFollowDraft(draft)
    } catch (err) {
      setError(err.message)
    } finally {
      setFollowBusy(false)
    }
  }

  const sendFollowUp = async () => {
    if (!selected || !followDraft) return
    setFollowBusy(true)
    setError('')
    try {
      await api.followUp(selected, followDraft.field, true)
      setFollowDraft(null)
      await fetchDetail(selected, true)
      await fetchApplications(true)
    } catch (err) {
      setError(err.message)
    } finally {
      setFollowBusy(false)
    }
  }

  const assessment = detail?.assessment || {}
  const reviewedFields = detail?.reviewed_fields || {}
  const missingFields = detail
    ? REQUIRED_FIELDS.filter(field => !detail.extracted?.[field]?.value)
    : []
  const isClosed = detail && (detail.status === 'approve' || detail.status === 'reject')

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 min-h-[70vh]">
      <section className="bg-white rounded-2xl shadow-sm border border-slate-200 flex flex-col overflow-hidden">
        <header className="px-5 py-4 border-b flex items-center justify-between">
          <div>
            <h2 className="font-semibold">Applications</h2>
            <p className="text-xs text-slate-500">{applications.length} records · updating live</p>
          </div>
          <button onClick={() => fetchApplications()} disabled={loading} className="p-2 hover:bg-slate-100 rounded-lg" title="Refresh">
            <RefreshCw size={16} className={loading ? 'animate-spin' : ''} />
          </button>
        </header>
        <div className="flex-1 overflow-y-auto">
          {applications.length === 0 ? (
            <p className="p-8 text-center text-sm text-slate-400">No applications yet.</p>
          ) : applications.map(app => (
            <button
              key={app.id}
              onClick={() => setSelected(app.id)}
              className={`w-full text-left px-5 py-4 border-b flex items-center gap-3 hover:bg-slate-50 ${selected === app.id ? 'bg-primary-50 border-l-4 border-l-primary-600' : ''}`}
            >
              <div className="w-9 h-9 rounded-full bg-slate-100 flex items-center justify-center"><User size={16} /></div>
              <div className="flex-1 min-w-0">
                <div className="font-medium text-sm truncate">{app.name}</div>
                <div className="flex flex-wrap gap-2 items-center mt-1">
                  <span className={`text-[10px] px-2 py-0.5 rounded-full ${STATUS_STYLE[app.status] || 'bg-slate-100'}`}>
                    {STATUS[app.status] || app.status}
                  </span>
                  <span className="text-[10px] text-slate-400">{app.progress}% collected</span>
                  {app.evidence_score != null && (
                    <span className="text-[10px] text-slate-500">evidence {app.evidence_score}/100</span>
                  )}
                </div>
              </div>
              <ChevronRight size={16} className="text-slate-300" />
            </button>
          ))}
        </div>
      </section>

      <section className="lg:col-span-2 bg-white rounded-2xl shadow-sm border border-slate-200 flex flex-col overflow-hidden">
        {!detail ? (
          <div className="flex-1 flex items-center justify-center text-slate-400">
            <div className="text-center">
              <FileText size={42} className="mx-auto mb-3 opacity-40" />
              <p className="font-medium">Select an application</p>
              <p className="text-sm mt-1">Review applicant information, evidence, and conversation</p>
            </div>
          </div>
        ) : (
          <>
            <header className="px-6 py-4 border-b flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="font-semibold text-lg">{detail.extracted?.full_name?.value || 'Applicant'}</h2>
                <div className="flex flex-wrap gap-3 mt-1 text-xs text-slate-500">
                  <span className="flex items-center gap-1"><Clock size={12} />{new Date(detail.created_at).toLocaleString()}</span>
                  <span>{detail.language}</span>
                  <span>{missingFields.length === 0 ? 'All required fields present' : `${missingFields.length} required missing`}</span>
                </div>
              </div>
              <span className={`px-2.5 py-1 rounded-full text-xs ${STATUS_STYLE[detail.status] || 'bg-slate-100'}`}>
                {STATUS[detail.status] || detail.status}
              </span>
            </header>

            <div className="flex-1 overflow-y-auto p-6 space-y-6">
              <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
                <div>
                  <h3 className="text-sm font-semibold mb-3 flex gap-2 items-center"><Shield size={16} /> Applicant-provided fields</h3>
                  <div className="space-y-2">
                    {Object.entries(detail.extracted || {}).map(([key, field]) => {
                      const review = reviewedFields[key]
                      return (
                        <div key={key} className="bg-slate-50 rounded-xl p-3">
                          <div className="flex items-start justify-between gap-2">
                            <div className="text-[11px] uppercase text-slate-500">{pretty(key)}</div>
                            {review && (
                              <span className={`text-[10px] px-2 py-0.5 rounded-full ${REVIEW_STYLE[review.status] || 'bg-slate-200'}`}>
                                {review.status}
                              </span>
                            )}
                          </div>
                          <div className="text-sm font-medium mt-1 break-words">{field.value || '—'}</div>
                          {field.extraction_source && (
                            <span className={`text-[10px] mt-1 inline-block px-1.5 py-0.5 rounded ${field.extraction_source === 'llm' ? 'bg-indigo-100 text-indigo-700' : 'bg-slate-200 text-slate-600'}`}>
                              {field.extraction_source === 'llm' ? 'LLM' : 'Rules'}
                            </span>
                          )}
                          {field.evidence && field.evidence !== field.value && (
                            <div className="text-[11px] text-slate-500 mt-1 break-words">Spoken: {field.evidence}</div>
                          )}
                          <div className="text-[11px] mt-1 text-amber-700">
                            {field.confidence == null
                              ? 'Confidence not assessed · Human verification required'
                              : `${Math.round(field.confidence * 100)}% model confidence · Verify independently`}
                          </div>
                          {review?.note && <div className="text-[11px] text-slate-500 mt-1">Note: {review.note}</div>}
                          <div className="flex gap-2 mt-2">
                            <button
                              onClick={() => handleReview(key, 'verified')}
                              disabled={Boolean(reviewBusy)}
                              className="text-[11px] flex items-center gap-1 px-2 py-1 rounded-lg bg-green-50 text-green-700 disabled:opacity-40"
                            >
                              <CheckCircle size={12} /> {reviewBusy === `${key}:verified` ? 'Saving…' : 'Mark reviewed'}
                            </button>
                            <button
                              onClick={() => handleReview(key, 'flagged')}
                              disabled={Boolean(reviewBusy)}
                              className="text-[11px] flex items-center gap-1 px-2 py-1 rounded-lg bg-red-50 text-red-700 disabled:opacity-40"
                            >
                              <Flag size={12} /> {reviewBusy === `${key}:flagged` ? 'Saving…' : 'Flag'}
                            </button>
                          </div>
                        </div>
                      )
                    })}
                    {!Object.keys(detail.extracted || {}).length && <p className="text-sm text-slate-400">No fields collected.</p>}
                  </div>
                </div>

                <div className="space-y-6">
                  <div>
                    <h3 className="text-sm font-semibold mb-3 flex gap-2 items-center"><Activity size={16} /> Evidence summary</h3>
                    <div className="bg-slate-50 rounded-xl p-4">
                      <div className="flex items-end justify-between gap-3">
                        <div>
                          <div className="text-3xl font-bold text-slate-900">{assessment.score ?? '—'}<span className="text-sm font-normal text-slate-400">/100</span></div>
                          <div className="text-[11px] text-slate-500 capitalize">{pretty(assessment.evidence_band)}</div>
                        </div>
                        <span className="text-[10px] px-2 py-1 rounded-full bg-amber-100 text-amber-800">
                          {pretty(assessment.recommendation) || 'manual review'}
                        </span>
                      </div>
                      <div className="h-1.5 bg-slate-200 rounded-full mt-3">
                        <div className="h-1.5 bg-primary-500 rounded-full" style={{ width: `${Math.max(0, Math.min(100, assessment.score || 0))}%` }} />
                      </div>
                      <div className="mt-3 space-y-1.5">
                        {(assessment.reasons || []).map(reason => (
                          <div key={reason.code} className="flex gap-2 text-[11px] leading-snug">
                            <span className={`shrink-0 px-1.5 rounded ${reason.points > 0 ? 'bg-green-100 text-green-700' : reason.points < 0 ? 'bg-red-100 text-red-700' : 'bg-slate-200 text-slate-600'}`}>
                              {reason.points > 0 ? '+' : ''}{reason.points}
                            </span>
                            <span className="text-slate-600"><strong className="text-slate-800">{reason.label}:</strong> {reason.detail}</span>
                          </div>
                        ))}
                      </div>
                      <p className="text-[10px] text-slate-500 mt-3">{assessment.disclaimer}</p>
                    </div>
                  </div>

                  <div>
                    <h3 className="text-sm font-semibold mb-3 flex gap-2 items-center"><FileText size={16} /> Identity documents</h3>
                    <div className="space-y-2">
                      {documents.length === 0 && (
                        <p className="text-xs text-slate-500 bg-slate-50 rounded-xl p-3">
                          No PAN/Aadhaar uploaded. Collect and inspect one before deciding.
                        </p>
                      )}
                      {documents.map(document => {
                        const mismatches = document.cross_check?.mismatches || []
                        return (
                          <div key={document.id} className="bg-slate-50 rounded-xl p-3">
                            <div className="flex items-center justify-between gap-2">
                              <span className="text-xs font-semibold uppercase">{document.doc_type}</span>
                              <CrossCheckBadge check={document.cross_check} />
                            </div>
                            <div className="text-[11px] text-slate-600 mt-1">
                              {document.extracted_fields?.number_masked || 'Number not extracted'}
                              {document.extracted_fields?.full_name ? ` · ${document.extracted_fields.full_name}` : ''}
                            </div>
                            <div className="text-[10px] text-slate-500 mt-1">
                              Method: {pretty(document.extraction_method)}
                              {document.extracted_fields?.date_of_birth ? ` · DOB ${document.extracted_fields.date_of_birth}` : ''}
                            </div>
                            {mismatches.length > 0 && (
                              <div className="text-[11px] text-red-700 mt-1">
                                Mismatch: {mismatches.map(item => FIELD_LABELS[item.field] || item.field).join(', ')}
                              </div>
                            )}
                          </div>
                        )
                      })}
                      <p className="text-[10px] text-slate-500">
                        A consistent cross-check is not identity verification. Inspect the original document.
                      </p>
                    </div>
                  </div>
                </div>
              </div>

              <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
                <div>
                  <h3 className="text-sm font-semibold mb-3 flex gap-2 items-center"><Send size={16} /> Follow-up question</h3>
                  <div className="bg-slate-50 rounded-xl p-4">
                    <div className="flex flex-wrap gap-2">
                      <select
                        value={followField}
                        onChange={event => { setFollowField(event.target.value); setFollowDraft(null) }}
                        className="border rounded-lg px-2 py-1.5 text-xs bg-white"
                      >
                        <option value="">Next missing field</option>
                        {REQUIRED_FIELDS.map(field => <option key={field} value={field}>{FIELD_LABELS[field]}</option>)}
                      </select>
                      <button onClick={generateFollowUp} disabled={followBusy || isClosed} className="px-3 py-1.5 rounded-lg bg-slate-800 text-white text-xs disabled:opacity-40">
                        {followBusy ? 'Preparing…' : 'Generate'}
                      </button>
                    </div>
                    {followDraft && (
                      <div className="mt-3">
                        <p className="text-xs text-slate-700 whitespace-pre-wrap bg-white border rounded-lg p-2">{followDraft.question}</p>
                        <button onClick={sendFollowUp} disabled={followBusy || isClosed} className="mt-2 w-full py-2 rounded-lg bg-primary-600 text-white text-xs disabled:opacity-40">
                          Send to applicant and mark information requested
                        </button>
                      </div>
                    )}
                    {missingFields.length > 0 && (
                      <p className="text-[10px] text-amber-700 mt-2">Missing: {missingFields.map(field => FIELD_LABELS[field]).join(', ')}</p>
                    )}
                  </div>

                  <h3 className="text-sm font-semibold mb-3 mt-6 flex gap-2 items-center"><Shield size={16} /> Recorded audio</h3>
                  <div className="space-y-2">
                    {audio.length === 0 && <p className="text-xs text-slate-500 bg-slate-50 rounded-xl p-3">No applicant audio stored for this application.</p>}
                    {audio.map(upload => (
                      <div key={upload.id} className="bg-slate-50 rounded-xl p-3">
                        <div className="text-[11px] text-slate-600 flex justify-between gap-2">
                          <span className="truncate">{upload.filename || 'recording'}</span>
                          <span>{upload.duration_ms != null ? `${(upload.duration_ms / 1000).toFixed(1)}s` : pretty(upload.status)}</span>
                        </div>
                        {upload.status === 'complete' ? (
                          <audio controls preload="none" src={api.audioUrl(detail.id, upload.id)} className="w-full h-9 mt-2" />
                        ) : (
                          <p className="text-[10px] text-amber-700 mt-1">Upload incomplete ({upload.received_bytes} bytes received).</p>
                        )}
                      </div>
                    ))}
                    <p className="text-[10px] text-slate-500">
                      Audio is the applicant's own recording. Transcripts are not speaker-verified.
                    </p>
                  </div>
                </div>

                <div>
                  <h3 className="text-sm font-semibold mb-3">Conversation transcript</h3>
                  <div className="bg-slate-50 rounded-xl p-4 max-h-[32rem] overflow-y-auto space-y-3">
                    {(detail.conversation || []).map((message, index) => (
                      <div key={index} className={message.role === 'user' ? 'text-right' : ''}>
                        <div className={`inline-block px-3 py-2 rounded-lg max-w-[95%] text-sm whitespace-pre-wrap ${message.role === 'user' ? 'bg-primary-100 text-primary-900' : 'bg-white border text-slate-700'}`}>
                          {message.content}
                        </div>
                        <div className="text-[10px] text-slate-400 mt-1">
                          {message.role === 'user' ? 'Applicant' : 'Assistant'}
                          {message.channel === 'follow_up' ? ' · agent follow-up' : ''}
                        </div>
                      </div>
                    ))}
                    {!detail.conversation?.length && <p className="text-sm text-slate-400">No messages.</p>}
                  </div>
                </div>
              </div>
            </div>

            {detail.status === 'pending_review' || detail.status === 'in_progress' ? (
              <footer className="px-6 py-4 border-t bg-slate-50">
                <div className="flex gap-2 text-xs text-amber-800 mb-3">
                  <AlertTriangle size={15} />
                  Review all applicant-provided information before recording a decision. This dashboard does not verify identity or creditworthiness.
                </div>
                <textarea
                  value={note}
                  onChange={event => setNote(event.target.value)}
                  placeholder="Review note (optional)…"
                  rows={2}
                  className="w-full p-3 border rounded-lg text-sm mb-3 resize-none"
                />
                <div className="flex flex-wrap gap-2">
                  <button onClick={() => handleDecision('approve')} disabled={submitting || detail.status !== 'pending_review'} className="flex-1 min-w-28 flex justify-center gap-2 py-2.5 bg-green-600 text-white rounded-xl disabled:opacity-40">
                    <CheckCircle size={16} /> Approve
                  </button>
                  <button onClick={() => handleDecision('request_info')} disabled={submitting} className="flex-1 min-w-28 flex justify-center gap-2 py-2.5 bg-amber-500 text-white rounded-xl disabled:opacity-40">
                    <AlertCircle size={16} /> Request info
                  </button>
                  <button onClick={() => handleDecision('reject')} disabled={submitting} className="flex-1 min-w-28 flex justify-center gap-2 py-2.5 bg-red-600 text-white rounded-xl disabled:opacity-40">
                    <XCircle size={16} /> Reject
                  </button>
                </div>
                {detail.status !== 'pending_review' && (
                  <p className="text-xs text-slate-500 mt-2">Approval is enabled only after all required fields have been collected.</p>
                )}
              </footer>
            ) : (
              <footer className="px-6 py-4 border-t bg-slate-50 text-sm text-center">
                Recorded human decision: <strong>{STATUS[detail.status] || detail.status}</strong>
                {detail.decision_note && <p className="text-xs text-slate-500 mt-1">{detail.decision_note}</p>}
              </footer>
            )}
          </>
        )}
      </section>

      {error && <div className="lg:col-span-3 p-3 rounded-lg bg-red-50 text-red-700 text-sm">{error}</div>}
    </div>
  )
}
