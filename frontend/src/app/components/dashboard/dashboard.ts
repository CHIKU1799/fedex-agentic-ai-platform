import { Component, OnDestroy, OnInit, signal, ViewChild } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { TrackingComponent } from '../tracking/tracking';
import { ChatComponent } from '../chat/chat';
import { UpdatesComponent } from '../updates/updates';
import { AnalyticsComponent } from '../analytics/analytics';
import { VoiceSidebarComponent } from '../voice-sidebar/voice-sidebar';
import { ApiService } from '../../services/api.service';
import { ToastsComponent } from '../ui/toasts';

type Tab = 'shipments' | 'ask' | 'updates' | 'ops';

/**
 * The signed-in app shell: a left rail with the three things a customer
 * does here (see shipments, ask FedE, read updates) plus an operations
 * view for FedEx agents. On phones the rail becomes a bottom bar.
 */
@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [RouterLink, TrackingComponent, ChatComponent, UpdatesComponent,
            AnalyticsComponent, VoiceSidebarComponent, ToastsComponent],
  templateUrl: './dashboard.html',
  styleUrl: './dashboard.css'
})
export class DashboardComponent implements OnInit, OnDestroy {
  activeTab = signal<Tab>('shipments');
  /** Minutes left on the signed-in session, shown so expiry is never a surprise. */
  minutesLeft = signal<number | null>(null);
  private tick: any = null;

  @ViewChild('voiceSidebar') voiceSidebar!: VoiceSidebarComponent;

  constructor(public api: ApiService, private router: Router) {}

  ngOnInit(): void {
    this.updateClock();
    this.tick = setInterval(() => this.updateClock(), 30000);
  }

  ngOnDestroy(): void {
    if (this.tick) clearInterval(this.tick);
  }

  private updateClock(): void {
    const exp = this.api.currentUser()?.exp;
    if (!exp) { this.minutesLeft.set(null); return; }
    const left = Math.round((exp * 1000 - Date.now()) / 60000);
    this.minutesLeft.set(Math.max(0, left));
    if (left <= 0) {
      // The token is dead; end the session cleanly instead of failing calls.
      this.api.logout();
      this.router.navigate(['/signin'], { queryParams: { reason: 'expired' } });
    }
  }

  sessionLabel(): string {
    const m = this.minutesLeft();
    if (m === null) return 'Secure session';
    if (m >= 60) return `Session ends in ${Math.round(m / 60)}h`;
    return `Session ends in ${m} min`;
  }

  setTab(tab: Tab) {
    this.activeTab.set(tab);
  }

  isAgent(): boolean {
    return this.api.currentUser()?.role === 'agent';
  }

  openVoice() {
    this.voiceSidebar.open();
  }

  signOut() {
    this.api.logout();
    this.router.navigate(['/']);
  }
}
