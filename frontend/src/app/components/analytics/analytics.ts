import { Component, OnInit, signal, computed } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ApiService } from '../../services/api.service';

/**
 * Agentic analytics (ops console, agent role): what the AI actually did.
 * Stat tiles, a single-hue tool-usage bar list (magnitude), provider mix,
 * commitments health, the audit feed, and a live fleet-watch trigger.
 * Status colors always ride with their text label, never color alone.
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

  maxToolCount = computed(() => {
    const usage = this.data()?.tool_usage || [];
    return usage.length ? usage[0].count : 1;
  });

  constructor(private api: ApiService) {}

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.api.getAgentAnalytics().subscribe({
      next: (res) => this.data.set(res),
      error: (err) => this.error.set(
        err.status === 403
          ? 'Agent role required: sign in as "agent" to view the ops console.'
          : 'Could not load analytics.'),
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
        this.error.set('Fleet watch failed to run.');
        this.sweeping.set(false);
      }
    });
  }

  barWidth(count: number): string {
    return Math.max(4, Math.round((count / this.maxToolCount()) * 100)) + '%';
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
