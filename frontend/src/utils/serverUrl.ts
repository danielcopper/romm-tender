/**
 * Client-side RomM server-URL validation + normalization.
 *
 * Mirrors the scheme half of the backend's `lib.url_host.is_valid_server_url`:
 * a usable server URL carries an `http://` or `https://` scheme. The backend's
 * other check, a URL carrying a user name or password, has no copy here. The
 * sign-in handlers trim and reject scheme-less or empty input before calling
 * the backend; the URL save does not, because the backend answers a
 * scheme-less URL with its https:// completion. Trimming also keeps a leading
 * space from hiding the "Allow Insecure SSL" toggle.
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
