import {useCallback, useEffect, useRef, useState} from 'react';
import {api, base, bytes, paused, short, terminal} from './api';
import type {Capabilities, Graph as GraphData, Policy, PolicyView, Progress, Repository, Run, Submission, Workflow} from './api';
import {Graph} from './Graph';
import {Benchmarks} from './Benchmarks';
import type {Benchmark} from './Benchmarks';

const tabs = ['Repository', 'Archive', 'Benchmarks', 'Optimization'] as const;
type Tab = typeof tabs[number];
const query = new URLSearchParams(location.search);
const defaultPolicy: Policy = {schema_version: 1, name: 'baseline', codec: 'zstd', zstd_level: 3, dictionary_id: null, grouping: 'none', metadata_encoding: 'json'};
const message = (error: unknown) => error instanceof Error ? error.message : 'Request failed';

function Empty({children}: {children: React.ReactNode}) {return <div className="empty"><span aria-hidden="true">↗</span><p>{children}</p></div>;}
function JsonDetails({title, value}: {title: string; value: unknown}) {return <details><summary>{title}</summary><pre>{JSON.stringify(value, null, 2)}</pre></details>;}

export default function App() {
  const [tab, setTab] = useState<Tab>(tabs.find(t => t.toLowerCase() === query.get('view')) || 'Repository');
  const [repositories, setRepositories] = useState<Repository[]>([]);
  const [repoId, setRepoId] = useState(query.get('repo') || '');
  const [runs, setRuns] = useState<Run[]>([]);
  const [runId, setRunId] = useState(query.get('run') || '');
  const [run, setRun] = useState<Run | null>(null);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [fixture, setFixture] = useState('');
  const [graph, setGraph] = useState<GraphData | null>(null);
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [activePolicy, setActivePolicy] = useState<PolicyView | null>(null);
  const [useActive, setUseActive] = useState(true);
  const [policy, setPolicy] = useState<Policy>(defaultPolicy);
  const [execution, setExecution] = useState<'local' | 'daytona'>('local');
  const [approvedTransfer, setApprovedTransfer] = useState(false);
  const [events, setEvents] = useState<Progress[]>([]);
  const [connection, setConnection] = useState('Connecting');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [reportKey, setReportKey] = useState('');
  const pendingSubmission = useRef<{key: string; body: string} | null>(null);
  const repository = repositories.find(r => r.id === repoId);
  const snapshot = repository?.snapshot;
  const selectedPolicy = useActive ? activePolicy?.policy || defaultPolicy : policy;
  const policyUnavailable = (useActive && !activePolicy) || !!selectedPolicy.dictionary_id;

  const loadLists = useCallback(async () => {
    const [rs, jobs] = await Promise.all([api<Repository[]>('/repositories'), api<Run[]>('/runs')]);
    setRepositories(rs); setRuns(jobs);
    return rs;
  }, []);
  useEffect(() => {
    let alive = true;
    Promise.all([api<Capabilities>('/fixtures'), api<Workflow>('/workflow'), loadLists()]).then(([caps, topology, rs]) => {
      if (!alive) return;
      setCapabilities(caps); setFixture(caps.fixtures[0] || ''); setWorkflow(topology); setConnection('Local API ready');
      setRepoId(id => id || rs[0]?.id || '');
    }).catch(e => {if (alive) {setError(message(e)); setConnection('API unavailable');}});
    return () => {alive = false;};
  }, [loadLists]);

  useEffect(() => {
    const params = new URLSearchParams();
    params.set('view', tab.toLowerCase());
    if (repoId) params.set('repo', repoId);
    if (runId) params.set('run', runId);
    history.replaceState(null, '', '?' + params);
  }, [tab, repoId, runId]);

  useEffect(() => {
    setApprovedTransfer(false); setGraph(null); setActivePolicy(null);
    if (!repoId) return;
    const controller = new AbortController();
    Promise.all([api<GraphData>(`/repositories/${repoId}/graph`, {signal: controller.signal}),
      api<PolicyView>(`/repositories/${repoId}/policy`, {signal: controller.signal})]).then(([g, p]) => {
      setGraph(g); setActivePolicy(p);
    }).catch(e => {if (!controller.signal.aborted) setError(message(e));});
    return () => controller.abort();
  }, [repoId]);

  useEffect(() => {
    setRun(null); setEvents([]); setReportKey('');
    if (!runId) return;
    let alive = true, cursor = 0, status = '', source: EventSource | null = null;
    let reconnect: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    const refresh = async () => {
      try {
        const latest = await api<Run>(`/runs/${runId}`, {signal: controller.signal});
        if (!alive) return;
        status = latest.status; setRun(latest);
        setRuns(previous => [...previous.filter(r => r.id !== latest.id), latest]);
        if (latest.status === 'promoted') {
          const p = await api<PolicyView>(`/repositories/${latest.repository_id}/policy`, {signal: controller.signal});
          if (alive) setActivePolicy(p);
        }
      } catch (e) {if (alive) {setError(message(e)); setConnection('Reconnecting');}}
    };
    const connect = () => {
      if (!alive) return;
      source = new EventSource(`${base}/runs/${runId}/events?after=${cursor}`);
      source.onopen = () => {if (alive) setConnection('Progress connected');};
      source.addEventListener('progress', event => {
        const e = event as MessageEvent;
        const sequence = Number(e.lastEventId);
        if (!Number.isSafeInteger(sequence) || sequence <= cursor) return;
        try {
          const payload = JSON.parse(e.data);
          cursor = sequence;
          setEvents(previous => [...previous, {sequence, ...payload}].slice(-200));
          void refresh();
        } catch {setError('Invalid progress event. Reload to recover persisted state.');}
      });
      source.onerror = () => {
        source?.close();
        if (!alive) return;
        void refresh();
        if (paused(status)) {setConnection('Run state saved'); return;}
        setConnection('Reconnecting');
        reconnect = setTimeout(connect, 1500);
      };
    };
    void refresh(); connect();
    const poll = setInterval(() => {if (!paused(status)) void refresh();}, 2500);
    return () => {alive = false; controller.abort(); source?.close(); clearTimeout(reconnect); clearInterval(poll);};
  }, [runId]);

  const act = async (operation: () => Promise<void>) => {
    setBusy(true); setError('');
    try {await operation();} catch (e) {setError(message(e));} finally {setBusy(false);}
  };
  const register = () => act(async () => {
    const saved = await api<Repository>('/repositories', {method: 'POST', body: JSON.stringify({fixture})});
    await loadLists(); setRepoId(saved.id); setRunId(''); setTab('Repository'); setUseActive(true);
  });
  const submit = (operation: Submission['operation']) => act(async () => {
    const payload: Submission = {repository_id: repoId, operation, policy: selectedPolicy,
      execution: operation === 'optimize' ? execution : 'local', repeats: 2, warmups: 1,
      limits: {encode_seconds: 120, decode_seconds: 120, peak_memory_bytes: 1073741824}};
    if (payload.execution === 'daytona') {
      if (!approvedTransfer) throw new Error('Approve this snapshot transfer before starting Daytona.');
      payload.approved_snapshot = repoId;
    }
    const body = JSON.stringify(payload);
    if (pendingSubmission.current?.body !== body) pendingSubmission.current = {body, key: crypto.randomUUID()};
    const job = await api<Run>('/runs', {method: 'POST', body,
      headers: {'Idempotency-Key': pendingSubmission.current.key}});
    pendingSubmission.current = null;
    await loadLists(); setRunId(job.id); setTab(operation === 'pack' ? 'Archive' : operation === 'benchmark' ? 'Benchmarks' : 'Optimization');
  });
  const control = (action: 'approve' | 'resume' | 'cancel') => act(async () => {
    let latest = await api<Run>(`/runs/${runId}/${action}`, {method: 'POST'});
    if (action === 'approve') latest = await api<Run>(`/runs/${runId}/resume`, {method: 'POST'});
    setRun(latest);
    // Re-open the event stream from persisted history after a paused checkpoint.
    setRunId(''); setTimeout(() => setRunId(latest.id), 0);
    await loadLists();
  });
  const chooseRun = (id: string) => {
    setRunId(id);
    const selected = runs.find(r => r.id === id);
    if (selected) {setRepoId(selected.repository_id); setTab(selected.operation === 'pack' ? 'Archive' : selected.operation === 'benchmark' ? 'Benchmarks' : 'Optimization');}
  };

  const reports: Record<string, any> = run?.reports || {};
  const benchmarks: [string, Benchmark][] = Object.entries(reports).filter(([, value]) => Array.isArray(value.results)) as [string, Benchmark][];
  if (run?.operation === 'benchmark' && run.metrics) benchmarks.unshift(['benchmark', run.metrics as Benchmark]);
  const report = benchmarks.find(([key]) => key === reportKey)?.[1] || benchmarks[0]?.[1];
  const decision = reports.decision;
  const verification = reports['archive-verification'] || reports['independent-decode'];
  const visited = events.filter(e => e.kind === 'node').map(e => String(e.data.node));
  const lastNode = visited.at(-1) || '';
  const canApprove = run?.status === 'awaiting-approval' && decision?.eligible === true && !run.cancel_requested && !run.approved;
  const visibleRuns = runs.filter(r => !repoId || r.repository_id === repoId).slice().reverse();

  return <div className="app-shell">
    <aside className="sidebar"><a href="?view=repository" className="brand"><span className="brand-symbol" aria-hidden="true">W</span>RepoWeave<span className="beta">LAB</span></a>
      <p className="side-caption">COMPRESSION WORKBENCH</p>
      <nav aria-label="Workspace views">{tabs.map((t, i) => <button key={t} aria-current={tab === t ? 'page' : undefined} onClick={() => setTab(t)}><span className="nav-number">0{i + 1}</span>{t}<span aria-hidden="true">↗</span></button>)}</nav>
      <section className="run-history"><div className="history-heading"><h2>Run history</h2><button className="icon-button" aria-label="Refresh runs" onClick={() => act(async () => {await loadLists();})}>↻</button></div>
        {visibleRuns.length === 0 ? <p className="side-muted">Your first run will appear here.</p> : visibleRuns.map(r => <button key={r.id} className={`history-item ${runId === r.id ? 'selected' : ''}`} onClick={() => chooseRun(r.id)}><span>{r.operation}<code>{short(r.id)}</code></span><small>{r.status}</small></button>)}
      </section><div className="side-footer"><span className="status-dot" />Local workspace<p>Runs stay on this computer.<br />Public hosting is optional.</p></div>
    </aside>
    <main id="main"><header className="topbar"><span>Workspace <span className="slash">/</span> <strong>{tab}</strong></span><span className="connection" role="status"><span className="status-dot" />{connection}</span></header>
      <div className="page"><div className="page-heading"><div><p className="eyebrow">LOSSLESS BY CONSTRUCTION</p><h1>{tab === 'Repository' ? 'Know what you compress.' : tab === 'Archive' ? 'Every byte accounted for.' : tab === 'Benchmarks' ? 'Let the measurements decide.' : 'Explore. Measure. Verify.'}</h1><p className="lede">{tab === 'Repository' ? 'Capture an immutable snapshot, inspect its structure, and start a measured experiment.' : tab === 'Archive' ? 'Portable archives, explicit policies, and exact reconstruction.' : tab === 'Benchmarks' ? 'Compare complete deliverables under the same resource limits.' : 'Follow real workflow progress. Promote only a verified improvement.'}</p></div><span className="local-badge">↳ LOCAL FIRST</span></div>
      {error && <div className="error" role="alert">{error}<button onClick={() => setError('')} aria-label="Dismiss error">×</button></div>}
      <section className="panel setup-panel" aria-label="Repository and run controls">
        <div className="control-row"><label>Approved fixture<select aria-label="Approved fixture" value={fixture} onChange={e => setFixture(e.target.value)}>{capabilities?.fixtures.map(name => <option key={name}>{name}</option>)}</select></label><button className="primary" disabled={busy || !fixture} onClick={register}>Capture snapshot <span aria-hidden="true">↗</span></button>
          <label className="snapshot-select">Saved snapshot<select aria-label="Saved snapshot" value={repoId} onChange={e => {setRepoId(e.target.value); setRunId(''); setUseActive(true);}}><option value="">Choose a snapshot</option>{repositories.map(r => <option key={r.id} value={r.id}>{short(r.id)} · {r.snapshot.file_count} files</option>)}</select></label>
        </div>
        {repository && <><div className="control-row job-controls"><label className="check"><input type="checkbox" checked={useActive} onChange={e => setUseActive(e.target.checked)} />Use active policy</label>
          {!useActive && <><label>Codec<select aria-label="Codec" value={policy.codec} onChange={e => setPolicy({...defaultPolicy, codec: e.target.value as Policy['codec']})}><option value="zstd">Zstd</option><option value="stored">Stored (uncompressed control)</option><option value="lzma">LZMA</option></select></label>{policy.codec === 'zstd' && <label>Zstd level<input aria-label="Zstd level" type="number" min="-7" max="22" value={policy.zstd_level} onChange={e => setPolicy({...policy, zstd_level: Number(e.target.value)})} /></label>}<label>Metadata<select aria-label="Metadata" value={policy.metadata_encoding || 'json'} onChange={e => setPolicy({...policy, metadata_encoding: e.target.value as Policy['metadata_encoding']})}><option value="json">JSON</option><option value="compressed">Compressed JSON</option><option value="compact">Compact</option></select></label>{policy.codec !== 'stored' && <label>Shared compression blocks<select aria-label="Shared compression blocks" value={policy.solid_block_bytes || 0} onChange={e => setPolicy({...policy, solid_block_bytes: Number(e.target.value) || null})}><option value="0">Per file</option><option value="1048576">1 MiB target</option><option value="8388608">8 MiB target</option></select></label>}</>}
          <span className="policy-label">{selectedPolicy.codec}{selectedPolicy.codec === 'zstd' ? ` · level ${selectedPolicy.zstd_level}` : ''}{selectedPolicy.dictionary_id ? ' · dictionary' : ''}{selectedPolicy.solid_block_bytes ? ` · ${selectedPolicy.solid_block_bytes / 1048576} MiB solid` : ''}{selectedPolicy.metadata_encoding && selectedPolicy.metadata_encoding !== 'json' ? ` · ${selectedPolicy.metadata_encoding} metadata` : ''}</span>
          <div className="action-buttons"><button disabled={busy || policyUnavailable} onClick={() => submit('pack')}>Create archive</button><button disabled={busy || policyUnavailable} onClick={() => submit('benchmark')}>Run benchmark</button></div></div>
          {selectedPolicy.dictionary_id && <p className="notice">Dictionary policies require training partition metadata through the CLI or API. Uncheck Use active policy to choose an ordinary codec here.</p>}
          <details className="optimization-controls"><summary>Optimization settings</summary><div className="control-row"><label>Experiment execution<select aria-label="Experiment execution" value={execution} onChange={e => {setExecution(e.target.value as 'local' | 'daytona'); setApprovedTransfer(false);}}><option value="local">This computer</option><option value="daytona" disabled={!capabilities?.daytona}>Daytona sandbox{!capabilities?.daytona ? ' (backend not enabled)' : ''}</option></select></label><p className="muted">Factory receives measurement summaries. Two measured repetitions and one warmup; 120 seconds per phase.</p></div>
            {execution === 'daytona' && <label className="check transfer"><input type="checkbox" checked={approvedTransfer} onChange={e => setApprovedTransfer(e.target.checked)} />I approve uploading snapshot {short(repoId)} to Daytona for this experiment and independent decoding.</label>}
            <button className="primary" disabled={busy || policyUnavailable || !capabilities?.factory || (execution === 'daytona' && !approvedTransfer)} onClick={() => submit('optimize')}>Start optimization</button>
            {!capabilities?.factory && <p className="muted">Factory’s Droid CLI must be installed on the backend to optimize. Packing and benchmarks work without it.</p>}
          </details></>}
      </section>

      {tab === 'Repository' && (snapshot ? <><div className="metrics-grid"><Metric label="Included files" value={String(snapshot.file_count)} /><Metric label="Original bytes" value={bytes(snapshot.total_bytes)} /><Metric label="Import edges" value={String(graph?.edges.length ?? '—')} /><Metric label="Excluded entries" value={String(snapshot.exclusions?.length || 0)} /></div>
        <section className="panel"><div className="section-heading"><div><h2>Repository structure</h2><p>Static Python imports. This is not the archive decode graph.</p></div><span className="tag">Snapshot {short(repoId)}</span></div>{graph && <Graph graph={graph} title="Repository import graph" />}{graph?.diagnostics?.length ? <JsonDetails title={`Unresolved imports and diagnostics (${graph.diagnostics.length})`} value={graph.diagnostics} /> : null}</section>
        <section className="panel"><h2>Snapshot inventory</h2><p className="muted">{snapshot.scope} · Paths and metadata only; source files are never executed.</p><Inventory entries={snapshot.entries || []} /><JsonDetails title={`Excluded entries (${snapshot.exclusions?.length || 0})`} value={snapshot.exclusions || []} /><JsonDetails title="Snapshot identity" value={{id: repoId, active_policy: activePolicy}} /></section>
      </> : <Empty>Capture an approved fixture to inspect its files and import graph.</Empty>)}

      {tab !== 'Repository' && <section className="panel run-panel"><div className="section-heading"><div><h2>{run ? `${run.operation[0].toUpperCase()}${run.operation.slice(1)} run` : 'Choose a run'}</h2><p>{run ? `${short(run.id)} · ${run.execution === 'daytona' ? 'Daytona execution' : 'Local execution'}` : 'Create a run above or select one from the history.'}</p></div>{run && <span data-testid="run-status" className={`pill ${terminal.has(run.status) && !['failed', 'cancelled'].includes(run.status) ? 'good' : 'warn'}`}>{run.status}</span>}</div>
        {run && <><div className="run-actions"><button disabled={busy || !canApprove} onClick={() => control('approve')}>Approve &amp; promote</button><button disabled={busy || !(run.status === 'interrupted' || (run.status === 'awaiting-approval' && run.approved))} onClick={() => control('resume')}>Resume run</button><button disabled={busy || terminal.has(run.status) || run.cancel_requested} onClick={() => control('cancel')}>{run.cancel_requested ? 'Cancellation requested' : 'Cancel run'}</button></div>
        {run.status === 'failed' && <p role="alert" className="notice">This run failed. No improvement is claimed. Inspect the event timeline for the failure category.</p>}
        {run.status === 'completed-without-improvement' && <p className="notice">No verified improvement. The active policy was retained.</p>}
        {run.status === 'awaiting-approval' && <p className="notice">A measured improvement passed verification. Approval is required before changing the active policy.</p>}</>}
      </section>}

      {tab === 'Archive' && (run ? <section className="panel"><h2>Portable artifacts</h2><p>Downloads are checked against their recorded size and SHA-256 before transfer.</p>
        <div className="artifact-list">{run.artifacts.filter(a => a.kind === 'archive').map(a => <article key={a.id}><div><span className="file-icon">RPW</span><h3>Archive · {short(a.id)}</h3><p>{bytes(a.size)} · full deliverable</p><code className="hash">SHA-256 {a.sha256}</code></div><a className="button primary" href={`${base}/artifacts/${a.id}`} download={`${a.id}.rpw`}>Download archive ↓</a></article>)}</div>
        {!run.artifacts.some(a => a.kind === 'archive') && <Empty>The archive appears here after successful verification.</Empty>}
        <h3>Independent verification</h3>{verification ? <><p className="verified">✓ Exact reconstruction verified</p><JsonDetails title="Verification receipt" value={verification} /></> : <p className="muted">No independent verification receipt is available for this run yet.</p>}
        <JsonDetails title="Parent policy submitted for this run" value={run.policy} />{decision && <JsonDetails title="Selected archive policy identity" value={decision.candidate_id} />}<ArtifactLinks run={run} /></section> : <Empty>Create an archive or select a completed run.</Empty>)}

      {tab === 'Benchmarks' && (report ? <section className="panel">{benchmarks.length > 1 && <label>Comparison report<select aria-label="Comparison report" value={reportKey || benchmarks[0][0]} onChange={e => setReportKey(e.target.value)}>{benchmarks.map(([key]) => <option key={key} value={key}>{key === 'baseline' ? 'Original policy' : `Candidate ${short(key.replace('candidate-', ''))}`}</option>)}</select></label>}<Benchmarks report={report} />{run && <ArtifactLinks run={run} />}</section> : <Empty>Run a benchmark to compare RPW, plain zstd and whole-tree tar baselines.</Empty>)}

      {tab === 'Optimization' && <><section className="panel"><div className="section-heading"><div><h2>Optimization workflow</h2><p>Topology from the running LangGraph definition. Highlighted nodes have persisted execution events.</p></div><span className="tag">{lastNode || 'Not started'}</span></div>{workflow && <Graph graph={{nodes: workflow.nodes.map(n => n.id), edges: workflow.edges}} visited={visited} active={run?.status === 'running' ? lastNode : ''} title="Optimization workflow graph" />}</section>
        {decision && <section className="panel"><h2>{decision.eligible ? 'Verified candidate' : 'Policy retained'}</h2><div className="metrics-grid"><Metric label="Original RPW" value={bytes(decision.baseline_bytes)} /><Metric label="Selected RPW" value={bytes(decision.selected_bytes)} /><Metric label="Decision" value={decision.eligible ? 'Eligible for approval' : 'No improvement'} /></div><p className="notice">Eligibility compares complete candidate bytes against this run’s parent policy, within resource limits. Whole-tree baselines are available in Benchmarks.</p><JsonDetails title="Decision evidence IDs" value={decision} /></section>}
        {reports.diagnosis && <section className="panel"><h2>Agent evidence</h2><p>{reports.diagnosis.summary}</p><JsonDetails title="Hypotheses and evidence references" value={reports.diagnosis} />{reports.proposals && <JsonDetails title="Bounded policy proposals" value={reports.proposals} />}</section>}
        {reports.summary && <section className="panel"><h2>Experiment conclusion</h2><p>{reports.summary.outcome}</p><p>{reports.summary.limitations}</p></section>}
        {!!run?.sandboxes?.length && <section className="panel"><h2>Remote reconstruction showcase</h2><p>Compress remotely → reconstruct in a fresh environment → verify exact bytes</p><ul>{run.sandboxes.map(s => <li key={s.role}>{s.role}: <strong>{s.status}</strong></li>)}</ul>{reports['independent-decode'] && <JsonDetails title="Independent decoder sandbox receipt" value={reports['independent-decode']} />}</section>}
        <section className="panel"><h2>Persisted event timeline</h2><p className="muted">Reconnects resume after the last sequence. Showing the latest {events.length} events (up to 200).</p>{events.length ? <ol className="timeline">{events.map(e => <li key={e.sequence}><span className="event-sequence">{e.sequence}</span><div><strong>{e.kind}</strong><code>{JSON.stringify(e.data)}</code></div></li>)}</ol> : <Empty>Start an optimization to follow measured progress.</Empty>}{run && <ArtifactLinks run={run} />}</section></>}
      <footer className="page-footer">RepoWeave <span>Supported files are reconstructed byte for byte. Research results are specific to the measured dataset.</span></footer>
      </div>
    </main>
  </div>;
}

function Metric({label, value}: {label: string; value: string}) {return <div className="metric"><p>{label}</p><strong>{value}</strong></div>;}
function ArtifactLinks({run}: {run: Run}) {return <div className="report-links">{run.artifacts.filter(a => a.kind !== 'archive').map(a => <a key={a.id} href={`${base}/artifacts/${a.id}`} download={`${a.kind}.json`}>Download {a.kind} ↓</a>)}</div>;}
function Inventory({entries}: {entries: {path: string; kind: string; size_bytes?: number | null; sha256?: string | null}[]}) {
  const [filter, setFilter] = useState('');
  const matching = entries.filter(e => e.path.toLowerCase().includes(filter.toLowerCase()));
  return <><label>Filter paths<input type="search" value={filter} onChange={e => setFilter(e.target.value)} placeholder="Search snapshot files…" /></label><div className="table-scroll"><table><caption>{matching.length} matching entries; displaying at most 200</caption><thead><tr><th>Path</th><th>Kind</th><th>Bytes</th><th>SHA-256</th></tr></thead><tbody>{matching.slice(0, 200).map(e => <tr key={e.path}><th scope="row"><code>{e.path}</code></th><td>{e.kind}</td><td>{bytes(e.size_bytes)}</td><td><code title={e.sha256 || undefined}>{e.sha256 ? short(e.sha256) : '—'}</code></td></tr>)}</tbody></table></div></>;
}
