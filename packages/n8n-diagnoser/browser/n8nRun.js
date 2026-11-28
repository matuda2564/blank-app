(function initN8nDiagnoser() {
  if (typeof window === 'undefined') return;

  const DEFAULT_CONFIG = {
    n8nBaseUrl: typeof location !== 'undefined' ? location.origin : '',
    apiKey: null,
    fetchTimeoutMs: 15000,
    runnerBaseUrl: null,
    runnerHealthPath: '/health',
    runnerJobsPath: '/jobs',
    runnerAuthHeader: 'Authorization',
    runnerAuthScheme: 'Bearer',
  };

  function withTimeout(promise, ms, label) {
    const timeout = new Promise((_, reject) => {
      const id = setTimeout(() => reject(new Error(`${label || 'Request'} timed out after ${ms}ms`)), ms);
      timeout.clear = () => clearTimeout(id);
    });
    return Promise.race([promise, timeout]).finally(() => {
      if (timeout.clear) timeout.clear();
    });
  }

  function deepGet(obj, path) {
    if (!obj || !path) return undefined;
    const parts = Array.isArray(path) ? path : String(path).replace(/\[(\d+)\]/g, '.$1').split('.');
    let current = obj;
    for (const key of parts) {
      if (current == null) return undefined;
      const k = key.replace(/^['"]|['"]$/g, '');
      current = current[k];
    }
    return current;
  }

  function normalizeValue(value) {
    if (typeof value === 'string') {
      const trimmed = value.trim();
      if (trimmed === 'true') return true;
      if (trimmed === 'false') return false;
      if (trimmed === 'null') return null;
      if (trimmed === 'undefined') return undefined;
      if (!Number.isNaN(Number(trimmed)) && trimmed !== '') return Number(trimmed);
      return trimmed;
    }
    return value;
  }

  function unwrapExpression(value, itemJson) {
    if (typeof value !== 'string') return value;
    const str = value.trim();
    if (str.startsWith('={{') && str.endsWith('}}')) {
      const expr = str.slice(3, -2).trim();
      // Very small evaluator: supports $json.xxx or $json['xxx'] path lookups
      const jsonMatch = expr.match(/^\$json(\..+|\[['"].+['"]\].*)?$/);
      if (jsonMatch) {
        const rawPath = expr.replace(/^\$json\.?/, '');
        const path = rawPath
          .replace(/^\[(.*)\]$/, '$1')
          .replace(/\[(["'])(.*?)\1\]/g, '.$2')
          .replace(/^\./, '');
        if (!path) return itemJson;
        return deepGet(itemJson, path.split('.'));
      }
      // Fallback: do not eval arbitrary code for safety
      return undefined;
    }
    return value;
  }

  class N8NClient {
    constructor(config) {
      this.config = config;
    }

    _headers() {
      const headers = { 'Content-Type': 'application/json' };
      if (this.config.apiKey) headers['X-N8N-API-KEY'] = this.config.apiKey;
      return headers;
    }

    async _request(path, options = {}) {
      const url = (this.config.n8nBaseUrl || '').replace(/\/$/, '') + path;
      const fetchOpts = Object.assign(
        {
          credentials: 'include',
          headers: this._headers(),
        },
        options
      );
      const start = typeof performance !== 'undefined' ? performance.now() : Date.now();
      const resp = await withTimeout(fetch(url, fetchOpts), this.config.fetchTimeoutMs, `GET ${path}`);
      const ms = (typeof performance !== 'undefined' ? performance.now() : Date.now()) - start;
      const text = await resp.text();
      let data;
      try { data = text ? JSON.parse(text) : null; } catch { data = text; }
      return { status: resp.status, ok: resp.ok, ms, data, headers: resp.headers };
    }

    async getExecutions(limit = 10, includeData = true) {
      const q = `?limit=${encodeURIComponent(limit)}${includeData ? '&includeData=true' : ''}`;
      return this._request(`/rest/executions${q}`, { method: 'GET' });
    }

    async getExecution(executionId, includeData = true) {
      const q = includeData ? '?includeData=true' : '';
      return this._request(`/rest/executions/${encodeURIComponent(executionId)}${q}`, { method: 'GET' });
    }

    async getWorkflow(workflowId) {
      return this._request(`/rest/workflows/${encodeURIComponent(workflowId)}`, { method: 'GET' });
    }
  }

  class SnapshotBuilder {
    constructor(execution, workflowCurrent) {
      this.execution = execution || {};
      this.workflowCurrent = workflowCurrent || {};
    }

    getExecutionWorkflowData() {
      const ex = this.execution;
      const wf =
        ex.workflowData ||
        ex.data?.workflowData ||
        ex.data?.executionData?.workflowData ||
        ex.executionData?.workflowData ||
        null;
      return wf || null;
    }

    getExecutionRunData() {
      const ex = this.execution;
      return (
        ex.data?.resultData?.runData ||
        ex.data?.executionData?.resultData?.runData ||
        ex.resultData?.runData ||
        ex.executionData?.resultData?.runData ||
        null
      );
    }

    getNodeExecutionStack() {
      const ex = this.execution;
      return (
        ex.data?.nodeExecutionStack ||
        ex.data?.executionData?.nodeExecutionStack ||
        ex.nodeExecutionStack ||
        ex.executionData?.nodeExecutionStack ||
        []
      );
    }

    buildNodesSnapshot(prefer = 'snapshot') {
      const wfExec = this.getExecutionWorkflowData();
      const nodesFromExec = wfExec?.nodes;
      const nodesFromCurrent = this.workflowCurrent?.data?.nodes || this.workflowCurrent?.nodes || [];
      let nodes = [];
      if (prefer === 'snapshot' && Array.isArray(nodesFromExec)) nodes = nodesFromExec;
      if (!Array.isArray(nodes) || nodes.length === 0) nodes = nodesFromCurrent;

      // Fallback: reconstruct from nodeExecutionStack
      if (!Array.isArray(nodes) || nodes.length === 0) {
        const stack = this.getNodeExecutionStack();
        const seen = new Map();
        for (const frame of Array.isArray(stack) ? stack : []) {
          const node = frame?.node;
          if (node && node.name && !seen.has(node.name)) seen.set(node.name, node);
        }
        nodes = Array.from(seen.values());
      }
      return nodes || [];
    }

    buildConnectionsSnapshot(prefer = 'snapshot') {
      const wfExec = this.getExecutionWorkflowData();
      const connsFromExec = wfExec?.connections;
      const connsFromCurrent = this.workflowCurrent?.data?.connections || this.workflowCurrent?.connections || {};
      let connections = {};
      if (prefer === 'snapshot' && connsFromExec && typeof connsFromExec === 'object') connections = connsFromExec;
      if (!connections || Object.keys(connections).length === 0) connections = connsFromCurrent || {};
      return connections || {};
    }
  }

  class RunDataHelper {
    constructor(runData) {
      this.runData = runData || {};
    }

    listExecutedNodeNames() {
      return Object.keys(this.runData || {});
    }

    getNodeExecutions(nodeName) {
      return this.runData?.[nodeName] || [];
    }

    nodeExecuted(nodeName) {
      const arr = this.getNodeExecutions(nodeName);
      if (!Array.isArray(arr) || arr.length === 0) return false;
      return arr.some(e => {
        if (!e) return false;
        if (e.error) return true;
        const data = e.data;
        const main = data?.main;
        if (Array.isArray(main)) {
          for (const out of main) {
            if (Array.isArray(out) && out.length > 0) return true;
          }
        }
        if (e.startedAt || e.stoppedAt || e.startTime || e.executionTime) return true;
        return false;
      });
    }

    countOutputItems(nodeName) {
      const arr = this.getNodeExecutions(nodeName);
      let count = 0;
      for (const e of arr) {
        const main = e?.data?.main;
        if (!Array.isArray(main)) continue;
        for (const out of main) {
          if (Array.isArray(out)) count += out.length;
        }
      }
      return count;
    }

    countOutputItemsForPort(nodeName, outputIndex) {
      const arr = this.getNodeExecutions(nodeName);
      let count = 0;
      for (const e of arr) {
        const main = e?.data?.main;
        if (!Array.isArray(main)) continue;
        const out = main[outputIndex];
        if (Array.isArray(out)) count += out.length;
      }
      return count;
    }

    getLastHttpStatus(nodeName) {
      const arr = this.getNodeExecutions(nodeName);
      for (let i = arr.length - 1; i >= 0; i -= 1) {
        const e = arr[i];
        const code = e?.data?.response?.statusCode ?? e?.data?.httpResponse?.status ?? e?.data?.statusCode ?? e?.data?.code ?? e?.httpCode;
        if (typeof code === 'number') return code;
        if (typeof code === 'string' && !Number.isNaN(Number(code))) return Number(code);
      }
      return undefined;
    }

    hasError(nodeName) {
      const arr = this.getNodeExecutions(nodeName);
      return arr.some(e => Boolean(e?.error));
    }

    getFirstInputItemForNode(nodeName) {
      // Search previous node execution's first output that feeds this node
      // Not trivial without connections; this function is a placeholder to feed If re-eval with something
      const arr = this.getNodeExecutions(nodeName);
      if (Array.isArray(arr) && arr.length > 0) {
        const first = arr[0];
        const inputMain = first?.data?.main;
        if (Array.isArray(inputMain) && inputMain[0] && Array.isArray(inputMain[0]) && inputMain[0][0]) {
          return inputMain[0][0].json ?? {};
        }
      }
      // Fallback: scan any node for some output item
      for (const name of Object.keys(this.runData || {})) {
        const list = this.runData[name];
        for (const e of list) {
          const main = e?.data?.main;
          if (Array.isArray(main) && main[0] && Array.isArray(main[0]) && main[0][0]) {
            return main[0][0].json ?? {};
          }
        }
      }
      return {};
    }
  }

  function normalizeIfParameters(node) {
    const params = node?.parameters || {};
    // New UI builder
    if (params.conditions && Array.isArray(params.conditions)) {
      const combinator = params.combinator || params.aggregate || 'AND';
      return { kind: 'new', combinator: String(combinator).toUpperCase(), conditions: params.conditions };
    }
    // Old UI
    const ui = params.conditionsUi || {};
    if (Array.isArray(ui.conditions)) {
      const combinator = ui.combineOperation === 'any' ? 'OR' : 'AND';
      const mapped = ui.conditions.map(c => ({
        leftValue: c.value1,
        operation: c.operation,
        rightValue: c.value2,
      }));
      return { kind: 'old', combinator, conditions: mapped };
    }
    return { kind: 'unknown', combinator: 'AND', conditions: [] };
  }

  function evaluateIfNode(node, inputJson) {
    const conf = normalizeIfParameters(node);
    const results = [];
    for (const cond of conf.conditions) {
      const leftRaw = unwrapExpression(cond.leftValue, inputJson);
      const rightRaw = unwrapExpression(cond.rightValue, inputJson);
      const left = normalizeValue(leftRaw);
      const right = normalizeValue(rightRaw);
      const op = String(cond.operation || '').toLowerCase();
      let pass = false;
      if (op === 'true') pass = Boolean(left) === true;
      else if (op === 'false' || op === 'isfalse') pass = Boolean(left) === false;
      else if (op === 'isnottrue') pass = Boolean(left) !== true;
      else if (op === 'isnotfalse') pass = Boolean(left) !== false;
      else if (op === 'equal' || op === 'equals') pass = left == right; // loose
      else if (op === 'notequal' || op === 'notEquals' || op === 'not_equals') pass = left != right;
      else if (op === 'isempty') pass = (left == null || String(left).trim() === '');
      else if (op === 'isnotempty') pass = !(left == null || String(left).trim() === '');
      else pass = false;
      results.push(pass);
    }
    const all = conf.combinator === 'AND';
    const final = results.length === 0 ? false : (all ? results.every(Boolean) : results.some(Boolean));
    return final ? 0 : 1; // 0 => true branch, 1 => false branch
  }

  class GraphTracer {
    constructor(nodes, connections, runDataHelper) {
      this.nodesByName = new Map();
      for (const n of nodes || []) this.nodesByName.set(n.name, n);
      this.connections = connections || {};
      this.runDataHelper = runDataHelper;
    }

    nextNodes(nodeName, outputIndex) {
      const conns = this.connections?.[nodeName]?.main || [];
      const outs = typeof outputIndex === 'number' ? [outputIndex] : conns.map((_, idx) => idx);
      const next = new Set();
      for (const idx of outs) {
        const arr = conns[idx] || [];
        for (const link of arr) {
          if (link?.node) next.add(link.node);
        }
      }
      return Array.from(next);
    }

    bfsFrom(nodeName, outputIndex) {
      const rows = [];
      const queue = [];
      const visited = new Set();
      const inbound = new Map(); // nodeName -> Set of parents
      if (!nodeName) return { order: rows, inbound };
      const startNeighbors = this.nextNodes(nodeName, outputIndex);
      for (const n of startNeighbors) {
        queue.push(n);
        inbound.set(n, new Set([nodeName]));
      }
      visited.add(nodeName);
      while (queue.length > 0) {
        const cur = queue.shift();
        if (visited.has(cur)) continue;
        visited.add(cur);
        const children = this.nextNodes(cur);
        for (const c of children) {
          if (!inbound.has(c)) inbound.set(c, new Set());
          inbound.get(c).add(cur);
          if (!visited.has(c)) queue.push(c);
        }
        rows.push(cur);
      }
      return { order: rows, inbound };
    }

    buildTraceRows(fromNode, outputIndex) {
      const { order, inbound } = this.bfsFrom(fromNode, outputIndex);
      const result = [];
      const firstHop = new Set(this.nextNodes(fromNode, outputIndex));
      for (const name of order) {
        const node = this.nodesByName.get(name) || {};
        const executed = this.runDataHelper ? this.runDataHelper.nodeExecuted(name) : false;
        const items = this.runDataHelper ? this.runDataHelper.countOutputItems(name) : 0;
        const reachable = true;
        let reason = executed ? '' : 'not executed';
        // enrich reasons
        if (!executed && node.disabled) reason = 'disabled';
        if (!executed && /wait/i.test(node.type || node.displayName || '')) reason = 'waiting';
        if (!executed && firstHop.has(name) && this.runDataHelper && typeof outputIndex === 'number') {
          const zero = this.runDataHelper.countOutputItemsForPort(fromNode, outputIndex) === 0;
          if (zero) reason = 'no items from upstream output';
        }
        if (executed && this.runDataHelper) {
          const status = this.runDataHelper.getLastHttpStatus(name);
          if (typeof status === 'number' && status >= 400) {
            reason = `HTTP ${status}`;
          } else if (this.runDataHelper.hasError(name)) {
            reason = 'error';
          }
        }
        result.push({ name, type: node.type, reachable, executed, items, reason });
      }
      return result;
    }

    findStopNode(fromNode, outputIndex) {
      const { order, inbound } = this.bfsFrom(fromNode, outputIndex);
      for (const name of order) {
        const executed = this.runDataHelper?.nodeExecuted(name) || false;
        if (executed) continue;
        const parents = Array.from(inbound.get(name) || []);
        const hasExecutedUpstream = parents.some(p => this.runDataHelper?.nodeExecuted(p));
        if (hasExecutedUpstream) {
          return name;
        }
      }
      return undefined;
    }
  }

  class RunnerClient {
    constructor(config) {
      this.config = config;
    }

    _headers() {
      const headers = { 'Content-Type': 'application/json' };
      const token = this.config.runnerToken;
      if (token) headers[this.config.runnerAuthHeader || 'Authorization'] = `${this.config.runnerAuthScheme || 'Bearer'} ${token}`;
      return headers;
    }

    async _request(path, init) {
      const base = (this.config.runnerBaseUrl || '').replace(/\/$/, '');
      if (!base) throw new Error('Runner base URL not configured');
      const url = base + path;
      const start = typeof performance !== 'undefined' ? performance.now() : Date.now();
      const resp = await withTimeout(fetch(url, { ...init, headers: { ...(init?.headers || {}), ...this._headers() } }), this.config.fetchTimeoutMs || 15000, `${init?.method || 'GET'} ${path}`);
      const ms = (typeof performance !== 'undefined' ? performance.now() : Date.now()) - start;
      const text = await resp.text();
      let data;
      try { data = text ? JSON.parse(text) : null; } catch { data = text; }
      return { status: resp.status, ok: resp.ok, ms, data, headers: resp.headers };
    }

    async health() {
      return this._request(this.config.runnerHealthPath || '/health', { method: 'GET' });
    }

    async fireJob(payload) {
      const body = payload ? JSON.stringify(payload) : '{}';
      return this._request(this.config.runnerJobsPath || '/jobs', { method: 'POST', body });
    }
  }

  class BrowserDiagnoser {
    constructor() {
      this.config = { ...DEFAULT_CONFIG };
      this.client = new N8NClient(this.config);
    }

    configure(overrides) {
      this.config = { ...this.config, ...(overrides || {}) };
      this.client = new N8NClient(this.config);
      return this.config;
    }

    async _loadLatestExecutionWithWorkflow() {
      const exListResp = await this.client.getExecutions(25, true);
      if (!exListResp.ok) throw new Error(`Failed to list executions: HTTP ${exListResp.status}`);
      const list = Array.isArray(exListResp.data?.data) ? exListResp.data.data : Array.isArray(exListResp.data?.executions) ? exListResp.data.executions : [];
      if (!Array.isArray(list) || list.length === 0) throw new Error('No recent executions found');
      const sorted = list.slice().sort((a, b) => new Date(b.startedAt || b.startTime || 0) - new Date(a.startedAt || a.startTime || 0));
      const latestMeta = sorted[0];
      const exId = latestMeta?.id || latestMeta?._id || latestMeta?.executionId || latestMeta?.data?.id;
      const workflowId = latestMeta?.workflowId || latestMeta?.workflow?.id || latestMeta?.workflowData?.id;
      const exResp = await this.client.getExecution(exId, true);
      if (!exResp.ok) throw new Error(`Failed to fetch execution ${exId}: HTTP ${exResp.status}`);
      let wfCurrent = null;
      if (workflowId != null) {
        const wfResp = await this.client.getWorkflow(workflowId);
        if (wfResp.ok) wfCurrent = wfResp.data;
      }
      return { execution: exResp.data?.data || exResp.data, workflowCurrent: wfCurrent?.data || wfCurrent };
    }

    _findLastIfNodeName(runData, nodes) {
      const rd = runData || {};
      const candidates = Object.keys(rd);
      const byName = new Map();
      for (const n of nodes || []) byName.set(n.name, n);
      let last = null;
      for (const name of candidates) {
        const n = byName.get(name) || {};
        const t = String(n.type || '').toLowerCase();
        const dn = String(n.displayName || '').toLowerCase();
        if (t.includes('if') || dn === 'if') {
          last = name; // last as we iterate; order from Object.keys is arbitrary; refine below
        }
      }
      if (!last) return undefined;
      // refine by latest stoppedAt
      let best = last;
      let bestTs = 0;
      const helper = new RunDataHelper(rd);
      for (const name of candidates) {
        const n = byName.get(name) || {};
        const t = String(n.type || '').toLowerCase();
        const dn = String(n.displayName || '').toLowerCase();
        if (!(t.includes('if') || dn === 'if')) continue;
        const arr = helper.getNodeExecutions(name);
        for (const e of arr) {
          const ts = new Date(e.stoppedAt || e.startedAt || 0).getTime();
          if (ts > bestTs) { bestTs = ts; best = name; }
        }
      }
      return best;
    }

    async nextNodes(nodeName, outputIndex) {
      const { execution, workflowCurrent } = await this._loadLatestExecutionWithWorkflow();
      const sb = new SnapshotBuilder(execution, workflowCurrent);
      const nodes = sb.buildNodesSnapshot('snapshot');
      const connections = sb.buildConnectionsSnapshot('snapshot');
      const tracer = new GraphTracer(nodes, connections, null);
      return tracer.nextNodes(nodeName, outputIndex);
    }

    async traceFromIf(outputIndex = 0, opts = { source: 'snapshot' }) {
      const { execution, workflowCurrent } = await this._loadLatestExecutionWithWorkflow();
      const sb = new SnapshotBuilder(execution, workflowCurrent);
      const nodes = sb.buildNodesSnapshot(opts?.source === 'current' ? 'current' : 'snapshot');
      const connections = sb.buildConnectionsSnapshot(opts?.source === 'current' ? 'current' : 'snapshot');
      const runData = sb.getExecutionRunData() || {};
      const helper = new RunDataHelper(runData);
      const startIf = this._findLastIfNodeName(runData, nodes);
      const tracer = new GraphTracer(nodes, connections, helper);
      const rows = tracer.buildTraceRows(startIf, outputIndex);
      const actualBranch = this._computeActualIfBranch(startIf, tracer, helper);
      console.group(`Trace from IF: ${startIf || 'unknown'} (expected output ${outputIndex}, actual ${actualBranch ?? 'unknown'})`);
      console.table(rows);
      console.groupEnd();
      return rows;
    }

    async whereStopped(opts = { source: 'snapshot' }) {
      const { execution, workflowCurrent } = await this._loadLatestExecutionWithWorkflow();
      const sb = new SnapshotBuilder(execution, workflowCurrent);
      const nodes = sb.buildNodesSnapshot(opts?.source === 'current' ? 'current' : 'snapshot');
      const connections = sb.buildConnectionsSnapshot(opts?.source === 'current' ? 'current' : 'snapshot');
      const runData = sb.getExecutionRunData() || {};
      const helper = new RunDataHelper(runData);
      const startIf = this._findLastIfNodeName(runData, nodes);
      const tracer = new GraphTracer(nodes, connections, helper);
      const rows = tracer.buildTraceRows(startIf, typeof opts.outputIndex === 'number' ? opts.outputIndex : 0);
      const stopAt = tracer.findStopNode(startIf, typeof opts.outputIndex === 'number' ? opts.outputIndex : 0);
      console.group(`Where stopped (from IF ${startIf || 'unknown'})`);
      if (stopAt) console.log('Stop at:', stopAt);
      else console.log('No stop detected on reachable path');
      console.table(rows);
      console.groupEnd();
      return { stopAt, rows };
    }

    async detectRunnerNodes(opts = { source: 'snapshot' }) {
      const { execution, workflowCurrent } = await this._loadLatestExecutionWithWorkflow();
      const sb = new SnapshotBuilder(execution, workflowCurrent);
      const nodes = sb.buildNodesSnapshot(opts?.source === 'current' ? 'current' : 'snapshot');
      const summaries = [];
      for (const n of nodes) {
        const type = String(n.type || '').toLowerCase();
        const name = n.name;
        const p = n.parameters || {};
        const lowerName = String(name || '').toLowerCase();
        const isHttp = type.includes('http');
        const isExec = type.includes('execute') || type.includes('command');
        const isCode = type.includes('code') || type.includes('function');
        const isWebhook = type.includes('webhook');
        const isQueue = type.includes('queue') || type.includes('kafka') || type.includes('rabbit');
        const isRunnerLike = /runner|selenium|webdriver|playwright|worker/.test(type + ' ' + lowerName);
        if (isHttp || isExec || isCode || isWebhook || isQueue || isRunnerLike) {
          const summary = { name, type: n.type, kind: null, details: {} };
          if (isHttp) {
            summary.kind = 'http';
            summary.details.method = p.method || p.requestMethod || 'GET';
            summary.details.url = p.url || p.endpoint || p.path || '';
          } else if (isExec) {
            summary.kind = 'exec';
            summary.details.command = p.command || p.shell || '';
            summary.details.args = p.parameters || p.args || '';
          } else if (isCode) {
            summary.kind = 'code';
            const code = p.functionCode || p.jsCode || p.code || '';
            summary.details.contains = [
              /selenium/i.test(code) ? 'selenium' : null,
              /webdriver/i.test(code) ? 'webdriver' : null,
              /playwright/i.test(code) ? 'playwright' : null,
            ].filter(Boolean);
          } else if (isWebhook) {
            summary.kind = 'webhook';
            summary.details.path = p.path || p.endpoint || '';
            summary.details.method = p.httpMethod || p.method || 'GET';
          } else if (isQueue) {
            summary.kind = 'queue';
            summary.details.queue = p.queue || p.topic || p.channel || '';
          } else if (isRunnerLike) {
            summary.kind = 'runner-like';
          }
          summaries.push(summary);
        }
      }
      console.group('Runner candidates');
      console.table(summaries.map(s => ({ name: s.name, type: s.type, kind: s.kind, ...s.details })));
      console.groupEnd();
      return summaries;
    }

    async checkRunnerHealth() {
      const rc = new RunnerClient(this.config);
      try {
        const res = await rc.health();
        if (!res.ok) {
          const hint = res.status === 401 ? 'Unauthorized: Set a valid token in config (Authorization header)' : res.status === 404 ? 'Health endpoint not found: verify runnerHealthPath' : 'Check runner service availability and CORS';
          console.error('Runner health failed', { status: res.status, ms: res.ms, data: res.data, hint });
        } else {
          console.log('Runner health OK', { status: res.status, ms: Math.round(res.ms), data: res.data });
        }
        return res;
      } catch (err) {
        console.error('Runner health error', err);
        throw err;
      }
    }

    async dryRunRunner(payload = { ping: 'n8n-diagnoser' }) {
      const rc = new RunnerClient(this.config);
      try {
        const res = await rc.fireJob(payload);
        if (!res.ok) {
          const hint = res.status === 401 ? 'Unauthorized: Set token' : res.status === 404 ? 'Jobs endpoint not found: verify runnerJobsPath' : 'Inspect runner logs and CORS';
          console.error('Runner fire failed', { status: res.status, ms: res.ms, data: res.data, hint });
        } else {
          console.log('Runner fire OK', { status: res.status, ms: Math.round(res.ms), data: res.data });
        }
        return res;
      } catch (err) {
        console.error('Runner fire error', err);
        throw err;
      }
    }

    async quickDiagnose() {
      const out = {};
      try {
        const { execution, workflowCurrent } = await this._loadLatestExecutionWithWorkflow();
        const sb = new SnapshotBuilder(execution, workflowCurrent);
        const nodes = sb.buildNodesSnapshot('snapshot');
        const conns = sb.buildConnectionsSnapshot('snapshot');
        const runData = sb.getExecutionRunData() || {};
        const helper = new RunDataHelper(runData);
        const lastIf = this._findLastIfNodeName(runData, nodes);
        let outputIndex = 0;
        if (lastIf) {
          const inputJson = helper.getFirstInputItemForNode(lastIf);
          const ifNode = nodes.find(n => n.name === lastIf) || {};
          try { outputIndex = evaluateIfNode(ifNode, inputJson); } catch { outputIndex = 0; }
        }
        const tracer = new GraphTracer(nodes, conns, helper);
        const rows = tracer.buildTraceRows(lastIf, outputIndex);
        const stopAt = tracer.findStopNode(lastIf, outputIndex);
        const actualBranch = this._computeActualIfBranch(lastIf, tracer, helper);
        out.ifNode = lastIf;
        out.ifOutput = outputIndex;
        out.ifActual = actualBranch;
        out.rows = rows;
        out.stopAt = stopAt;
        console.group('n8n quickDiagnose');
        console.log('IF node:', lastIf, 'expected output:', outputIndex, 'actual:', actualBranch);
        console.table(rows);
        if (stopAt) console.warn('Stopped at:', stopAt);
        if (lastIf && actualBranch != null && outputIndex !== actualBranch) {
          console.warn('IF branch mismatch between re-eval and runtime branch');
        }
        // Recommendations
        const recs = [];
        if (!this.config.runnerBaseUrl) recs.push('Set runnerBaseUrl and runnerToken then re-run checkRunnerHealth');
        if (lastIf && actualBranch != null && outputIndex !== actualBranch) recs.push('Review IF conditions and input values (trim/unwrap), ensure isFalse normalization');
        const firstRow = rows[0];
        if (firstRow && firstRow.reason === 'no items from upstream output') recs.push('IF output produced 0 items; verify condition and input payload');
        for (const r of rows) {
          if (r.reason && /^HTTP\s+401/.test(r.reason)) recs.push('HTTP 401: Configure Authorization header/token for the target');
          if (r.reason && /^HTTP\s+403/.test(r.reason)) recs.push('HTTP 403: Check permissions or IP allowlist on target');
          if (!r.executed && r.reason === 'disabled') recs.push(`Enable node ${r.name}`);
          if (!r.executed && r.reason === 'waiting') recs.push(`Node ${r.name} is waiting; resume or configure timeout`);
        }
        if (recs.length > 0) {
          console.group('Recommendations');
          recs.forEach(x => console.log('- ' + x));
          console.groupEnd();
        }
        console.groupEnd();
      } catch (err) {
        console.error('quickDiagnose error', err);
        out.error = err?.message || String(err);
      }

      // Runner side
      try {
        const candidates = await this.detectRunnerNodes({ source: 'snapshot' });
        out.runnerCandidates = candidates;
      } catch (err) {
        console.error('detectRunnerNodes error', err);
      }

      try {
        if (this.config.runnerBaseUrl) {
          await this.checkRunnerHealth();
        } else {
          console.info('Runner base URL not configured. Call window.n8nRun.configure({ runnerBaseUrl: "https://runner" , runnerToken: "..." })');
        }
      } catch {}

      return out;
    }

    _computeActualIfBranch(ifNodeName, tracer, helper) {
      if (!ifNodeName || !tracer) return undefined;
      const nextTrue = tracer.nextNodes(ifNodeName, 0) || [];
      const nextFalse = tracer.nextNodes(ifNodeName, 1) || [];
      const anyTrue = nextTrue.some(n => helper.nodeExecuted(n));
      const anyFalse = nextFalse.some(n => helper.nodeExecuted(n));
      if (anyTrue && !anyFalse) return 0;
      if (anyFalse && !anyTrue) return 1;
      if (!anyTrue && !anyFalse) return undefined;
      // both true/false next nodes executed: ambiguous parallel paths
      return undefined;
    }
  }

  const singleton = new BrowserDiagnoser();

  const api = {
    configure: (cfg) => singleton.configure(cfg),
    nextNodes: (nodeName, outputIndex = 0) => singleton.nextNodes(nodeName, outputIndex),
    traceFromIf: (outputIndex = 0, opts) => singleton.traceFromIf(outputIndex, opts),
    detectRunnerNodes: (opts) => singleton.detectRunnerNodes(opts),
    checkRunnerHealth: () => singleton.checkRunnerHealth(),
    whereStopped: (opts) => singleton.whereStopped(opts),
    dryRunRunner: (payload) => singleton.dryRunRunner(payload),
    quickDiagnose: () => singleton.quickDiagnose(),
    // Expose internals (read-only)
    _internals: { N8NClient, SnapshotBuilder, RunDataHelper, GraphTracer }
  };

  window.n8nRun = Object.freeze(api);
})();

