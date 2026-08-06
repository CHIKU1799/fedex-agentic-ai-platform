import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { ApiService } from './services/api.service';

/**
 * Secure by Design: the dashboard route is reachable only with a signed-in
 * session. Anonymous visitors are redirected to the sign-in page. (This is a
 * UX gate; the real enforcement is the JWT check on every backend endpoint.)
 */
export const authGuard: CanActivateFn = () => {
  const api = inject(ApiService);
  const router = inject(Router);
  return api.isSignedIn() ? true : router.createUrlTree(['/signin']);
};

/** Send already-signed-in users straight to the app from / and /signin. */
export const guestGuard: CanActivateFn = () => {
  const api = inject(ApiService);
  const router = inject(Router);
  return api.isSignedIn() ? router.createUrlTree(['/app']) : true;
};
