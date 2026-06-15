import { useEffect, useRef, useState } from 'react';
import { MessageBubble } from '../components/agent/MessageBubble';
import { useAgentStream } from '../hooks/useAgentStream';
import './Agent.css';

const EXAMPLE_QUESTIONS = [
  'Give me a full profile of my listening habits',
  'Who are my top 5 artists of all time?',
  'Recommend tracks similar to music I love',
  'What hour of the day do I listen to music most?',
];

export function AgentPage() {
  const { messages, isStreaming, sendMessage, newChat } = useAgentStream();
  const [input, setInput] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to the bottom whenever messages change (streaming or new turn).
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  function handleSend() {
    const text = input.trim();
    if (!text || isStreaming) return;
    setInput('');
    void sendMessage(text);
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  function handleExampleClick(question: string) {
    setInput(question);
  }

  return (
    <div className="agent-page">
      <div className="agent-page__toolbar">
        <h1 className="agent-page__title">Spotify Analyst</h1>
        <button className="secondary" onClick={newChat} disabled={isStreaming}>
          New chat
        </button>
      </div>

      <div className="agent-page__messages">
        {messages.length === 0 && (
          <div className="agent-page__empty">
            <p className="agent-page__empty-hint">
              Ask me anything about your Spotify listening history.
            </p>
            <div className="agent-page__examples">
              {EXAMPLE_QUESTIONS.map((q) => (
                <button
                  key={q}
                  className="secondary agent-page__example-btn"
                  onClick={() => handleExampleClick(q)}
                  disabled={isStreaming}
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg, i) => (
          <MessageBubble key={i} message={msg} />
        ))}

        {/* Scroll anchor — kept at the bottom of the list */}
        <div ref={messagesEndRef} />
      </div>

      <div className="agent-page__input-row">
        <textarea
          className="agent-page__input"
          rows={2}
          placeholder="Ask about your listening history… (Enter to send, Shift+Enter for newline)"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={isStreaming}
        />
        <button
          className="agent-page__send-btn"
          onClick={handleSend}
          disabled={isStreaming || !input.trim()}
        >
          {isStreaming ? 'Sending…' : 'Send'}
        </button>
      </div>
    </div>
  );
}
