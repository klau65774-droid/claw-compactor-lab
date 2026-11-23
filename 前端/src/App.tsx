import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import type {
  BenchmarksSummaryResponse,
  CompressCompareRequest,
  CompressCompareResponse,
  HealthResponse,
  PresetItem,
} from './api/contract';
import { compareCompression, getBenchmarksSummary, getHealth, getPresets } from './api/client';
import {
  PRESET_ITEMS,
  getPreviewBenchmarksSummary,
  getPreviewHealth,
  runPreviewCompare,
} from './lib/previewEngine';
import { FusionDiffTab } from './features/fusion/FusionDiffTab';
import { KvCacheTab } from './features/kv/KvCacheTab';
import { BenchmarksTab } from './features/benchmarks/BenchmarksTab';
import { SecurityVaultTab } from './features/security/SecurityVaultTab';

type TabId = 'fusion' | 'kv' | 'benchmarks' | 'security';

const TABS: Array<{ id: TabId; label: string }> = [
  { id: 'fusion', label: '01 · 14-Stage Fusion Diff' },
  { id: 'kv', label: '02 · KV-Cache & Tool Budget' },
  { id: 'benchmarks', label: '03 · 3-Tier Benchmarks & DoS' },
  { id: 'security', label: '04 · Secret Guard & Rewind' },
];

function buildInitialForm(preset: PresetItem): CompressCompareRequest {
  return {
    preset_id: preset.id,
    text: preset.text,
    role: preset.role,
    content_type: preset.content_type,
    query: preset.query,
    target_budget_tokens: preset.target_budget_tokens,
    redact_secrets: preset.redact_secrets,
    deterministic_seed: 42,
    mode: preset.mode,
  };
}

export function App() {
  const [activeTab, setActiveTab] = useState<TabId>('fusion');
  const [announcement, setAnnouncement] = useState('');
  const [health, setHealth] = useState<HealthResponse>(() => getPreviewHealth());
  const [isLive, setIsLive] = useState(false);
  const [presets, setPresets] = useState<PresetItem[]>(PRESET_ITEMS);
  const [selectedPresetId, setSelectedPresetId] = useState(PRESET_ITEMS[0].id);
  const [form, setForm] = useState<CompressCompareRequest>(() => buildInitialForm(PRESET_ITEMS[0]));
  const [compareResult, setCompareResult] = useState<CompressCompareResponse | null>(() =>
    runPreviewCompare(buildInitialForm(PRESET_ITEMS[0])),
  );
  const [benchSummary, setBenchSummary] = useState<BenchmarksSummaryResponse>(() =>
    getPreviewBenchmarksSummary(),
  );
  const [running, setRunning] = useState(false);
  const [streamStage, setStreamStage] = useState('');
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);

  useEffect(() => {
    let active = true;
    (async () => {
      const h = await getHealth();
      if (!active || !h.live) return;
      setHealth(h.data);
      setIsLive(true);
      const [p, b] = await Promise.all([getPresets(), getBenchmarksSummary()]);
      if (!active) return;
      const activePresets = p.data.presets.length > 0 ? p.data.presets : PRESET_ITEMS;
      if (p.data.presets.length > 0) {
        setPresets(p.data.presets);
      }
      setBenchSummary(b.data);
      const initialPreset = activePresets[0];
      if (initialPreset) {
        setSelectedPresetId(initialPreset.id);
        const liveForm = buildInitialForm(initialPreset);
        setForm(liveForm);
        const cmp = await compareCompression(liveForm);
        if (active && cmp.live) {
          setCompareResult(cmp.data);
        }
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  const selectTab = (tabId: TabId, idx: number, focusIt = false) => {
    setActiveTab(tabId);
    const label = TABS[idx]?.label ?? tabId;
    setAnnouncement(`Switched to ${label}`);
    const el = tabRefs.current[idx];
    if (el) {
      if (focusIt) el.focus();
      if (typeof el.scrollIntoView === 'function') {
        el.scrollIntoView({ block: 'nearest', inline: 'nearest' });
      }
    }
  };

  const handleTabKeyDown = (e: KeyboardEvent<HTMLButtonElement>, idx: number) => {
    let nextIdx: number | null = null;
    if (e.key === 'ArrowRight') nextIdx = (idx + 1) % TABS.length;
    else if (e.key === 'ArrowLeft') nextIdx = (idx - 1 + TABS.length) % TABS.length;
    else if (e.key === 'Home') nextIdx = 0;
    else if (e.key === 'End') nextIdx = TABS.length - 1;

    if (nextIdx !== null) {
      e.preventDefault();
      selectTab(TABS[nextIdx].id, nextIdx, true);
    }
  };

  const handleSelectPreset = async (preset: PresetItem) => {
    setSelectedPresetId(preset.id);
    const nextForm = buildInitialForm(preset);
    setForm(nextForm);
    if (!isLive) {
      setCompareResult(runPreviewCompare(nextForm));
      setAnnouncement(`Loaded preset ${preset.name} and updated preview comparison.`);
      return;
    }
    setRunning(true);
    try {
      const res = await compareCompression(nextForm, (s) => setStreamStage(s.stage));
      setCompareResult(res.data);
      setIsLive(res.live);
      setAnnouncement(`Loaded preset ${preset.name} and completed comparison.`);
    } finally {
      setRunning(false);
      setStreamStage('');
    }
  };

  const handleRunCompare = async () => {
    if (!isLive) {
      const preview = runPreviewCompare(form);
      setCompareResult(preview);
      setAnnouncement(
        `Preview comparison complete: Enhanced verdict ${preview.enhanced.verdict}, Upstream verdict ${preview.upstream.verdict}.`,
      );
      return;
    }
    setRunning(true);
    try {
      const res = await compareCompression(form, (s) => setStreamStage(s.stage));
      setCompareResult(res.data);
      setIsLive(res.live);
      setAnnouncement(
        `Comparison complete: Enhanced verdict ${res.data.enhanced.verdict}, Upstream verdict ${res.data.upstream.verdict}.`,
      );
    } finally {
      setRunning(false);
      setStreamStage('');
    }
  };

  return (
    <div className="app-shell">
      <a href="#main-workspace" className="skip-link visually-hidden">
        Skip to main workspace
      </a>

      <header className="app-header">
        <div className="brand-block">
          <h1 className="brand-title">ClawCompactor Context Lab</h1>
          <p className="brand-subtitle">
            Bilingual Context Engineering, Deterministic KV-Cache Alignment &amp; Reversible Rewind Console
          </p>
        </div>

        <div className="status-cluster">
          <span className={`status-pill mono ${isLive ? 'live' : 'preview'}`}>
            {isLive ? `LIVE BACKEND · v${health.upstream_version}` : health.mode}
          </span>
          <span className="status-pill mono">
            {health.active_stages} Stages · Base {health.upstream_commit.slice(0, 12)}
          </span>
        </div>
      </header>

      <div
        role="tablist"
        aria-label="Context Engineering Workspaces"
        className="tablist"
      >
        {TABS.map((tab, idx) => {
          const isSelected = activeTab === tab.id;
          return (
            <button
              key={`tab-${idx}-${tab.id}`}
              ref={(el) => {
                tabRefs.current[idx] = el;
              }}
              id={`tab-btn-${tab.id}`}
              type="button"
              role="tab"
              aria-selected={isSelected}
              tabIndex={isSelected ? 0 : -1}
              className="tab-btn"
              onClick={() => selectTab(tab.id, idx, false)}
              onKeyDown={(e) => handleTabKeyDown(e, idx)}
            >
              {tab.label}
            </button>
          );
        })}
      </div>

      <div className="visually-hidden" aria-live="polite">
        {announcement}
      </div>

      <main id="main-workspace" tabIndex={-1}>
        <section
          role="tabpanel"
          id="panel-fusion"
          aria-labelledby="tab-btn-fusion"
          hidden={activeTab !== 'fusion'}
        >
          <FusionDiffTab
            isLive={isLive}
            presets={presets}
            selectedPresetId={selectedPresetId}
            onSelectPreset={handleSelectPreset}
            form={form}
            onChangeForm={setForm}
            result={compareResult}
            running={running}
            streamStage={streamStage}
            onRunCompare={handleRunCompare}
          />
        </section>

        <section
          role="tabpanel"
          id="panel-kv"
          aria-labelledby="tab-btn-kv"
          hidden={activeTab !== 'kv'}
        >
          <KvCacheTab isLive={isLive} />
        </section>

        <section
          role="tabpanel"
          id="panel-benchmarks"
          aria-labelledby="tab-btn-benchmarks"
          hidden={activeTab !== 'benchmarks'}
        >
          <BenchmarksTab isLive={isLive} initialSummary={benchSummary} />
        </section>

        <section
          role="tabpanel"
          id="panel-security"
          aria-labelledby="tab-btn-security"
          hidden={activeTab !== 'security'}
        >
          <SecurityVaultTab isLive={isLive} />
        </section>
      </main>
    </div>
  );
}

export default App;
