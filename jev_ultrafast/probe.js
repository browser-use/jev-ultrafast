payload => {
  // Read-only freshness probe.  It never rebuilds Ego's ref map, never takes a
  // snapshot, and never walks the document: it only answers "is this observed
  // target still the same live, usable element, in the same document?".
  const cache = window.__jevFast;
  const identity = {
    epoch: performance.timeOrigin,
    url: location.href,
    ready: document.readyState === 'complete',
  };
  const nodes = {};
  for (const id of (payload && payload.nodes) || []) {
    const e = cache && cache.nodes.get(id);
    if (!e || !e.isConnected) {
      nodes[String(id)] = null;
      continue;
    }
    const guard = cache.guard(e);
    const r = e.getBoundingClientRect(), x = r.x + r.width / 2, y = r.y + r.height / 2;
    const inViewport = r.width > 0 && r.height > 0 && x >= 0 && y >= 0 && x < innerWidth && y < innerHeight;
    nodes[String(id)] = {
      // Code-owned identity and control state, without nearby container text.
      guard: guard && guard.slice(0, 13),
      actionable: !!guard && !e.matches(':disabled') && !e.closest('[aria-disabled="true"],[inert]') &&
        inViewport && e.contains(document.elementFromPoint(x, y)),
      writable: !e.readOnly && e.getAttribute('aria-readonly') !== 'true',
    };
  }
  return {identity, nodes};
}