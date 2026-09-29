const BASE = ''

async function get<T>(path: string): Promise<T> {
  const resp = await fetch(BASE + path)
  const body = await resp.json().catch(() => null)
  if (!resp.ok || !body || body.ok === false) {
    const msg = body && body.error ? body.error : `HTTP ${resp.status}`
    throw new Error(msg)
  }
  return body.data as T
}

export const api = {
  runs: () => get<any[]>('/api/runs'),
  agents: () => get<any[]>('/api/agents'),
  runView: (runId: string) => get<any>(`/api/runs/${encodeURIComponent(runId)}`),
  replay: (runId: string) => get<any[]>(`/api/runs/${encodeURIComponent(runId)}/replay`),
  iteration: (runId: string, n: number) =>
    get<any>(`/api/runs/${encodeURIComponent(runId)}/iterations/${n}`),
  cases: (runId: string, n: number, offset: number, limit: number, result?: string) =>
    get<any>(`/api/runs/${encodeURIComponent(runId)}/iterations/${n}/cases?offset=${offset}&limit=${limit}${result ? `&result=${result}` : ''}`),
  logTail: (runId: string, n: number, name: string, bytes = 16384) =>
    get<any>(`/api/runs/${encodeURIComponent(runId)}/iterations/${n}/logs/tail?name=${encodeURIComponent(name)}&bytes=${bytes}`),
  artifact: (runId: string, path: string) =>
    get<any>(`/api/runs/${encodeURIComponent(runId)}/artifact?path=${encodeURIComponent(path)}`),
}
