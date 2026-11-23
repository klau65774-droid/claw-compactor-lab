import { useEffect, useState, type CSSProperties } from 'react';
import type { BenchmarksSummaryResponse, DosAuditResponse } from '../../api/contract';
import { getBenchmarksSummary, runDosAudit } from '../../api/client';
import { getPreviewBenchmarksSummary, getPreviewDosAudit } from '../../lib/previewEngine';

interface BenchmarksTabProps {
  isLive: boolean;
  initialSummary: BenchmarksSummaryResponse;
}

export function BenchmarksTab({ isLive, initialSummary }: BenchmarksTabProps) {
  const [summary, setSummary] = useState<BenchmarksSummaryResponse>(initialSummary);
  const [audit, setAudit] = useState<DosAuditResponse>(() => getPreviewDosAudit());
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setSummary(initialSummary);
  }, [initialSummary]);

  useEffect(() => {
    if (!isLive) return;
    let active = true;
    (async () => {
      try {
        const auditRes = await runDosAudit();
        if (active) setAudit(auditRes.data);
      } catch {
        // Keep current audit state if secondary call fails
      }
    })();
    return () => {
      active = false;
    };
  }, [isLive]);

  const handleRefreshBench = async () => {
    if (!isLive) {
      setSummary(getPreviewBenchmarksSummary());
      setAudit(getPreviewDosAudit());
      return;
    }
    setBusy(true);
    try {
      const [benchRes, auditRes] = await Promise.all([getBenchmarksSummary(), runDosAudit()]);
      setSummary(benchRes.data);
      setAudit(auditRes.data);
    } finally {
      setBusy(false);
    }
  };

  const benchLabels = {
    '--label-2': '"Dataset"',
    '--label-3': '"Metric"',
    '--label-4': '"Upstream v7.1.0"',
    '--label-5': '"Enhanced Engine"',
    '--label-6': '"Status"',
  } as CSSProperties;

  const dosLabels = {
    '--label-2': '"Status"',
    '--label-3': '"Latency"',
    '--label-4': '"Measured Metric"',
    '--label-5': '"Enhanced Safeguard"',
  } as CSSProperties;

  return (
    <div className="panel-inner">
      <section className="card" aria-labelledby="bench-title">
        <div className="card-header">
          <div>
            <h2 id="bench-title" className="card-title">
              3-Tier Sealed External Benchmarks (LongBench EN+ZH &amp; SWE-bench_Lite)
            </h2>
            <p className="muted" style={{ margin: '0.2rem 0 0' }}>
              Baseline: {summary.upstream_baseline} · Report mode:{' '}
              <strong className="mono">{summary.status.toUpperCase()}</strong>
            </p>
          </div>
          <button type="button" className="btn-primary" disabled={busy} onClick={handleRefreshBench}>
            {busy ? 'Refreshing…' : 'Sync Live Benchmarks & Run 4-Gate DoS Audit'}
          </button>
        </div>

        <div className="grid-3">
          <div className="metric-box">
            <div className="metric-label">Sealed External Corpus</div>
            <div className="metric-val mono">1,800 LongBench + 300 SWE</div>
          </div>
          <div className="metric-box">
            <div className="metric-label">OOD Secret Recall / Benign FPR</div>
            <div className="metric-val mono">
              {summary.ood_security.secret_recall_pct}% ({summary.ood_security.secret_total}) /{' '}
              {summary.ood_security.benign_fpr_pct}% ({summary.ood_security.benign_total})
            </div>
          </div>
          <div className="metric-box">
            <div className="metric-label">4-Gate DoS &amp; Scale Audit</div>
            <div className="metric-val mono">
              {audit.passed} / {audit.total} PASS
            </div>
          </div>
        </div>

        <div
          tabIndex={0}
          role="region"
          aria-labelledby="bench-table-caption"
          className="table-wrapper"
          style={benchLabels}
        >
          <table className="data-table">
            <caption id="bench-table-caption">
              External Blind Evaluation: Unmodified Upstream vs Enhanced Engine
            </caption>
            <thead>
              <tr>
                <th scope="col">Tier</th>
                <th scope="col">Dataset</th>
                <th scope="col">Metric</th>
                <th scope="col">Upstream v7.1.0</th>
                <th scope="col">Enhanced Engine</th>
                <th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {summary.rows.map((row, idx) => (
                <tr key={`bench-row-${idx}-${row.tier}-${row.metric}`}>
                  <th scope="row">{row.tier}</th>
                  <td>{row.dataset}</td>
                  <td>{row.metric}</td>
                  <td className="mono">{row.upstream_value}</td>
                  <td className="mono">{row.enhanced_value}</td>
                  <td>
                    <span className={`verdict-badge verdict-${row.status}`}>{row.status}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div
          tabIndex={0}
          role="region"
          aria-labelledby="dos-table-caption"
          className="table-wrapper"
          style={dosLabels}
        >
          <table className="data-table">
            <caption id="dos-table-caption">Bounded-Resource DoS, Scaling &amp; Non-Expansion Audit</caption>
            <thead>
              <tr>
                <th scope="col">Audit Gate</th>
                <th scope="col">Status</th>
                <th scope="col">Latency</th>
                <th scope="col">Measured Metric</th>
                <th scope="col">Enhanced Safeguard</th>
              </tr>
            </thead>
            <tbody>
              {audit.audits.map((item, idx) => (
                <tr key={`dos-row-${idx}-${item.id}`}>
                  <th scope="row">{item.name}</th>
                  <td>
                    <span className={`verdict-badge verdict-${item.status}`}>{item.status}</span>
                  </td>
                  <td className="mono">{item.latency_ms} ms</td>
                  <td className="mono">{item.metric_label}</td>
                  <td>{item.enhanced_behavior}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
