import { apiFetch } from './http.js'

export async function getDecisions(page = 1, size = 20) {
  const response = await apiFetch(`/api/decisions?page=${page}&size=${size}`)
  return response.json()
}

export async function getDecision(decisionId) {
  const response = await apiFetch(`/api/decisions/${encodeURIComponent(decisionId)}`)
  return response.json()
}

export async function getReplayPolicies() {
  const response = await apiFetch('/api/decisions/policies')
  return response.json()
}

export async function replayDecision(decisionId, policyVersion) {
  const response = await apiFetch(`/api/decisions/${encodeURIComponent(decisionId)}/replay`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ policyVersion }),
  })
  return response.json()
}
