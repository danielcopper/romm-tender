/**
 * Client-side RomM server-URL validation + normalization.
 *
 * Mirrors the scheme half of the backend's `lib.url_host.is_valid_server_url`:
 * a usable server URL carries an `http://` or `https://` scheme. The backend's
 * other check, a URL carrying a user name or password, has no copy here. The frontend trims and rejects scheme-less /
 * empty input before calling the backend so a bad URL never reaches a network
 * round-trip (and a leading space never hides the "Allow Insecure SSL" toggle).
 */

/** Trim surrounding whitespace from a user-entered server URL. */
export function trimServerUrl(url: string): string {
  return url.trim();
}

/** True iff the trimmed *url* starts with an `http://` or `https://` scheme. */
export function isValidServerUrl(url: string): boolean {
  const trimmed = trimServerUrl(url);
  return /^https?:\/\/.+/i.test(trimmed);
}

/** True iff the trimmed *url* is an `https://` URL (drives the SSL toggle visibility). */
export function isHttpsUrl(url: string): boolean {
  return trimServerUrl(url).toLowerCase().startsWith("https://");
}
