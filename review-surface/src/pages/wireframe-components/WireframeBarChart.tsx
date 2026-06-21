import { useNavigate } from 'react-router-dom';
import { C } from '@/utils/colors';
import { fmtScore, fmtTokens } from '@/utils/helpers';
import { runLabel } from '@/utils/verdict';
import type { RunRecord } from '@/utils/types';

const WIDTH = 560;
const HEIGHT = 210;
const PAD = { left: 42, right: 14, top: 16, bottom: 28 };
const TOKEN_COLORS = { input: C.cyan, output: C.orange };
const TOKEN_GROUP_WIDTH = 92;

type TooltipPayload = {
  id: string;
  x: number;
  y: number;
  title: string;
  lines: string[];
};

function tokenTickLabel(tokens: number) {
  return fmtTokens(Math.round(tokens));
}

export function WireframeBarChart({
  runs,
  modelLabel,
  onOpenTooltip,
  onCloseTooltip,
}: {
  runs: RunRecord[];
  modelLabel: (agentSpec: string) => string;
  onOpenTooltip: (payload: TooltipPayload) => void;
  onCloseTooltip: () => void;
}) {
  const navigate = useNavigate();
  const maxTokenAmount = Math.max(...runs.flatMap((run) => [run.total_input_tokens ?? 0, run.total_output_tokens ?? 0]), 1) * 1.2;
  const chartWidth = Math.max(WIDTH, PAD.left + PAD.right + runs.length * TOKEN_GROUP_WIDTH);
  const tokenGroupX = (index: number) => PAD.left + TOKEN_GROUP_WIDTH * index + TOKEN_GROUP_WIDTH / 2;
  const tokenY = (tokens: number) => PAD.top + (1 - tokens / maxTokenAmount) * (HEIGHT - PAD.top - PAD.bottom);
  const tokenPayload = (run: RunRecord, kind: 'input' | 'output', clientX: number, clientY: number): TooltipPayload => {
    const input = run.total_input_tokens ?? 0;
    const output = run.total_output_tokens ?? 0;
    return {
      id: `token:${kind}:${run.id}`,
      x: clientX,
      y: clientY,
      title: `${kind === 'input' ? 'Input' : 'Output'} tokens`,
      lines: [
        `agent spec: ${run.agent_spec}`,
        `run: ${runLabel(run.id)}`,
        `outcome: ${fmtScore(run.composite_score)}`,
        `input: ${fmtTokens(input)}`,
        `output: ${fmtTokens(output)}`,
        `total: ${fmtTokens(input + output)}`,
        run.id,
      ],
    };
  };

  return (
    <div className="sb overflow-x-auto">
      <svg viewBox={`0 0 ${chartWidth} ${HEIGHT}`} className="min-w-full" style={{ width: chartWidth }} role="img" aria-label="Input and output token spend by agent spec">
        {[0, 0.25, 0.5, 0.75, 1].map((tick) => {
          const ty = PAD.top + (1 - tick) * (HEIGHT - PAD.top - PAD.bottom);
          return (
            <g key={tick}>
              <line x1={PAD.left} x2={chartWidth - PAD.right} y1={ty} y2={ty} stroke="rgba(255,255,255,0.05)" />
              <text x={PAD.left - 6} y={ty + 3} textAnchor="end" fontSize="8" fill={C.fg0}>
                {tokenTickLabel(maxTokenAmount * tick)}
              </text>
            </g>
          );
        })}
        {runs.map((run, index) => {
          const tx = tokenGroupX(index);
          return <line key={`${run.id}-grid`} x1={tx} x2={tx} y1={PAD.top} y2={HEIGHT - PAD.bottom} stroke="rgba(255,255,255,0.04)" />;
        })}
        <text x={(chartWidth - PAD.right + PAD.left) / 2} y={HEIGHT - 3} textAnchor="middle" fontSize="8" fill={C.fg0}>
          agent spec
        </text>
        {runs.map((run, index) => {
          const centerX = tokenGroupX(index);
          const baseY = HEIGHT - PAD.bottom;
          const barWidth = 8;
          const gap = 3;
          const input = run.total_input_tokens ?? 0;
          const output = run.total_output_tokens ?? 0;
          const inputY = tokenY(input);
          const outputY = tokenY(output);

          return (
            <g key={run.id}>
              <rect
                x={centerX - barWidth - gap / 2}
                y={inputY}
                width={barWidth}
                height={Math.max(baseY - inputY, 1)}
                rx={1}
                fill={TOKEN_COLORS.input}
                fillOpacity={0.84}
                style={{ cursor: 'pointer' }}
                onMouseEnter={(event) => onOpenTooltip(tokenPayload(run, 'input', event.clientX, event.clientY))}
                onMouseMove={(event) => onOpenTooltip(tokenPayload(run, 'input', event.clientX, event.clientY))}
                onMouseLeave={onCloseTooltip}
                onClick={() => navigate(`/runs/${encodeURIComponent(run.id)}`)}
              />
              <rect
                x={centerX + gap / 2}
                y={outputY}
                width={barWidth}
                height={Math.max(baseY - outputY, 1)}
                rx={1}
                fill={TOKEN_COLORS.output}
                fillOpacity={0.84}
                style={{ cursor: 'pointer' }}
                onMouseEnter={(event) => onOpenTooltip(tokenPayload(run, 'output', event.clientX, event.clientY))}
                onMouseMove={(event) => onOpenTooltip(tokenPayload(run, 'output', event.clientX, event.clientY))}
                onMouseLeave={onCloseTooltip}
                onClick={() => navigate(`/runs/${encodeURIComponent(run.id)}`)}
              />
              <text x={centerX} y={Math.min(inputY, outputY) - 4} textAnchor="middle" fontSize="7" fill={C.fg0}>
                {fmtScore(run.composite_score)}
              </text>
              <text x={centerX} y={HEIGHT - PAD.bottom + 10} textAnchor="middle" fontSize="7" fill={C.fg0}>
                {modelLabel(run.agent_spec)}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
