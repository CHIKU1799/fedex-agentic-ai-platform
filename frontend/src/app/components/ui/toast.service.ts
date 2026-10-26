import { Injectable, signal } from '@angular/core';

export interface Toast { id: number; kind: 'ok' | 'bad' | 'info'; text: string; }

/** Small, self-dismissing confirmations for things that just happened. */
@Injectable({ providedIn: 'root' })
export class ToastService {
  toasts = signal<Toast[]>([]);
  private seq = 0;

  show(kind: Toast['kind'], text: string, ms = 4500): void {
    const id = ++this.seq;
    this.toasts.update(t => [...t, { id, kind, text }]);
    setTimeout(() => this.dismiss(id), ms);
  }

  dismiss(id: number): void {
    this.toasts.update(t => t.filter(x => x.id !== id));
  }
}
