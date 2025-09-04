n8n Diagnoser (browser helper)

Use in n8n Cloud browser console to diagnose why external runner (Selenium/Playwright/etc.) is not starting.

Load

In the browser devtools console, paste:

```js
// If bundling is not available, you can paste the content of browser/n8nRun.js.
// If hosted as an asset, load via:
// const s = document.createElement('script');
// s.src = 'https://your-host/n8n-diagnoser/n8nRun.js';
// document.head.appendChild(s);
```

After loading, `window.n8nRun` is available.

Configure

```js
n8nRun.configure({
  n8nBaseUrl: location.origin,
  apiKey: null, // or your X-N8N-API-KEY
  runnerBaseUrl: 'https://runner.example.com',
  runnerToken: 'YOUR_TOKEN',
  runnerHealthPath: '/health',
  runnerJobsPath: '/jobs'
});
```

Commands

- `n8nRun.quickDiagnose()`
  - Shows IF re-evaluation vs runtime branch, BFS trace, stop node, runner candidates, health.
- `n8nRun.whereStopped({ source: 'snapshot' | 'current' })`
- `n8nRun.traceFromIf(outputIndex = 0, { source })`
- `n8nRun.detectRunnerNodes({ source })`
- `n8nRun.checkRunnerHealth()`
- `n8nRun.dryRunRunner(payload)`

Output

- Console tables with `name`, `type`, `reachable`, `executed`, `items`, `reason`.
- Reasons include: `no items from upstream output`, `HTTP 4xx/5xx`, `disabled`, `waiting`, `error`.
- Recommendations printed when mismatches or failures are detected.

Notes

- Snapshot prefers execution-time workflow; falls back to current workflow then nodeExecutionStack.
- IF evaluator normalizes true/false and unwraps `{{$json...}}` minimal paths.
- n8n Cloud may restrict Execute Command nodes; prefer HTTP-based runners.
