import { Component, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { CommonModule } from '@angular/common';
import { ApiService } from '../../services/api.service';
import { JourneyMapComponent, JourneyData } from '../journey-map/journey-map';

@Component({
  selector: 'app-tracking',
  standalone: true,
  imports: [FormsModule, CommonModule, JourneyMapComponent],
  templateUrl: './tracking.html',
  styleUrl: './tracking.css'
})
export class TrackingComponent {
  trackingId = signal('');
  result = signal<any>(null);
  journey = signal<JourneyData | null>(null);
  error = signal('');
  loading = signal(false);

  constructor(private api: ApiService) {}

  track() {
    const id = this.trackingId().trim();
    if (!id) return;
    this.loading.set(true);
    this.error.set('');
    this.result.set(null);
    this.journey.set(null);

    this.api.trackShipment(id).subscribe({
      next: (data) => {
        this.result.set(data);
        this.loading.set(false);
        // The animated journey map rides along with every track result.
        this.api.getJourney(id).subscribe({
          next: (j) => this.journey.set(j as JourneyData),
          error: () => { /* map is a bonus — details already rendered */ }
        });
      },
      error: (err) => {
        this.error.set(err.error?.detail || 'Shipment not found');
        this.loading.set(false);
      }
    });
  }
}
