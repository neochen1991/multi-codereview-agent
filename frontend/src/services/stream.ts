import type { ReviewEvent } from "@/services/api";

// 把后端事件流补齐为稳定的 ReviewEvent 结构，避免页面直接消费脏数据。
export const normalizeReviewEvent = (input: Partial<ReviewEvent>): ReviewEvent => ({
  event_id: String(input.event_id || ""),
  review_id: String(input.review_id || ""),
  event_type: String(input.event_type || "unknown"),
  phase: String(input.phase || "unknown"),
  message: String(input.message || ""),
  created_at: String(input.created_at || new Date().toISOString()),
  payload: (input.payload || {}) as Record<string, unknown>,
});

export const subscribeReviewEventStream = (
  url: string,
  onEvent: (event: ReviewEvent) => void,
): (() => void) => {
  // 工作台过程页通过 SSE 订阅审核事件流，这里统一封装连接和清理逻辑。
  const source = new EventSource(url);
  source.addEventListener("message", (message) => {
    try {
      const payload = JSON.parse((message as MessageEvent).data) as ReviewEvent;
      onEvent(normalizeReviewEvent(payload));
    } catch {
      // ignore malformed payloads from the fallback stream endpoint
    }
  });
  return () => source.close();
};
