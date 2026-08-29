import { Component, Input, OnChanges, OnDestroy, signal } from '@angular/core';
import { CommonModule } from '@angular/common';

export interface JourneyHub {
  name: string;
  lat: number;
  lon: number;
  risk: string;
}

export interface JourneyData {
  tracking_id: string;
  status: string;
  origin: string;
  destination: string;
  current_location: string;
  eta: string;
  hours_remaining: number | null;
  days_remaining: number | null;
  progress: number;
  route: {
    available: boolean;
    reason?: string;
    rerouted?: boolean;
    explanation?: string;
    route?: { hubs: JourneyHub[]; km: number; est_transit_hours: number };
  };
}

interface Point {
  x: number;
  y: number;
  name: string;
  risk: string;
}

/**
 * Cartoon journey map: the shipment's Dijkstra hub route drawn as a
 * playful landscape, with a truck that drives along the path up to the
 * live progress point and an ETA bubble counting down to delivery.
 */
@Component({
  selector: 'app-journey-map',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './journey-map.html',
  styleUrl: './journey-map.css'
})
export class JourneyMapComponent implements OnChanges, OnDestroy {
  @Input() journey?: JourneyData;

  points: Point[] = [];
  pathD = '';
  totalKm = 0;
  transitHours = 0;
  animProgress = signal(0);

  private rafId = 0;

  ngOnChanges(): void {
    this.project();
    this.animateTo(this.journey?.progress ?? 0);
  }

  ngOnDestroy(): void {
    cancelAnimationFrame(this.rafId);
  }

  get available(): boolean {
    return !!this.journey?.route?.available && (this.journey?.route?.route?.hubs?.length || 0) > 0;
  }

  get rerouted(): boolean {
    return !!this.journey?.route?.rerouted;
  }

  get explanation(): string {
    return this.journey?.route?.explanation || '';
  }

  /** Lat/lon → viewBox coordinates, fit to this route's bounding box. */
  private project(): void {
    const hubs = this.journey?.route?.route?.hubs || [];
    this.totalKm = this.journey?.route?.route?.km || 0;
    this.transitHours = this.journey?.route?.route?.est_transit_hours || 0;
    if (!hubs.length) { this.points = []; this.pathD = ''; return; }

    const lons = hubs.map(h => h.lon);
    const lats = hubs.map(h => h.lat);
    const minLon = Math.min(...lons), maxLon = Math.max(...lons);
    const minLat = Math.min(...lats), maxLat = Math.max(...lats);
    const lonSpan = Math.max(maxLon - minLon, 0.1);
    const latSpan = Math.max(maxLat - minLat, 0.1);

    this.points = hubs.map(h => ({
      x: 80 + ((h.lon - minLon) / lonSpan) * 640,
      y: 235 - ((h.lat - minLat) / latSpan) * 140,
      name: h.name,
      risk: h.risk || '',
    }));
    this.pathD = this.points
      .map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`)
      .join(' ');
  }

  /** Drive the truck from the start up to the live progress point. */
  private animateTo(target: number): void {
    cancelAnimationFrame(this.rafId);
    const start = performance.now();
    const duration = 2200;
    const tick = (now: number) => {
      const t = Math.min((now - start) / duration, 1);
      const eased = 1 - Math.pow(1 - t, 3); // easeOutCubic
      this.animProgress.set(target * eased);
      if (t < 1) this.rafId = requestAnimationFrame(tick);
    };
    this.rafId = requestAnimationFrame(tick);
  }

  /** Point at a fraction of the polyline's total length. */
  pointAt(fraction: number): { x: number; y: number } {
    const pts = this.points;
    if (!pts.length) return { x: 0, y: 0 };
    if (pts.length === 1) return { x: pts[0].x, y: pts[0].y };

    const lengths: number[] = [];
    let total = 0;
    for (let i = 1; i < pts.length; i++) {
      const d = Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y);
      lengths.push(d);
      total += d;
    }
    let remaining = Math.max(0, Math.min(1, fraction)) * total;
    for (let i = 0; i < lengths.length; i++) {
      if (remaining <= lengths[i] || i === lengths.length - 1) {
        const t = lengths[i] === 0 ? 0 : remaining / lengths[i];
        return {
          x: pts[i].x + (pts[i + 1].x - pts[i].x) * t,
          y: pts[i].y + (pts[i + 1].y - pts[i].y) * t,
        };
      }
      remaining -= lengths[i];
    }
    const last = pts[pts.length - 1];
    return { x: last.x, y: last.y };
  }

  get truck(): { x: number; y: number } {
    return this.pointAt(this.animProgress());
  }

  get etaLabel(): string {
    const j = this.journey;
    if (!j) return '';
    if (j.status === 'delivered') return 'Delivered! 🎉';
    if (j.hours_remaining === null || j.hours_remaining === undefined) return `ETA ${j.eta}`;
    if (j.hours_remaining === 0) return 'Arriving today!';
    if (j.hours_remaining < 24) return `~${j.hours_remaining}h to go`;
    const days = Math.floor(j.hours_remaining / 24);
    const hours = j.hours_remaining % 24;
    return hours ? `~${days}d ${hours}h to go` : `~${days}d to go`;
  }

  hubEmoji(index: number): string {
    if (!this.points.length) return '';
    if (index === 0) return '📦';
    if (index === this.points.length - 1) return '🏠';
    const risk = this.points[index].risk;
    if (risk === 'high') return '⛈️';
    if (risk === 'moderate') return '🌧️';
    return '';
  }

  riskClass(risk: string): string {
    return risk ? `hub--${risk}` : 'hub--low';
  }
}
