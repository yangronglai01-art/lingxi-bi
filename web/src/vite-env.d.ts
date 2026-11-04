/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE?: string;
  readonly VITE_OIDC_ISSUER?: string;
  readonly VITE_OIDC_CLIENT_ID?: string;
  readonly VITE_OIDC_ROLE?: string;
  readonly VITE_OIDC_LOGIN_HINT?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
