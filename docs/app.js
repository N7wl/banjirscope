/*
 * BanjirScope web app — static, no backend.
 *
 * Reads data/events.json (index of processed flood events) and, for the
 * selected event, data/<id>/meta.json plus PNG overlays produced by
 * scripts/run_pipeline.py. Everything is plain files, so the site runs on
 * GitHub Pages.
 *
 * Swipe comparison: the "before" and "after" radar images live in two
 * separate Leaflet panes. Each pane is clipped with CSS `clip: rect(...)`
 * so the before image shows left of the divider and the after image right
 * of it. Panes sit at the map's layer origin, so the clip rectangle is
 * computed in layer-point coordinates and refreshed whenever the map moves.
 */
(() => {
  "use strict";

  const DATA = "data";
  const state = { meta: null, layers: {}, swipeFrac: 0.5 };

  // ---------------------------------------------------------------- map
  const map = L.map("map", { zoomControl: false, zoomSnap: 0.25, minZoom: 3 });
  L.control.zoom({ position: "topright" }).addTo(map);
  L.control.scale({ position: "bottomright", imperial: false }).addTo(map);

  // Keyless Esri basemaps; each has a matching labels layer drawn on top
  const ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services";
  const esri = (path, opts) => L.tileLayer(`${ESRI}/${path}/MapServer/tile/{z}/{y}/{x}`, { maxZoom: 19, ...opts });
  const basemaps = {
    dark: {
      base: esri("Canvas/World_Dark_Gray_Base", { maxNativeZoom: 16, attribution: "Basemap &copy; Esri, HERE, Garmin, &copy; OpenStreetMap contributors" }),
      labels: esri("Canvas/World_Dark_Gray_Reference", { maxNativeZoom: 16, pane: "labels" }),
    },
    satellite: {
      base: esri("World_Imagery", { maxNativeZoom: 18, attribution: "Imagery &copy; Esri, Maxar, Earthstar Geographics" }),
      labels: esri("Reference/World_Boundaries_and_Places", { maxNativeZoom: 18, pane: "labels" }),
    },
  };

  // Panes, bottom to top: radar before/after, change, water masks, labels
  const pane = (name, z) => { const p = map.createPane(name); p.style.zIndex = z; return p; };
  const beforePane = pane("before", 410);
  const afterPane = pane("after", 411);
  pane("change", 420);
  pane("water", 430);
  pane("labels", 640).style.pointerEvents = "none";

  function setBasemap(name) {
    Object.entries(basemaps).forEach(([k, { base, labels }]) => {
      if (k === name) { base.addTo(map); labels.addTo(map); }
      else { map.removeLayer(base); map.removeLayer(labels); }
    });
  }
  setBasemap("dark");

  // ----------------------------------------------------------- helpers
  const $ = (id) => document.getElementById(id);
  const fmtKm2 = (v) => `${Math.round(v).toLocaleString()} km²`;
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const fmtDate = (iso) => { const [y, m, d] = iso.split("-").map(Number); return `${d} ${MONTHS[m - 1]} ${y}`; };

  async function getJSON(url) {
    const res = await fetch(url, { cache: "no-cache" });
    if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
    return res.json();
  }

  // ------------------------------------------------------------- swipe
  const swipe = $("swipe");
  const handle = swipe.querySelector(".swipe-handle");

  function updateClip() {
    if (!state.meta) return;
    const size = map.getSize();
    const x = Math.round(size.x * state.swipeFrac);
    swipe.style.left = `${x}px`;
    const nw = map.containerPointToLayerPoint([0, 0]);
    const se = map.containerPointToLayerPoint(size);
    const cx = nw.x + x;
    beforePane.style.clip = `rect(${nw.y}px, ${cx}px, ${se.y}px, ${nw.x}px)`;
    afterPane.style.clip = `rect(${nw.y}px, ${se.x}px, ${se.y}px, ${cx}px)`;
  }

  function setSwipe(frac) {
    state.swipeFrac = Math.min(0.98, Math.max(0.02, frac));
    updateClip();
  }

  handle.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    handle.setPointerCapture(e.pointerId);
    map.dragging.disable();
    const rect = map.getContainer().getBoundingClientRect();
    const move = (ev) => setSwipe((ev.clientX - rect.left) / rect.width);
    const up = () => {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", up);
      map.dragging.enable();
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", up);
  });
  handle.addEventListener("keydown", (e) => {   // keyboard accessible
    if (e.key === "ArrowLeft") setSwipe(state.swipeFrac - 0.03);
    if (e.key === "ArrowRight") setSwipe(state.swipeFrac + 0.03);
  });
  L.DomEvent.disableClickPropagation(swipe);
  map.on("move zoom resize viewreset", updateClip);

  // ------------------------------------------------------------ layers
  function clearLayers() {
    Object.values(state.layers).forEach((l) => map.removeLayer(l));
    state.layers = {};
  }

  function buildLayers(meta) {
    clearLayers();
    const base = `${DATA}/${meta.id}/`;
    const b = meta.bounds;
    const overlay = (file, opts) => L.imageOverlay(base + file, b, { interactive: false, ...opts });
    state.layers = {
      before: overlay(meta.layers.before, { pane: "before" }),
      after: overlay(meta.layers.after, { pane: "after" }),
      change: overlay(meta.layers.log_ratio, { pane: "change", opacity: 0.75 }),
      perm: overlay(meta.layers.permanent_water, { pane: "water", className: "crisp" }),
      flood: overlay(meta.layers.flood, { pane: "water", className: "crisp" }),
      outline: L.rectangle(b, { color: "#ffffff", weight: 1, opacity: 0.6, dashArray: "4 6", fill: false, interactive: false }),
    };
    state.layers.outline.addTo(map);
    syncLayerToggles();
    // Keep the footprint clear of the panel (side panel on desktop, bottom sheet on phones)
    const panel = $("panel");
    const wide = window.innerWidth > 720;
    map.fitBounds(b, {
      paddingTopLeft: wide ? [panel.offsetWidth + 30, 60] : [10, 50],
      paddingBottomRight: wide ? [30, 20] : [10, panel.offsetHeight + 16],
      animate: false,
    });
    // Start the swipe divider in the middle of the radar footprint
    const c = map.latLngToContainerPoint(L.latLngBounds(b).getCenter());
    setSwipe(c.x / map.getSize().x);
  }

  function show(name, on) {
    const layer = state.layers[name];
    if (!layer) return;
    if (on) layer.addTo(map); else map.removeLayer(layer);
  }

  function syncLayerToggles() {
    const radarOn = $("layer-radar").checked;
    show("before", radarOn);
    show("after", radarOn);
    swipe.hidden = !radarOn;
    show("change", $("layer-change").checked);
    $("change-legend").hidden = !$("layer-change").checked;
    show("perm", $("layer-perm").checked);
    show("flood", $("layer-flood").checked);
    // keep flood above permanent water
    if (map.hasLayer(state.layers.flood)) state.layers.flood.bringToFront();
    setRadarOpacity();
    updateClip();
  }

  function setRadarOpacity() {
    const o = $("radar-opacity").value / 100;
    ["before", "after"].forEach((k) => state.layers[k] && state.layers[k].setOpacity(o));
  }

  ["layer-flood", "layer-perm", "layer-change", "layer-radar"].forEach((id) =>
    $(id).addEventListener("change", syncLayerToggles));
  $("radar-opacity").addEventListener("input", setRadarOpacity);

  document.querySelectorAll(".segmented button").forEach((btn) =>
    btn.addEventListener("click", () => {
      document.querySelectorAll(".segmented button").forEach((b) => b.classList.toggle("active", b === btn));
      setBasemap(btn.dataset.base);
    }));

  $("panel-toggle").addEventListener("click", () => {
    const panel = $("panel");
    const collapsed = panel.classList.toggle("collapsed");
    $("panel-toggle").textContent = collapsed ? "+" : "–";
    $("panel-toggle").setAttribute("aria-expanded", String(!collapsed));
  });

  // -------------------------------------------------------------- panel
  function renderPanel(meta) {
    const s = meta.stats;
    $("event-title").textContent = meta.title;
    $("event-location").textContent = meta.location;
    const badge = $("mode-badge");
    if (meta.mode === "gcov") {
      badge.className = "badge gcov";
      badge.textContent = "Calibrated NISAR L2 GCOV · HH";
      badge.title = "Radiometrically terrain-corrected backscatter (gamma-naught) from NISAR Level-2 GCOV products.";
    } else {
      badge.className = "badge preview";
      badge.textContent = "Preview · uncalibrated NISAR browse images";
      badge.title = "Computed from ASF's public browse images (~150 m). Areas are approximate until calibrated GCOV data is processed.";
    }
    $("flood-km2").textContent = Math.round(s.flood_km2).toLocaleString();
    $("flood-pct").textContent = `· ${s.flood_pct}% of the analysed area`;
    $("perm-km2").textContent = fmtKm2(s.permanent_water_km2);
    $("mapped-km2").textContent = fmtKm2(s.mapped_area_km2);
    $("date-before").textContent = fmtDate(meta.before.date);
    $("date-after").textContent = fmtDate(meta.after.date);
    $("days-between").textContent = `${meta.days_between} days apart · same orbit track`;
    $("label-before").textContent = `◀ Before · ${fmtDate(meta.before.date)}`;
    $("label-after").textContent = `After · ${fmtDate(meta.after.date)} ▶`;
    $("thresholds").textContent =
      `This event: water below ${meta.thresholds.water_db} dB on the flood date, ` +
      `change below ${meta.thresholds.change_db} dB, ${meta.processing.resolution_m} m pixels, ` +
      `track ${meta.stack.track} / frame ${meta.stack.frame} (${meta.stack.direction.toLowerCase()}), ${meta.polarization} polarisation.`;

    const links = [
      { title: `Before scene (${meta.before.date}) on ASF Vertex`, url: meta.before.url },
      { title: `After scene (${meta.after.date}) on ASF Vertex`, url: meta.after.url },
      { title: "NISAR mission (NASA)", url: "https://science.nasa.gov/mission/nisar/" },
      { title: "NISAR GCOV product guide (ASF)", url: "https://nisar-docs.asf.alaska.edu/gcov/" },
      ...meta.news,
    ];
    $("source-list").replaceChildren(...links.map(({ title, url }) => {
      const li = document.createElement("li");
      const a = document.createElement("a");
      a.href = url; a.textContent = title; a.target = "_blank"; a.rel = "noopener";
      li.append(a);
      return li;
    }));
    document.title = `${meta.title} — BanjirScope`;
  }

  // -------------------------------------------------------------- load
  async function loadEvent(id) {
    const meta = await getJSON(`${DATA}/${id}/meta.json`);
    state.meta = meta;
    renderPanel(meta);
    buildLayers(meta);
    history.replaceState(null, "", `#${id}`);
  }

  async function init() {
    try {
      const events = await getJSON(`${DATA}/events.json`);
      if (!events.length) throw new Error("No processed events found in data/events.json");
      const select = $("event-select");
      events.forEach((e) => select.add(new Option(`${e.title} (${e.after_date})`, e.id)));
      $("event-picker").hidden = events.length < 2;
      select.addEventListener("change", () => loadEvent(select.value));
      window.addEventListener("hashchange", () => {
        const id = location.hash.slice(1);
        if (id !== state.meta?.id && events.some((e) => e.id === id)) { select.value = id; loadEvent(id); }
      });
      const fromHash = decodeURIComponent(location.hash.slice(1));
      const first = events.find((e) => e.id === fromHash) ? fromHash : events[0].id;
      select.value = first;
      await loadEvent(first);
    } catch (err) {
      console.error(err);
      $("event-title").textContent = "Could not load flood data";
      $("event-location").textContent = String(err.message || err) +
        " — if you opened index.html directly from disk, serve the folder instead: python -m http.server";
    }
  }

  init();
})();
