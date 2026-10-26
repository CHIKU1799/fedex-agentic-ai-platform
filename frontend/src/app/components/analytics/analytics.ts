import { Component, OnInit, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ApiService } from '../../services/api.service';

/**
 * Operations (agent role): what FedE did across the fleet. Stat tiles,
 * deliveries at risk, fleet status, the recent-actions record, and a
 * button to run the fleet check now. Status colours always ride with a
 * text label, never colour alone.
 */
@Component({
  selector: 'app-analytics',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './analytics.html',
  styleUrl: './analytics.css'
})
export class AnalyticsComponent implements OnInit {
  data = signal<any>(null);
  sweep = signal<any>(null);
  sweeping = signal(false);
  error = signal('');

  constructor(private api: ApiService) {}

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.api.getAgentAnalytics().subscribe({
      next: (res) => this.data.set(res),
      error: (err) => this.error.set(
        err.status === 403
          ? 'Operations is only available to FedEx agents.'
          : 'Operations data could not be loaded. Try again in a moment.'),
    });
  }

  runSweep(): void {
    if (this.sweeping()) return;
    this.sweeping.set(true);
    this.api.runFleetWatch().subscribe({
      next: (res) => {
        this.sweep.set(res);
        this.sweeping.set(false);
        this.load();
      },
      error: () => {
        this.error.set('The fleet check did not finish. Try again in a moment.');
        this.sweeping.set(false);
      }
    });
  }

  objectEntries(obj: any): { key: string; value: any }[] {
    return Object.entries(obj || {}).map(([key, value]) => ({ key, value }));
  }

  pct(value: number | null): string {
    return value === null || value === undefined ? 'n/a' : Math.round(value * 100) + '%';
  }

  when(at: string | null): string {
    if (!at) return '';
    return new Date(at).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  }
}
