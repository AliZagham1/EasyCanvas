import { safeSourceUrl, sourceLabel, studentSourceLocation } from "@/lib/assistant";
import type { AssistantSource } from "@/lib/assistant";

type Origin = AssistantSource["origins"][number];

export default function AssistantSources({ sources, messageId, courseLabel }: {
  sources: AssistantSource[]; messageId: string; courseLabel: string;
}) {
  if (!sources.length) return null;
  // One document row can support several passage IDs without losing provenance.
  const groups = new Map<string, { origin: Origin; ids: Set<string>; locations: Origin["locations"] }>();
  for (const source of sources) for (const origin of source.origins) {
    const key = `${origin.document_key}:${origin.url || ""}`;
    const group = groups.get(key) || { origin, ids: new Set<string>(), locations: [] };
    group.ids.add(source.id); group.locations.push(...origin.locations); groups.set(key, group);
  }
  const anchored = new Set<string>();
  return <details className="conversation-sources">
    <summary>Sources ({groups.size})</summary>
    <ul>{[...groups].map(([key, group]) => {
      const url = safeSourceUrl(group.origin.url);
      const title = sourceLabel(group.origin, courseLabel);
      const location = studentSourceLocation(group.locations);
      return <li key={key}>
        <div className="conversation-source-ids">{[...group.ids].map((id) => {
          const first = !anchored.has(id); anchored.add(id);
          return <span key={id} className="conversation-source-id" id={first ? `${messageId}-${id}` : undefined} tabIndex={first ? -1 : undefined}>[{id}]</span>;
        })}</div>
        <div>
          {url ? <a className="text-link" href={url} target="_blank" rel="noopener noreferrer" aria-label={`${title} (opens in a new tab)`}>{title}</a> : <span>{title}</span>}
          {location && <p className="assistant-hint">{location}</p>}
        </div>
      </li>;
    })}</ul>
  </details>;
}
