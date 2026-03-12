import type { ReviewEvent } from "@/services/api";

type Listener = () => void;

class ReviewEventStore {
  private listeners = new Set<Listener>();
  private eventMap = new Map<string, ReviewEvent[]>();

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  getEvents(reviewId: string): ReviewEvent[] {
    return this.eventMap.get(reviewId) || [];
  }

  replace(reviewId: string, events: ReviewEvent[]): void {
    this.eventMap.set(reviewId, events);
    this.emit();
  }

  append(reviewId: string, event: ReviewEvent): void {
    const current = this.getEvents(reviewId);
    this.eventMap.set(reviewId, [...current, event]);
    this.emit();
  }

  private emit(): void {
    this.listeners.forEach((listener) => listener());
  }
}

export const reviewEventStore = new ReviewEventStore();
