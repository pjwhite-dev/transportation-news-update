/* Past days: the live page's daily archive. ?d=2026-10-30 shows one day; ?q= searches every day, in the browser. */
(function () {
  "use strict";
  var TZ = "America/New_York";
  // The day files sit beside feed.json on the live-feed branch (live_days.py); the local preview has them here.
  var feedMeta = document.querySelector('meta[name="live-feed"]');
  var PUBLIC = !/^(localhost|127\.0\.0\.1|\[::1\])$/.test(location.hostname) && !!(feedMeta && feedMeta.content);
  var BASE = PUBLIC ? feedMeta.content.replace(/feed\.json$/, "days/") : "./";
  var PAGE = 40;
  var state = { index: null, day: null, files: {}, q: "", limit: PAGE, view: "us" };

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
  function getJson(name) {
    return fetch(BASE + name + (PUBLIC ? "" : "?t=" + Date.now()), { cache: "no-cache" })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); });
  }

  var timeFmt = new Intl.DateTimeFormat("en-US", { timeZone: TZ, hour: "numeric", minute: "2-digit" });
  var fullFmt = new Intl.DateTimeFormat("en-US", { timeZone: TZ, weekday: "short", month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" });
  // A day key is a calendar date; noon UTC keeps it on that date in every time zone it is formatted in.
  var dayLong = new Intl.DateTimeFormat("en-US", { timeZone: "UTC", weekday: "long", month: "long", day: "numeric", year: "numeric" });
  var dayShort = new Intl.DateTimeFormat("en-US", { timeZone: "UTC", weekday: "short", month: "short", day: "numeric" });
  function ampm(text) { return text.replace("AM", "a.m.").replace("PM", "p.m."); }
  function dayDate(key) { return new Date(key + "T12:00:00Z"); }
  function timeNode(story) {
    var node = el("time");
    node.dateTime = story.published;
    if (story.time_known === false) {
      node.textContent = "No time given";
      node.title = "The source gives only the date." + (story.found ? " We first saw it " + ampm(fullFmt.format(new Date(story.found))) + " ET." : "");
    } else {
      node.textContent = ampm(timeFmt.format(new Date(story.published)));
      node.title = ampm(fullFmt.format(new Date(story.published))) + " ET";
    }
    return node;
  }

  // Search: every word must appear in the headline, summary, outlets or category (case and accents ignored).
  function fold(text) { return String(text || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase(); }
  function words(q) { return fold(q).split(/\s+/).filter(Boolean); }
  function matches(s, ws, desks) {
    var hay = fold([s.title, s.summary, s.source, desks[s.desk] || ""].concat((s.also || []).map(function (a) { return a.source + " " + (a.title || ""); })).join(" "));
    return ws.every(function (w) { return hay.indexOf(w) >= 0; });
  }
  function marked(text, ws) {
    var frag = document.createDocumentFragment();
    if (!ws.length) { frag.appendChild(document.createTextNode(text)); return frag; }
    var re = new RegExp("(" + ws.map(function (w) { return w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }).join("|") + ")", "gi");
    text.split(re).forEach(function (part, i) { if (part) frag.appendChild(i % 2 ? el("mark", null, part) : document.createTextNode(part)); });
    return frag;
  }
  function deskNames(file) {
    var names = {};
    (file.desks || []).forEach(function (d) { names[d.key] = d.name; });
    return names;
  }

  function badge(story) {
    var b = null;
    if (story.check === "checked") {
      b = el("span", "badge checked", "✓✓ Double-checked");
      b.title = "Our code matched every name, number and date in this summary to the article, and a second AI model confirmed each sentence. Not a check of the reporting itself.";
    } else if (story.check === "quick") {
      b = el("span", "badge quick", "✓");
      b.title = "Our code matched every name, number and date in this summary to the article. Not a check of the reporting itself.";
      b.setAttribute("aria-label", "Summary names, numbers and dates matched to the article");
      b.setAttribute("role", "img");
    }
    return b;
  }

  function storyNode(story, desks, ws) {
    var art = el("article", "story");
    art.id = "s-" + story.id;
    art.dataset.cat = story.desk;
    var side = el("div", "cat");
    side.appendChild(el("b", null, desks[story.desk] || ""));
    side.appendChild(timeNode(story));
    art.appendChild(side);
    var body = el("div"), meta = el("div", "meta");
    meta.appendChild(el("span", "who", story.source));
    var b = badge(story);
    if (b) meta.appendChild(b);
    if (story.official) meta.appendChild(el("span", "badge official", "Official source"));
    if (story.scope === "world") meta.appendChild(el("span", "badge world", "World"));
    body.appendChild(meta);
    var h = el("h3"), a = link(story.url, null);
    a.appendChild(marked(story.title, ws));
    h.appendChild(a);
    body.appendChild(h);
    if (story.summary) { var p = el("p"); p.appendChild(marked(story.summary, ws)); body.appendChild(p); }
    if (story.also && story.also.length) {
      var also = el("div", "also");
      also.appendChild(el("b", null, story.also.length === 1 ? "Also reported by " : "Also reported by " + story.also.length + " outlets: "));
      story.also.forEach(function (o, i) {
        if (i) also.appendChild(document.createTextNode(", "));
        var l = link(o.url, o.source);
        if (o.title) l.title = o.title;
        also.appendChild(l);
      });
      body.appendChild(also);
    }
    if (story.correction) body.appendChild(el("div", "fix", story.correction));
    art.appendChild(body);
    return art;
  }

  function dayHref(key) { return "?d=" + key + (state.q.trim() ? "&q=" + encodeURIComponent(state.q.trim()) : ""); }
  function saveAddress() {
    var parts = [];
    if (state.day) parts.push("d=" + state.day);
    if (state.view === "all") parts.push("view=all");
    if (state.q.trim()) parts.push("q=" + encodeURIComponent(state.q.trim()));
    history.replaceState(null, "", location.pathname + (parts.length ? "?" + parts.join("&") : "") + location.hash);
  }

  function renderNav() {
    var nav = $("day-nav"), days = (state.index && state.index.days) || [];
    nav.textContent = "";
    if (!state.day) return;
    var at = days.findIndex(function (d) { return d.day === state.day; });
    var later = at > 0 ? days[at - 1] : null, earlier = at >= 0 && at < days.length - 1 ? days[at + 1] : null;
    if (earlier) { var e = el("a", null, "← " + dayShort.format(dayDate(earlier.day))); e.href = dayHref(earlier.day); nav.appendChild(e); }
    var all = el("a", null, "All days"); all.href = "./" + (state.q.trim() ? "?q=" + encodeURIComponent(state.q.trim()) : ""); nav.appendChild(all);
    if (later) { var l = el("a", null, dayShort.format(dayDate(later.day)) + " →"); l.href = dayHref(later.day); nav.appendChild(l); }
  }

  function moreButton(box, left, again) {
    if (left <= 0) return;
    var more = el("button", "more", "Show " + Math.min(PAGE, left) + " more stories");
    more.type = "button";
    more.addEventListener("click", function () { state.limit += PAGE; again(); });
    box.appendChild(more);
  }

  // U.S. focus or Everything, as on the live page (the same saved choice); a search looks through everything.
  function renderScope() {
    var box = $("scope");
    box.textContent = "";
    box.hidden = !state.day;
    [["us", "U.S. focus", "Stories about U.S. government, companies and places"], ["all", "Everything", "Also stories from abroad with no U.S. angle"]].forEach(function (v) {
      var b = el("button", null, v[1]);
      b.type = "button";
      b.title = v[2];
      b.setAttribute("aria-pressed", String(state.view === v[0]));
      b.addEventListener("click", function () { state.view = v[0]; store("live-view", state.view); saveAddress(); render(); });
      box.appendChild(b);
    });
  }

  // One day: its stories newest first; a search narrows them.
  function renderDay() {
    var file = state.files[state.day], box = $("feed"), ws = words(state.q), desks = deskNames(file);
    box.textContent = "";
    $("feed-head").classList.toggle("searching", ws.length > 0);
    var list = file.stories.filter(function (s) { return ws.length ? matches(s, ws, desks) : state.view === "all" || s.scope !== "world"; });
    var away = ws.length ? 0 : file.stories.length - list.length;
    $("feed-title").textContent = ws.length ? (list.length === 1 ? "1 story matches" : list.length + " stories match") : (list.length === 1 ? "1 story" : list.length + " stories");
    if (ws.length) {
      var found = el("p", "found");
      found.appendChild(document.createTextNode(list.length ? "" : "Nothing on this day matches “" + state.q.trim() + "”. "));
      var every = el("a", null, "Search every day");
      every.href = "./?q=" + encodeURIComponent(state.q.trim());
      found.appendChild(every);
      box.appendChild(found);
    }
    list.slice(0, state.limit).forEach(function (s) { box.appendChild(storyNode(s, desks, ws)); });
    moreButton(box, list.length - state.limit, renderDay);
    if (away) {
      var world = el("button", "more world-more", away + (away === 1 ? " story from abroad is" : " stories from abroad are") + " hidden. Show everything");
      world.type = "button";
      world.addEventListener("click", function () { state.view = "all"; store("live-view", "all"); saveAddress(); render(); });
      box.appendChild(world);
    }
    jumpToStory();
  }

  // No day chosen: the list of days, or, with a search, the matches from every day under each day's heading.
  function renderAll() {
    var box = $("feed"), ws = words(state.q), days = (state.index && state.index.days) || [];
    box.textContent = "";
    $("feed-head").classList.toggle("searching", ws.length > 0);
    if (!ws.length) {
      $("feed-title").textContent = days.length === 1 ? "1 day" : days.length + " days";
      if (!days.length) { box.appendChild(el("p", "empty", "No finished days yet. Today's stories are on the live page.")); return; }
      var ul = el("ul", "day-list");
      days.forEach(function (d) {
        var li = el("li"), a = el("a");
        a.href = "?d=" + d.day;
        a.appendChild(el("b", null, dayLong.format(dayDate(d.day))));
        a.appendChild(el("span", null, d.stories === 1 ? "1 story" : d.stories + " stories"));
        li.appendChild(a);
        ul.appendChild(li);
      });
      box.appendChild(ul);
      return;
    }
    var missing = days.filter(function (d) { return !state.files[d.day]; });
    if (missing.length) {
      $("feed-title").textContent = "Searching " + days.length + " days…";
      Promise.all(missing.map(function (d) { return getJson(d.day + ".json").then(function (f) { state.files[d.day] = f; }, function () { state.files[d.day] = { stories: [], desks: [] }; }); }))
        .then(function () { if (!state.day) renderAll(); });
      return;
    }
    var hits = [];
    days.forEach(function (d) {
      var file = state.files[d.day], desks = deskNames(file);
      file.stories.forEach(function (s) { if (matches(s, ws, desks)) hits.push({ day: d.day, story: s, desks: desks }); });
    });
    $("feed-title").textContent = (hits.length === 1 ? "1 story matches" : hits.length + " stories match") + " (" + (days.length === 1 ? "1 day" : days.length + " days") + " searched)";
    if (!hits.length) box.appendChild(el("p", "found", "Nothing in the past days matches “" + state.q.trim() + "”."));
    var lastDay = "";
    hits.slice(0, state.limit).forEach(function (h) {
      if (h.day !== lastDay) {
        var head = el("h2", "day"), a = el("a", null, dayLong.format(dayDate(h.day)));
        a.href = dayHref(h.day);
        head.appendChild(a);
        box.appendChild(head);
        lastDay = h.day;
      }
      box.appendChild(storyNode(h.story, h.desks, ws));
    });
    moreButton(box, hits.length - state.limit, renderAll);
  }

  function render() {
    renderNav();
    renderScope();
    if (state.day && state.files[state.day]) renderDay();
    else if (!state.day) renderAll();
  }

  function jumpToStory() {
    var m = /^#s-([\w-]+)$/.exec(location.hash), node = m && $("s-" + m[1]);
    if (node && !state.jumped) { state.jumped = true; node.classList.add("target"); node.scrollIntoView({ block: "center" }); }
  }

  function showDay(key) {
    $("title").textContent = dayLong.format(dayDate(key));
    $("intro").textContent = "The day's stories as they stood at the end of it, newest first. Times are Eastern.";
    $("q").placeholder = "Search this day";
    $("search").querySelector("label").textContent = "Search this day";
    document.title = dayLong.format(dayDate(key)) + " · Advanced Transportation Live";
    getJson(key + ".json").then(function (file) { state.files[key] = file; render(); }, function () {
      $("feed").textContent = "";
      $("feed").appendChild(el("p", "empty", "There is no page for that day. Pick one from the list of days."));
      $("feed-title").textContent = "";
    });
  }

  var timer = null;
  $("q").addEventListener("input", function () {
    clearTimeout(timer);
    timer = setTimeout(function () { state.q = $("q").value; state.limit = PAGE; saveAddress(); render(); }, 200);
  });
  $("q").addEventListener("keydown", function (e) {
    if (e.key === "Escape" && $("q").value) { $("q").value = ""; $("q").dispatchEvent(new Event("input")); }
  });
  $("search").addEventListener("submit", function (e) { e.preventDefault(); $("q").blur(); });
  document.addEventListener("keydown", function (e) {
    var tag = (document.activeElement || {}).tagName || "";
    if (e.key === "/" && !e.ctrlKey && !e.metaKey && !e.altKey && !/^(INPUT|TEXTAREA|SELECT)$/.test(tag)) { e.preventDefault(); $("q").focus(); }
  });

  var params = new URLSearchParams(location.search), d = params.get("d") || "";
  state.day = /^\d{4}-\d{2}-\d{2}$/.test(d) ? d : null;
  state.q = params.get("q") || "";
  state.view = params.get("view") === "all" || (!params.has("view") && store("live-view") === "all") ? "all" : "us";
  $("q").value = state.q;
  getJson("index.json").then(function (index) { state.index = index; render(); }, function () {
    state.index = { days: [] };
    if (!state.day) { $("feed").textContent = ""; $("feed").appendChild(el("p", "empty", "The list of days could not be loaded. Try again in a minute.")); }
  });
  if (state.day) showDay(state.day);
})();
