/* Live page: poll feed.json, render the lead, ticker, river and desks; never move the page under a reader. */
(function () {
  "use strict";
  var TZ = "America/New_York";
  var STALE_MS = 10 * 60 * 1000; // no new feed for 10 minutes: tell the reader updates are paused
  var state = { feed: null, shown: null, seen: {}, view: "river", desk: "all", pending: 0, timer: null, tickerKey: "" };

  function $(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }
  function safeUrl(url) { return /^https?:\/\//i.test(url || "") ? url : "#"; }
  function link(url, text, cls) {
    var a = el("a", cls, text);
    a.href = safeUrl(url);
    a.target = "_blank";
    a.rel = "noopener";
    return a;
  }
  function store(key, value) { try { if (value === undefined) return localStorage.getItem(key); localStorage.setItem(key, value); } catch (e) { return null; } }

  var timeFmt = new Intl.DateTimeFormat("en-US", { timeZone: TZ, hour: "numeric", minute: "2-digit" });
  var dayFmt = new Intl.DateTimeFormat("en-US", { timeZone: TZ, weekday: "long", month: "short", day: "numeric" });
  var keyFmt = new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" });
  function clock(iso) { return iso ? timeFmt.format(new Date(iso)).replace("AM", "a.m.").replace("PM", "p.m.") : ""; }
  function dayLabel(iso) {
    var d = new Date(iso), today = keyFmt.format(new Date()), key = keyFmt.format(d);
    if (key === today) return "Today";
    if (key === keyFmt.format(new Date(Date.now() - 864e5))) return "Yesterday";
    return dayFmt.format(d);
  }
  function deskName(key) {
    var desks = (state.shown && state.shown.desks) || [];
    for (var i = 0; i < desks.length; i++) if (desks[i].key === key) return desks[i].name;
    return "";
  }

  function badge(story) {
    if (story.check === "verified") return el("span", "badge verified", "✓ Verified");
    if (story.check === "first-pass") return el("span", "badge first-pass", "First pass");
    return null;
  }

  function storyNode(story, isNew, compact) {
    var art = el("article", "story" + (isNew ? " arrive" : ""));
    art.dataset.id = story.id;
    var t = el("time", null, clock(story.published));
    t.dateTime = story.published;
    art.appendChild(t);
    var body = el("div");
    var meta = el("div", "meta");
    meta.appendChild(el("span", "who", story.source));
    if (!compact && deskName(story.desk)) meta.appendChild(el("span", "deskname", deskName(story.desk)));
    var b = badge(story);
    if (b) meta.appendChild(b);
    if (story.official) meta.appendChild(el("span", "badge official", "Official source"));
    body.appendChild(meta);
    var h = el("h3");
    h.appendChild(link(story.url, story.title));
    body.appendChild(h);
    if (story.summary) body.appendChild(el("p", null, story.summary));
    if (story.also && story.also.length) {
      var also = el("div", "also", "Also reported by ");
      story.also.slice(0, 4).forEach(function (a, i) {
        if (i) also.appendChild(document.createTextNode(", "));
        also.appendChild(link(a.url, a.source));
      });
      body.appendChild(also);
    }
    if (story.correction) body.appendChild(el("div", "fix", story.correction));
    art.appendChild(body);
    return art;
  }

  function byId(feed) {
    var map = {};
    feed.stories.forEach(function (s) { map[s.id] = s; });
    return map;
  }

  function renderLead(feed) {
    var box = $("lead");
    box.textContent = "";
    var main = el("div");
    var lead = feed.lead;
    if (!lead) {
      main.appendChild(el("p", "lead-empty", "The lead desk is choosing today's lead story."));
    } else {
      var eye = el("p", "eyebrow");
      eye.appendChild(el("span", "tag", "Lead"));
      if (deskName(lead.desk)) eye.appendChild(el("span", null, deskName(lead.desk)));
      eye.appendChild(el("span", "since", "since " + clock(lead.since)));
      main.appendChild(eye);
      var h = el("h1");
      h.appendChild(link(lead.url, lead.title));
      main.appendChild(h);
      if (lead.summary) main.appendChild(el("p", "deck", lead.summary));
      var src = el("p", "src");
      src.appendChild(link(lead.url, lead.source + " ↗"));
      if (lead.also && lead.also.length) src.appendChild(document.createTextNode(" · also " + lead.also.map(function (a) { return a.source; }).slice(0, 3).join(", ")));
      main.appendChild(src);
    }
    box.appendChild(main);
    var dev = el("div", "developing");
    var map = byId(feed);
    var items = (feed.developing || []).map(function (id) { return map[id]; }).filter(Boolean);
    if (items.length) {
      dev.appendChild(el("h2", null, "Developing"));
      var ol = el("ol");
      items.forEach(function (s) {
        var li = el("li");
        li.appendChild(link(s.url, s.title));
        li.appendChild(el("div", "meta", s.source + " · " + clock(s.published)));
        ol.appendChild(li);
      });
      dev.appendChild(ol);
    }
    box.appendChild(dev);
  }

  function renderTicker(feed) {
    var items = feed.ticker || [];
    var key = items.map(function (t) { return t.id; }).join("|");
    if (key === state.tickerKey) return; // unchanged: let the marquee keep rolling
    state.tickerKey = key;
    var track = $("ticker");
    track.textContent = "";
    for (var pass = 0; pass < 2; pass++) { // two copies make the loop seamless
      items.forEach(function (t) {
        var a = link(t.url, null);
        a.appendChild(el("b", null, t.source));
        a.appendChild(document.createTextNode(t.title));
        if (pass) { a.tabIndex = -1; a.setAttribute("aria-hidden", "true"); }
        track.appendChild(a);
      });
    }
    track.style.setProperty("--tick-seconds", Math.max(40, items.length * 6) + "s");
  }

  function renderChips(feed) {
    var nav = $("chips");
    nav.textContent = "";
    var all = [{ key: "all", name: "All desks", today: null }].concat(feed.desks);
    all.forEach(function (d) {
      var b = el("button", null, d.name);
      b.type = "button";
      b.setAttribute("aria-pressed", String(state.desk === d.key));
      if (d.today != null) b.appendChild(el("span", null, String(d.today)));
      b.addEventListener("click", function () { state.desk = d.key; store("live-desk", d.key); render(true); });
      nav.appendChild(b);
    });
  }

  function renderRiver(feed, fresh) {
    var river = $("river");
    river.textContent = "";
    var list = feed.stories.filter(function (s) { return state.desk === "all" || s.desk === state.desk; });
    if (!list.length) { river.appendChild(el("p", "empty", "Nothing on this desk in the last three days.")); return; }
    var lastDay = "";
    list.forEach(function (s) {
      var d = dayLabel(s.published);
      if (d !== lastDay) { river.appendChild(el("h2", "day", d)); lastDay = d; }
      river.appendChild(storyNode(s, fresh[s.id]));
    });
  }

  function renderSide(feed) {
    var pulse = $("pulse");
    pulse.textContent = "";
    var max = Math.max.apply(null, feed.desks.map(function (d) { return d.today; }).concat([1]));
    feed.desks.forEach(function (d) {
      var li = el("li");
      li.appendChild(el("span", null, d.name));
      li.appendChild(el("b", null, String(d.today)));
      var bar = el("span", "bar");
      var fill = el("i");
      fill.style.width = Math.round(100 * d.today / max) + "%";
      bar.appendChild(fill);
      li.appendChild(bar);
      pulse.appendChild(li);
    });
    var docket = $("docket");
    docket.textContent = "";
    (feed.docket || []).forEach(function (r) {
      var li = el("li");
      li.appendChild(link(r.url, r.agency + ": " + r.action));
      li.appendChild(el("span", "st", r.next || r.status));
      docket.appendChild(li);
    });
  }

  function renderDesks(feed, fresh) {
    var box = $("desks-view");
    box.textContent = "";
    feed.desks.forEach(function (d) {
      var col = el("section", "desk");
      var h = el("h2", null, d.name);
      h.appendChild(el("span", null, String(d.today)));
      col.appendChild(h);
      feed.stories.filter(function (s) { return s.desk === d.key; }).slice(0, 25).forEach(function (s) {
        col.appendChild(storyNode(s, fresh[s.id], true));
      });
      box.appendChild(col);
    });
  }

  function renderStatus() {
    var feed = state.feed;
    if (!feed) return;
    var age = Date.now() - new Date(feed.generated).getTime();
    var dot = $("dot"), text = $("status-text"), banner = $("banner");
    var level = age > STALE_MS ? "FROZEN" : feed.level;
    dot.className = "dot " + (level === "LIVE" || level === "LITE" ? "on" : level === "HEADLINES" ? "warn" : "off");
    var secs = Math.max(0, Math.round(age / 1000));
    text.textContent = level === "FROZEN" ? "Paused since " + clock(feed.generated) : "Updated " + (secs < 60 ? secs + " s" : Math.round(secs / 60) + " min") + " ago";
    var msg = "";
    if (level === "FROZEN") msg = "Updates are paused since " + clock(feed.generated) + " ET. The stories below were current then.";
    else if (level === "HEADLINES") msg = "Summaries are paused for a few minutes. New stories appear as headline, source and link.";
    banner.hidden = !msg;
    banner.className = "banner wrap" + (level === "FROZEN" ? " warn" : "");
    banner.textContent = msg;
    var wire = feed.wire || [];
    var line = $("sweep");
    line.textContent = "";
    if (wire.length && level !== "FROZEN") {
      line.appendChild(document.createTextNode("Checking the wire · "));
      line.appendChild(el("b", null, wire[0].source + " " + clock(wire[0].at)));
      wire.slice(1, 6).forEach(function (w) { line.appendChild(document.createTextNode(" · " + w.source + " " + clock(w.at))); });
    }
  }

  function atTop() { return window.scrollY < 140; }

  function render(force) {
    var feed = state.feed;
    if (!feed) return;
    if (!force && state.shown && !atTop()) {
      var known = byId(state.shown), count = 0;
      feed.stories.forEach(function (s) { if (!known[s.id]) count++; });
      state.pending = count;
      $("new-count").textContent = count === 1 ? "1 new story" : count + " new stories";
      $("new-pill").hidden = count === 0;
      renderTicker(feed);
      renderStatus();
      return;
    }
    var fresh = {};
    if (state.shown) feed.stories.forEach(function (s) { if (!state.seen[s.id]) fresh[s.id] = true; });
    feed.stories.forEach(function (s) { state.seen[s.id] = true; });
    state.shown = feed;
    state.pending = 0;
    $("new-pill").hidden = true;
    $("site-name").textContent = feed.site.name;
    $("foot-name").textContent = feed.site.name;
    $("site-label").textContent = feed.site.label;
    document.title = feed.site.name + " · " + feed.site.label;
    renderLead(feed);
    renderTicker(feed);
    renderChips(feed);
    if (state.view === "river") { renderRiver(feed, fresh); renderSide(feed); }
    else renderDesks(feed, fresh);
    renderStatus();
  }

  function setView(view) {
    state.view = view;
    store("live-view", view);
    $("river-view").hidden = view !== "river";
    $("desks-view").hidden = view !== "desks";
    document.querySelectorAll(".views button").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.view === view)); });
    render(true);
  }

  function poll() {
    fetch("feed.json?t=" + Date.now(), { cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (feed) {
        var changed = !state.feed || feed.generated !== state.feed.generated;
        state.feed = feed;
        if (changed) render(false); else renderStatus();
        schedule(feed.site.poll_seconds);
      })
      .catch(function () { renderStatus(); schedule(30); });
  }
  function schedule(seconds) {
    clearTimeout(state.timer);
    state.timer = setTimeout(poll, Math.max(5, seconds || 15) * 1000);
  }

  $("new-pill").addEventListener("click", function () {
    window.scrollTo({ top: 0, behavior: "smooth" });
    render(true);
  });
  window.addEventListener("scroll", function () { if (atTop() && state.pending) render(true); }, { passive: true });
  document.querySelectorAll(".views button").forEach(function (b) { b.addEventListener("click", function () { setView(b.dataset.view); }); });
  document.addEventListener("visibilitychange", function () { if (!document.hidden) poll(); });
  setInterval(renderStatus, 5000);

  state.desk = store("live-desk") || "all";
  var view = store("live-view");
  if (view === "desks") { state.view = "desks"; $("river-view").hidden = true; $("desks-view").hidden = false; document.querySelectorAll(".views button").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.view === "desks")); }); }
  poll();
})();
