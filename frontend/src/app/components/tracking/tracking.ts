import { Component, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { CommonModule } from '@angular/common';
import { ApiService } from '../../services/api.service';

@Component({
  selector: 'app-tracking',
  standalone: true,
  imports: [FormsModule, CommonModule],
  templateUrl: './tracking.html',
  styleUrl: './tracking.css'
})
export class TrackingComponent {
  trackingId = signal('');
  result = signal<any>(null);
  error = signal('');
  loading = signal(false);

  constructor(private api: ApiService) {}

  track() {
    const id = this.trackingId().trim();
    if (!id) return;
    this.loading.set(true);
    this.error.set('');
    this.result.set(null);

    this.api.trackShipment(id).subscribe({
      next: (data) => {
        this.result.set(data);
        this.loading.set(false);
      },
      error: (err) => {
        this.error.set(err.error?.detail || 'Shipment not found');
        this.loading.set(false);
      }
    });
  }
}
