import { Component, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { CommonModule } from '@angular/common';
import { ApiService } from '../../services/api.service';

interface Notification {
  id: string;
  tracking_id: string;
  message: string;
  notification_type: string;
  is_read: string;
  created_at?: string;
}

interface ActivityEvent {
  tool: string;
  ok: boolean;
  note: string;
  tracking_id: string;
  at: string | null;
}

interface Commitment {
  commitment_id: string;
  tracking_id: string;
  target_date: string;
  status: string;
  risk_reason: string;
}

/**
 * Updates: everything that happened to a customer's deliveries in one
 * place. Alerts FedE raised, the delivery goals it is watching, and the
 * record of every action it took. Customers see their own; agents can
 * look up any customer.
 */
@Component({
  selector: 'app-updates',
  standalone: true,
  imports: [FormsModule, CommonModule],
  templateUrl: './updates.html',
  styleUrl: './updates.css'
})
export class UpdatesComponent implements OnInit {
  customerId = signal('');
  notifications = signal<Notification[]>([]);
  events = signal<ActivityEvent[]>([]);
  commitments = signal<Commitment[]>([]);
  loading = signal(false);
  error = signal('');

  constructor(public api: ApiService) {}

  ngOnInit(): void {
    this.customerId.set(this.api.ownCustomerId() || 'CUST001');
    this.load();
  }

  isAgent(): boolean {
    return this.api.currentUser()?.role === 'agent';
  }

  load(): void {
    const id = this.customerId().trim().toUpperCase();
    if (!id) return;
    this.customerId.set(id);
    this.loading.set(true);
    this.error.set('');

    this.api.getNotifications(id).subscribe({
      next: (res) => { this.notifications.set(res.notifications || []); this.loading.set(false); },
      error: (err) => {
        this.error.set(err.status === 403 ? 'You can only see updates for your own account.' : `No updates found for ${id}.`);
        this.loading.set(false);
      }
    });
    this.api.getActivity(id).subscribe({
      next: (res) => this.events.set(res.events || []),
      error: () => this.events.set([])
    });
    this.api.getCommitments(id).subscribe({
      next: (res) => this.commitments.set(res.commitments || []),
      error: () => this.commitments.set([])
    });
  }

  unreadCount(): number {
    return this.notifications().filter(n => n.is_read !== 'true').length;
  }

  markRead(id: string): void {
    this.api.markNotificationRead(id).subscribe({
      next: () => this.notifications.update(list => list.map(n => n.id === id ? { ...n, is_read: 'true' } : n))
    });
  }

  typeLabel(t: string): string {
    const labels: Record<string, string> = {
      delay: 'Delay', eta_update: 'New delivery date', delivered: 'Delivered', exception: 'Problem',
      weather_alert: 'Weather', delivery_attempt: 'Missed delivery', hold: 'Held for pickup',
      commitment_at_risk: 'Goal at risk', commitment_met: 'Goal met', commitment_recovered: 'Back on track',
    };
    return labels[t] || (t || '').replace(/_/g, ' ');
  }

  when(at?: string | null): string {
    if (!at) return '';
    const d = new Date(at);
    if (isNaN(d.getTime())) return '';
    return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  }
}
