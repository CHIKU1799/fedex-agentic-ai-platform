import { HttpInterceptorFn } from '@angular/common/http';

/**
 * Attaches the FedE bearer token (if the customer is signed in) to every
 * backend request. Anonymous requests still work for public tracking; the
 * backend enforces auth on mutating endpoints. Token is stored under
 * `fede_token` after a successful /auth/token exchange.
 */
export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const token = (typeof localStorage !== 'undefined') ? localStorage.getItem('fede_token') : null;
  if (token) {
    req = req.clone({ setHeaders: { Authorization: `Bearer ${token}` } });
  }
  return next(req);
};
