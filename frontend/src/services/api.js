/** Fired on window when the server says the session is gone, so the app can show the sign-in page. */
export const SESSION_EXPIRED = "marathon:session-expired";

/** fetch + JSON, turning FastAPI error bodies into readable Error messages. */
export async function api(path, options) {
  const res = await fetch(path, options);
  if (res.status === 401 && !path.startsWith("/api/auth/")) {
    window.dispatchEvent(new Event(SESSION_EXPIRED));
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = Array.isArray(body.detail)
        ? body.detail.map(d => `${(d.loc || []).slice(1).join(".")}: ${d.msg}`).join("; ")
        : body.detail || detail;
    } catch {
      /* keep statusText */
    }
    throw new Error(detail);
  }
  return res.json();
}

export const jsonRequest = (method, body) => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});
