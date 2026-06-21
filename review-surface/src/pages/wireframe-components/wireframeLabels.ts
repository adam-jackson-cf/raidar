function compactHarness(raw: string) {
  const text = raw.toLowerCase();
  if (text.includes('codex-cli')) return 'Codex';
  if (text.includes('claude-code')) return 'Claude';
  if (text.includes('agentic')) return 'Agentic';
  if (text.includes('rag')) return 'RAG';
  return raw
    .split(/[^a-z0-9]/i)
    .filter(Boolean)
    .map((part) => part[0]?.toUpperCase() + part.slice(1).toLowerCase())
    .join('');
}

function compactModel(raw: string) {
  const text = raw.trim();
  const model = text.toLowerCase();
  const openAI = model.match(/gpt[\- ]?([0-9]+(?:\.[0-9]+)?)(?::|\s+)?([a-z]{1,})?/);
  if (openAI && openAI[1]) {
    const version = openAI[1];
    const quality = openAI[2]?.slice(0, 1).toUpperCase();
    return `GPT-${version}${quality ? `-${quality}` : ''}`;
  }

  const claude = model.match(/claude[-_]([a-z]+)[-_]?([0-9]+(?:\.[0-9]+)?)/);
  if (claude && claude[1] && claude[2]) {
    const fam = claude[1]?.[0]?.toUpperCase() ?? 'C';
    return `Claude-${fam}-${claude[2]}`;
  }

  const parts = text.split('/');
  const provider = (parts[0] ?? 'Model').slice(0, 3).toUpperCase();
  const name = (parts[1] ?? text).replace(/[^a-z0-9.]/gi, ' ');
  const token = name
    .split(/\s+/)
    .filter(Boolean)
    .map((segment, index) => (index === 0 ? segment[0]?.toUpperCase() + segment.slice(1) : segment.slice(0, 3)))
    .join('-')
    .slice(0, 12);
  return `${provider}-${token}`;
}

export function compactSpec(label: string) {
  const [harness = '', model = ''] = label.split('·').map((part) => part.trim());
  const suffix = compactModel(model);
  return `${compactHarness(harness)}-${suffix}`.replace(/-+$/, '');
}
