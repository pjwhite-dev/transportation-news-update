/* Live page: poll feed.json, render the tickers, lead, docket and one feed; never move the page under a reader. */
(function () {
  "use strict";
  var TZ = "America/New_York";
  var STALE_MS = 10 * 60 * 1000; // no new feed for 10 minutes: tell the reader updates are paused
  var SWEEP_MS = 8000; // the "checking the wire" line shows one source at a time, this long each
  var state = { feed: null, shown: null, seen: {}, hidden: {}, pending: 0, timer: null, tickerKey: "", marketKey: "", sweepAt: 0 };

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
  // A source that gives only a date: show when we found it (on that day), never a made-up time.
  function storyTime(story) {
    if (story.time_known !== false) return clock(story.published);
    if (story.found && keyFmt.format(new Date(story.found)) === keyFmt.format(new Date(story.published))) return "found " + clock(story.found);
    return "no time given";
  }
  function dayLabel(iso) {
    var d = new Date(iso), today = keyFmt.format(new Date()), key = keyFmt.format(d);
    if (key === today) return "Today";
    if (key === keyFmt.format(new Date(Date.now() - 864e5))) return "Yesterday";
    return dayFmt.format(d);
  }
  function catName(key) {
    var cats = (state.shown && state.shown.desks) || [];
    for (var i = 0; i < cats.length; i++) if (cats[i].key === key) return cats[i].name;
    return "";
  }

  function badge(story) {
    if (story.check === "checked") return el("span", "badge checked", "Summary double-checked");
    if (story.check === "quick") return el("span", "badge quick", "Quick summary");
    return null;
  }

  function storyNode(story, isNew) {
    var art = el("article", "story" + (isNew ? " arrive" : ""));
    art.dataset.id = story.id;
    art.dataset.cat = story.desk;
    var side = el("div", "cat");
    side.appendChild(el("b", null, catName(story.desk)));
    var t = el("time", null, storyTime(story));
    t.dateTime = story.published;
    side.appendChild(t);
    art.appendChild(side);
    var body = el("div");
    var meta = el("div", "meta");
    meta.appendChild(el("span", "who", story.source));
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
    var main = $("lead");
    main.textContent = "";
    var lead = feed.lead;
    if (!lead) {
      main.appendChild(el("p", "lead-empty", "No story so far has been significant enough to lead the page. The newest stories are below, as they come in."));
    } else {
      var eye = el("p", "eyebrow");
      eye.appendChild(el("span", "tag", "Lead"));
      if (catName(lead.desk)) eye.appendChild(el("span", null, catName(lead.desk)));
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
    var dev = $("developing");
    dev.textContent = "";
    var map = byId(feed);
    var items = (feed.developing || []).map(function (id) { return map[id]; }).filter(Boolean);
    if (items.length) {
      dev.appendChild(el("h2", null, "Developing"));
      var ol = el("ol");
      items.forEach(function (s) {
        var li = el("li");
        li.appendChild(link(s.url, s.title));
        li.appendChild(el("div", "meta", s.source + " · " + storyTime(s)));
        ol.appendChild(li);
      });
      dev.appendChild(ol);
    }
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

  function quoteNode(q) {
    var item = el("span", "q");
    var ab = el("abbr", null, q.symbol);
    ab.title = q.name || q.symbol;
    item.appendChild(ab);
    item.appendChild(el("span", "px", Number(q.price).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })));
    var p = Number(q.percent), tone = p > 0 ? "up" : p < 0 ? "down" : "flat";
    item.appendChild(el("span", "chg " + tone, (p > 0 ? "▲ " : p < 0 ? "▼ " : "– ") + Math.abs(p).toFixed(2) + "%"));
    return item;
  }

  function renderMarket(feed) {
    var m = feed.market, box = $("market");
    box.hidden = !(m && m.quotes && m.quotes.length);
    if (box.hidden) return;
    var key = m.as_of + "|" + m.label;
    if (key === state.marketKey) return; // unchanged: let it keep rolling
    state.marketKey = key;
    var track = $("quotes");
    track.textContent = "";
    for (var pass = 0; pass < 2; pass++) { // two copies make the loop seamless, as on the wire
      var when = el("span", "q when", m.open ? m.label + " · " + clock(m.as_of) : m.label);
      track.appendChild(when);
      m.quotes.forEach(function (q) { track.appendChild(quoteNode(q)); });
      if (pass) Array.prototype.forEach.call(track.children, function (c, i) { if (i > m.quotes.length) c.setAttribute("aria-hidden", "true"); });
    }
    track.style.setProperty("--tick-seconds", Math.max(30, m.quotes.length * 4) + "s");
  }

  function saveHidden() { store("live-hidden", Object.keys(state.hidden).filter(function (k) { return state.hidden[k]; }).join(",")); }

  function renderFilters(feed) {
    var box = $("filters");
    box.textContent = "";
    feed.desks.forEach(function (d) {
      var b = el("button");
      b.type = "button";
      b.dataset.cat = d.key;
      b.setAttribute("aria-pressed", String(!state.hidden[d.key]));
      b.title = (state.hidden[d.key] ? "Show " : "Hide ") + d.name;
      b.appendChild(el("i"));
      b.appendChild(document.createTextNode(d.name));
      b.appendChild(el("span", null, String(d.today)));
      b.addEventListener("click", function () { state.hidden[d.key] = !state.hidden[d.key]; saveHidden(); render(true); });
      box.appendChild(b);
    });
    var anyHidden = feed.desks.some(function (d) { return state.hidden[d.key]; });
    if (anyHidden) {
      var all = el("button", "all", "Show all");
      all.type = "button";
      all.addEventListener("click", function () { state.hidden = {}; saveHidden(); render(true); });
      box.appendChild(all);
    }
  }

  function docketItem(r) {
    var li = el("li");
    li.appendChild(link(r.url, r.agency + ": " + r.action));
    li.appendChild(el("span", "st", r.next || r.status));
    return li;
  }

  function renderDocket(feed) {
    var rows = feed.docket || [], first = 4;
    $("docket-box").hidden = !rows.length;
    var top = $("docket"), rest = $("docket-rest");
    top.textContent = "";
    rest.textContent = "";
    rows.slice(0, first).forEach(function (r) { top.appendChild(docketItem(r)); });
    rows.slice(first).forEach(function (r) { rest.appendChild(docketItem(r)); });
    $("docket-more").hidden = rows.length <= first;
    $("docket-more-label").textContent = (rows.length - first) + " more on the docket";
  }

  function renderFeed(feed, fresh) {
    var box = $("feed");
    box.textContent = "";
    var list = feed.stories.filter(function (s) { return !state.hidden[s.desk]; });
    if (!list.length) {
      box.appendChild(el("p", "empty", feed.stories.length ? "Every category is switched off. Turn one back on above." : "No stories in the last three days."));
      return;
    }
    var lastDay = "";
    list.forEach(function (s) {
      var d = dayLabel(s.published);
      if (d !== lastDay) { box.appendChild(el("h2", "day", d)); lastDay = d; }
      box.appendChild(storyNode(s, fresh[s.id]));
    });
  }

  function level() {
    var feed = state.feed;
    return Date.now() - new Date(feed.generated).getTime() > STALE_MS ? "FROZEN" : feed.level;
  }

  function renderStatus() {
    var feed = state.feed;
    if (!feed) return;
    var lv = level(), dot = $("dot"), text = $("status-text"), banner = $("banner");
    dot.className = "dot " + (lv === "LIVE" || lv === "LITE" ? "on" : lv === "HEADLINES" ? "warn" : "off");
    var secs = Math.max(0, Math.round((Date.now() - new Date(feed.generated).getTime()) / 1000));
    text.textContent = lv === "FROZEN" ? "Paused since " + clock(feed.generated) : "Live · updated " + (secs < 60 ? secs + " s" : Math.round(secs / 60) + " min") + " ago";
    var msg = "";
    if (lv === "FROZEN") msg = "Updates are paused since " + clock(feed.generated) + " ET. The stories below were current then.";
    else if (lv === "HEADLINES") msg = "Summaries are paused for a few minutes. New stories appear as headline, source and link.";
    banner.hidden = !msg;
    banner.className = "banner wrap" + (lv === "FROZEN" ? " warn" : "");
    banner.textContent = msg;
  }

  // One source at a time, a few seconds each, with a gentle fade (no fade for readers who ask for less motion).
  var quiet = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  function sweepText(w) {
    var line = $("sweep");
    line.textContent = "";
    if (!w) return;
    line.appendChild(document.createTextNode("Checking the wire · "));
    line.appendChild(el("b", null, w.source));
    line.appendChild(document.createTextNode(" · " + clock(w.at)));
  }
  function stepSweep() {
    var wire = (state.feed && level() !== "FROZEN" && state.feed.wire) || [];
    var w = wire.length ? wire[state.sweepAt++ % wire.length] : null;
    var line = $("sweep");
    if (quiet || !line.textContent) { sweepText(w); return; }
    line.classList.add("fade");
    setTimeout(function () { sweepText(w); line.classList.remove("fade"); }, 600);
  }

  function atTop() { return window.scrollY < 140; }

  function render(force) {
    var feed = state.feed;
    if (!feed) return;
    if (!force && state.shown && !atTop()) {
      var known = byId(state.shown), count = 0;
      feed.stories.forEach(function (s) { if (!known[s.id] && !state.hidden[s.desk]) count++; });
      state.pending = count;
      $("new-count").textContent = count === 1 ? "1 new story" : count + " new stories";
      $("new-pill").hidden = count === 0;
      renderTicker(feed);
      renderMarket(feed);
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
    if (feed.site.tagline) $("tagline").textContent = feed.site.tagline;
    document.title = feed.site.name + " · " + feed.site.label;
    renderLead(feed);
    renderTicker(feed);
    renderMarket(feed);
    renderFilters(feed);
    renderDocket(feed);
    renderFeed(feed, fresh);
    renderStatus();
  }

  function poll() {
    fetch("feed.json?t=" + Date.now(), { cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (feed) {
        var first = !state.feed, changed = first || feed.generated !== state.feed.generated;
        state.feed = feed;
        if (changed) render(false); else renderStatus();
        if (first) stepSweep();
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
  document.addEventListener("visibilitychange", function () { if (!document.hidden) poll(); });
  setInterval(renderStatus, 5000);
  setInterval(stepSweep, SWEEP_MS);

  (store("live-hidden") || "").split(",").forEach(function (k) { if (k) state.hidden[k] = true; });
  poll();
})();
