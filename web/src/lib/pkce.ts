/**
 * PKCE（RFC 7636）工具：基于 Web Crypto API，浏览器与 Node 18+ 均可用。
 */

const UNRESERVED =
  "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~";

function randomString(length: number): string {
  const bytes = new Uint8Array(length);
  crypto.getRandomValues(bytes);
  let out = "";
  for (const b of bytes) out += UNRESERVED[b % UNRESERVED.length];
  return out;
}

/** 生成 code_verifier（43–128 字符，默认 64）。 */
export function generateCodeVerifier(length = 64): string {
  return randomString(length);
}

/** 生成不透明 state（用于 CSRF 防护）。 */
export function generateState(length = 32): string {
  return randomString(length);
}

export function base64UrlEncode(bytes: Uint8Array): string {
  let binary = "";
  bytes.forEach((b) => (binary += String.fromCharCode(b)));
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** PKCE S256：base64url(sha256(code_verifier))。 */
export async function codeChallengeS256(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest(
    "SHA-256",
    new TextEncoder().encode(verifier),
  );
  return base64UrlEncode(new Uint8Array(digest));
}
