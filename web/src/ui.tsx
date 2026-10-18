import type {Policy} from './api';

export function Metric({label, value}: {label: string; value: string}) {return <div className="metric"><p>{label}</p><strong>{value}</strong></div>;}
export function Empty({children}: {children: React.ReactNode}) {return <div className="empty"><p className="eyebrow">READY WHEN YOU ARE</p><p>{children}</p></div>;}
export const statusLabel = (status: string) => ({'completed-without-improvement': 'Parent policy retained', 'awaiting-approval': 'Awaiting approval', 'promoted': 'Policy promoted'}[status] || status.replaceAll('-', ' '));
export function JsonDetails({title, value}: {title: string; value: unknown}) {return <details><summary>{title}</summary><pre>{JSON.stringify(value, null, 2)}</pre></details>;}
export const describe = (policy: Partial<Policy>) => `${policy.codec}${policy.codec === 'zstd' ? ` · level ${policy.zstd_level}` : ''}${policy.dictionary_id ? ' · dictionary' : ''}${policy.solid_block_bytes ? ` · ${policy.solid_block_bytes / 1048576} MiB solid` : ''}${policy.metadata_encoding && policy.metadata_encoding !== 'json' ? ` · ${policy.metadata_encoding} metadata` : ''}`;
