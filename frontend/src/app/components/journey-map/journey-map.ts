import { Component, ElementRef, Input, OnChanges, OnDestroy, ViewChild, signal } from '@angular/core';
import { CommonModule } from '@angular/common';

export interface JourneyHub {
  name: string;
  lat: number;
  lon: number;
  risk: string;
}

export interface TraceCheckpoint {
  hub: string;
  label: string;
  state: 'completed' | 'current' | 'upcoming';
  time: string;
  estimated: boolean;
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
    baseline_route?: { name: string; lat: number; lon: number }[];
    route?: { hubs: JourneyHub[]; km: number; est_transit_hours: number };
  };
  trace?: TraceCheckpoint[];
}

interface Point {
  x: number;
  y: number;
  name: string;
  risk: string;
  frac: number;  // fraction of total path length at this hub
}

/**
 * The journey map: the shipment's weather-adjusted Dijkstra route drawn as
 * a stylized landscape. The truck follows the real curved path geometry
 * (SVG getPointAtLength) up to the live progress point; a ghost path shows
 * the storm route that was avoided when the planner rerouted.
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
  ghostD = '';
  totalKm = 0;
  transitHours = 0;
  animProgress = signal(0);

  @ViewChild('routePath') routePath?: ElementRef<SVGPathElement>;

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

  /** Hubs on the storm route that the chosen route avoids (for labeling). */
  get avoidedHubs(): { x: number; y: number; name: string }[] {
    if (!this.rerouted) return [];
    const chosen = new Set(this.points.map(p => p.name));
    return (this.journey?.route?.baseline_route || [])
      .filter(h => !chosen.has(h.name))
      .map(h => ({ ...this.projectPoint(h.lat, h.lon), name: h.name }));
  }

  private bbox = { minLon: 0, lonSpan: 1, minLat: 0, latSpan: 1 };

  private projectPoint(lat: number, lon: number): { x: number; y: number } {
    return {
      x: 95 + ((lon - this.bbox.minLon) / this.bbox.lonSpan) * 710,
      y: 292 - ((lat - this.bbox.minLat) / this.bbox.latSpan) * 175,
    };
  }

  /** Lat/lon → viewBox coordinates, fit to this route (plus any ghost). */
  private project(): void {
    const hubs = this.journey?.route?.route?.hubs || [];
    const ghost = this.journey?.route?.baseline_route || [];
    this.totalKm = this.journey?.route?.route?.km || 0;
    this.transitHours = this.journey?.route?.route?.est_transit_hours || 0;
    if (!hubs.length) { this.points = []; this.pathD = ''; this.ghostD = ''; return; }

    const all = [...hubs, ...ghost];
    const lons = all.map(h => h.lon);
    const lats = all.map(h => h.lat);
    const minLon = Math.min(...lons), maxLon = Math.max(...lons);
    const minLat = Math.min(...lats), maxLat = Math.max(...lats);
    this.bbox = {
      minLon, lonSpan: Math.max(maxLon - minLon, 0.1),
      minLat, latSpan: Math.max(maxLat - minLat, 0.1),
    };

    this.points = hubs.map(h => ({
      ...this.projectPoint(h.lat, h.lon),
      name: h.name,
      risk: h.risk || '',
      frac: 0,
    }));
    let total = 0;
    const cumulative = [0];
    for (let i = 1; i < this.points.length; i++) {
      total += Math.hypot(this.points[i].x - this.points[i - 1].x,
                          this.points[i].y - this.points[i - 1].y);
      cumulative.push(total);
    }
    this.points.forEach((p, i) => p.frac = total ? cumulative[i] / total : 0);
    this.pathD = this.smooth(this.points);
    this.ghostD = ghost.length > 1
      ? this.smooth(ghost.map(h => this.projectPoint(h.lat, h.lon)))
      : '';
  }

  /** Catmull-Rom smoothing: gentle curves instead of ruler segments. */
  private smooth(pts: { x: number; y: number }[]): string {
    if (!pts.length) return '';
    if (pts.length < 3) {
      return pts.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ');
    }
    let d = `M ${pts[0].x.toFixed(1)} ${pts[0].y.toFixed(1)}`;
    for (let i = 0; i < pts.length - 1; i++) {
      const p0 = pts[Math.max(i - 1, 0)];
      const p1 = pts[i];
      const p2 = pts[i + 1];
      const p3 = pts[Math.min(i + 2, pts.length - 1)];
      const c1x = p1.x + (p2.x - p0.x) / 6, c1y = p1.y + (p2.y - p0.y) / 6;
      const c2x = p2.x - (p3.x - p1.x) / 6, c2y = p2.y - (p3.y - p1.y) / 6;
      d += ` C ${c1x.toFixed(1)} ${c1y.toFixed(1)}, ${c2x.toFixed(1)} ${c2y.toFixed(1)}, ${p2.x.toFixed(1)} ${p2.y.toFixed(1)}`;
    }
    return d;
  }

  /** Drive the truck from the start up to the live progress point. */
  private animateTo(target: number): void {
    cancelAnimationFrame(this.rafId);
    const start = performance.now();
    const duration = 2400;
    const tick = (now: number) => {
      const t = Math.min((now - start) / duration, 1);
      const eased = 1 - Math.pow(1 - t, 3); // easeOutCubic
      this.animProgress.set(target * eased);
      if (t < 1) this.rafId = requestAnimationFrame(tick);
    };
    this.rafId = requestAnimationFrame(tick);
  }

  /** Point along the real curved path; falls back to linear before render. */
  pointAt(fraction: number): { x: number; y: number } {
    const el = this.routePath?.nativeElement;
    const f = Math.max(0, Math.min(1, fraction));
    if (el && typeof el.getTotalLength === 'function') {
      try {
        const length = el.getTotalLength();
        if (length > 0) {
          const pt = el.getPointAtLength(f * length);
          return { x: pt.x, y: pt.y };
        }
      } catch { /* fall through to linear */ }
    }
    const pts = this.points;
    if (!pts.length) return { x: 0, y: 0 };
    for (let i = 1; i < pts.length; i++) {
      if (f <= pts[i].frac || i === pts.length - 1) {
        const span = pts[i].frac - pts[i - 1].frac || 1;
        const t = (f - pts[i - 1].frac) / span;
        return {
          x: pts[i - 1].x + (pts[i].x - pts[i - 1].x) * t,
          y: pts[i - 1].y + (pts[i].y - pts[i - 1].y) * t,
        };
      }
    }
    return { x: pts[pts.length - 1].x, y: pts[pts.length - 1].y };
  }

  get truck(): { x: number; y: number } {
    return this.pointAt(this.animProgress());
  }

  hubDone(index: number): boolean {
    const p = this.points[index];
    return !!p && p.frac <= this.animProgress() + 0.001;
  }

  labelWidth(name: string): number {
    return Math.round(name.length * 6.6) + 16;
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

  get trace(): TraceCheckpoint[] {
    return this.journey?.trace || [];
  }

  traceIcon(t: TraceCheckpoint): string {
    if (t.state === 'completed') return '✓';
    if (t.state === 'current') return '🚚';
    return '·';
  }

  hubEmoji(index: number): string {
    if (!this.points.length) return '';
    if (index === 0) return '📦';
    if (index === this.points.length - 1) return '🏠';
    const risk = this.points[index].risk;
    if (risk === 'high') return '⛈️';
    if (risk === 'moderate') return '🌦️';
    return '';
  }

  riskClass(risk: string): string {
    return risk ? `hub--${risk}` : 'hub--low';
  }
}
