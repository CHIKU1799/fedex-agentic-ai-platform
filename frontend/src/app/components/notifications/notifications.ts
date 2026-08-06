import { Component, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { CommonModule } from '@angular/common';
import { ApiService } from '../../services/api.service';

@Component({
  selector: 'app-notifications',
  standalone: true,
  imports: [FormsModule, CommonModule],
  templateUrl: './notifications.html',
  styleUrl: './notifications.css'
})
export class NotificationsComponent {
  customerId = signal('CUST001');
  notifications = signal<any[]>([]);
  loading = signal(false);
  error = signal('');

  constructor(private api: ApiService) {}

  load() {
    const id = this.customerId().trim();
    if (!id) return;
    this.loading.set(true);
    this.error.set('');

    this.api.getNotifications(id).subscribe({
      next: (res) => {
        this.notifications.set(res.notifications || []);
        this.loading.set(false);
      },
      error: () => {
        this.error.set('Failed to load notifications');
        this.loading.set(false);
      }
    });
  }

  markRead(id: string) {
    this.api.markNotificationRead(id).subscribe({
      next: () => {
        this.notifications.update(list =>
          list.map(n => n.id === id ? { ...n, is_read: 'true' } : n)
        );
      }
    });
  }
}
