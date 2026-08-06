import { Component, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { ApiService } from '../../services/api.service';

interface DemoCred { label: string; username: string; password: string; role: string; }

// Dev-only demo credentials, surfaced in the UI so judges can sign in quickly.
const DEMO_CREDS: DemoCred[] = [
  { label: 'FedEx Agent',   username: 'agent',   password: 'fedex-agent-demo', role: 'agent' },
  { label: 'Customer 001',  username: 'cust001', password: 'cust001-demo',     role: 'customer' },
  { label: 'Customer 002',  username: 'cust002', password: 'cust002-demo',     role: 'customer' },
  { label: 'Customer 003',  username: 'cust003', password: 'cust003-demo',     role: 'customer' },
];

@Component({
  selector: 'app-login',
  standalone: true,
  imports: [FormsModule],
  templateUrl: './login.html',
  styleUrl: './login.css'
})
export class LoginComponent {
  open = signal(false);
  username = signal('');
  password = signal('');
  error = signal('');
  loading = signal(false);
  demoCreds = DEMO_CREDS;

  constructor(public api: ApiService, private router: Router) {}

  toggle(): void {
    this.open.update(o => !o);
    this.error.set('');
  }

  fill(cred: DemoCred): void {
    this.username.set(cred.username);
    this.password.set(cred.password);
    this.submit();
  }

  submit(): void {
    const u = this.username().trim();
    const p = this.password();
    if (!u || !p) { this.error.set('Enter a username and password.'); return; }
    this.loading.set(true);
    this.error.set('');

    this.api.login(u, p).subscribe({
      next: () => {
        this.loading.set(false);
        this.open.set(false);
        this.username.set('');
        this.password.set('');
      },
      error: (err) => {
        this.loading.set(false);
        this.error.set(err?.error?.detail || 'Sign in failed. Check your credentials.');
      }
    });
  }

  logout(): void {
    this.api.logout();
    this.open.set(false);
    // The dashboard is guarded, so a signed-out visitor goes back home.
    this.router.navigate(['/']);
  }
}
