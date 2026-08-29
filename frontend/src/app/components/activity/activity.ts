import { Component, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ApiService } from '../../services/api.service';

interface ActivityEvent {
  tool: string;
  ok: boolean;
  note: string;
  tracking_id: string;
  cost_avoided: number;
  at: string | null;
}

interface Commitment {
  commitment_id: string;
  tracking_id: string;
  target_date: string;
  status: string;
  priority: string;
  risk_reason: string;
}

/**
 * FedE Activity: the customer-facing proof-of-work feed. Every action the
 * agent took on this customer's behalf (with verification and estimated
 * cost avoided), plus the delivery commitments FedE is currently watching.
 */
@Component({
  selector: 'app-activity',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './activity.html',
  styleUrl: './activity.css'
})
export class ActivityComponent implements OnInit {
  events = signal<ActivityEvent[]>([]);
  commitments = signal<Commitment[]>([]);
  totalCostAvoided = signal(0);
  loading = signal(false);
  error = signal('');

  constructor(private api: ApiService) {}

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    const customerId = this.api.ownCustomerId();
    if (!customerId) {
      this.error.set('Sign in as a customer to see your FedE activity.');
      return;
    }
    this.loading.set(true);
    this.error.set('');
    this.api.getActivity(customerId).subscribe({
      next: (res) => {
        this.events.set(res.events || []);
        this.totalCostAvoided.set(res.total_cost_avoided || 0);
        this.loading.set(false);
      },
      error: () => {
        this.error.set('Could not load your activity feed.');
        this.loading.set(false);
      }
    });
    this.api.getCommitments(customerId).subscribe({
      next: (res) => this.commitments.set(res.commitments || []),
      error: () => { /* commitments panel is optional */ }
    });
  }

  icon(e: ActivityEvent): string {
    if (!e.ok) return '⚠️';
    if (e.tool.startsWith('commitment')) return '🎯';
    if (e.tool === 'create_support_case') return '🤝';
    if (e.cost_avoided > 0) return '💰';
    return '✓';
  }

  when(at: string | null): string {
    if (!at) return '';
    const d = new Date(at);
    return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  }

  commitmentBadge(status: string): string {
    // Status is never color-alone: the word itself is the badge text.
    return 'cmt-badge cmt-badge--' + status.toLowerCase();
  }
}
