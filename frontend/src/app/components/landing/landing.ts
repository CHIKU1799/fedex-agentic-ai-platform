import { Component } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { ApiService } from '../../services/api.service';

@Component({
  selector: 'app-landing',
  standalone: true,
  imports: [RouterLink],
  templateUrl: './landing.html',
  styleUrl: './landing.css'
})
export class LandingComponent {
  constructor(public api: ApiService, private router: Router) {}

  start(): void {
    this.router.navigate([this.api.isSignedIn() ? '/app' : '/signin']);
  }
}
