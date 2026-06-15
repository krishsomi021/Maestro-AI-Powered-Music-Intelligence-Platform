import type { AgentMessage } from '../../hooks/useAgentStream';
import { ChartRenderer } from './ChartRenderer';
import { ToolCallPanel } from './ToolCallPanel';
import './MessageBubble.css';

interface Props {
  message: AgentMessage;
}

export function MessageBubble({ message }: Props) {
  const isUser = message.role === 'user';

  return (
    <div className={`message-bubble message-bubble--${message.role}`}>
      <div className="message-bubble__body">
        {!isUser && message.tool_calls.length > 0 && (
          <ToolCallPanel toolCalls={message.tool_calls} />
        )}
        {/* pre preserves streamed line breaks; font: inherit keeps body font */}
        <pre className="message-bubble__text">{message.content}</pre>
        {!isUser && message.charts.map((spec, i) => (
          <ChartRenderer key={i} spec={spec} />
        ))}
      </div>
    </div>
  );
}
