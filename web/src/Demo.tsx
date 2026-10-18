import {useCallback, useEffect, useState} from 'react';
import {base, bytes, short} from './api';
import type {Policy, Repository, Run, Workflow} from './api';
import {Graph} from './Graph';
import {Empty, Metric, describe} from './ui';

type Report = {policy: Policy; policy_id: string; results: {score_bytes: number | null}[]};
type Candidate = {policy: Partial<Policy>; rationale: string};

export const beats = [
  {time: '0–8 s', title: 'Captured input', claim: 'This is the fixed input snapshot. Verification checks the supported files captured here.'},
  {time: '8–20 s', title: 'Proposal and measured result', claim: 'Factory proposed these settings. The compressor produced this actual archive size.'},
  {time: '20–30 s', title: 'Remote experiment', claim: 'The workflow ran the experiment in a Daytona sandbox.'},
  {time: '30–47 s', title: 'Independent decoder receipt', claim: 'A second, fresh sandbox received only the archive. It restored the contents, and the backend compared them with the original snapshot byte for byte.'},
  {time: '47–55 s', title: 'Sandbox cleanup', claim: 'Both temporary environments were deleted after verification.'},
  {time: '55–60 s', title: 'Portable archive', claim: 'The result is a portable archive. Restoring it needs neither Factory nor a cloud account.'},
];

const samePolicy = (candidate: Partial<Policy>, measured: Policy) =>
  Object.entries(candidate).every(([key, value]) => JSON.stringify(measured[key as keyof Policy] ?? null) === JSON.stringify(value ?? null));
const percent = (part: number, whole: number) => `${(100 * part / whole).toFixed(1)}%`;

export function Demo({run, snapshot, workflow, visited, present, setPresent}: {
  run: Run | null; snapshot?: Repository['snapshot']; workflow: Workflow | null; visited: string[];
  present: boolean; setPresent: (on: boolean) => void;
}) {
  const [beat, setBeat] = useState(0);
  const go = useCallback((index: number) => {
    const next = Math.max(0, Math.min(beats.length - 1, index));
    setBeat(next);
    const smooth = !matchMedia('(prefers-reduced-motion: reduce)').matches;
    document.getElementById(`beat-${next + 1}`)?.scrollIntoView({behavior: smooth ? 'smooth' : 'auto', block: 'start'});
  }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).closest('input, select, textarea')) return;
      if (e.key === 'ArrowRight' || e.key === 'PageDown') {e.preventDefault(); go(beat + 1);}
      else if (e.key === 'ArrowLeft' || e.key === 'PageUp') {e.preventDefault(); go(beat - 1);}
      else if (e.key === 'Escape' && present) setPresent(false);
    };
    addEventListener('keydown', onKey);
    return () => removeEventListener('keydown', onKey);
  }, [beat, go, present, setPresent]);

  if (!run) return <Empty>Complete an optimization run to present it here. A Daytona run shows every beat.</Empty>;
  const reports: Record<string, any> = run.reports || {};
  const decision = reports.decision;
  const candidates: Candidate[] = reports.proposals?.candidates || [];
  const measured = Object.entries(reports).filter(([key]) => key.startsWith('candidate-')).map(([, value]) => value as Report);
  const receipt = reports['independent-decode'] as {snapshot_id: string; archive_sha256: string; engine_id: string} | undefined;
  const verified = !!receipt && receipt.snapshot_id === run.repository_id;
  const archive = run.artifacts.find(a => a.kind === 'archive');
  const cloud = run.execution === 'daytona';
  const sandbox = (role: string) => run.sandboxes?.find(s => s.role === role);
  const missing = (text: string) => <p className="notice">{text}</p>;

  return <div className="demo">
    <nav className="demo-stepper" aria-label="Demo beats">
      <ol>{beats.map((b, i) => <li key={b.title}><button aria-current={beat === i ? 'step' : undefined} onClick={() => go(i)}><span className="beat-time">{b.time}</span>{b.title}</button></li>)}</ol>
      <button className={present ? '' : 'primary'} onClick={() => setPresent(!present)}>{present ? 'Exit presenter (Esc)' : 'Present ▸'}</button>
    </nav>
    <p className="muted demo-run">Presenting {run.operation} run <code>{short(run.id)}</code> · {cloud ? 'Daytona execution' : 'local execution'} · status <strong>{run.status}</strong>. Use ← → to move between beats.</p>

    <Beat index={0} current={beat}>{snapshot ? <>
      <div className="metrics-grid"><Metric label="Snapshot" value={short(snapshot.snapshot_id)} /><Metric label="Included files" value={String(snapshot.file_count)} /><Metric label="Original bytes" value={bytes(snapshot.total_bytes)} /><Metric label="Excluded entries" value={String(snapshot.exclusions?.length || 0)} /></div>
      <div className="table-scroll"><table><caption>First {Math.min(6, snapshot.entries.length)} of {snapshot.entries.length} captured entries. Paths and hashes only; sources are never executed.</caption>
        <thead><tr><th>Path</th><th>Bytes</th><th>SHA-256</th></tr></thead>
        <tbody>{snapshot.entries.slice(0, 6).map(e => <tr key={e.path}><th scope="row"><code>{e.path}</code></th><td>{e.kind === 'file' ? bytes(e.size_bytes) : 'directory'}</td><td><code>{e.sha256 ? short(e.sha256) : '—'}</code></td></tr>)}</tbody></table></div>
    </> : missing('The snapshot for this run is not loaded.')}</Beat>

    <Beat index={1} current={beat}>
      {candidates.length ? <div className="proposal-list">{candidates.map((c, i) => {
        const report = measured.find(r => samePolicy(c.policy, r.policy));
        const size = report?.results[0]?.score_bytes;
        const selected = !!report && report.policy_id === decision?.candidate_id;
        return <article key={i} className={selected ? 'proposal selected' : 'proposal'}>
          <div className="proposal-head"><span className="policy-label">{describe(c.policy)}</span>{selected ? <span className="pill good">Selected</span> : <span className="pill">Not selected</span>}</div>
          <p>{c.rationale}</p>
          <strong className="proposal-bytes">{size == null ? 'No measurement' : bytes(size)}</strong>
        </article>;
      })}</div> : missing('This run has no Factory proposals.')}
      {decision ? <>
        <div className="metrics-grid"><Metric label="Original snapshot" value={bytes(snapshot?.total_bytes)} /><Metric label="Parent policy archive" value={bytes(decision.baseline_bytes)} /><Metric label="Selected archive" value={bytes(decision.selected_bytes)} />
          <Metric label="Change vs parent" value={!decision.baseline_bytes ? '—' : decision.selected_bytes === decision.baseline_bytes ? 'No change' : `${decision.selected_bytes < decision.baseline_bytes ? '−' : '+'}${percent(Math.abs(decision.baseline_bytes - decision.selected_bytes), decision.baseline_bytes)}`} /></div>
        {!decision.eligible && <p className="notice">No proposal beat the parent policy. The parent policy's archive was kept.</p>}
      </> : missing('No measured decision is recorded for this run yet.')}
    </Beat>

    <Beat index={2} current={beat}>
      <div className="evidence-row"><span className={cloud ? 'pill good' : 'pill warn'}>{cloud ? 'Ran in a Daytona sandbox' : 'Ran on this computer'}</span>
        {cloud && <span className="tag">Experiment sandbox · {sandbox('experiment')?.status || 'no record'}</span>}
        {receipt && <span className="tag">Engine {receipt.engine_id}</span>}
        <span className="tag">{visited.length} workflow steps recorded</span></div>
      {workflow ? <Graph graph={{nodes: workflow.nodes.map(n => n.id), edges: workflow.edges}} visited={visited} title="Optimization workflow for this run" /> : missing('Workflow topology is not loaded.')}
    </Beat>

    <Beat index={3} current={beat}>{receipt ? <>
      <ol className="receipt-flow">
        <li><span className="step-label">Archive sent</span><code>SHA-256 {archive ? short(archive.sha256) : '—'}</code><small>{archive ? bytes(archive.size) : ''}</small></li>
        <li><span className="step-label">Fresh decoder sandbox</span><strong>Archive only</strong><small>No snapshot, credentials or training data</small></li>
        <li><span className="step-label">Restored and returned</span><code>snapshot {short(receipt.snapshot_id)}</code><small>Uncompressed transfer {short(receipt.archive_sha256)} · {receipt.engine_id}</small></li>
        <li><span className="step-label">Backend compared</span><strong>Every byte and path</strong><small>{snapshot ? `Against snapshot ${short(run.repository_id)}: ${snapshot.file_count} files · ${bytes(snapshot.total_bytes)}` : `Against snapshot ${short(run.repository_id)}`}</small></li>
      </ol>
      <p className={verified ? 'receipt-result good' : 'receipt-result warn'} role="status">{verified ? '✓ Byte-for-byte match with the original snapshot' : 'Receipt names a different snapshot. Not verified.'}</p>
    </> : missing(cloud ? 'No independent decoder receipt is recorded for this run yet.' : 'This run executed locally, so there is no fresh-sandbox decoder receipt. Present a Daytona run.')}</Beat>

    <Beat index={4} current={beat}>{cloud ? <div className="sandbox-cards">{['experiment', 'decoder'].map(role => {
      const status = sandbox(role)?.status;
      return <article key={role} className="sandbox-card"><h3>{role === 'experiment' ? 'Experiment sandbox' : 'Decoder sandbox'}</h3>
        <p>{role === 'experiment' ? 'Ran the baseline and Factory candidates.' : 'Received only the archive and restored it.'}</p>
        <span className={status === 'deleted' ? 'pill good' : 'pill warn'}>{status === 'deleted' ? '✓ Deleted' : status || 'No record'}</span></article>;
    })}</div> : missing('This run executed locally and created no sandboxes.')}</Beat>

    <Beat index={5} current={beat}>{archive ? <div className="artifact-list"><article><div><span className="file-icon">RPW</span><h3>Archive · {short(archive.id)}</h3>
      <p>{bytes(archive.size)}{snapshot?.total_bytes ? ` · ${percent(archive.size, snapshot.total_bytes)} of the original snapshot` : ''}</p>
      <code className="hash">SHA-256 {archive.sha256}</code><p className="muted">Restore offline: <code>repoweave unpack {short(archive.id)}.rpw -o restored</code></p></div>
      <a className="button primary" href={`${base}/artifacts/${archive.id}`} download={`${archive.id}.rpw`}>Download archive ↓</a></article></div> : missing('No archive is attached to this run.')}</Beat>
  </div>;
}

function Beat({index, current, children}: {index: number; current: number; children: React.ReactNode}) {
  const b = beats[index];
  return <section id={`beat-${index + 1}`} className={`panel beat ${current === index ? 'current' : ''}`} aria-labelledby={`beat-${index + 1}-title`}>
    <div className="beat-head"><span className="beat-time">{b.time}</span><h2 id={`beat-${index + 1}-title`}>{b.title}</h2></div>
    <p className="claim">{b.claim}</p>{children}</section>;
}
