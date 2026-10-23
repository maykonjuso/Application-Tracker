import createClient, { type Middleware } from "openapi-fetch";
import type { paths } from "./api-schema";

/**
 * Cliente tipado da API (tipos gerados do OpenAPI do FastAPI: `pnpm gen:api`).
 * Mesma origem via rewrite do Next (/api → backend), cookie de sessão HTTP-only.
 */
const csrf: Middleware = {
  async onRequest({ request }) {
    request.headers.set("x-tracker-csrf", "1");
    return request;
  },
  async onResponse({ response }) {
    if (response.status === 401 && typeof window !== "undefined") {
      if (!window.location.pathname.startsWith("/login")) window.location.href = "/login";
    }
    return response;
  },
};

export const api = createClient<paths>({ baseUrl: "", credentials: "include" });
api.use(csrf);

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

/** Extrai `data` ou lança erro com a mensagem padrão da API. */
export function unwrap<T>(res: { data?: T; error?: unknown; response: Response }): T {
  if (res.error !== undefined || res.data === undefined) {
    const err = res.error as { error?: { message?: unknown } } | undefined;
    const msg = err?.error?.message;
    throw new ApiError(typeof msg === "string" ? msg : `Erro ${res.response.status}`, res.response.status);
  }
  return res.data;
}

/** Para rotas sem tipagem específica (download etc.). */
export async function rawFetch(path: string, init?: RequestInit) {
  const res = await fetch(path, {
    credentials: "include",
    ...init,
    headers: { "x-tracker-csrf": "1", ...(init?.headers ?? {}) },
  });
  if (res.status === 401 && typeof window !== "undefined" && !window.location.pathname.startsWith("/login") && !path.includes("/auth/")) {
    window.location.href = "/login"; // sessão expirou
  }
  if (!res.ok) {
    let message = `Erro ${res.status}`;
    try {
      const body = await res.json();
      const apiError = body?.error;
      const validationErrors = apiError?.details?.errors;
      const reasons = Array.isArray(validationErrors)
        ? validationErrors
            .map((item: { loc?: unknown; msg?: unknown }) => {
              const message = typeof item?.msg === "string" ? item.msg.replace(/^Value error,\s*/i, "") : "";
              const location = Array.isArray(item?.loc)
                ? item.loc.filter((part): part is string | number => typeof part === "string" || typeof part === "number").join(".")
                : "";
              return message && location ? `${location}: ${message}` : message;
            })
            .filter(Boolean)
        : [];
      const summary = typeof apiError?.message === "string" ? apiError.message : message;
      if (reasons.length) {
        const requestPath = new URL(res.url, "http://localhost").pathname;
        const requestMethod = (init?.method ?? "GET").toUpperCase();
        message = `${summary} (${requestMethod} ${requestPath}): ${reasons.join("; ")}`;
      } else {
        message = summary;
      }
    } catch {}
    throw new ApiError(message, res.status);
  }
  return res;
}
