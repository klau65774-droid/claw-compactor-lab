import type { StageStepMetrics } from '../api/contract';

interface TokenWaterfallChartProps {
  upstreamStages: StageStepMetrics[];
  enhancedStages: StageStepMetrics[];
  originalTokens: number;
}

export function TokenWaterfallChart({
  upstreamStages,
  enhancedStages,
  originalTokens,
}: TokenWaterfallChartProps) {
  const upByOrder = new Map<number, StageStepMetrics>();
  const enhByOrder = new Map<number, StageStepMetrics>();
  for (const s of upstreamStages) {
    upByOrder.set(s.order, s);
  }
  for (const s of enhancedStages) {
    enhByOrder.set(s.order, s);
  }

  const activeOrders = Array.from(
    new Set([
      ...upstreamStages.filter((s) => !s.skipped).map((s) => s.order),
      ...enhancedStages.filter((s) => !s.skipped).map((s) => s.order),
    ]),
  ).sort((a, b) => a - b);

  let upCursor = originalTokens;
  let enhCursor = originalTokens;
  const pairedRows = activeOrders.map((order) => {
    const up = upByOrder.get(order);
    const enh = enhByOrder.get(order);
    if (up && !up.skipped) upCursor = up.compressed_tokens;
    if (enh && !enh.skipped) enhCursor = enh.compressed_tokens;
    const labelBase = enh && !enh.skipped ? enh.name : up?.name ?? `stage_${order}`;
    const label = enh?.rolled_back ? `${labelBase} (RB)` : labelBase;
    return {
      order,
      label,
      upTokens: upCursor,
      enhTokens: enhCursor,
    };
  });

  const maxVal = Math.max(
    originalTokens,
    ...pairedRows.map((r) => Math.max(r.upTokens, r.enhTokens)),
    1,
  );

  const width = 680;
  const rowH = 34;
  const padTop = 28;
  const height = padTop + Math.max(1, pairedRows.length) * rowH + 18;
  const labelW = 180;
  const chartW = width - labelW - 72;

  return (
    <figure className="figure-chart">
      <svg
        role="img"
        aria-label="Per-stage token waterfall comparing upstream v7.1.0 against enhanced engine"
        viewBox={`0 0 ${width} ${height}`}
        style={{
          width: '100%',
          height: 'auto',
          background: '#080d1a',
          borderRadius: '0.5rem',
          border: '1px solid #243044',
        }}
      >
        <title>Per-Stage Token Waterfall Comparison</title>
        <text x="12" y="18" fill="#94a3b8" fontSize="11" fontWeight="700">
          STAGE
        </text>
        <text x={labelW} y="18" fill="#f43f5e" fontSize="11" fontWeight="700">
          ■ Upstream v7.1.0
        </text>
        <text x={labelW + 140} y="18" fill="#34d399" fontSize="11" fontWeight="700">
          ■ Enhanced Monotonic
        </text>
        {pairedRows.map((row, idx) => {
          const y = padTop + idx * rowH;
          const upW = Math.max(4, Math.round((row.upTokens / maxVal) * chartW));
          const enhW = Math.max(4, Math.round((row.enhTokens / maxVal) * chartW));

          return (
            <g key={`waterfall-${idx}-${row.order}-${row.label}`}>
              <text x="12" y={y + 16} fill="#cbd5e1" fontSize="11">
                {row.label}
              </text>
              <rect x={labelW} y={y + 3} width={upW} height={10} rx="2" fill="#f43f5e" opacity="0.85" />
              <text x={labelW + upW + 6} y={y + 11} fill="#fda4af" fontSize="10">
                {row.upTokens}
              </text>
              <rect x={labelW} y={y + 16} width={enhW} height={10} rx="2" fill="#34d399" opacity="0.9" />
              <text x={labelW + enhW + 6} y={y + 24} fill="#6ee7b7" fontSize="10">
                {row.enhTokens}
              </text>
            </g>
          );
        })}
      </svg>
      <figcaption className="figure-caption">
        Token waterfall per active stage (lower is better; RB = negative-compression stage automatically rolled back).
      </figcaption>
    </figure>
  );
}
