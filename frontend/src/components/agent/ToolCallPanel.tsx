import type { ToolCallEntry } from '../../hooks/useAgentStream';
import './ToolCallPanel.css';

interface Props {
  toolCalls: ToolCallEntry[];
}

function statusLabel(tc: ToolCallEntry): 'running' | 'done' | 'error' {
  if (tc.status === 'running') return 'running';
  if (tc.success === false) return 'error';
  return 'done';
}

export function ToolCallPanel({ toolCalls }: Props) {
  if (toolCalls.length === 0) return null;

  return (
    <details className="tool-panel">
      <summary className="tool-panel__summary">
        {toolCalls.length} tool call{toolCalls.length !== 1 ? 's' : ''} used
      </summary>
      <div className="tool-panel__list">
        {toolCalls.map((tc, i) => {
          const status = statusLabel(tc);
          return (
            <div key={tc.call_id || i} className="tool-panel__entry">
              <div className="tool-panel__entry-header">
                <code className="tool-panel__name">{tc.tool_name}</code>
                <span className={`tool-panel__badge tool-panel__badge--${status}`}>
                  {status}
                </span>
                {tc.latency_ms !== undefined && (
                  <span className="tool-panel__latency">{tc.latency_ms}ms</span>
                )}
              </div>
              <pre className="tool-panel__input">
                {JSON.stringify(tc.tool_input, null, 2)}
              </pre>
              {tc.error && <p className="tool-panel__error">{tc.error}</p>}
            </div>
          );
        })}
      </div>
    </details>
  );
}
