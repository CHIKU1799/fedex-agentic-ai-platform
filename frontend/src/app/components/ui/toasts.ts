import { Component } from '@angular/core';
import { ToastService } from './toast.service';

@Component({
  selector: 'app-toasts',
  standalone: true,
  template: `
    <div class="toasts" aria-live="polite">
      @for (t of toasts.toasts(); track t.id) {
        <div class="toast" [class]="'toast toast--' + t.kind">
          <span>{{ t.text }}</span>
          <button class="toast__x" (click)="toasts.dismiss(t.id)" aria-label="Dismiss">✕</button>
        </div>
      }
    </div>
  `,
  styles: [`
    .toasts { position: fixed; right: 20px; bottom: 20px; z-index: 150; display: flex; flex-direction: column; gap: 8px; max-width: min(380px, calc(100vw - 32px)); }
    .toast { display: flex; align-items: flex-start; gap: 12px; padding: 12px 14px; border-radius: 12px; background: var(--ink); color: #fff; font-size: 14px; line-height: 1.4; box-shadow: 0 16px 40px -20px rgba(0,0,0,.5); animation: toast-in .2s ease; border-left: 4px solid var(--purple); }
    .toast--ok { border-left-color: #3ccf84; }
    .toast--bad { border-left-color: #ff6b6b; }
    .toast__x { margin-left: auto; background: transparent; border: none; color: rgba(255,255,255,.7); font-size: 12px; }
    .toast__x:hover { color: #fff; }
    @keyframes toast-in { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
    @media (max-width: 820px) { .toasts { bottom: 96px; right: 16px; } }
  `]
})
export class ToastsComponent {
  constructor(public toasts: ToastService) {}
}
