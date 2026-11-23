import { useState } from 'react';
import type { RewindRetrieveResponse, SecurityRedactResponse } from '../../api/contract';
import { retrieveRewind, runSecurityRedact } from '../../api/client';
import { runPreviewRewindRetrieve, runPreviewSecurityRedact } from '../../lib/previewEngine';

const DEFAULT_SECRET_TEXT = [
  'Release window: 2026-01-15 through 2026-06-30.',
  'OpenAI Key: sk-proj-TestDemoKey9876543210abcdef1234',
  'Aliyun AK: LTAI5tDemoTestAccessKey99',
  'AWS AK: AKIAIOSFODNN7EXAMPLE',
  'JWT: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyMSJ9.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c',
  'Benign decoys: UUID 550e8400-e29b-41d4-a716-446655440000 · Order 110105194912310029',
].join('\n');

export function SecurityVaultTab({ isLive }: { isLive: boolean }) {
  const [text, setText] = useState(DEFAULT_SECRET_TEXT);
  const [redactSecrets, setRedactSecrets] = useState(true);
  const [indexPlaceholders, setIndexPlaceholders] = useState(true);
  const [secResult, setSecResult] = useState<SecurityRedactResponse>(() =>
    runPreviewSecurityRedact({
      text: DEFAULT_SECRET_TEXT,
      redact_secrets: true,
      index_placeholders: true,
    }),
  );
  const [hashInput, setHashInput] = useState('');
  const [rewindRes, setRewindRes] = useState<RewindRetrieveResponse | null>(null);
  const [busy, setBusy] = useState(false);

  const handleScan = async () => {
    const req = {
      text,
      redact_secrets: redactSecrets,
      index_placeholders: indexPlaceholders,
    };
    if (!isLive) {
      setSecResult(runPreviewSecurityRedact(req));
      return;
    }
    setBusy(true);
    try {
      const res = await runSecurityRedact(req);
      setSecResult(res.data);
    } finally {
      setBusy(false);
    }
  };

  const handleLookup = async () => {
    if (!hashInput.trim()) return;
    if (!isLive) {
      setRewindRes(runPreviewRewindRetrieve(hashInput.trim()));
      return;
    }
    const res = await retrieveRewind(hashInput.trim());
    setRewindRes(res.data);
  };

  return (
    <div className="panel-inner">
      <section className="card" aria-labelledby="sec-title">
        <div className="card-header">
          <h2 id="sec-title" className="card-title">
            QuantumLock Secret Leak Audit &amp; Indexed Disambiguation
          </h2>
          <span className="muted">Zero raw secret appendix echoes · 0% benign UUID/order-ID false positives</span>
        </div>

        <div className="field-group">
          <label htmlFor="sec-prompt-input" className="field-label">
            System Prompt / Telemetry Payload
          </label>
          <textarea
            id="sec-prompt-input"
            className="textarea-input"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
        </div>

        <div className="toolbar-row">
          <label htmlFor="sec-redact-chk">
            <input
              id="sec-redact-chk"
              type="checkbox"
              checked={redactSecrets}
              onChange={(e) => setRedactSecrets(e.target.checked)}
            />
            <span>Mask credentials in appendix ([REDACTED])</span>
          </label>

          <label htmlFor="sec-index-chk">
            <input
              id="sec-index-chk"
              type="checkbox"
              checked={indexPlaceholders}
              onChange={(e) => setIndexPlaceholders(e.target.checked)}
            />
            <span>Bijective indexed placeholders (&lt;date_1&gt;, &lt;date_2&gt;)</span>
          </label>

          <button type="button" className="btn-primary" disabled={busy} onClick={handleScan}>
            {busy ? 'Scanning…' : 'Run Secret & Placeholder Audit'}
          </button>
        </div>

        <div className="grid-2">
          <div className="card">
            <div className="card-header">
              <h3 className="card-title">Upstream QuantumLock</h3>
              <span className={`verdict-badge verdict-${secResult.upstream.verdict}`}>
                {secResult.upstream.verdict}
              </span>
            </div>
            <div className="muted">
              Raw secret echoed: <strong className="mono">{String(secResult.upstream.raw_secret_echoed)}</strong> ·
              Token delta: <strong className="mono">+{secResult.upstream.expansion_tokens}</strong>
            </div>
            <pre className="code-pane" tabIndex={0}>
              {secResult.upstream.stabilized_text}
            </pre>
          </div>

          <div className="card">
            <div className="card-header">
              <h3 className="card-title">Enhanced SafeQuantumLock</h3>
              <span className={`verdict-badge verdict-${secResult.enhanced.verdict}`}>
                {secResult.enhanced.verdict}
              </span>
            </div>
            <div className="muted">
              Raw secret echoed: <strong className="mono">{String(secResult.enhanced.raw_secret_echoed)}</strong> ·
              Findings: <strong className="mono">{secResult.enhanced.findings.length}</strong>
            </div>
            <pre className="code-pane" tabIndex={0}>
              {secResult.enhanced.stabilized_text}
            </pre>
          </div>
        </div>
      </section>

      <section className="card" aria-labelledby="vault-title">
        <div className="card-header">
          <h3 id="vault-title" className="card-title">
            Bounded LRU RewindStore Inspector
          </h3>
          <span className="muted">24-char SHA-256 hash lookup for lossless tool-result recovery</span>
        </div>

        <div className="toolbar-row">
          <div className="field-group" style={{ flex: '1 1 280px' }}>
            <label htmlFor="rewind-hash-input" className="field-label">
              Rewind Hash ID
            </label>
            <input
              id="rewind-hash-input"
              type="text"
              className="text-input mono"
              placeholder="Paste 24-char hash from Tab 1 Rewind marker"
              value={hashInput}
              onChange={(e) => setHashInput(e.target.value)}
            />
          </div>
          <button type="button" className="btn-secondary" onClick={handleLookup}>
            Retrieve Original Payload
          </button>
        </div>

        {rewindRes && (
          <pre className="code-pane" tabIndex={0}>
            {rewindRes.found
              ? rewindRes.original_text
              : `Hash '${rewindRes.hash_id}' not found (expired or evicted from bounded LRU store).`}
          </pre>
        )}
      </section>
    </div>
  );
}
