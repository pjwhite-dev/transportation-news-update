(function () {
  const sectionOrder = [
    'Trump Administration Wins',
    'Top Developments',
    'UAS and Drones',
    'UAS Security and C-UAS',
    'Military',
    'eVTOL Integration Pilot Program and AAM',
    'Autonomous Vehicles',
    'Other Advanced Transportation',
    'International',
    'Federal Actions'
  ];

  const loginView = document.getElementById('login-view');
  const editorView = document.getElementById('editor-view');
  const loginForm = document.getElementById('login-form');
  const editorForm = document.getElementById('editor-form');
  const loginStatus = document.getElementById('login-status');
  const saveStatus = document.getElementById('save-status');
  const logout = document.getElementById('logout');
  const sectionsRoot = document.getElementById('sections');
  const template = document.getElementById('story-template');
  let briefing = null;
  let storyBindings = [];

  async function api(action, options = {}) {
    const response = await fetch(`/api?action=${encodeURIComponent(action)}`, {
      credentials: 'same-origin',
      headers: {'Content-Type': 'application/json'},
      ...options
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || 'The request could not be completed.');
    return payload;
  }

  function setStatus(element, message, error = false) {
    element.textContent = message;
    element.classList.toggle('error', error);
  }

  function setBusy(busy) {
    document.querySelectorAll('button').forEach(button => { button.disabled = busy; });
  }

  function showLogin() {
    loginView.hidden = false;
    editorView.hidden = true;
    logout.hidden = true;
    document.getElementById('password').focus();
  }

  function showEditor() {
    loginView.hidden = true;
    editorView.hidden = false;
    logout.hidden = false;
  }

  function bindStory(section, story, card) {
    const include = card.querySelector('.include');
    const headline = card.querySelector('.headline');
    const summary = card.querySelector('.summary');
    const innovation = card.querySelector('.innovation');
    const winExplanation = card.querySelector('.win-explanation');
    const source = card.querySelector('.source');
    const articleLink = card.querySelector('.article-link');
    const identity = story.id || story.url || `${section}:${story.title}`;

    headline.value = story.title || '';
    summary.value = story.summary || '';
    innovation.value = story.innovative_uas_use || '';
    winExplanation.value = story.win_explanation || '';
    source.textContent = [story.source, story.date_label].filter(Boolean).join(' · ');
    articleLink.href = story.url || '#';
    card.querySelector('.innovation-field').hidden = section !== 'UAS and Drones';
    card.querySelector('.win-field').hidden = section !== 'Trump Administration Wins';
    include.addEventListener('change', () => {
      card.classList.toggle('excluded', !include.checked);
    });
    storyBindings.push({section, story, identity, include, headline, summary, innovation, winExplanation});
  }

  function renderEdition() {
    storyBindings = [];
    sectionsRoot.replaceChildren();
    document.getElementById('executive-summary').value = briefing.executive_summary || '';
    document.getElementById('what-to-watch').value = (briefing.what_to_watch || []).join('\n');
    const rawDate = briefing.window_end || briefing.generated_at || '';
    const date = rawDate ? new Date(rawDate) : null;
    document.getElementById('edition-date').textContent = date && !Number.isNaN(date.valueOf())
      ? date.toLocaleDateString(undefined, {weekday: 'long', year: 'numeric', month: 'long', day: 'numeric'})
      : '';

    sectionOrder.forEach(section => {
      const items = briefing.sections?.[section] || [];
      if (!items.length) return;
      const wrapper = document.createElement('section');
      wrapper.className = 'section-editor';
      const heading = document.createElement('h2');
      heading.textContent = section;
      wrapper.appendChild(heading);
      items.forEach(story => {
        const card = template.content.firstElementChild.cloneNode(true);
        bindStory(section, story, card);
        wrapper.appendChild(card);
      });
      sectionsRoot.appendChild(wrapper);
    });
  }

  async function loadEdition() {
    setBusy(true);
    try {
      const payload = await api('edition');
      briefing = payload.briefing;
      renderEdition();
      showEditor();
    } catch (error) {
      showLogin();
      setStatus(loginStatus, error.message, true);
    } finally {
      setBusy(false);
    }
  }

  function editedBriefing() {
    const next = structuredClone(briefing);
    next.executive_summary = document.getElementById('executive-summary').value.trim();
    next.what_to_watch = document.getElementById('what-to-watch').value
      .split('\n').map(item => item.trim()).filter(Boolean);
    const excluded = new Set(storyBindings.filter(binding => !binding.include.checked).map(binding => binding.identity));
    next.sections = Object.fromEntries(sectionOrder.map(section => [section, []]));
    storyBindings.forEach(binding => {
      if (excluded.has(binding.identity)) return;
      const story = structuredClone(binding.story);
      story.title = binding.headline.value.trim();
      story.summary = binding.summary.value.trim();
      if (binding.section === 'UAS and Drones') story.innovative_uas_use = binding.innovation.value.trim();
      if (binding.section === 'Trump Administration Wins') story.win_explanation = binding.winExplanation.value.trim();
      next.sections[binding.section].push(story);
    });
    return next;
  }

  async function save() {
    setStatus(saveStatus, 'Saving…');
    setBusy(true);
    try {
      const payload = await api('save', {
        method: 'POST',
        body: JSON.stringify({briefing: editedBriefing()})
      });
      setStatus(saveStatus, payload.message || 'Saved.');
      briefing = editedBriefing();
    } catch (error) {
      setStatus(saveStatus, error.message, true);
    } finally {
      setBusy(false);
    }
  }

  loginForm.addEventListener('submit', async event => {
    event.preventDefault();
    setStatus(loginStatus, 'Signing in…');
    setBusy(true);
    try {
      await api('login', {
        method: 'POST',
        body: JSON.stringify({password: document.getElementById('password').value})
      });
      document.getElementById('password').value = '';
      setStatus(loginStatus, '');
      await loadEdition();
    } catch (error) {
      setStatus(loginStatus, error.message, true);
      setBusy(false);
    }
  });

  editorForm.addEventListener('submit', event => { event.preventDefault(); save(); });
  document.getElementById('save-top').addEventListener('click', save);
  logout.addEventListener('click', async () => {
    setBusy(true);
    try { await api('logout', {method: 'POST', body: '{}'}); } catch (_) { /* The local session is still cleared by returning to login. */ }
    briefing = null;
    storyBindings = [];
    setBusy(false);
    showLogin();
  });

  api('session').then(result => result.authenticated ? loadEdition() : showLogin()).catch(showLogin);
})();
