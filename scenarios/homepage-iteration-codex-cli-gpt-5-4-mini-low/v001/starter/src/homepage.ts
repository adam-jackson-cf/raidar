export function renderHomepage(): string {
  return `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width,initial-scale=1" />
    <title>Raidar</title>
  </head>
  <body>
    <header>
      <nav aria-label="Primary">
        <a href="#hero">Raidar</a>
      </nav>
    </header>
    <main>
      <section id="hero">
        <h1>Ship reliable agentic workflows</h1>
        <p>Design, validate, and iterate with confidence.</p>
      </section>
      <section id="features">
        <h2>Core features</h2>
        <ul>
          <li>Deterministic scenario contracts</li>
          <li>Repeatable quality gates</li>
          <li>Benchmark-oriented iteration loops</li>
        </ul>
      </section>
      <section id="proof">
        <h2>Why teams trust Raidar</h2>
        <p>Teams standardize validation workflows and reduce benchmark variance.</p>
      </section>
      <section id="cta">
        <h2>Start your next benchmark cycle</h2>
        <a href="#" data-testid="primary-cta">Run benchmark</a>
      </section>
    </main>
    <footer>
      <small>© Raidar</small>
    </footer>
  </body>
</html>`;
}
