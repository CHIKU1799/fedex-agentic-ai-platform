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

/**
 * Track tab as a live map command center: the customer's shipments as
 * one-tap chips, a details panel with the live countdown and weather risk,
 * and the journey map with checkpoint tracing as the centerpiece.
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

    this.api.trackShipment(id).subscribe({
      next: (data) => {
        this.result.set(data);
        this.loading.set(false);
        this.api.getJourney(id).subscribe({
          next: (j) => this.journey.set(j as JourneyData),
          error: () => { /* map is a bonus, details already rendered */ }
        });
      },
      error: (err) => {
        this.result.set(null);
        this.error.set(err.error?.detail || 'Shipment not found');
        this.loading.set(false);
      }
    });
  }

  statusClass(status: string): string {
    return 'st st--' + (status || 'unknown').toLowerCase();
  }

  statusLabel(status: string): string {
    return (status || '').replace(/_/g, ' ');
  }

  countdown(): string {
    const j = this.journey();
    if (!j || j.hours_remaining === null || j.hours_remaining === undefined) {
      return this.result()?.eta || '';
    }
    if (j.status === 'delivered') return 'Delivered';
    if (j.hours_remaining === 0) return 'Today';
    const days = Math.floor(j.hours_remaining / 24);
    const hours = j.hours_remaining % 24;
    if (days === 0) return `~${hours}h`;
    return hours ? `~${days}d ${hours}h` : `~${days}d`;
  }

  destinationRisk(): string {
    const hubs = this.journey()?.route?.route?.hubs;
    return hubs?.length ? (hubs[hubs.length - 1].risk || '') : '';
  }
}
