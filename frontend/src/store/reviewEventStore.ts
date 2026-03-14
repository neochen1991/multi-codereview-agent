import type { ReviewEvent } from "@/services/api";

type Listener = () => void;

// 轻量事件存储用于在多个组件之间共享同一条 review 的事件时间线。
class ReviewEventStore {
  private listeners = new Set<Listener>();
  private eventMap = new Map<string, ReviewEvent[]>();

  subscribe(listener: Listener): () => void {
    // 订阅事件更新，并返回取消订阅函数。
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  getEvents(reviewId: string): ReviewEvent[] {
    // 获取某次审核当前缓存的事件列表。
    return this.eventMap.get(reviewId) || [];
  }

  replace(reviewId: string, events: ReviewEvent[]): void {
    // 用后端最新快照整体替换本地事件缓存。
    this.eventMap.set(reviewId, events);
    this.emit();
  }

  append(reviewId: string, event: ReviewEvent): void {
    // 在 SSE 实时模式下向事件缓存追加一条新事件。
    const current = this.getEvents(reviewId);
    this.eventMap.set(reviewId, [...current, event]);
    this.emit();
  }

  private emit(): void {
    // 通知所有订阅者重新读取事件缓存。
    this.listeners.forEach((listener) => listener());
  }
}

export const reviewEventStore = new ReviewEventStore();
