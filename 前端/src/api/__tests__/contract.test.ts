import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import spec from '../contract.spec.json';
import {
  PRESET_ITEMS,
  computeHonestVerdict,
  runPreviewCompare,
  runPreviewSecurityRedact,
} from '../../lib/previewEngine';

interface OpenApiDocument {
  paths?: Record<string, Record<string, unknown>>;
}

describe('ClawCompactor API Contract', () => {
  it('defines all 9 canonical REST and SSE routes with unique method+path pairs', () => {
    expect(spec.routes).toHaveLength(9);
    const keys = spec.routes.map((r) => `${r.method.toUpperCase()} ${r.path}`);
    expect(new Set(keys).size).toBe(9);
    expect(keys).toContain('GET /api/v1/health');
    expect(keys).toContain('GET /api/v1/presets');
    expect(keys).toContain('POST /api/v1/compress/compare');
    expect(keys).toContain('POST /api/v1/compress/stream');
    expect(keys).toContain('POST /api/v1/kv-cache/simulate');
    expect(keys).toContain('POST /api/v1/rewind/retrieve');
    expect(keys).toContain('POST /api/v1/security/redact');
    expect(keys).toContain('POST /api/v1/dos/audit');
    expect(keys).toContain('GET /api/v1/benchmarks/summary');
  });

  it('keeps all 6 required preset IDs synchronized', () => {
    const ids = PRESET_ITEMS.map((p) => p.id);
    for (const reqId of spec.required_presets) {
      expect(ids).toContain(reqId);
    }
  });

  it('never reports PASSED for bloated, secret-leaking, empty, or non-deterministic outputs', () => {
    expect(
      computeHonestVerdict({
        compressedText: '',
        originalTokens: 100,
        compressedTokens: 0,
        secretLeaksCount: 0,
        prefixDeterministic: true,
        structurePreserved: true,
        needleRetentionPct: 100,
      }),
    ).toBe('FAILED');

    expect(
      computeHonestVerdict({
        compressedText: 'leaked sk-proj-1234',
        originalTokens: 100,
        compressedTokens: 90,
        secretLeaksCount: 1,
        prefixDeterministic: true,
        structurePreserved: true,
        needleRetentionPct: 100,
      }),
    ).toBe('VULNERABLE');

    expect(
      computeHonestVerdict({
        compressedText: 'expanded text',
        originalTokens: 100,
        compressedTokens: 135,
        secretLeaksCount: 0,
        prefixDeterministic: true,
        structurePreserved: true,
        needleRetentionPct: 100,
      }),
    ).toBe('BLOATED');

    expect(
      computeHonestVerdict({
        compressedText: 'unstable output',
        originalTokens: 100,
        compressedTokens: 60,
        secretLeaksCount: 0,
        prefixDeterministic: false,
        structurePreserved: true,
        needleRetentionPct: 100,
      }),
    ).toBe('DEGRADED');
  });

  it('reproduces upstream vulnerabilities while enhanced passes cleanly on key presets', () => {
    const cnJson = PRESET_ITEMS.find((p) => p.id === 'cn_json_tool_errors')!;
    const cnRes = runPreviewCompare({
      preset_id: cnJson.id,
      text: cnJson.text,
      role: cnJson.role,
      content_type: cnJson.content_type,
      query: cnJson.query,
      target_budget_tokens: cnJson.target_budget_tokens,
      redact_secrets: true,
      deterministic_seed: 42,
      mode: cnJson.mode,
    });
    expect(cnRes.upstream.verdict).toBe('BLOATED');
    expect(cnRes.enhanced.verdict).toBe('PASSED');

    const secPreset = PRESET_ITEMS.find((p) => p.id === 'kv_cache_secret_prompt')!;
    const secRes = runPreviewSecurityRedact({
      text: secPreset.text,
      redact_secrets: true,
      index_placeholders: true,
    });
    expect(secRes.upstream.verdict).toBe('VULNERABLE');
    expect(secRes.upstream.raw_secret_echoed).toBe(true);
    expect(secRes.enhanced.verdict).toBe('SAFE');
    expect(secRes.enhanced.raw_secret_echoed).toBe(false);
  });

  it('matches exported backend docs/openapi.json whenever backend schema exists', () => {
    const openapiPath = resolve(process.cwd(), '../后端/docs/openapi.json');
    if (!existsSync(openapiPath)) {
      // Backend is scheduled for Codex; skip cross-file parity until docs/openapi.json is generated
      return;
    }
    const raw = readFileSync(openapiPath, 'utf8');
    const doc = JSON.parse(raw) as OpenApiDocument;
    const paths = doc.paths ?? {};
    for (const route of spec.routes) {
      const pathItem = paths[route.path];
      expect(pathItem, `Missing OpenAPI path: ${route.path}`).toBeDefined();
      const methodKey = route.method.toLowerCase();
      expect(pathItem[methodKey], `Missing OpenAPI method ${route.method} ${route.path}`).toBeDefined();
    }
  });
});
