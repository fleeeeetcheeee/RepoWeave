import type { components } from './generated/api';

export type Run = components['schemas']['RunView'];
export type Repository = components['schemas']['SnapshotView'];
export type Policy = components['schemas']['Policy'];
export type PolicyView = components['schemas']['PolicyView'];
export type Submission = components['schemas']['Submission'];
export type Capabilities = components['schemas']['Capabilities'];
export type Progress = {sequence: number; kind: string; data: Record<string, unknown>};
export type Graph = {nodes: string[]; edges: {source: string; target: string; kind?: string}[];
  diagnostics?: {path: string; kind: string; module: string}[]};
export type Workflow = {nodes: {id: string}[]; edges: {source: string; target: string; conditional?: boolean}[]};

export const base = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '');
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(base + path, {...init, headers: {
    ...(init.body ? {'Content-Type': 'application/json'} : {}), ...init.headers,
  }});
  if (!response.ok) {
    // Server validation deliberately returns payload-free messages.
    const body = await response.json().catch(() => ({}));
    throw new Error(`${response.status}: ${typeof body.detail === 'string' ? body.detail : 'Request failed'}`);
  }
  return response.json();
}
export const terminal = new Set(['completed', 'promoted', 'completed-without-improvement', 'failed', 'cancelled']);
export const paused = (status: string) => terminal.has(status) || ['awaiting-approval', 'interrupted'].includes(status);
export const short = (id: string) => id.slice(0, 10);
export const bytes = (n: number | null | undefined) => n == null ? '—' : `${n.toLocaleString()} B`;
