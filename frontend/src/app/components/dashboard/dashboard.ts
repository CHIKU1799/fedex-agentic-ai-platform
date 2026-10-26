import { Component, signal, ViewChild } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { TrackingComponent } from '../tracking/tracking';
import { ChatComponent } from '../chat/chat';
import { UpdatesComponent } from '../updates/updates';
import { AnalyticsComponent } from '../analytics/analytics';
import { VoiceSidebarComponent } from '../voice-sidebar/voice-sidebar';
import { ApiService } from '../../services/api.service';

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
            AnalyticsComponent, VoiceSidebarComponent],
  templateUrl: './dashboard.html',
  styleUrl: './dashboard.css'
})
export class DashboardComponent {
  activeTab = signal<Tab>('shipments');

  @ViewChild('voiceSidebar') voiceSidebar!: VoiceSidebarComponent;

  constructor(public api: ApiService, private router: Router) {}

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
