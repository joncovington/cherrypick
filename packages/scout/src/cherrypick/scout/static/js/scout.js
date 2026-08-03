"use strict";

function csrfToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? meta.content : "";
}

// Every mutating fetch (Alpine components) and every htmx POST carries the CSRF header the
// SecurityMiddleware requires — attached here once rather than at each call site.
document.body.addEventListener("htmx:configRequest", (evt) => {
  if (evt.detail.verb !== "get") {
    evt.detail.headers["X-Csrf-Token"] = csrfToken();
  }
});

function postJson(path, body) {
  return fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Csrf-Token": csrfToken() },
    body: JSON.stringify(body),
  }).then((r) => r.json());
}

function watchlistStore() {
  return {
    symbols: [],
    draft: "",
    async load() {
      const res = await fetch("/api/watchlist").then((r) => r.json());
      if (res.ok) this.symbols = res.symbols;
    },
    async addFromInput() {
      const sym = this.draft.trim().toUpperCase();
      if (!sym) return;
      const res = await postJson("/api/watchlist", { action: "add", symbols: [sym] });
      if (res.ok) {
        this.symbols = res.symbols;
        this.draft = "";
      }
    },
    async remove(sym) {
      const res = await postJson("/api/watchlist", { action: "remove", symbols: [sym] });
      if (res.ok) this.symbols = res.symbols;
    },
  };
}

// Views mount/unmount charts on htmx:afterSwap from M3 (symbol) onward; nothing to wire yet in M1.
document.body.addEventListener("htmx:afterSwap", () => {});
