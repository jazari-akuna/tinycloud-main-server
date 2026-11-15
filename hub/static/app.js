"use strict";
const CSRF = document.querySelector('meta[name="csrf"]').content;
const $ = (s) => document.querySelector(s);
let DATA = null;
let showHidden = false;

const fmtBytes = (n) => {
  if (n == null) return "–";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1000 && i < u.length - 1) { n /= 1000; i++; }
  return (n >= 100 || i === 0 ? n.toFixed(0) : n.toFixed(1)) + " " + u[i];
};
const fmtNum = (n) => (n == null ? "–" : Number(n).toLocaleString("en-US"));
const fmtUptime = (s) => {
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d} d ${h} h` : h ? `${h} h ${m} min` : `${m} min`;
};
const ago = (iso) => {
  if (!iso) return "never";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  return s < 90 ? "just now" : s < 5400 ? `${Math.round(s / 60)} min ago` : `${Math.round(s / 3600)} h ago`;
};
const el = (tag, attrs = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k === "style") e.style.cssText = v; // CSSOM, allowed by the page's content policy
    else if (k === "text") e.textContent = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) e.setAttribute(k, v);
  }
  for (const k of kids) if (k != null) e.append(k);
  return e;
};
const hue = (s) => [...s].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7);
const badge = (name) => el("span", { class: "mono-badge", text: (name || "?").trim().charAt(0).toUpperCase(),
  style: `background: hsl(${hue(name || "?")} 42% 42%)` });

function meter(label, value, unit, pct, sub) {
  const cls = pct >= 90 ? "down" : pct >= 75 ? "warn" : "";
  return el("div", { class: "meter" },
    el("span", { class: "label", text: label }),
    el("span", { class: "value" }, value, unit ? el("small", { text: " " + unit }) : null),
    pct == null ? null : el("div", { class: "bar", role: "img", "aria-label": `${Math.round(pct)}%` },
      el("span", { class: cls, style: `width:${Math.min(100, Math.max(2, pct))}%` })),
    sub ? el("span", { class: "sub", text: sub }) : null);
}

function renderHost(h, updates) {
  const m = $("#meters"), c = $("#chips");
  m.replaceChildren(); c.replaceChildren();
  if (!h || h.error) { m.append(el("p", { class: "empty", text: "Server figures are not in yet. They appear within a minute." })); return; }
  $("#hostname").textContent = h.hostname;
  $("#subtitle").textContent = `${h.os} · kernel ${h.kernel} · up ${fmtUptime(h.uptime_s)}`;
  const memUsed = h.mem_total - h.mem_available;
  const rootUsed = h.root_total - h.root_free;
  m.append(
    meter("Load", h.load[0].toFixed(2), `/ ${h.cpus} cores`, (h.load[0] / h.cpus) * 100, `5 min ${h.load[1].toFixed(2)} · 15 min ${h.load[2].toFixed(2)}`),
    meter("Memory", fmtBytes(memUsed), "used", (memUsed / h.mem_total) * 100,
      `of ${fmtBytes(h.mem_total)}` + (h.swap_total ? ` · swap ${fmtBytes(h.swap_total - h.swap_free)}` : "")),
  );
  if (h.zfs) {
    const total = h.zfs.used + h.zfs.avail;
    m.append(meter("Storage pool", fmtBytes(h.zfs.avail), "free", (h.zfs.used / total) * 100,
      `${fmtBytes(h.zfs.used)} of ${fmtBytes(total)} · ${h.zfs.health.toLowerCase()}`));
  }
  m.append(meter("System disk", fmtBytes(h.root_free), "free", (rootUsed / h.root_total) * 100, `${fmtBytes(rootUsed)} of ${fmtBytes(h.root_total)}`));
  if (h.temp_c != null) m.append(meter("Temperature", h.temp_c.toFixed(0), "°C", null, "Warmest sensor"));
  const chips = [];
  if (updates != null) chips.push([updates ? `${updates} updates waiting` : "Up to date", updates ? "warn" : "good"]);
  if (h.reboot_required) chips.push(["Restarts at midnight (Swiss time) for an update", "warn"]);
  chips.push([`${h.ssh_banned} addresses blocked from SSH`, ""]);
  chips.push([`Network since start: ↓ ${fmtBytes(h.net_rx)} ↑ ${fmtBytes(h.net_tx)}`, ""]);
  if (h.zfs && h.zfs.scrub) chips.push([`Last pool check: ${h.zfs.scrub.replace(/^scrub /, "").replace(/ with .*? on /, ", ")}`, ""]);
  for (const [t, cls] of chips) c.append(el("span", { class: "chip " + cls, text: t }));
}

function serviceTile(s) {
  const status = !s.enabled ? "Switched off in the proxy" : s.up ? `Answering · ${s.ms} ms` : s.code ? `Not answering (error ${s.code})` : "Not answering";
  return el("div", { class: "tile" + (s.hidden ? " off" : "") },
    badge(s.name),
    el("div", {},
      el("a", { class: "t-name", href: s.url, target: "_blank", rel: "noopener" },
        el("span", { class: "dot " + (s.up ? "up" : "down"), title: status }), s.name),
      s.description ? el("div", { class: "t-desc", text: s.description }) : null,
      el("div", { class: "t-meta", text: s.domain + " · " + status })),
    el("button", { class: "edit", type: "button", "aria-label": `Edit ${s.name}`, text: "Edit", onclick: () => editService(s) }));
}

function renderServices(list) {
  const box = $("#services");
  const hiddenCount = list.filter((s) => s.hidden).length;
  const btn = $("#toggle-hidden");
  btn.hidden = hiddenCount === 0;
  btn.textContent = showHidden ? "Hide the hidden ones" : `Show hidden (${hiddenCount})`;
  const shown = list.filter((s) => showHidden || !s.hidden);
  box.replaceChildren(...(shown.length ? shown.map(serviceTile) : [el("p", { class: "empty", text: "No sites found in the proxy yet." })]));
}

function linkTile(l) {
  let host = "";
  try { host = new URL(l.url).host; } catch (e) { host = l.url; }
  return el("div", { class: "tile" },
    badge(l.name),
    el("div", {},
      el("a", { class: "t-name", href: l.url, target: "_blank", rel: "noopener", text: l.name }),
      l.description ? el("div", { class: "t-desc", text: l.description }) : null,
      el("div", { class: "t-meta", text: host })),
    el("button", { class: "edit", type: "button", "aria-label": `Edit ${l.name}`, text: "Edit", onclick: () => openLinkForm(l) }));
}

function renderLinks(links) {
  $("#links").replaceChildren(...(links.length ? links.map(linkTile)
    : [el("p", { class: "empty", text: "Links you add here stay on the server for everyone who signs in. Add your router, printer, or anything else you open often." })]));
}

function fig(k, v, bad) {
  return el("div", { class: "fig" + (bad ? " bad" : "") }, el("div", { class: "v", text: v }), el("div", { class: "k", text: k }));
}

function appCard(title, url, figs, note) {
  return el("div", { class: "app" },
    el("div", { class: "app-head" }, el("h3", { text: title }), url ? el("a", { class: "small", href: url, target: "_blank", rel: "noopener", text: "Open" }) : null),
    el("div", { class: "figs" }, ...figs),
    note ? el("p", { class: "muted small", style: "margin:0", text: note }) : null);
}

function renderApps(a) {
  const cards = [];
  const ok = (x) => x && !x.error;
  if (ok(a.nextcloud)) cards.push(appCard("Nextcloud", "https://c.raph.io/", [
    fig("people", fmtNum(a.nextcloud.users)), fig("files", fmtNum(a.nextcloud.files)),
    fig("active today", fmtNum(a.nextcloud.active_24h)), fig("shared items", fmtNum(a.nextcloud.shares))], `Version ${a.nextcloud.version}`));
  if (ok(a.jellyfin)) cards.push(appCard("Jellyfin", "https://stream.raph.io/", [
    fig("films", fmtNum(a.jellyfin.movies)), fig("series", fmtNum(a.jellyfin.series)),
    fig("episodes", fmtNum(a.jellyfin.episodes)), fig("songs", fmtNum(a.jellyfin.songs))]));
  if (ok(a.n8n)) cards.push(appCard("n8n", "https://n8n-alex.raph.io/", [
    fig("workflows", fmtNum(a.n8n.workflows)), fig("switched on", fmtNum(a.n8n.active)),
    fig("runs today", fmtNum(a.n8n.runs_24h)), fig("failed today", fmtNum(a.n8n.failed_24h), a.n8n.failed_24h > 0)]));
  if (ok(a.yourls)) cards.push(appCard("Short links", "https://s.raph.io/admin/", [
    fig("links", fmtNum(a.yourls.links)), fig("clicks", fmtNum(a.yourls.clicks))]));
  if (ok(a.authelia)) cards.push(appCard("Sign-in", "https://sso.raph.io/", [
    fig("people", fmtNum(a.authelia.enabled)), fig("sign-ins today", fmtNum(a.authelia.signins_24h)),
    fig("wrong passwords today", fmtNum(a.authelia.failed_24h), a.authelia.failed_24h > 0)]));
  if (ok(a.proxy) && a.proxy.cert_expires) {
    const days = Math.round((new Date(a.proxy.cert_expires.replace(" ", "T") + "Z") - Date.now()) / 864e5);
    cards.push(appCard("Certificates", "https://rp.raph.io/", [fig("days left", fmtNum(days), days < 14)],
      "The proxy renews it automatically before then."));
  }
  $("#apps").replaceChildren(...(cards.length ? cards : [el("p", { class: "empty", text: "App figures are not in yet." })]));
}

function renderContainers(list) {
  const body = $("#containers tbody");
  body.replaceChildren(...(list || []).map((c) => el("tr", {},
    el("td", { text: c.name }),
    el("td", {}, el("span", { class: "pill " + (c.state === "running" ? "up" : "down"), text: c.state })),
    el("td", { class: "muted", text: c.status }),
    el("td", { class: "num", text: c.cpu || "–" }),
    el("td", { class: "num", text: c.mem || "–" }))));
}

async function load() {
  try {
    const r = await fetch("/api/data", { headers: { Accept: "application/json" } });
    if (r.status === 401 || r.redirected) { location.reload(); return; }
    DATA = await r.json();
  } catch (e) {
    $("#footer").textContent = "Can't reach the hub right now. It will try again shortly.";
    return;
  }
  renderHost(DATA.host, DATA.updates);
  renderServices(DATA.services || []);
  renderLinks(DATA.links || []);
  renderApps(DATA.apps || {});
  renderContainers(DATA.containers);
  $("#footer").textContent = `Figures updated ${ago(DATA.generated)}. They refresh every minute while this page is open, and much less often otherwise.`;
}

async function post(url, body) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF": CSRF }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || "Something went wrong. Try again.");
  return j;
}

function openLinkForm(l) {
  const f = $("#link-form");
  f.hidden = false;
  $("#link-id").value = l ? l.id : "";
  $("#link-name").value = l ? l.name : "";
  $("#link-url").value = l ? l.url : "";
  $("#link-desc").value = l ? l.description || "" : "";
  $("#link-delete").hidden = !l;
  $("#link-save").textContent = l ? "Save changes" : "Save link";
  $("#link-error").hidden = true;
  $("#link-name").focus();
}

function editService(s) {
  const d = $("#svc-dialog");
  $("#svc-title").textContent = s.domain;
  $("#svc-name").value = s.name;
  $("#svc-desc").value = s.description || "";
  $("#svc-hidden").checked = !!s.hidden;
  d.onclose = async () => {
    try {
      if (d.returnValue === "save") {
        await post("/api/services/" + encodeURIComponent(s.key),
          { name: $("#svc-name").value, description: $("#svc-desc").value, hidden: $("#svc-hidden").checked });
      } else if (d.returnValue === "reset") {
        await post("/api/services/" + encodeURIComponent(s.key), { reset: true });
      } else return;
      await load();
    } catch (e) { alert(e.message); }
  };
  d.showModal();
}

document.addEventListener("DOMContentLoaded", () => {
  $("#add-link").addEventListener("click", () => openLinkForm(null));
  $("#link-cancel").addEventListener("click", () => { $("#link-form").hidden = true; });
  $("#toggle-hidden").addEventListener("click", () => { showHidden = !showHidden; renderServices(DATA.services || []); });
  $("#link-delete").addEventListener("click", async () => {
    const id = $("#link-id").value;
    if (!id) return;
    const b = $("#link-delete");
    if (b.dataset.sure !== "1") { b.dataset.sure = "1"; b.textContent = "Click again to remove"; return; }
    b.dataset.sure = ""; b.textContent = "Remove";
    try { await post("/api/links/" + id, { delete: true }); $("#link-form").hidden = true; await load(); }
    catch (e) { $("#link-error").textContent = e.message; $("#link-error").hidden = false; }
  });
  $("#link-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const id = $("#link-id").value;
    const body = { name: $("#link-name").value, url: $("#link-url").value.trim(), description: $("#link-desc").value };
    try {
      await post(id ? "/api/links/" + id : "/api/links", body);
      $("#link-form").hidden = true;
      await load();
    } catch (e) { $("#link-error").textContent = e.message; $("#link-error").hidden = false; }
  });
  load();
  // Refresh only while the tab is visible, so a forgotten tab costs nothing.
  setInterval(() => { if (document.visibilityState === "visible") load(); }, 60000);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") load(); });
});
