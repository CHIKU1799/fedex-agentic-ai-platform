import { Component, signal, ViewChild, ElementRef } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { CommonModule } from '@angular/common';
import { ApiService } from '../../services/api.service';
import { JourneyMapComponent, JourneyData } from '../journey-map/journey-map';

interface TrackingCard {
  tracking_id: string;
  status: string;
  location?: string;
  eta?: string;
  destination?: string;
}

interface ActionChip {
  tool: string;
  ok: boolean;
}

interface WeatherReport {
  available: boolean;
  reason?: string;
  location?: string;
  forecast_date?: string;
  risk?: string;
  conditions?: string;
  temperature_max_c?: number;
  temperature_min_c?: number;
  eta_within_forecast?: boolean;
  note?: string | null;
}

interface OutlookDay {
  date: string;
  risk: string;
  conditions: string;
}

interface DeliveryOutlook {
  tracking_id: string;
  recommended_date?: string | null;
  days: OutlookDay[];
}

interface ChatMessage {
  role: 'user' | 'ai';
  text: string;
  actions?: ActionChip[];
  card?: TrackingCard;
  shipments?: TrackingCard[];
  weather?: WeatherReport;
  weatherTrackingId?: string;
  notificationCreated?: boolean;
  outlook?: DeliveryOutlook;
  journey?: JourneyData;
  costAvoided?: number;
  tokens?: number;
}

const SUGGESTIONS = [
  'Where is my package FX100001?',
  'Show me the route for FX100001',
  'I need FX100001 delivered before friday',
  'Will weather delay FX100001?',
  'Hold FX100006 for pickup',
  'My package arrived damaged, I need help',
];

// Friendly labels for the tools the agent can invoke.
const TOOL_LABELS: Record<string, string> = {
  track_shipment: 'Tracked shipment',
  reschedule_delivery: 'Rescheduled delivery',
  redirect_package: 'Redirected package',
  cancel_shipment: 'Cancelled shipment',
  list_customer_shipments: 'Listed packages',
  get_customer_notifications: 'Fetched notifications',
  check_weather_impact: 'Checked weather risk',
  suggest_delivery_dates: 'Suggested delivery dates',
  hold_at_location: 'Requested hold for pickup',
  plan_optimal_route: 'Planned optimal route',
  save_customer_preference: 'Remembered your preference',
  get_customer_preferences: 'Recalled your preferences',
  create_support_case: 'Opened a support case',
  set_delivery_commitment: 'Took on your delivery goal',
  get_delivery_commitments: 'Checked your delivery goals',
};

@Component({
  selector: 'app-chat',
  standalone: true,
  imports: [FormsModule, CommonModule, JourneyMapComponent],
  templateUrl: './chat.html',
  styleUrl: './chat.css'
})
export class ChatComponent {
  query = signal('');
  messages = signal<ChatMessage[]>([]);
  loading = signal(false);
  suggestions = SUGGESTIONS;
  sessionTokens = signal(0);  // cumulative tokens this session (agentic mode)

  // Stable per-session id so the agent has multi-turn memory.
  private readonly sessionId =
    (typeof crypto !== 'undefined' && 'randomUUID' in crypto)
      ? crypto.randomUUID()
      : 'sess-' + Math.floor(Math.abs(Math.sin(Date.now())) * 1e9);

  @ViewChild('scrollAnchor') scrollAnchor?: ElementRef<HTMLDivElement>;

  constructor(private api: ApiService) {}

  toolLabel(tool: string): string {
    return TOOL_LABELS[tool] || tool;
  }

  useSuggestion(text: string): void {
    this.query.set(text);
    this.send();
  }

  send(): void {
    const q = this.query().trim();
    if (!q || this.loading()) return;

    this.messages.update(m => [...m, { role: 'user', text: q }]);
    this.query.set('');
    this.loading.set(true);
    this.scrollSoon();

    this.api.askAI(q, this.sessionId).subscribe({
      next: (res) => {
        const msg = this.toMessage(res.response);
        this.messages.update(m => [...m, msg]);
        this.maybeAttachJourney(msg);
        this.loading.set(false);
        this.scrollSoon();
      },
      error: () => {
        this.messages.update(m => [...m, { role: 'ai', text: 'Sorry, something went wrong reaching FedE.' }]);
        this.loading.set(false);
        this.scrollSoon();
      }
    });
  }

  /** Normalize both the agentic and offline-fallback response shapes. */
  private toMessage(resp: any): ChatMessage {
    const msg: ChatMessage = { role: 'ai', text: '' };
    if (!resp) { msg.text = 'No response.'; return msg; }

    const data = resp.data || {};

    // Natural-language reply (agentic) or derived text (fallback).
    if (resp.response) {
      msg.text = resp.response;
    } else if (data.weather) {
      const w = data.weather as WeatherReport;
      msg.text = w.available
        ? `Forecast near ${w.location} around ${w.forecast_date}: ${w.conditions}. Delivery risk: ${w.risk}.`
        : (w.reason || 'Weather data is unavailable right now.');
    } else if (data.outlook) {
      msg.text = data.recommended_date
        ? `The earliest low-risk delivery date for ${data.tracking_id} is ${data.recommended_date}.`
        : `I could not find a low-risk date in the next week for ${data.tracking_id}.`;
    } else if (data.commitment_id) {
      msg.text = data.message
        || `Commitment ${data.commitment_id}: watching ${data.tracking_id} to arrive by ${data.target_date}.`;
    } else if (data.tracking_id && data.location) {
      msg.text = `Shipment ${data.tracking_id} is "${data.status}" at ${data.location}. ETA ${data.eta}.`;
    } else if (data.message) {
      msg.text = data.message;
    } else if (data.response) {
      msg.text = data.response;
    } else if (data.message) {
      msg.text = data.message;
    } else if (data.error) {
      msg.text = `Error: ${data.error}`;
    } else {
      msg.text = 'Done.';
    }

    // Action chips (what the agent actually did).
    if (Array.isArray(resp.actions_taken) && resp.actions_taken.length) {
      msg.actions = resp.actions_taken.map((a: any) => ({ tool: a.tool, ok: !!a.ok }));
    }

    // Weather risk card.
    if (data.weather) {
      msg.weather = data.weather as WeatherReport;
      msg.weatherTrackingId = data.tracking_id;
      msg.notificationCreated = !!data.notification_created;
    }

    // Delivery-date outlook strip.
    if (data.outlook?.days?.length) {
      msg.outlook = {
        tracking_id: data.tracking_id,
        recommended_date: data.recommended_date,
        days: data.outlook.days as OutlookDay[],
      };
    }

    // Journey payload (from the plan_optimal_route tool): render the map.
    if (data.route?.route?.hubs || data.route?.available === false) {
      msg.journey = data as JourneyData;
      if (!resp.response) {
        msg.text = `Here's how ${data.tracking_id} travels to you.`;
      }
    }

    // Inline tracking card / shipment list. Weather, outlook, journey and
    // commitment replies carry a tracking_id too, but render differently.
    if (data.tracking_id && data.location && !msg.weather && !msg.outlook
        && !msg.journey && !data.commitment_id) {
      msg.card = data as TrackingCard;
    }
    if (Array.isArray(data.shipments)) {
      msg.shipments = data.shipments as TrackingCard[];
    }

    // Cost avoided (customer-first byproduct, shown transparently).
    if (data.estimated_cost_avoided_usd > 0) {
      msg.costAvoided = data.estimated_cost_avoided_usd;
    }

    // Token usage (agentic only).
    const total = resp.usage?.total_tokens || 0;
    if (total > 0) {
      msg.tokens = total;
      this.sessionTokens.update(t => t + total);
    }

    return msg;
  }

  /** Tracking replies get the animated journey map fetched alongside. */
  private maybeAttachJourney(msg: ChatMessage): void {
    const trackingId = msg.card?.tracking_id;
    if (!trackingId || msg.journey) return;
    this.api.getJourney(trackingId).subscribe({
      next: (j) => {
        msg.journey = j as JourneyData;
        this.messages.update(m => [...m]);  // re-emit so the map renders
        this.scrollSoon();
      },
      error: () => { /* map is a bonus — tracking card already rendered */ }
    });
  }

  statusClass(status: string): string {
    return 'badge--' + (status || 'unknown').toLowerCase().replace(/\s+/g, '-');
  }

  riskClass(risk?: string): string {
    return 'risk--' + (risk || 'unknown');
  }

  riskIcon(risk?: string): string {
    if (risk === 'high') return '⛈';
    if (risk === 'moderate') return '🌧';
    return '☀️';
  }

  /** One-click reschedule from the outlook strip. */
  rescheduleTo(trackingId: string, date: string): void {
    if (!trackingId || this.loading()) return;
    this.query.set(`Reschedule ${trackingId} to ${date}`);
    this.send();
  }

  private scrollSoon(): void {
    setTimeout(() => this.scrollAnchor?.nativeElement.scrollIntoView({ behavior: 'smooth' }), 50);
  }
}
