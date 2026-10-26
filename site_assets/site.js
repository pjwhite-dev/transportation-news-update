(function () {
  /* ---- Copy for email (unchanged behavior) ---- */
  var button = document.querySelector('[data-copy-edition]');
  var content = document.querySelector('[data-edition-content]');
  var status = document.querySelector('[data-copy-status]');
  if (button && content) {
    button.addEventListener('click', async function () {
      var rich = content.innerHTML;
      var encoded = content.dataset.outlookEmailB64;
      if (encoded) {
        var bytes = Uint8Array.from(atob(encoded), function (character) {
          return character.charCodeAt(0);
        });
        rich = new TextDecoder('utf-8').decode(bytes);
      }
      var plain = content.innerText.trim();
      try {
        if (window.ClipboardItem && navigator.clipboard && navigator.clipboard.write) {
          await navigator.clipboard.write([new ClipboardItem({
            'text/html': new Blob([rich], {type: 'text/html'}),
            'text/plain': new Blob([plain], {type: 'text/plain'})
          })]);
        } else {
          await navigator.clipboard.writeText(plain);
        }
        status.textContent = 'Copied.';
        setTimeout(function () { status.textContent = ''; }, 2500);
      } catch (error) {
        status.textContent = 'Copy was blocked. Select the edition and copy it manually.';
      }
    });
  }

  /* ---- Phone section menu: close after choosing a section ---- */
  var menu = document.querySelector('.menu');
  if (menu) {
    menu.addEventListener('click', function (event) {
      if (event.target.closest && event.target.closest('.menu-grid a')) menu.open = false;
    });
  }

  /* ---- Archive search: headlines, sources, sections and dates only (never article text) ---- */
  var root = document.querySelector('[data-search]');
  if (!root) return;
  var input = root.querySelector('input[type=search]');
  var sectionSelect = root.querySelector('select[data-section]');
  var yearSelect = root.querySelector('select[data-year]');
  var list = document.querySelector('[data-results]');
  var statusEl = document.querySelector('[data-search-status]');
  var moreBox = document.querySelector('[data-more]');
  var PAGE = 40;
  var index = [];
  var shown = PAGE;
  var current = [];

  function esc(text) {
    return String(text).replace(/[&<>"']/g, function (c) {
      return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
    });
  }
  function words(text) {
    return String(text).toLowerCase().split(/[^a-z0-9$%.]+/).filter(Boolean);
  }
  function longDate(iso) {
    var parts = iso.split('-');
    var d = new Date(Date.UTC(+parts[0], +parts[1] - 1, +parts[2]));
    return d.toLocaleDateString('en-US', {month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC'});
  }
  function highlight(text, tokens) {
    var out = esc(text);
    tokens.forEach(function (token) {
      if (token.length < 2) return;
      var safe = token.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      out = out.replace(new RegExp('(^|[^A-Za-z0-9])(' + safe + '[A-Za-z0-9]*)', 'gi'), '$1<mark>$2</mark>');
    });
    return out;
  }
  function run() {
    var q = input.value.trim();
    var tokens = words(q);
    var section = sectionSelect.value;
    var year = yearSelect.value;
    var found = [];
    for (var i = 0; i < index.length; i++) {
      var row = index[i];
      if (section && row.s !== section) continue;
      if (year && row.d.slice(0, 4) !== year) continue;
      var score = 0;
      if (tokens.length) {
        var hay = row._w;
        var ok = true;
        for (var t = 0; t < tokens.length; t++) {
          var hit = false;
          for (var w = 0; w < hay.length; w++) {
            if (hay[w].indexOf(tokens[t]) === 0) { hit = true; score += (hay[w] === tokens[t]) ? 2 : 1; break; }
          }
          if (!hit) { ok = false; break; }
        }
        if (!ok) continue;
      }
      found.push({row: row, score: score});
    }
    found.sort(function (a, b) {
      return (b.score - a.score) || (a.row.d < b.row.d ? 1 : a.row.d > b.row.d ? -1 : 0);
    });
    current = found;
    shown = PAGE;
    draw(tokens, q);
    try {
      var url = new URL(window.location.href);
      if (q) url.searchParams.set('q', q); else url.searchParams.delete('q');
      history.replaceState(null, '', url);
    } catch (e) { /* history is optional */ }
  }
  function draw(tokens, q) {
    var slice = current.slice(0, shown);
    list.innerHTML = slice.map(function (item) {
      var r = item.row;
      return '<a class="hit" href="' + esc(r.h) + '"><b>' + highlight(r.t, tokens) + '</b><span>' +
        esc(longDate(r.d)) + ' &middot; ' + esc(r.s) + (r.o ? ' &middot; ' + esc(r.o) : '') + '</span></a>';
    }).join('');
    var total = current.length;
    statusEl.textContent = q || sectionSelect.value || yearSelect.value
      ? total + (total === 1 ? ' headline' : ' headlines') + ' found'
      : 'Search ' + index.length.toLocaleString('en-US') + ' headlines across the archive.';
    moreBox.hidden = total <= shown;
  }

  moreBox.querySelector('button').addEventListener('click', function () {
    shown += PAGE;
    draw(words(input.value), input.value.trim());
  });
  var timer = null;
  input.addEventListener('input', function () { clearTimeout(timer); timer = setTimeout(run, 120); });
  sectionSelect.addEventListener('change', run);
  yearSelect.addEventListener('change', run);
  root.addEventListener('submit', function (event) { event.preventDefault(); run(); });

  fetch(root.dataset.index).then(function (response) { return response.json(); }).then(function (rows) {
    var sections = {}, years = {};
    index = rows;
    index.forEach(function (row) {
      row._w = words(row.t + ' ' + (row.o || '') + ' ' + row.s);
      sections[row.s] = true;
      years[row.d.slice(0, 4)] = true;
    });
    Object.keys(sections).sort().forEach(function (name) {
      sectionSelect.insertAdjacentHTML('beforeend', '<option>' + esc(name) + '</option>');
    });
    Object.keys(years).sort().reverse().forEach(function (name) {
      yearSelect.insertAdjacentHTML('beforeend', '<option>' + esc(name) + '</option>');
    });
    var params = new URLSearchParams(window.location.search);
    if (params.get('q')) input.value = params.get('q');
    run();
  }).catch(function () {
    statusEl.textContent = 'Search is unavailable right now. Browse the archive by date instead.';
  });
})();
