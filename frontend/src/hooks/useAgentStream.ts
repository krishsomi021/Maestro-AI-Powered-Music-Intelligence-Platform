import { useCallback, useRef, useState } from 'react';
import { API_BASE_URL } from '../constants';

// ---------------------------------------------------------------------------
// Shared types — exported so components can import them from one place
// ---------------------------------------------------------------------------

export interface ChartSpec {
  type: string;
  title: string;
  x: string[];
  y: number[];
}

export interface ToolCallEntry {
  call_id: string;
  tool_name: string;
  tool_input: Record<string, unknown>;
  status: 'running' | 'done';
  success?: boolean;
  error?: string | null;
  chart_spec?: ChartSpec | null;
  latency_ms?: number;
}

export interface AgentMessage {
  role: 'user' | 'assistant';
  content: string;
  tool_calls: ToolCallEntry[];
  charts: ChartSpec[];
}

// ---------------------------------------------------------------------------
// Wire event shapes (match Python dataclass fields exactly)
// ---------------------------------------------------------------------------

type WireEvent =
  | { type: 'text_chunk'; text: string }
  | { type: 'tool_start'; call_id: string; tool_name: string; tool_input: Record<string, unknown>; iteration: number }
  | { type: 'tool_end'; call_id: string; tool_name: string; success: boolean; data: unknown; error: string | null; chart_spec: ChartSpec | null; latency_ms: number; iteration: number }
  | { type: 'done'; total_iterations: number }
  | { type: 'error'; message: string };

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useAgentStream() {
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const sessionIdRef = useRef<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  function ensureSessionId(): string {
    if (!sessionIdRef.current) {
      sessionIdRef.current = crypto.randomUUID();
    }
    return sessionIdRef.current;
  }

  const sendMessage = useCallback(async (text: string) => {
    if (!text.trim()) return;

    // Cancel any in-flight stream before starting a new one.
    if (abortRef.current) {
      abortRef.current.abort();
    }
    const controller = new AbortController();
    abortRef.current = controller;

    const sessionId = ensureSessionId();

    // Append the user turn and an empty assistant placeholder atomically.
    const userMsg: AgentMessage = { role: 'user', content: text, tool_calls: [], charts: [] };
    const assistantMsg: AgentMessage = { role: 'assistant', content: '', tool_calls: [], charts: [] };
    setMessages(prev => [...prev, userMsg, assistantMsg]);
    setIsStreaming(true);

    try {
      const response = await fetch(`${API_BASE_URL}/agent/message`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, user_message: text }),
        signal: controller.signal,
      });

      if (!response.ok) {
        const body = await response.json().catch(() => null);
        const detail =
          body && typeof body === 'object' && 'detail' in body
            ? String(body.detail)
            : response.statusText;
        setMessages(prev => {
          const next = [...prev];
          const last = { ...next[next.length - 1] };
          last.content = `Error ${response.status}: ${detail}`;
          next[next.length - 1] = last;
          return next;
        });
        return;
      }

      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      // eslint-disable-next-line no-constant-condition
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        // SSE frames are delimited by blank lines ("\n\n").
        // Split on the delimiter; the last element is the incomplete tail.
        const frames = buffer.split('\n\n');
        buffer = frames.pop() ?? '';

        for (const frame of frames) {
          const line = frame.trim();
          if (!line.startsWith('data: ')) continue;

          let event: WireEvent;
          try {
            event = JSON.parse(line.slice('data: '.length)) as WireEvent;
          } catch {
            continue;
          }

          if (event.type === 'text_chunk') {
            const chunk = event.text;
            // Immutable update: copy the array and the last message object,
            // append the chunk, return the new array.
            setMessages(prev => {
              const next = [...prev];
              const last = { ...next[next.length - 1] };
              last.content = last.content + chunk;
              next[next.length - 1] = last;
              return next;
            });
          } else if (event.type === 'tool_start') {
            const entry: ToolCallEntry = {
              call_id: event.call_id,
              tool_name: event.tool_name,
              tool_input: event.tool_input,
              status: 'running',
            };
            setMessages(prev => {
              const next = [...prev];
              const last = { ...next[next.length - 1] };
              last.tool_calls = [...last.tool_calls, entry];
              next[next.length - 1] = last;
              return next;
            });
          } else if (event.type === 'tool_end') {
            const { call_id, success, error, chart_spec, latency_ms } = event;
            setMessages(prev => {
              const next = [...prev];
              const last = { ...next[next.length - 1] };
              last.tool_calls = last.tool_calls.map(tc =>
                tc.call_id === call_id
                  ? { ...tc, status: 'done' as const, success, error, chart_spec, latency_ms }
                  : tc,
              );
              if (chart_spec) {
                last.charts = [...last.charts, chart_spec];
              }
              next[next.length - 1] = last;
              return next;
            });
          } else if (event.type === 'done') {
            setIsStreaming(false);
          } else if (event.type === 'error') {
            const msg = event.message;
            setMessages(prev => {
              const next = [...prev];
              const last = { ...next[next.length - 1] };
              last.content = last.content || `Agent error: ${msg}`;
              next[next.length - 1] = last;
              return next;
            });
            setIsStreaming(false);
          }
        }
      }
    } catch (err) {
      if ((err as Error).name === 'AbortError') return;
      const msg = (err as Error).message;
      setMessages(prev => {
        const next = [...prev];
        const last = { ...next[next.length - 1] };
        last.content = last.content || `Network error: ${msg}`;
        next[next.length - 1] = last;
        return next;
      });
    } finally {
      setIsStreaming(false);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const newChat = useCallback(() => {
    // Abort any in-flight stream first.
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    // Fire-and-forget the DELETE — do not block the UI reset on it.
    const oldId = sessionIdRef.current;
    if (oldId) {
      fetch(`${API_BASE_URL}/agent/session/${oldId}`, { method: 'DELETE' }).catch(() => {});
    }
    sessionIdRef.current = crypto.randomUUID();
    setMessages([]);
    setIsStreaming(false);
  }, []);

  return { messages, isStreaming, sendMessage, newChat };
}
