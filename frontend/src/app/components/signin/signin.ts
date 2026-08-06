import { Component, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { ApiService } from '../../services/api.service';

interface DemoCred { label: string; username: string; password: string; role: string; hint: string; }

// Dev-only demo credentials, surfaced so judges can sign in in one click.
const DEMO_CREDS: DemoCred[] = [
  { label: 'FedEx Agent',  username: 'agent',   password: 'fedex-agent-demo', role: 'agent',    hint: 'sees and manages all shipments' },
  { label: 'Customer 001', username: 'cust001', password: 'cust001-demo',     role: 'customer', hint: 'sees only their own packages' },
  { label: 'Customer 002', username: 'cust002', password: 'cust002-demo',     role: 'customer', hint: 'has a delayed shipment; other packages: denied' },
  { label: 'Customer 003', username: 'cust003', password: 'cust003-demo',     role: 'customer', hint: 'fresh shipment, try redirect or cancel' },
];

@Component({
  selector: 'app-signin',
  standalone: true,
  imports: [FormsModule, RouterLink],
  templateUrl: './signin.html',
  styleUrl: './signin.css'
})
export class SignInComponent {
  username = signal('');
  password = signal('');
  error = signal('');
  loading = signal(false);
  demoCreds = DEMO_CREDS;

  constructor(private api: ApiService, private router: Router) {}

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
        this.router.navigate(['/app']);
      },
      error: (err) => {
        this.loading.set(false);
        this.error.set(err?.error?.detail || 'Sign in failed. Check your credentials.');
      }
    });
  }
}
