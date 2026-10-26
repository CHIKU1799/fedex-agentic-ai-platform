import { Component, computed, input, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

/**
 * Verification pop-up for high-impact changes. Shows exactly what will
 * change, and for irreversible actions makes the customer type the
 * tracking number so a stray click cannot cancel a shipment. Mirrors
 * the backend's autonomy tier 3 rule: the UI asks, the tool layer
 * refuses without confirmation, so both sides must agree.
 */
@Component({
  selector: 'app-confirm-dialog',
  standalone: true,
  imports: [FormsModule],
  template: `
    <div class="cd__backdrop" (click)="cancel.emit()"></div>
    <div class="cd" role="dialog" aria-modal="true" [attr.aria-labelledby]="'cd-title'">
      <p class="cd__kicker" [class.cd__kicker--danger]="danger()">{{ danger() ? 'Cannot be undone' : 'Please confirm' }}</p>
      <h3 id="cd-title">{{ title() }}</h3>
      <dl class="cd__facts">
        @for (f of facts(); track f.label) {
          <div><dt>{{ f.label }}</dt><dd>{{ f.value }}</dd></div>
        }
      </dl>
      @if (typeToConfirm()) {
        <label class="cd__type" for="cd-type">Type <b>{{ typeToConfirm() }}</b> to confirm</label>
        <input id="cd-type" class="input" type="text" autocomplete="off" [ngModel]="typed()" (ngModelChange)="typed.set($event)" (keyup.enter)="ready() && confirm.emit()" />
      }
      <div class="cd__actions">
        <button class="btn btn--quiet" type="button" (click)="cancel.emit()">Keep it as it is</button>
        <button class="btn" type="button" [class.btn--primary]="!danger()" [class.btn--danger]="danger()"
                [disabled]="!ready() || busy()" (click)="confirm.emit()">
          {{ busy() ? 'Working…' : confirmLabel() }}
        </button>
      </div>
    </div>
  `,
  styles: [`
    .cd__backdrop { position: fixed; inset: 0; background: rgba(28, 21, 48, .45); z-index: 200; }
    .cd {
      position: fixed; z-index: 201; left: 50%; top: 50%; transform: translate(-50%, -50%);
      width: min(440px, calc(100vw - 32px)); background: var(--surface); border: 1px solid var(--line);
      border-radius: var(--radius-lg); padding: 24px; display: flex; flex-direction: column; gap: 14px;
      box-shadow: 0 30px 70px -30px rgba(46, 10, 92, .5);
      animation: cd-in .18s ease;
    }
    @keyframes cd-in { from { opacity: 0; transform: translate(-50%, -48%); } to { opacity: 1; transform: translate(-50%, -50%); } }
    .cd__kicker { font-size: 13px; font-weight: 600; color: var(--warn); }
    .cd__kicker--danger { color: var(--bad); }
    .cd h3 { font-size: 22px; }
    .cd__facts { display: flex; flex-direction: column; gap: 6px; border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); padding: 12px 0; }
    .cd__facts div { display: grid; grid-template-columns: 110px 1fr; gap: 8px; font-size: 14px; }
    .cd__facts dt { color: var(--muted); }
    .cd__type { font-size: 14px; color: var(--muted); }
    .cd__type b { font-family: var(--mono); color: var(--ink); }
    .cd__actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 4px; }
    @media (max-width: 480px) { .cd__actions { flex-direction: column-reverse; } .cd__actions .btn { width: 100%; } }
  `]
})
export class ConfirmDialogComponent {
  title = input.required<string>();
  facts = input<{ label: string; value: string }[]>([]);
  confirmLabel = input('Confirm');
  danger = input(false);
  typeToConfirm = input<string | null>(null);
  busy = input(false);
  confirm = output<void>();
  cancel = output<void>();

  typed = signal('');
  ready = computed(() => {
    const need = this.typeToConfirm();
    return !need || this.typed().trim().toUpperCase() === need.toUpperCase();
  });
}
