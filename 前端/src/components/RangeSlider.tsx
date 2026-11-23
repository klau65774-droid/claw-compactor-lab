import type { CSSProperties } from 'react';

interface RangeSliderProps {
  id: string;
  label: string;
  min: number;
  max: number;
  step?: number;
  value: number;
  unitLabel: string;
  onChange: (value: number) => void;
}

export function RangeSlider({
  id,
  label,
  min,
  max,
  step = 1,
  value,
  unitLabel,
  onChange,
}: RangeSliderProps) {
  const styleVars = {
    '--attr-min': min,
    '--attr-max': max,
    '--control-value': value,
  } as CSSProperties;

  return (
    <div className="slider-wrap">
      <div className="slider-head">
        <label htmlFor={id} className="field-label">
          {label}
        </label>
        <span className="mono">{unitLabel}</span>
      </div>
      <input
        id={id}
        type="range"
        className="styled-range"
        min={min}
        max={max}
        step={step}
        value={value}
        aria-valuetext={unitLabel}
        style={styleVars}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </div>
  );
}
