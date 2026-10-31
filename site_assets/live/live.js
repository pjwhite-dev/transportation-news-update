/* Live page: poll feed.json, render the tickers, lead and one feed; never move the page under a reader. */
(function () {
  "use strict";
  var TZ = "America/New_York";
  // On the public site the feed comes from the live-feed branch on GitHub (pushed every few minutes, cached
  // up to 5 minutes by GitHub); the local preview reads the file the service writes next to the page.
  var feedMeta = document.querySelector('meta[name="live-feed"]');
  var PUBLIC = !/^(localhost|127\.0\.0\.1|\[::1\])$/.test(location.hostname) && !!(feedMeta && feedMeta.content);
  var FEED_URL = PUBLIC ? feedMeta.content : "feed.json";
  var STALE_MS = (PUBLIC ? 25 : 10) * 60 * 1000; // no new feed for this long: tell the reader updates are paused
  var PAGE = 25; // stories shown at first, and added by each "Show more"
  var SWEEP_MS = 1400; // the "Checking the wire:" line steps through the sources, this long each
  var state = { feed: null, shown: null, seen: {}, hidden: {}, pending: 0, timer: null, tickerKey: "", marketKey: "", sweepAt: 0, limit: 25,
    view: "us", target: "", targetDone: false, openAlso: {} };

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
  var hourFmt = new Intl.DateTimeFormat("en-US", { timeZone: TZ, hour: "numeric", hourCycle: "h23" });
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
    var b = null;
    if (story.check === "checked") {
      b = el("span", "badge checked", "\u2713\u2713 Double-checked");
      b.title = "Our code matched every name, number and date in this summary to the article, and a second AI model confirmed each sentence. Not a check of the reporting itself.";
    } else if (story.check === "quick") {
      b = el("span", "badge quick", "\u2713");
      b.title = "Our code matched every name, number and date in this summary to the article. Not a check of the reporting itself.";
      b.setAttribute("aria-label", "Summary names, numbers and dates matched to the article");
      b.setAttribute("role", "img");
    }
    return b;
  }

  // AI-estimated impact, shown as a word; a story not scored yet has none.
  var IMPACT = {
    significant: ["Significant", "Major government action, a first-of-its-kind operation, or widely reported"],
    notable: ["Notable", "Worth knowing: real news with a narrower reach"],
    minor: ["Minor", "Incremental news: updates, small contracts, local items"]
  };
  function impactBadge(story) {
    var t = IMPACT[story.impact];
    if (!t) return null;
    var b = el("span", "impact " + story.impact, t[0]);
    b.title = "AI estimate of impact: " + t[1] + ". Not a judgment of the reporting.";
    return b;
  }

  function storyNode(story, isNew) {
    var art = el("article", "story" + (isNew ? " arrive" : "") + (story.id === state.target ? " target" : ""));
    art.id = "s-" + story.id;
    art.dataset.id = story.id;
    art.dataset.cat = story.desk;
    var side = el("div", "cat");
    side.appendChild(el("b", null, catName(story.desk)));
    var t = el("time", null, storyTime(story));
    t.dateTime = story.published;
    side.appendChild(t);
    var imp = impactBadge(story);
    if (imp) side.appendChild(imp);
    art.appendChild(side);
    var body = el("div");
    var meta = el("div", "meta");
    meta.appendChild(el("span", "who", story.source));
    var b = badge(story);
    if (b) meta.appendChild(b);
    if (story.official) meta.appendChild(el("span", "badge official", "Official source"));
    if (story.scope === "world") meta.appendChild(el("span", "badge world", "World"));
    meta.appendChild(copyButton(story));
    body.appendChild(meta);
    var h = el("h3");
    h.appendChild(link(story.url, story.title));
    body.appendChild(h);
    if (story.summary) body.appendChild(el("p", null, story.summary));
    if (story.also && story.also.length) body.appendChild(alsoLine(story));
    if (story.correction) body.appendChild(el("div", "fix", story.correction));
    art.appendChild(body);
    return art;
  }

  // The other outlets on the same event: three by name, the rest one click away.
  function alsoLine(story) {
    var all = story.also, open = state.openAlso[story.id], box = el("div", "also");
    box.appendChild(el("b", null, all.length === 1 ? "Also reported by " : "Also reported by " + all.length + " outlets: "));
    (open ? all : all.slice(0, 3)).forEach(function (a, i) {
      if (i) box.appendChild(document.createTextNode(", "));
      var l = link(a.url, a.source);
      if (a.title) l.title = a.title;
      box.appendChild(l);
    });
    if (!open && all.length > 3) {
      var more = el("button", "also-more", "+" + (all.length - 3) + " more");
      more.type = "button";
      more.addEventListener("click", function () { state.openAlso[story.id] = true; box.replaceWith(alsoLine(story)); });
      box.appendChild(document.createTextNode(" "));
      box.appendChild(more);
    }
    return box;
  }

  // Every story has its own address (#s-<id>); a link to any outlet's report of an event finds the event's entry.
  function storyLink(story) { return location.origin + location.pathname + "#s-" + story.id; }
  function copyButton(story) {
    var b = el("button", "copy", "Copy link");
    b.type = "button";
    b.setAttribute("aria-label", "Copy a link to this story");
    b.addEventListener("click", function () {
      var url = storyLink(story);
      var done = function () { b.textContent = "Copied"; setTimeout(function () { b.textContent = "Copy link"; }, 1600); };
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(url).then(done, function () { prompt("Copy this link:", url); });
      else prompt("Copy this link:", url);
      history.replaceState(null, "", location.pathname + location.search + "#s-" + story.id);
    });
    return b;
  }
  function findStory(feed, id) {
    for (var i = 0; i < feed.stories.length; i++) {
      var s = feed.stories[i];
      if (s.id === id || (s.members && s.members.indexOf(id) >= 0)) return s;
    }
    return null;
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
    // Big only while it is news: its first hour, a Significant story, or the owner's pick. Otherwise a compact card,
    // so the newest stories are on the first screen.
    var big = lead && ((lead.held_until && new Date(lead.held_until) > Date.now()) || lead.impact === "significant"
      || Date.now() - new Date(lead.since).getTime() < 3600e3);
    main.parentNode.classList.toggle("compact", !!lead && !big);
    if (!lead) {
      main.appendChild(el("p", "lead-empty", "No story so far has been significant enough to lead the page. The newest stories are below, as they come in."));
    } else {
      var eye = el("p", "eyebrow");
      eye.appendChild(el("span", "tag", "Lead"));
      if (catName(lead.desk)) eye.appendChild(el("span", null, catName(lead.desk)));
      var day = dayLabel(lead.since);
      eye.appendChild(el("span", "since", "since " + (day === "Today" ? "" : day === "Yesterday" ? "yesterday, " : day + ", ") + clock(lead.since)));
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
    var items = (feed.developing || []).map(function (id) { return map[id]; }).filter(function (s) { return s && inView(s); });
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

  // "From official sources": cards that step along every few seconds, paused while the reader is using them.
  function ago(iso) {
    var mins = Math.max(1, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
    if (mins < 60) return mins + " min ago";
    var hrs = Math.round(mins / 60);
    return hrs < 24 ? hrs + " h ago" : Math.round(hrs / 24) + " d ago";
  }
  function renderPosts(feed) {
    var posts = feed.posts || [], box = $("official"), track = $("official-track");
    box.hidden = posts.length < 3;
    var key = posts.map(function (p) { return p.url; }).join("|");
    if (key === state.postsKey) return;
    state.postsKey = key;
    track.textContent = "";
    posts.forEach(function (p) {
      var card = el("article", "official-card");
      var head = el("div", "who");
      head.appendChild(el("b", null, p.org));
      head.appendChild(el("span", null, p.kind + " \u00b7 " + ago(p.published)));
      card.appendChild(head);
      card.appendChild(link(p.url, p.title, "official-title"));
      track.appendChild(card);
    });
  }
  function stepPosts(dir) {
    var track = $("official-track"), card = track.firstElementChild;
    if (!card) return;
    var step = card.getBoundingClientRect().width + 12;
    var end = track.scrollLeft + track.clientWidth >= track.scrollWidth - 4;
    if (dir > 0 && end) track.scrollTo({ left: 0, behavior: quiet() ? "auto" : "smooth" });
    else track.scrollBy({ left: dir * step, behavior: quiet() ? "auto" : "smooth" });
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

  function shownDesks() { return ((state.feed && state.feed.desks) || []).filter(function (d) { return !state.hidden[d.key]; }).map(function (d) { return d.key; }); }
  function saveHidden() {
    store("live-hidden", Object.keys(state.hidden).filter(function (k) { return state.hidden[k]; }).join(","));
    store("live-view", state.view);
    // The address says what is shown (?desk=av,rail&view=all), so a filtered page can be shared.
    var q = [], all = (state.feed && state.feed.desks) || [], on = shownDesks();
    if (all.length && on.length < all.length) q.push("desk=" + on.join(","));
    if (state.view === "all") q.push("view=all");
    history.replaceState(null, "", location.pathname + (q.length ? "?" + q.join("&") : "") + location.hash);
  }
  function inView(s) { return state.view === "all" || s.scope !== "world" || s.id === state.target; }

  function renderFilters(feed) {
    var box = $("filters");
    box.textContent = "";
    var scope = el("div", "scope");
    scope.setAttribute("role", "group");
    scope.setAttribute("aria-label", "Which stories");
    [["us", "U.S. focus", "Stories about U.S. government, companies and places"], ["all", "Everything", "Also stories from abroad with no U.S. angle"]].forEach(function (v) {
      var b = el("button", null, v[1]);
      b.type = "button";
      b.title = v[2];
      b.setAttribute("aria-pressed", String(state.view === v[0]));
      b.addEventListener("click", function () { state.view = v[0]; saveHidden(); render(true); });
      scope.appendChild(b);
    });
    box.appendChild(scope);
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

  function renderFeed(feed, fresh) {
    var box = $("feed");
    box.textContent = "";
    var list = feed.stories.filter(function (s) { return (!state.hidden[s.desk] || s.id === state.target) && inView(s); });
    var away = feed.stories.filter(function (s) { return !state.hidden[s.desk] && !inView(s); }).length;
    if (state.target && !state.targetDone) {
      var at = list.findIndex(function (s) { return s.id === state.target; });
      if (at >= state.limit) state.limit = Math.ceil((at + 1) / PAGE) * PAGE;
    }
    if (!list.length) {
      box.appendChild(el("p", "empty", feed.stories.length ? "Every category is switched off. Turn one back on above." : "No stories in the last three days."));
      return;
    }
    var lastDay = "";
    list.slice(0, state.limit).forEach(function (s) {
      var d = dayLabel(s.published);
      if (d !== lastDay) { box.appendChild(el("h2", "day", d)); lastDay = d; }
      box.appendChild(storyNode(s, fresh[s.id]));
    });
    var left = list.length - state.limit;
    if (left > 0) {
      var more = el("button", "more", "Show " + Math.min(PAGE, left) + " more stories");
      more.type = "button";
      more.addEventListener("click", function () { state.limit += PAGE; renderFeed(state.shown, {}); });
      box.appendChild(more);
    }
    if (away) {
      var world = el("button", "more world-more", away + (away === 1 ? " story from abroad is" : " stories from abroad are") + " hidden. Show everything");
      world.type = "button";
      world.addEventListener("click", function () { state.view = "all"; saveHidden(); render(true); });
      box.appendChild(world);
    }
  }

  // Overnight (feed.overnight, Eastern hours) the public copy goes out once an hour, so a feed made then is not
  // late until the next hour's has had time to arrive.
  function overnight(feed) {
    var span = feed.overnight;
    if (!PUBLIC || !span) return false;
    var hour = parseInt(hourFmt.format(new Date(feed.generated)), 10) % 24;
    return hour >= span.from_hour && hour < span.to_hour;
  }
  function hourName(h) { return (h % 12 || 12) + (h < 12 ? " a.m." : " p.m."); }

  function level() {
    var feed = state.feed;
    var stale = overnight(feed) ? 75 * 60 * 1000 : STALE_MS;
    return Date.now() - new Date(feed.generated).getTime() > stale ? "FROZEN" : feed.level;
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
    else if (overnight(feed)) msg = "Overnight this page updates once an hour, until " + hourName(feed.overnight.to_hour) + " ET.";
    banner.hidden = !msg;
    banner.className = "banner wrap" + (lv === "FROZEN" ? " warn" : "");
    banner.textContent = msg;
  }

  // The label stays put; only the source changes, quickly, with a short fade (no fade for readers who ask for less motion).
  var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  function quiet() { return reduced || document.documentElement.classList.contains("still"); }
  function setStill(on) {
    document.documentElement.classList.toggle("still", on);
    var b = $("motion");
    b.setAttribute("aria-pressed", String(on));
    b.textContent = on ? "Play" : "Pause";
    b.title = on ? "Start the moving headlines again" : "Stop the moving headlines and animations";
    store("live-still", on ? "1" : "");
  }
  function sweepText(w) {
    var line = $("sweep");
    line.textContent = "";
    if (w) line.appendChild(el("b", null, w.source));
  }
  function stepSweep() {
    var wire = (state.feed && level() !== "FROZEN" && state.feed.wire) || [];
    var w = wire.length ? wire[state.sweepAt++ % wire.length] : null;
    var line = $("sweep");
    if (quiet() && line.textContent) return;
    if (quiet() || !line.textContent) { sweepText(w); return; }
    line.classList.add("fade");
    setTimeout(function () { sweepText(w); line.classList.remove("fade"); }, 150);
  }

  function atTop() { return window.scrollY < 140; }

  function render(force) {
    var feed = state.feed;
    if (!feed) return;
    if (!force && state.shown && !atTop()) {
      var known = byId(state.shown), count = 0;
      feed.stories.forEach(function (s) { if (!known[s.id] && !state.hidden[s.desk] && inView(s)) count++; });
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
    $("foot-name").textContent = feed.site.name + " " + feed.site.label;
    $("site-label").textContent = feed.site.label;
    document.title = feed.site.name + " " + feed.site.label;
    renderLead(feed);
    renderTicker(feed);
    renderMarket(feed);
    renderPosts(feed);
    renderFilters(feed);
    renderFeed(feed, fresh);
    renderStatus();
    showTarget();
  }

  function readTarget() {
    var m = /^#s-([\w-]+)$/.exec(location.hash);
    state.target = m ? m[1] : "";
    state.targetDone = false;
  }
  function showTarget() {
    if (!state.target || state.targetDone || !state.shown) return;
    var story = findStory(state.shown, state.target);
    if (!story) {
      state.targetDone = true;
      var banner = $("banner");
      banner.hidden = false;
      banner.className = "banner wrap";
      banner.textContent = "That story has left the live page: stories stay for three days.";
      return;
    }
    if (story.id !== state.target) { state.target = story.id; renderFeed(state.shown, {}); }
    state.targetDone = true;
    var node = $("s-" + story.id);
    if (node) node.scrollIntoView({ block: "center", behavior: quiet() ? "auto" : "smooth" });
  }

  function poll() {
    fetch(FEED_URL + (PUBLIC ? "" : "?t=" + Date.now()), { cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (feed) {
        var first = !state.feed, changed = first || feed.generated !== state.feed.generated;
        state.feed = feed;
        if (state.onlyDesks) {
          feed.desks.forEach(function (d) { state.hidden[d.key] = state.onlyDesks.indexOf(d.key) < 0; });
          state.onlyDesks = null;
        }
        if (changed) render(false); else renderStatus();
        if (first) stepSweep();
        schedule(feed.site.poll_seconds);
      })
      .catch(function () { renderStatus(); schedule(30); });
  }
  function schedule(seconds) {
    clearTimeout(state.timer);
    state.timer = setTimeout(poll, Math.max(PUBLIC ? 60 : 5, seconds || 15) * 1000);
  }

  $("new-pill").addEventListener("click", function () {
    window.scrollTo({ top: 0, behavior: "smooth" });
    render(true);
  });
  window.addEventListener("scroll", function () { if (atTop() && state.pending) render(true); }, { passive: true });
  document.addEventListener("visibilitychange", function () { if (!document.hidden) poll(); });
  setInterval(renderStatus, 5000);
  setInterval(stepSweep, SWEEP_MS);
  $("official-prev").addEventListener("click", function () { stepPosts(-1); });
  $("official-next").addEventListener("click", function () { stepPosts(1); });
  ["mouseenter", "focusin", "touchstart"].forEach(function (ev) { $("official").addEventListener(ev, function () { state.postsHeld = true; }, { passive: true }); });
  ["mouseleave", "focusout"].forEach(function (ev) { $("official").addEventListener(ev, function () { state.postsHeld = false; }); });
  setInterval(function () { if (!quiet() && !state.postsHeld && !document.hidden) stepPosts(1); }, 7000);
  $("motion").addEventListener("click", function () { setStill(!document.documentElement.classList.contains("still")); });
  setStill(!!store("live-still"));
  window.addEventListener("hashchange", function () { readTarget(); if (state.shown) { renderFeed(state.shown, {}); showTarget(); } });

  // The address wins over what this browser remembers: ?desk=av,rail shows only those desks, ?view=all everything.
  var params = new URLSearchParams(location.search), desks = params.get("desk");
  state.view = params.get("view") === "all" ? "all" : params.has("desk") ? "us" : (store("live-view") === "all" ? "all" : "us");
  if (desks) state.onlyDesks = desks.split(",");
  else (store("live-hidden") || "").split(",").forEach(function (k) { if (k) state.hidden[k] = true; });
  readTarget();
  poll();
})();
