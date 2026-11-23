import type {
  BenchmarksSummaryResponse,
  CompressCompareRequest,
  CompressCompareResponse,
  DosAuditResponse,
  HealthResponse,
  KvCacheSimulateRequest,
  KvCacheSimulateResponse,
  PresetsResponse,
  RewindRetrieveResponse,
  SecurityRedactRequest,
  SecurityRedactResponse,
} from './contract';
import {
  PRESET_ITEMS,
  getPreviewBenchmarksSummary,
  getPreviewDosAudit,
  getPreviewHealth,
  runPreviewCompare,
  runPreviewKvCache,
  runPreviewRewindRetrieve,
  runPreviewSecurityRedact,
} from '../lib/previewEngine';

export interface ApiResult<T> {
  data: T;
  live: boolean;
}

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), 3500);
  try {
    const res = await fetch(url, {
      ...init,
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        ...(init?.headers ?? {}),
      },
    });
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    return (await res.json()) as T;
  } finally {
    window.clearTimeout(timer);
  }
}

export async function getHealth(): Promise<ApiResult<HealthResponse>> {
  try {
    const data = await fetchJson<HealthResponse>('/api/v1/health');
    return { data, live: true };
  } catch {
    return { data: getPreviewHealth(), live: false };
  }
}

export async function getPresets(): Promise<ApiResult<PresetsResponse>> {
  try {
    const data = await fetchJson<PresetsResponse>('/api/v1/presets');
    return { data, live: true };
  } catch {
    return { data: { presets: PRESET_ITEMS }, live: false };
  }
}

export async function compareCompression(
  req: CompressCompareRequest,
  onStage?: (payload: { progress_pct: number; stage: string }) => void,
): Promise<ApiResult<CompressCompareResponse>> {
  try {
    const res = await fetch('/api/v1/compress/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify(req),
    });
    if (res.ok && res.body) {
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let finalPayload: CompressCompareResponse | null = null;
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split('\n\n');
        buffer = frames.pop() ?? '';
        for (const frame of frames) {
          const eventLine = frame.split('\n').find((l) => l.startsWith('event:'));
          const dataLine = frame.split('\n').find((l) => l.startsWith('data:'));
          if (!dataLine) continue;
          const eventName = eventLine ? eventLine.slice(6).trim() : 'message';
          const parsed = JSON.parse(dataLine.slice(5).trim());
          if (eventName === 'stage' && onStage) {
            onStage(parsed as { progress_pct: number; stage: string });
          } else if (eventName === 'complete') {
            finalPayload = parsed as CompressCompareResponse;
          }
        }
      }
      if (finalPayload) {
        return { data: finalPayload, live: true };
      }
    }
  } catch {
    // Fall through to unary JSON endpoint or preview
  }

  try {
    const data = await fetchJson<CompressCompareResponse>('/api/v1/compress/compare', {
      method: 'POST',
      body: JSON.stringify(req),
    });
    return { data, live: true };
  } catch {
    if (onStage) {
      onStage({ progress_pct: 50, stage: 'semantic_dedup / ionizer' });
      onStage({ progress_pct: 100, stage: 'monotonic_gate' });
    }
    return { data: runPreviewCompare(req), live: false };
  }
}

export async function simulateKvCache(
  req: KvCacheSimulateRequest,
): Promise<ApiResult<KvCacheSimulateResponse>> {
  try {
    const data = await fetchJson<KvCacheSimulateResponse>('/api/v1/kv-cache/simulate', {
      method: 'POST',
      body: JSON.stringify(req),
    });
    return { data, live: true };
  } catch {
    return { data: runPreviewKvCache(req), live: false };
  }
}

export async function retrieveRewind(hashId: string): Promise<ApiResult<RewindRetrieveResponse>> {
  try {
    const data = await fetchJson<RewindRetrieveResponse>('/api/v1/rewind/retrieve', {
      method: 'POST',
      body: JSON.stringify({ hash_id: hashId }),
    });
    return { data, live: true };
  } catch {
    return { data: runPreviewRewindRetrieve(hashId), live: false };
  }
}

export async function runSecurityRedact(
  req: SecurityRedactRequest,
): Promise<ApiResult<SecurityRedactResponse>> {
  try {
    const data = await fetchJson<SecurityRedactResponse>('/api/v1/security/redact', {
      method: 'POST',
      body: JSON.stringify(req),
    });
    return { data, live: true };
  } catch {
    return { data: runPreviewSecurityRedact(req), live: false };
  }
}

export async function runDosAudit(): Promise<ApiResult<DosAuditResponse>> {
  try {
    const data = await fetchJson<DosAuditResponse>('/api/v1/dos/audit', {
      method: 'POST',
      body: '{}',
    });
    return { data, live: true };
  } catch {
    return { data: getPreviewDosAudit(), live: false };
  }
}

export async function getBenchmarksSummary(): Promise<ApiResult<BenchmarksSummaryResponse>> {
  try {
    const data = await fetchJson<BenchmarksSummaryResponse>('/api/v1/benchmarks/summary');
    return { data, live: true };
  } catch {
    return { data: getPreviewBenchmarksSummary(), live: false };
  }
}
