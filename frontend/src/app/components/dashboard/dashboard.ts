import { Component, signal, ViewChild } from '@angular/core';
import { TrackingComponent } from '../tracking/tracking';
import { ChatComponent } from '../chat/chat';
import { ActionsComponent } from '../actions/actions';
import { NotificationsComponent } from '../notifications/notifications';
import { ActivityComponent } from '../activity/activity';
import { AnalyticsComponent } from '../analytics/analytics';
import { VoiceSidebarComponent } from '../voice-sidebar/voice-sidebar';
import { LoginComponent } from '../login/login';
import { ApiService } from '../../services/api.service';

type Tab = 'track' | 'chat' | 'actions' | 'notifications' | 'activity' | 'analytics';

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [TrackingComponent, ChatComponent, ActionsComponent, NotificationsComponent,
            ActivityComponent, AnalyticsComponent, VoiceSidebarComponent, LoginComponent],
  templateUrl: './dashboard.html',
  styleUrl: './dashboard.css'
})
export class DashboardComponent {
  activeTab = signal<Tab>('track');

  @ViewChild('voiceSidebar') voiceSidebar!: VoiceSidebarComponent;

  constructor(public api: ApiService) {}

  setTab(tab: Tab) {
    this.activeTab.set(tab);
  }

  isCustomer(): boolean {
    return !!this.api.ownCustomerId();
  }

  isAgent(): boolean {
    return this.api.currentUser()?.role === 'agent';
  }

  openVoice() {
    this.voiceSidebar.open();
  }
}
