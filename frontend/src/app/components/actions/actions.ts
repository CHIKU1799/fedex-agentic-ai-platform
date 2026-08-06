import { Component, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { CommonModule } from '@angular/common';
import { ApiService } from '../../services/api.service';

@Component({
  selector: 'app-actions',
  standalone: true,
  imports: [FormsModule, CommonModule],
  templateUrl: './actions.html',
  styleUrl: './actions.css'
})
export class ActionsComponent {
  activeAction = signal<'reschedule' | 'redirect' | 'cancel'>('reschedule');
  trackingId = signal('');
  newDate = signal('');
  newAddress = signal('');
  reason = signal('');
  result = signal<any>(null);
  error = signal('');
  loading = signal(false);

  constructor(private api: ApiService) {}

  setAction(action: 'reschedule' | 'redirect' | 'cancel') {
    this.activeAction.set(action);
    this.result.set(null);
    this.error.set('');
  }

  submit() {
    const id = this.trackingId().trim();
    if (!id) { this.error.set('Please enter a tracking ID'); return; }

    this.loading.set(true);
    this.error.set('');
    this.result.set(null);

    const action = this.activeAction();

    if (action === 'reschedule') {
      if (!this.newDate()) { this.error.set('Please enter a new date'); this.loading.set(false); return; }
      this.api.reschedule(id, this.newDate()).subscribe({
        next: (r) => { this.result.set(r); this.loading.set(false); },
        error: (e) => { this.error.set(e.error?.detail || 'Failed'); this.loading.set(false); }
      });
    } else if (action === 'redirect') {
      if (!this.newAddress()) { this.error.set('Please enter a new address'); this.loading.set(false); return; }
      this.api.redirect(id, this.newAddress()).subscribe({
        next: (r) => { this.result.set(r); this.loading.set(false); },
        error: (e) => { this.error.set(e.error?.detail || 'Failed'); this.loading.set(false); }
      });
    } else {
      this.api.cancel(id, this.reason()).subscribe({
        next: (r) => { this.result.set(r); this.loading.set(false); },
        error: (e) => { this.error.set(e.error?.detail || 'Failed'); this.loading.set(false); }
      });
    }
  }
}
