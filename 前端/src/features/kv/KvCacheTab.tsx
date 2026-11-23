import { useEffect, useState, type CSSProperties } from 'react';
import type { KvCacheSimulateResponse } from '../../api/contract';
import { simulateKvCache } from '../../api/client';
import { RangeSlider } from '../../components/RangeSlider';
import { runPreviewKvCache } from '../../lib/previewEngine';

const DEFAULT_MESSAGES = [
  {
    role: 'system' as const,
    content: [
      ...Array.from(
        { length: 6 },
        (_, i) =>
          `Policy clause ${i + 1}: production rollout window 2026-01-15 through 2026-06-30; verify Hangzhou failover drills before 2026-06-30 and log audit trace from 2026-01-15.`,
      ),
      'Provider key: sk-proj-TestDemoKey9876543210abcdef1234. Return exact exception root cause.',
    ].join('\n'),
  },
  {
    role: 'user' as const,
    content: 'Why did compute_margin fail on the Hangzhou worker batch?',
  },
  {
    role: 'tool' as const,
    name: 'db_tool',
    tool_call_id: 'call_db_01',
    content: JSON.stringify(
      Array.from({ length: 45 }, (_, i) =>
        i === 18
          ? { id: i, zone: 'hangzhou-a', status: 'error', msg: 'upstream 502 timeout on compute_margin' }
          : { id: i, zone: 'hangzhou-a', status: 'ok', msg: `health check passed worker-${i}` },
      ),
    ),
  },
  {
    role: 'tool' as const,
    name: 'python_tool',
    tool_call_id: 'call_tb_01',
    content: [
      'Traceback (most recent call last):',
      ...Array.from(
        { length: 120 },
        (_, i) =>
          `  File "/opt/app/workers/pipeline_stage_${i}.py", line ${100 + i}, in execute_step\n    return self.dispatch_downstream_handler(payload_batch_index=${i}, retry_budget=3)`,
      ),
      'ZeroDivisionError: division by zero in compute_margin',
    ].join('\n'),
  },
];

export function KvCacheTab({ isLive }: { isLive: boolean }) {
  const [runs, setRuns] = useState(20);
  const [keepRecent, setKeepRecent] = useState(2);
  const [redactSecrets, setRedactSecrets] = useState(true);
  const [query, setQuery] = useState('ZeroDivisionError compute_margin');
  const [busy, setBusy] = useState(false);
  const [simResult, setSimResult] = useState<KvCacheSimulateResponse>(() =>
    runPreviewKvCache({
      runs: 20,
      messages: DEFAULT_MESSAGES,
      keep_recent: 2,
      max_tokens_per_result: 1600,
      query: 'ZeroDivisionError compute_margin',
      redact_secrets: true,
    }),
  );

  useEffect(() => {
    if (!isLive) return;
    let cancelled = false;
    simulateKvCache({
      runs: 20,
      messages: DEFAULT_MESSAGES,
      keep_recent: 2,
      max_tokens_per_result: 1600,
      query: 'ZeroDivisionError compute_margin',
      redact_secrets: true,
    })
      .then((res) => {
        if (!cancelled) {
          setSimResult(res.data);
        }
      })
      .catch(() => {
        // Keep initial comparison when live backend call is aborted or unavailable
      });
    return () => {
      cancelled = true;
    };
  }, [isLive]);

  const handleRun = async () => {
    const req = {
      runs,
      messages: DEFAULT_MESSAGES,
      keep_recent: keepRecent,
      max_tokens_per_result: 1600,
      query,
      redact_secrets: redactSecrets,
    };
    if (!isLive) {
      setSimResult(runPreviewKvCache(req));
      return;
    }
    setBusy(true);
    try {
      const res = await simulateKvCache(req);
      setSimResult(res.data);
    } finally {
      setBusy(false);
    }
  };

  const tableLabels = {
    '--label-2': '"Unique Hashes"',
    '--label-3': '"KV-Cache Hit Rate"',
    '--label-4': '"Tail Exception Kept"',
    '--label-5': '"Indexed Dates"',
    '--label-6': '"Verdict"',
  } as CSSProperties;

  return (
    <div className="panel-inner">
      <section className="card" aria-labelledby="kv-title">
        <div className="card-header">
          <h2 id="kv-title" className="card-title">
            KV-Cache Prefix Determinism &amp; Tool Budget Traceback Retention
          </h2>
          <span className="muted">Exposes random.sample() cache thrashing &amp; head-only stack truncation</span>
        </div>

        <div className="controls-grid">
          <RangeSlider
            id="kv-runs-slider"
            label="Repeated Identical Runs"
            min={5}
            max={30}
            step={1}
            value={runs}
            unitLabel={`${runs} runs`}
            onChange={setRuns}
          />
          <RangeSlider
            id="kv-keep-slider"
            label="Recent Tool Window (keep_recent)"
            min={1}
            max={10}
            step={1}
            value={keepRecent}
            unitLabel={`${keepRecent} msgs`}
            onChange={setKeepRecent}
          />
          <div className="field-group">
            <label htmlFor="kv-query" className="field-label">
              Query Anchor for Stack Retention
            </label>
            <input
              id="kv-query"
              type="text"
              className="text-input"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          <div className="toolbar-row">
            <label htmlFor="kv-redact">
              <input
                id="kv-redact"
                type="checkbox"
                checked={redactSecrets}
                onChange={(e) => setRedactSecrets(e.target.checked)}
              />
              <span>Redact Appendix Secrets</span>
            </label>
            <button type="button" className="btn-primary" disabled={busy} onClick={handleRun}>
              {busy ? 'Simulating…' : 'Run Determinism Lab'}
            </button>
          </div>
        </div>

        <div
          tabIndex={0}
          role="region"
          aria-labelledby="kv-table-caption"
          className="table-wrapper"
          style={tableLabels}
        >
          <table className="data-table">
            <caption id="kv-table-caption">Repeated-Run Prefix Stability &amp; Multi-Turn Retention Matrix</caption>
            <thead>
              <tr>
                <th scope="col">Engine</th>
                <th scope="col">Unique Hashes</th>
                <th scope="col">KV-Cache Hit Rate</th>
                <th scope="col">Tail Exception Kept</th>
                <th scope="col">Indexed Dates</th>
                <th scope="col">Verdict</th>
              </tr>
            </thead>
            <tbody>
              {[simResult.upstream, simResult.enhanced].map((track, idx) => (
                <tr key={`kv-row-${idx}-${track.engine}`}>
                  <th scope="row">
                    {track.engine === 'upstream' ? 'Upstream v7.1.0' : 'Enhanced Deterministic'}
                  </th>
                  <td className="mono">
                    {track.unique_hashes} / {track.runs}
                  </td>
                  <td className="mono">{track.prefix_hit_rate_pct}%</td>
                  <td className="mono">{track.tail_exception_retained ? 'YES (ZeroDivisionError)' : 'NO (WIPED)'}</td>
                  <td className="mono">{track.indexed_placeholders_ok ? '<date_1>, <date_2>' : '<date> collision'}</td>
                  <td>
                    <span className={`verdict-badge verdict-${track.verdict}`}>{track.verdict}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <div className="grid-2">
        {[simResult.upstream, simResult.enhanced].map((track, tIdx) => (
          <section key={`kv-grid-${tIdx}-${track.engine}`} className="card" aria-labelledby={`hash-hdr-${track.engine}`}>
            <div className="card-header">
              <h3 id={`hash-hdr-${track.engine}`} className="card-title">
                {track.engine === 'upstream' ? 'Upstream Prefix Hash Churn' : 'Enhanced Locked Prefix Hashes'}
              </h3>
              <span className="mono muted">{track.prefix_hit_rate_pct}% reuse</span>
            </div>
            <div className="hash-grid">
              {track.hashes.map((h, hIdx) => (
                <div
                  key={`hash-${track.engine}-${hIdx}-${h}`}
                  className={`hash-chip mono ${track.unique_hashes === 1 ? 'locked' : 'churn'}`}
                >
                  #{hIdx + 1}: {h.slice(0, 8)}
                </div>
              ))}
            </div>
            <pre className="code-pane" tabIndex={0}>
              {track.sample_output}
            </pre>
          </section>
        ))}
      </div>
    </div>
  );
}
