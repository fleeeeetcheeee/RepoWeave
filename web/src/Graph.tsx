import type { Graph as GraphData } from './api';

export function Graph({graph, visited = [], active = '', title}: {
  graph: GraphData; visited?: string[]; active?: string; title: string;
}) {
  const nodes = graph.nodes.slice(0, 60);
  const columns = nodes.length > 8 ? 4 : 3;
  const position = (name: string) => {
    const index = nodes.indexOf(name);
    return {x: 22 + (index % columns) * 235, y: 30 + Math.floor(index / columns) * 95};
  };
  return <div className="graph-scroll">
    <svg role="img" aria-label={title} viewBox={`0 0 ${columns * 235 + 20} ${Math.max(115, Math.ceil(nodes.length / columns) * 95 + 20)}`}>
      <title>{title}</title>
      <defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#859593" /></marker></defs>
      {graph.edges.filter(e => nodes.includes(e.source) && nodes.includes(e.target)).map((e, i) => {
        const a = position(e.source), b = position(e.target);
        return <path key={i} d={`M ${a.x + 100} ${a.y + 44} C ${a.x + 100} ${a.y + 75}, ${b.x + 100} ${b.y - 25}, ${b.x + 100} ${b.y}`}
          fill="none" stroke="#859593" strokeWidth="1.4" markerEnd="url(#arrow)" />;
      })}
      {nodes.map(name => {const p = position(name); return <g key={name}>
        <rect x={p.x} y={p.y} width="202" height="44" rx="8" className={name === active ? 'node active' : visited.includes(name) ? 'node visited' : 'node'} />
        <text x={p.x + 12} y={p.y + 27}>{name.length > 25 ? name.slice(0, 23) + '…' : name}</text>
        <title>{name}{visited.includes(name) ? ' · visited' : ''}</title>
      </g>;})}
    </svg>
    {graph.nodes.length > nodes.length && <p className="muted">Showing {nodes.length} of {graph.nodes.length} nodes. All edges remain available in the table.</p>}
    <details><summary>Inspect graph edges ({graph.edges.length})</summary>
      <ul className="edge-list">{graph.edges.map((e, i) => <li key={i}><code>{e.source}</code> → <code>{e.target}</code> {e.kind}</li>)}</ul>
    </details>
  </div>;
}
