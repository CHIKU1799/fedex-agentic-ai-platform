import { inject } from '@angular/core';
import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { Router } from '@angular/router';
import { catchError, throwError } from 'rxjs';
import { ApiService } from './services/api.service';

/**
 * Attaches the FedE bearer token to every backend request and ends the
 * session the moment the backend says the token is no longer valid
 * (expired, tampered, or signed with a rotated secret). The customer is
 * taken back to sign-in with a plain explanation instead of seeing
 * half-broken screens.
 */
export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const api = inject(ApiService);
  const router = inject(Router);
  const token = api.token();
  if (token) {
    req = req.clone({ setHeaders: { Authorization: `Bearer ${token}` } });
  }
  return next(req).pipe(
    catchError((err: HttpErrorResponse) => {
      if (err.status === 401 && token && !req.url.endsWith('/auth/token')) {
        api.logout();
        router.navigate(['/signin'], { queryParams: { reason: 'expired' } });
      }
      return throwError(() => err);
    })
  );
};
