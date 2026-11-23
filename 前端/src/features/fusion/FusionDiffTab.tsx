import { useState } from 'react';
import type {
  CompressCompareRequest,
  CompressCompareResponse,
  CompressionMode,
  ContentTypeHint,
  MessageRole,
  PresetItem,
  RewindRetrieveResponse,
} from '../../api/contract';
import { retrieveRewind } from '../../api/client';
import { runPreviewRewindRetrieve } from '../../lib/previewEngine';
import { RangeSlider } from '../../components/RangeSlider';
import { TokenWaterfallChart } from '../../components/TokenWaterfallChart';

interface FusionDiffTabProps {
  isLive: boolean;
  presets: PresetItem[];
  selectedPresetId: string;
  onSelectPreset: (preset: PresetItem) => void;
  form: CompressCompareRequest;
  onChangeForm: (next: CompressCompareRequest) => void;
  result: CompressCompareResponse | null;
  running: boolean;
  streamStage: string;
  onRunCompare: () => void;
}

export function FusionDiffTab({
  isLive,
  presets,
  selectedPresetId,
  onSelectPreset,
  form,
  onChangeForm,
  result,
  running,
  streamStage,
  onRunCompare,
}: FusionDiffTabProps) {
  const [rewindResult, setRewindResult] = useState<RewindRetrieveResponse | null>(null);
  const [rewindBusy, setRewindBusy] = useState(false);

  const handleInspectRewind = async (hashId: string) => {
    if (!isLive) {
      setRewindResult(runPreviewRewindRetrieve(hashId));
      return;
    }
    setRewindBusy(true);
    try {
      const res = await retrieveRewind(hashId);
      setRewindResult(res.data);
    } finally {
      setRewindBusy(false);
    }
  };

  return (
    <div className="panel-inner">
      <section className="card" aria-labelledby="preset-strip-title">
        <div className="card-header">
          <h2 id="preset-strip-title" className="card-title">
            14-Stage Fusion Diff &amp; Empirical Defect Reproduction
          </h2>
          <span className="muted">Zero paid LLM inference · Pure CPU evaluation</span>
        </div>

        <div className="preset-strip" role="group" aria-label="Context engineering presets">
          {presets.map((p, idx) => (
            <button
              key={`preset-${idx}-${p.id}`}
              type="button"
              className={`preset-btn ${selectedPresetId === p.id ? 'active' : ''}`}
              aria-pressed={selectedPresetId === p.id}
              onClick={() => onSelectPreset(p)}
            >
              <div>{p.name}</div>
              <div className="muted">{p.subtitle}</div>
            </button>
          ))}
        </div>

        <div className="controls-grid">
          <div className="field-group">
            <label htmlFor="fusion-role" className="field-label">
              Message Role
            </label>
            <select
              id="fusion-role"
              className="select-input"
              value={form.role}
              onChange={(e) => onChangeForm({ ...form, role: e.target.value as MessageRole })}
            >
              <option value="user">user</option>
              <option value="system">system</option>
              <option value="tool">tool</option>
              <option value="assistant">assistant</option>
            </select>
          </div>

          <div className="field-group">
            <label htmlFor="fusion-content-type" className="field-label">
              Content Type Hint
            </label>
            <select
              id="fusion-content-type"
              className="select-input"
              value={form.content_type}
              onChange={(e) => onChangeForm({ ...form, content_type: e.target.value as ContentTypeHint })}
            >
              <option value="text">text</option>
              <option value="json">json</option>
              <option value="log">log</option>
              <option value="diff">diff</option>
              <option value="code">code</option>
              <option value="search">search</option>
            </select>
          </div>

          <div className="field-group">
            <label htmlFor="fusion-mode" className="field-label">
              Compression Mode
            </label>
            <select
              id="fusion-mode"
              className="select-input"
              value={form.mode}
              onChange={(e) => onChangeForm({ ...form, mode: e.target.value as CompressionMode })}
            >
              <option value="balanced">balanced</option>
              <option value="lossless">lossless</option>
              <option value="aggressive">aggressive</option>
            </select>
          </div>

          <RangeSlider
            id="fusion-budget-slider"
            label="Target Token Budget"
            min={100}
            max={2000}
            step={20}
            value={form.target_budget_tokens ?? 450}
            unitLabel={`${form.target_budget_tokens ?? 450} tok`}
            onChange={(val) => onChangeForm({ ...form, target_budget_tokens: val })}
          />
        </div>

        <div className="controls-grid">
          <div className="field-group">
            <label htmlFor="fusion-query" className="field-label">
              Query-Aware Needle Anchor
            </label>
            <input
              id="fusion-query"
              type="text"
              className="text-input"
              value={form.query ?? ''}
              onChange={(e) => onChangeForm({ ...form, query: e.target.value })}
            />
          </div>

          <div className="toolbar-row">
            <label htmlFor="fusion-redact-toggle">
              <input
                id="fusion-redact-toggle"
                type="checkbox"
                checked={form.redact_secrets}
                onChange={(e) => onChangeForm({ ...form, redact_secrets: e.target.checked })}
              />
              <span>Redact raw secrets (prevent QuantumLock echo)</span>
            </label>

            <button
              type="button"
              className="btn-primary"
              disabled={running}
              onClick={onRunCompare}
            >
              {running ? `Streaming (${streamStage || 'running'})…` : 'Run Side-by-Side Comparison'}
            </button>
          </div>
        </div>

        <div className="field-group">
          <label htmlFor="fusion-input-text" className="field-label">
            Input Context Payload
          </label>
          <textarea
            id="fusion-input-text"
            className="textarea-input"
            value={form.text}
            onChange={(e) => onChangeForm({ ...form, text: e.target.value })}
          />
        </div>
      </section>

      {result && (
        <>
          <div className="grid-2">
            {[result.upstream, result.enhanced].map((side, sideIdx) => (
              <section
                key={`side-${sideIdx}-${side.engine}`}
                className="card"
                aria-labelledby={`heading-${side.engine}`}
              >
                <div className="card-header">
                  <h3 id={`heading-${side.engine}`} className="card-title">
                    {side.engine === 'upstream'
                      ? 'Unmodified Upstream (claw-compactor v7.1.0)'
                      : 'Enhanced Bilingual Monotonic Engine'}
                  </h3>
                  <span className={`verdict-badge verdict-${side.verdict}`}>{side.verdict}</span>
                </div>

                <div className="metric-row">
                  <div className="metric-box">
                    <div className="metric-label">Tokens</div>
                    <div className="metric-val mono">
                      {side.original_tokens} → {side.compressed_tokens}
                    </div>
                  </div>
                  <div className="metric-box">
                    <div className="metric-label">Reduction</div>
                    <div className="metric-val mono">{side.reduction_pct}%</div>
                  </div>
                  <div className="metric-box">
                    <div className="metric-label">Prefix Hash</div>
                    <div className="metric-val mono" style={{ fontSize: '0.88rem' }}>
                      {side.prefix_hash.slice(0, 10)} ({side.prefix_deterministic ? 'LOCKED' : 'CHURN'})
                    </div>
                  </div>
                  <div className="metric-box">
                    <div className="metric-label">Needle / Leaks</div>
                    <div className="metric-val mono">
                      {side.needle_retention_pct}% / {side.secret_leaks_count} leaks
                    </div>
                  </div>
                </div>

                {side.notes.length > 0 && (
                  <ul className="notes-list">
                    {side.notes.map((n, nIdx) => (
                      <li key={`note-${side.engine}-${nIdx}`}>{n}</li>
                    ))}
                  </ul>
                )}

                <pre className="code-pane" tabIndex={0} aria-label={`${side.engine} compressed output`}>
                  {side.compressed_text}
                </pre>

                {side.rewind_markers.length > 0 && (
                  <div className="toolbar-row">
                    {side.rewind_markers.map((rm, rmIdx) => (
                      <button
                        key={`rm-${rmIdx}-${rm.hash_id}`}
                        type="button"
                        className="btn-secondary mono"
                        disabled={rewindBusy}
                        onClick={() => handleInspectRewind(rm.hash_id)}
                      >
                        Rewind Hash {rm.hash_id.slice(0, 12)}… ({rm.original_count} → {rm.compressed_count})
                      </button>
                    ))}
                  </div>
                )}
              </section>
            ))}
          </div>

          <section className="card" aria-labelledby="waterfall-title">
            <h3 id="waterfall-title" className="card-title">
              14-Stage Token Waterfall &amp; Monotonic Rollback Trace
            </h3>
            <TokenWaterfallChart
              upstreamStages={result.upstream.stages}
              enhancedStages={result.enhanced.stages}
              originalTokens={result.enhanced.original_tokens}
            />
          </section>

          {rewindResult && (
            <section className="card" aria-labelledby="rewind-inline-title">
              <div className="card-header">
                <h3 id="rewind-inline-title" className="card-title">
                  RewindStore Lossless Recovery ({rewindResult.hash_id})
                </h3>
                <span className={`verdict-badge ${rewindResult.found ? 'verdict-PASSED' : 'verdict-FAILED'}`}>
                  {rewindResult.found ? 'FOUND' : 'EVICTED / MISSING'}
                </span>
              </div>
              <pre className="code-pane" tabIndex={0}>
                {rewindResult.original_text ?? 'Entry not found in bounded LRU store.'}
              </pre>
            </section>
          )}
        </>
      )}
    </div>
  );
}
