import { Component, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { CommonModule } from '@angular/common';
import { ApiService } from '../../services/api.service';
import { JourneyMapComponent, JourneyData } from '../journey-map/journey-map';

interface ShipmentChip {
  tracking_id: string;
  status: string;
  eta?: string;
}

type Action = 'reschedule' | 'hold' | 'redirect' | 'cancel';

/**
 * Shipments: pick a package, see where it is on the map, and change the
 * delivery right there (reschedule, hold for pickup, redirect, cancel).
 * Redirect and cancel ask for confirmation, matching the agent's own
 * autonomy rules.
 */
@Component({
  selector: 'app-tracking',
  standalone: true,
  imports: [FormsModule, CommonModule, JourneyMapComponent],
  templateUrl: './tracking.html',
  styleUrl: './tracking.css'
})
export class TrackingComponent implements OnInit {
  trackingId = signal('');
  result = signal<any>(null);
  journey = signal<JourneyData | null>(null);
  chips = signal<ShipmentChip[]>([]);
  error = signal('');
  loading = signal(false);

  // Manage panel
  action = signal<Action | null>(null);
  newDate = signal('');
  newAddress = signal('');
  reason = signal('');
  confirming = signal(false);
  saving = signal(false);
  actionError = signal('');
  actionDone = signal('');

  // Seeded demo IDs shown to agents (customers get their real list).
  private readonly demoIds = ['FX100001', 'FX100002', 'FX100003', 'FX100004', 'FX100005', 'FX100006'];

  constructor(private api: ApiService) {}

  ngOnInit(): void {
    const customerId = this.api.ownCustomerId();
    if (customerId) {
      this.api.getShipments(customerId).subscribe({
        next: (res) => {
          this.chips.set(res.shipments || []);
          const first = (res.shipments || [])[0];
          if (first && !this.result()) this.trackId(first.tracking_id);
        },
        error: () => { /* chips are a convenience */ }
      });
    } else {
      this.chips.set(this.demoIds.map(id => ({ tracking_id: id, status: '' })));
      this.trackId(this.demoIds[0]);
    }
  }

  trackId(id: string): void {
    this.trackingId.set(id);
    this.track();
  }

  track(): void {
    const id = this.trackingId().trim().toUpperCase();
    if (!id || this.loading()) return;
    this.loading.set(true);
    this.error.set('');
    this.journey.set(null);
    this.resetAction();

    this.api.trackShipment(id).subscribe({
      next: (data) => {
        this.result.set(data);
        this.loading.set(false);
        this.loadJourney(id);
      },
      error: (err) => {
        this.result.set(null);
        this.error.set(err.error?.detail || `No shipment found for ${id}.`);
        this.loading.set(false);
      }
    });
  }

  private loadJourney(id: string): void {
    this.api.getJourney(id).subscribe({
      next: (j) => this.journey.set(j as JourneyData),
      error: () => { /* map is a bonus, details already rendered */ }
    });
  }

  // ── Manage this delivery ───────────────────────────

  canChange(): boolean {
    const s = this.result()?.status;
    return !!s && !['delivered', 'cancelled'].includes(s);
  }

  pick(action: Action): void {
    this.action.set(this.action() === action ? null : action);
    this.confirming.set(false);
    this.actionError.set('');
    this.actionDone.set('');
  }

  resetAction(): void {
    this.action.set(null);
    this.confirming.set(false);
    this.actionError.set('');
    this.actionDone.set('');
    this.newDate.set('');
    this.newAddress.set('');
    this.reason.set('');
  }

  actionLabel(a: Action): string {
    return { reschedule: 'Reschedule', hold: 'Hold for pickup', redirect: 'Redirect', cancel: 'Cancel shipment' }[a];
  }

  submit(): void {
    const id = this.result()?.tracking_id;
    const action = this.action();
    if (!id || !action || this.saving()) return;

    if (action === 'reschedule' && !this.newDate()) { this.actionError.set('Pick the new delivery date.'); return; }
    if (action === 'redirect' && this.newAddress().trim().length < 3) { this.actionError.set('Enter the full new address.'); return; }

    // High-impact changes wait for an explicit confirmation.
    if ((action === 'redirect' || action === 'cancel') && !this.confirming()) {
      this.confirming.set(true);
      this.actionError.set('');
      return;
    }

    this.saving.set(true);
    this.actionError.set('');
    const call = action === 'reschedule' ? this.api.reschedule(id, this.newDate())
      : action === 'hold' ? this.api.hold(id)
      : action === 'redirect' ? this.api.redirect(id, this.newAddress().trim())
      : this.api.cancel(id, this.reason().trim());

    call.subscribe({
      next: (r) => {
        this.saving.set(false);
        this.confirming.set(false);
        this.actionDone.set(this.doneText(action, r));
        this.action.set(null);
        // Refresh the card, the chip list and the map with the new state.
        this.api.trackShipment(id).subscribe({ next: (d) => this.result.set(d) });
        this.loadJourney(id);
        this.chips.update(list => list.map(c => c.tracking_id === id
          ? { ...c, status: r.status || c.status, eta: r.new_eta || c.eta } : c));
      },
      error: (e) => {
        this.saving.set(false);
        this.confirming.set(false);
        this.actionError.set(e.error?.detail || 'That change did not go through. Try again or ask FedE.');
      }
    });
  }

  private doneText(action: Action, r: any): string {
    if (action === 'reschedule') return `Rescheduled. New delivery date: ${r.new_eta || this.newDate()}.`;
    if (action === 'hold') return r.pickup_location ? `Held for pickup at ${r.pickup_location}.` : 'Hold for pickup requested.';
    if (action === 'redirect') return `Redirected to ${r.new_destination || this.newAddress()}.`;
    return 'Shipment cancelled.';
  }

  // ── Display helpers ────────────────────────────────

  pillClass(status: string): string {
    return 'pill pill--' + (status || 'unknown').toLowerCase();
  }

  statusLabel(status: string): string {
    return (status || '').replace(/_/g, ' ');
  }

  /** Big number: time until the planned delivery date, from the ETA itself. */
  countdown(): string {
    const r = this.result();
    if (!r) return '';
    if (r.status === 'delivered') return 'Delivered';
    if (r.status === 'cancelled') return 'Cancelled';
    const days = this.daysUntil(r.eta);
    if (days === null) return r.eta || 'Date pending';
    if (days < 0) return 'Overdue';
    if (days === 0) return 'Today';
    if (days === 1) return 'Tomorrow';
    return `${days} days`;
  }

  countdownLabel(): string {
    const r = this.result();
    if (!r) return '';
    if (r.status === 'delivered') return `on ${r.eta}`;
    if (r.status === 'cancelled') return 'this shipment will not be delivered';
    const days = this.daysUntil(r.eta);
    if (days === null) return 'planned delivery date';
    if (days < 0) return `was due ${r.eta}, FedE is on it`;
    return `until delivery, ${r.eta}`;
  }

  private daysUntil(eta: string): number | null {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(eta || '');
    if (!m) return null;
    const target = Date.UTC(+m[1], +m[2] - 1, +m[3]);
    const now = new Date();
    const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
    return Math.round((target - today) / 86400000);
  }

  destinationRisk(): string {
    const hubs = this.journey()?.route?.route?.hubs;
    return hubs?.length ? (hubs[hubs.length - 1].risk || '') : '';
  }

  riskText(risk: string): string {
    return risk === 'high' ? 'Storm risk at the destination'
      : risk === 'moderate' ? 'Weather watch at the destination'
      : 'Clear skies at the destination';
  }

  minDate(): string {
    return new Date().toISOString().slice(0, 10);
  }
}
