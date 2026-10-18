import {bytes} from './api';

export type Sample = {archive_bytes: number; reconstructed: boolean; encode_seconds: number;
  decode_seconds: number; encode_peak_rss_bytes: number; decode_peak_rss_bytes: number;
  manifest_bytes?: number; payload_bytes?: number; embedded_dictionary_bytes?: number};
export type Benchmark = {original_bytes: number; repeats: number; warmups: number; partition: string;
  policy_id: string; policy: {codec: string; zstd_level: number}; final: boolean;
  measurement: string | Record<string, string>; environment: Record<string, unknown>;
  results: {name: string; status: string; eligible: boolean; score_bytes: number | null;
    error?: string; ineligible_reason?: string; samples: Sample[]}[]};

export function Benchmarks({report}: {report: Benchmark}) {
  const mean = (samples: Sample[], key: 'encode_seconds' | 'decode_seconds') => samples.length
    ? (samples.reduce((n, sample) => n + sample[key], 0) / samples.length).toFixed(4) + ' s' : '—';
  return <>
    <div className="section-heading"><div><h2>Measured comparisons</h2><p>{report.repeats} measured repetitions · {report.warmups} discarded warmups · {report.partition}</p></div><span className="tag">{report.final ? 'Final holdout' : 'Exploratory / validation'}</span></div>
    <div className="table-scroll"><table><caption>Full archive sizes and mean encode/decode time on the same input</caption>
      <thead><tr><th>Method</th><th>Archive</th><th>Encode</th><th>Decode</th><th>Peak RSS</th><th>Reconstruction / eligibility</th></tr></thead>
      <tbody>{report.results.map(row => <tr key={row.name}><th scope="row">{row.name}</th>
        <td>{bytes(row.samples[0]?.archive_bytes)}</td><td>{mean(row.samples, 'encode_seconds')}</td><td>{mean(row.samples, 'decode_seconds')}</td>
        <td>{row.samples.length ? bytes(Math.max(...row.samples.flatMap(s => [s.encode_peak_rss_bytes, s.decode_peak_rss_bytes]))) : '—'}</td>
        <td><span className={row.eligible ? 'pill good' : 'pill warn'}>{row.status === 'ok' && row.samples.length && row.samples.every(s => s.reconstructed) ? 'Exact bytes' : 'Not verified'}</span>
          <small>{row.eligible ? 'Within measured limits' : row.error || row.ineligible_reason || 'Outside measured limits'}</small></td>
      </tr>)}</tbody></table></div>
    <p className="notice">Archive sizes include metadata and embedded assets. A win over per-file RPW zstd is not a win over whole-tree tar-zstd. No general compression advantage is implied.</p>
    <div className="two-columns"><section><h3>Archive overhead</h3><dl>{report.results.filter(r => r.samples[0]).map(row => <div key={row.name}><dt>{row.name}</dt><dd>Manifest {bytes(row.samples[0].manifest_bytes)} · Payload {bytes(row.samples[0].payload_bytes)} · Embedded dictionary {bytes(row.samples[0].embedded_dictionary_bytes)}</dd></div>)}</dl></section>
      <section><h3>Measurement method</h3>{typeof report.measurement === 'string' ? <p>{report.measurement}</p> : <dl>{Object.entries(report.measurement).map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>}
        <p className="muted">RSS is a process-lifetime peak including imports; it is an eligibility measurement, not OS-enforced memory isolation.</p>
      </section></div>
    <details><summary>Environment and raw repeated samples</summary><pre>{JSON.stringify({environment: report.environment, results: report.results}, null, 2)}</pre></details>
  </>;
}
