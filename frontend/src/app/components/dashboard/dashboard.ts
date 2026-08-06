import { Component, signal, ViewChild } from '@angular/core';
import { TrackingComponent } from '../tracking/tracking';
import { ChatComponent } from '../chat/chat';
import { ActionsComponent } from '../actions/actions';
import { NotificationsComponent } from '../notifications/notifications';
import { VoiceSidebarComponent } from '../voice-sidebar/voice-sidebar';
import { LoginComponent } from '../login/login';

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [TrackingComponent, ChatComponent, ActionsComponent, NotificationsComponent, VoiceSidebarComponent, LoginComponent],
  templateUrl: './dashboard.html',
  styleUrl: './dashboard.css'
})
export class DashboardComponent {
  activeTab = signal<'track' | 'chat' | 'actions' | 'notifications'>('track');

  @ViewChild('voiceSidebar') voiceSidebar!: VoiceSidebarComponent;

  setTab(tab: 'track' | 'chat' | 'actions' | 'notifications') {
    this.activeTab.set(tab);
  }

  openVoice() {
    this.voiceSidebar.open();
  }
}
