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
  /** Unix seconds when the access token expires (from the JWT payload). */
  exp?: number;
}

// The session lives in sessionStorage: it survives a reload but ends when
// the tab closes, so a shared or forgotten browser does not keep a usable
// token around. The backend's token TTL is the real limit.
const store = (): Storage | null => (typeof sessionStorage !== 'undefined' ? sessionStorage : null);

/** Read the JWT's expiry without trusting anything else in the payload. */
function tokenExpiry(token: string): number | undefined {
  try {
    const payload = JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')));
    return typeof payload.exp === 'number' ? payload.exp : undefined;
  } catch {
    return undefined;
  }
}

@Injectable({ providedIn: 'root' })
export class ApiService {
  // Reactive auth state, restored from sessionStorage on load.
  currentUser = signal<CurrentUser | null>(this.readUser());

  constructor(private http: HttpClient) {}

  private readUser(): CurrentUser | null {
    try {
      const raw = store()?.getItem('fede_user');
      const user = raw ? JSON.parse(raw) as CurrentUser : null;
      // An expired token is as good as no token: start signed out.
      if (user?.exp && user.exp * 1000 <= Date.now()) {
        this.logout();
        return null;
      }
      return user;
    } catch {
      return null;
    }
  }

  token(): string | null {
    return store()?.getItem('fede_token') ?? null;
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
        store()?.setItem('fede_token', res.access_token);
        const user: CurrentUser = { username, role: res.role, exp: tokenExpiry(res.access_token) };
        store()?.setItem('fede_user', JSON.stringify(user));
        this.currentUser.set(user);
      })
    );
  }

  logout(): void {
    store()?.removeItem('fede_token');
    store()?.removeItem('fede_user');
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

  hold(trackingId: string, location = ''): Observable<any> {
    return this.http.post(`${API}/hold`, { tracking_id: trackingId, location: location || null });
  }

  getJourney(trackingId: string): Observable<any> {
    return this.http.get(`${API}/journey/${trackingId}`);
  }

  getShipments(customerId: string): Observable<any> {
    return this.http.get(`${API}/customers/${customerId}/shipments`);
  }

  getAgentAnalytics(): Observable<any> {
    return this.http.get(`${API}/analytics/agent`);
  }

  getActivity(customerId: string): Observable<any> {
    return this.http.get(`${API}/activity/${customerId}`);
  }

  getCommitments(customerId: string): Observable<any> {
    return this.http.get(`${API}/commitments/${customerId}`);
  }

  runFleetWatch(): Observable<any> {
    return this.http.post(`${API}/agent/fleet-watch`, {});
  }

  /** CUST001-style id for the signed-in customer, or '' for agents/guests. */
  ownCustomerId(): string {
    const name = this.currentUser()?.username || '';
    return name.toLowerCase().startsWith('cust') ? name.toUpperCase() : '';
  }

  getNotifications(customerId: string): Observable<any> {
    return this.http.get(`${API}/notifications/${customerId}`);
  }

  markNotificationRead(notificationId: string): Observable<any> {
    return this.http.patch(`${API}/notifications/${notificationId}/read`, {});
  }
}
