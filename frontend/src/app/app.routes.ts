import { Routes } from '@angular/router';
import { LandingComponent } from './components/landing/landing';
import { SignInComponent } from './components/signin/signin';
import { DashboardComponent } from './components/dashboard/dashboard';
import { authGuard, guestGuard } from './auth.guard';

export const routes: Routes = [
  { path: '', component: LandingComponent },
  { path: 'signin', component: SignInComponent, canActivate: [guestGuard] },
  { path: 'app', component: DashboardComponent, canActivate: [authGuard] },
  { path: '**', redirectTo: '' },
];
