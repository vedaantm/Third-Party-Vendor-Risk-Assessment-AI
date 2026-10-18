import { vi } from "vitest";

export type Route = (url: URL, init?: RequestInit) => Response | Promise<Response> | undefined;

export function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

/**
 * Replace global fetch. Each route returns a Response for URLs it handles,
 * or undefined to pass; an unhandled URL fails the test loudly.
 */
export function mockFetch(...routes: Route[]) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url);
    for (const route of routes) {
      const res = await route(url, init);
      if (res) return res;
    }
    throw new Error(`Unmocked fetch: ${init?.method ?? "GET"} ${url.pathname}${url.search}`);
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

/** Route helper: match method + pathname (exact). */
export function on(method: string, pathname: string, respond: (url: URL, init?: RequestInit) => Response): Route {
  return (url, init) =>
    (init?.method ?? "GET") === method && url.pathname === pathname ? respond(url, init) : undefined;
}
