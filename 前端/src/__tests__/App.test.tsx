import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import App from '../App';

describe('ClawCompactor Context Lab UI', () => {
  it('renders all 4 single-row tabs and switches between workspaces cleanly', async () => {
    render(<App />);

    await waitFor(() => {
      expect(screen.getByText(/BACKEND OFFLINE/i)).toBeInTheDocument();
    });

    expect(screen.getByRole('heading', { level: 1, name: /ClawCompactor Context Lab/i })).toBeInTheDocument();

    const cnPresetBtn = screen.getByRole('button', { name: /Chinese JSON Tool Errors/i });
    fireEvent.click(cnPresetBtn);
    const waterfall = screen.getByRole('img', { name: /Per-stage token waterfall/i });
    expect(within(waterfall).getByText('942')).toBeInTheDocument();
    expect(within(waterfall).getByText('157')).toBeInTheDocument();

    const tabs = screen.getAllByRole('tab');
    expect(tabs).toHaveLength(4);
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true');

    fireEvent.click(tabs[1]);
    expect(tabs[1]).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText(/KV-Cache Prefix Determinism/i)).toBeVisible();

    fireEvent.click(tabs[2]);
    expect(tabs[2]).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText(/3-Tier Sealed External Benchmarks/i)).toBeVisible();

    fireEvent.click(tabs[3]);
    expect(tabs[3]).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText(/QuantumLock Secret Leak Audit/i)).toBeVisible();
  });
});
