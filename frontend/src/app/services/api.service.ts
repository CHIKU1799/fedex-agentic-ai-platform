import { Injectable, signal } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable, tap } from 'rxjs';

// Local dev talks to uvicorn directly; deployed builds use the same-origin
// /api prefix, which Vercel rewrites to the serverless FastAPI function.
const API = ['localhost', '127.0.0.1'].includes(window.location.hostname)
  ? 'http://127.0.0.1:8000'
  : '/api';

export interface CurrentUser {
  username: string;
  role: string;
}

@Injectable({ providedIn: 'root' })
export class ApiService {
  // Reactive auth state, restored from localStorage on load.
  currentUser = signal<CurrentUser | null>(this.readUser());

  constructor(private http: HttpClient) {}

  private readUser(): CurrentUser | null {
    try {
      const raw = localStorage.getItem('fede_user');
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  }

  health(): Observable<any> {
    return this.http.get(`${API}/health`);
  }

  // ── Auth (OAuth2 password flow) ──
  login(username: string, password: string): Observable<any> {
    // The /auth/token endpoint expects form-encoded credentials.
    const body = new URLSearchParams();
    body.set('username', username);
    body.set('password', password);
    return this.http.post(`${API}/auth/token`, body.toString(), {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    }).pipe(
      tap((res: any) => {
        localStorage.setItem('fede_token', res.access_token);
        const user = { username, role: res.role };
        localStorage.setItem('fede_user', JSON.stringify(user));
        this.currentUser.set(user);
      })
    );
  }

  logout(): void {
    localStorage.removeItem('fede_token');
    localStorage.removeItem('fede_user');
    this.currentUser.set(null);
  }

  isSignedIn(): boolean {
    return !!this.currentUser();
  }

  trackShipment(trackingId: string): Observable<any> {
    return this.http.get(`${API}/track/${trackingId}`);
  }

  askAI(query: string, sessionId?: string): Observable<any> {
    return this.http.post(`${API}/ask`, { query, session_id: sessionId });
  }

  createShipment(data: any): Observable<any> {
    return this.http.post(`${API}/shipment`, data);
  }

  reschedule(trackingId: string, newDate: string): Observable<any> {
    return this.http.post(`${API}/reschedule`, { tracking_id: trackingId, new_date: newDate });
  }

  redirect(trackingId: string, newAddress: string): Observable<any> {
    return this.http.post(`${API}/redirect`, { tracking_id: trackingId, new_address: newAddress });
  }

  cancel(trackingId: string, reason: string): Observable<any> {
    return this.http.post(`${API}/cancel`, { tracking_id: trackingId, reason });
  }

  getNotifications(customerId: string): Observable<any> {
    return this.http.get(`${API}/notifications/${customerId}`);
  }

  markNotificationRead(notificationId: string): Observable<any> {
    return this.http.patch(`${API}/notifications/${notificationId}/read`, {});
  }
}
