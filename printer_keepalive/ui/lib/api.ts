import type {
  HealthResponse,
  ConfigPayload,
  DiscoveryResponse,
  CardsResponse,
  TemplatesResponse,
  PrintResponse,
  ActionResponse,
} from "./types";

function getAuthToken(): string {
  return localStorage.getItem("pk_auth_token") || "";
}

export function setAuthToken(token: string) {
  if (token) {
    localStorage.setItem("pk_auth_token", token);
  } else {
    localStorage.removeItem("pk_auth_token");
  }
}

export function getStoredAuthToken(): string {
  return getAuthToken();
}

/**
 * Resolve API path relative to the ingress base.
 * When served behind HA ingress, the base path is extracted from the current URL.
 */
function apiPath(endpoint: string): string {
  // In dev mode (Vite proxy), just use the path directly
  if (import.meta.env.DEV) {
    return endpoint;
  }
  // In production behind ingress, resolve relative to current base
  const base = document.baseURI || window.location.origin;
  const url = new URL(endpoint, base);
  return url.pathname + url.search;
}

class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(
  endpoint: string,
  init?: RequestInit,
): Promise<T> {
  const token = getAuthToken();
  const headers: Record<string, string> = {
    ...(init?.headers as Record<string, string>),
  };
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  if (init?.body && typeof init.body === "string") {
    headers["Content-Type"] = "application/json";
  }

  const res = await fetch(apiPath(endpoint), { ...init, headers });

  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body.error) msg = body.error;
    } catch {
      // ignore parse errors
    }
    throw new ApiError(res.status, msg);
  }

  return res.json();
}

// GET endpoints
export const fetchHealth = () => request<HealthResponse>("/health");
export const fetchConfig = () => request<ConfigPayload>("/config");
export const fetchDiscovery = (force = false) =>
  request<DiscoveryResponse>(force ? "/discovery?force=true" : "/discovery");
export const fetchTemplates = () => request<TemplatesResponse>("/templates");
export const fetchCards = (style: string, printerIds?: string[]) => {
  const params = new URLSearchParams({ style });
  if (printerIds?.length) params.set("printers", printerIds.join(","));
  return request<CardsResponse>(`/cards?${params}`);
};
export const fetchPing = () => request<{ ok: boolean; version: string }>("/ping");

// Preview URL (returns JPEG, not JSON)
export function previewUrl(printerId: string, template: string): string {
  const path = `/printers/${encodeURIComponent(printerId)}/preview?template=${encodeURIComponent(template)}`;
  if (import.meta.env.DEV) return path;
  return apiPath(path);
}

// POST endpoints
export const postConfig = (options: Record<string, unknown>) =>
  request<ActionResponse>("/config", {
    method: "POST",
    body: JSON.stringify({ options }),
  });

export const postRestart = () =>
  request<ActionResponse>("/actions/restart", { method: "POST" });

export const postRescan = () =>
  request<DiscoveryResponse>("/discovery/rescan", { method: "POST" });

export const postPrint = (
  printerId: string,
  opts: { template?: string; force?: boolean } = {},
) =>
  request<PrintResponse>(`/printers/${encodeURIComponent(printerId)}/print`, {
    method: "POST",
    body: JSON.stringify(opts),
  });

export const postPrinterSettings = (
  printerId: string,
  settings: { enabled?: boolean; cadence_hours?: number; template?: string },
) =>
  request<ActionResponse>(
    `/printers/${encodeURIComponent(printerId)}/settings`,
    { method: "POST", body: JSON.stringify(settings) },
  );

export const postPrinterPoll = (printerId: string) =>
  request<ActionResponse>(
    `/printers/${encodeURIComponent(printerId)}/poll`,
    { method: "POST" },
  );

export const postPrinterDelete = (printerId: string) =>
  request<ActionResponse>(
    `/printers/${encodeURIComponent(printerId)}/delete`,
    { method: "POST" },
  );
