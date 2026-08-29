/**
 * Compatibility declarations for the byte-for-byte vendored gcip-iap 2.0.1
 * browser bundle. These are limited to the Authentication surface consumed by
 * the custom IAP page; they are not an upstream package declaration.
 */
import type { Auth, User, UserCredential } from "firebase/auth";

export interface SelectedTenantInfo {
  email?: string;
  tenantId: string | null;
  providerIds?: string[];
}

export interface AuthenticationHandler {
  languageCode?: string | null;
  getAuth(apiKey: string, tenantId: string | null): Auth;
  startSignIn(auth: Auth, match?: SelectedTenantInfo): Promise<UserCredential>;
  completeSignOut(): Promise<void>;
  processUser?(user: User): Promise<User>;
  showProgressBar?(): void;
  hideProgressBar?(): void;
  handleError?(error: unknown): void;
  selectTenant?(
    projectConfig: { projectId: string; apiKey: string },
    tenantIds: string[],
  ): Promise<SelectedTenantInfo>;
}

export class Authentication {
  constructor(handler: AuthenticationHandler);
  start(): Promise<void>;
  getOriginalURL(): Promise<string | null>;
}
