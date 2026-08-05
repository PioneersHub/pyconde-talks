// Bookmarks for visitors who are not signed in.
//
// A logged-out visitor has no SavedTalk row to toggle, so their bookmarks live in localStorage
// and are folded into the account on the next login. The server renders every button in the
// "not saved" state - it cannot know any better - and this corrects them after paint.
//
// Painting is therefore for anonymous visitors ONLY. For a signed-in user the server already
// renders the true state, and their localStorage is normally empty, so painting them from it
// would reset every "Saved" button to "Save". The one time a signed-in user's buttons need
// correcting is right after a merge, and then the authoritative list is the one the merge
// endpoint returns, not the local copy.
//
// Two things make that harder than it sounds. HTMX replaces button markup wholesale when a
// signed-in user toggles one, and it re-renders whole lists when filters change or a poll
// fires, so nothing may be bound to a button directly: the click handler is delegated from
// document.body, and state is re-applied on every htmx:load. And the translated labels and the
// unsaved-state color come from data attributes rather than being written here, so this file
// stays language-agnostic and does not need to know each variant's Tailwind classes.
(function() {
  'use strict';

  const KEY = 'savedTalks:v1';
  // Matches MAX_MERGE_IDS on the server. A cap keeps a runaway loop from filling storage and
  // then posting an enormous merge payload.
  const MAX_IDS = 500;

  const isAuthenticated = document.body.dataset.userAuthenticated === '1';

  function read() {
    try {
      const raw = localStorage.getItem(KEY);
      if (!raw) { return []; }
      const ids = JSON.parse(raw);
      if (!Array.isArray(ids)) { return []; }
      return ids.filter(function(id) { return Number.isInteger(id); }).slice(0, MAX_IDS);
    } catch (err) {
      // Corrupt value, or storage blocked in private mode. Degrade to "nothing saved" rather
      // than breaking the page.
      return [];
    }
  }

  function write(ids) {
    try {
      localStorage.setItem(KEY, JSON.stringify(ids.slice(0, MAX_IDS)));
    } catch (err) {
      // Quota exceeded or storage unavailable. The in-page state is still right for this view,
      // which is the best that can be done.
    }
  }

  function applyState(node, isSaved) {
    const solid = node.querySelector('[data-save-icon-solid]');
    const outline = node.querySelector('[data-save-icon-outline]');
    const label = node.querySelector('[data-save-label]');
    const button = node.matches('button') ? node : node.querySelector('button');

    if (solid) { solid.classList.toggle('hidden', !isSaved); }
    if (outline) { outline.classList.toggle('hidden', isSaved); }
    if (label) {
      label.textContent = isSaved
        ? label.dataset.labelSaved
        : label.dataset.labelUnsaved;
    }
    if (button) {
      button.classList.toggle('text-yellow-500', isSaved);
      // The schedule variant colors its unsaved state explicitly, and that class is mutually
      // exclusive with the saved color. Leaving it in place would put both on the element and
      // let Tailwind's emit order decide which wins. Variants without one name nothing here.
      const mutedClass = button.dataset.saveMutedClass;
      if (mutedClass) { button.classList.toggle(mutedClass, !isSaved); }
      button.title = isSaved ? button.dataset.titleSaved : button.dataset.titleUnsaved;
    }
    node.dataset.saved = isSaved ? '1' : '0';
  }

  function paintFrom(ids, root) {
    const scope = root?.querySelectorAll ? root : document;
    const nodes = scope.querySelectorAll('[data-save-talk]');
    for (let i = 0; i < nodes.length; i++) {
      applyState(nodes[i], ids.indexOf(Number.parseInt(nodes[i].dataset.saveTalk, 10)) !== -1);
    }
  }

  function paint(root) {
    paintFrom(read(), root);
  }

  function csrfToken() {
    // CSRF_COOKIE_HTTPONLY is on, so the cookie cannot be read here. The token comes from the
    // hidden form the base template renders, the same way the HTMX buttons get it.
    const input = document.querySelector('#csrf-form input[name="csrfmiddlewaretoken"]');
    return input ? input.value : '';
  }

  function mergeIntoAccount() {
    const ids = read();
    if (!ids.length) { return; }

    const url = document.body.dataset.mergeSavedUrl;
    if (!url) { return; }

    fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
      body: JSON.stringify({ ids: ids })
    }).then(function(response) {
      if (!response.ok) { return null; }
      // Clear only on success, so a failed merge is retried on the next page view instead of
      // silently losing the visitor's picks.
      localStorage.removeItem(KEY);
      const notice = document.getElementById('saved-merged-notice');
      if (notice) { notice.classList.remove('hidden'); }
      return response.json();
    }).then(function(data) {
      // Repaint from the account's full list, which the endpoint returns for exactly this
      // reason: the page was rendered before the merge, so the talks just folded in still
      // show as unsaved. Without this the visitor has to reload to see their own picks.
      if (data && Array.isArray(data.saved)) { paintFrom(data.saved); }
    }).catch(function() {
      // Offline, blocked, or a body that was not JSON. Keep the local copy and try again on
      // the next page view.
    });
  }

  function showLocalOnlyNotice() {
    const notice = document.getElementById('saved-local-notice');
    if (!notice || localStorage.getItem(KEY + ':noticeSeen')) { return; }
    notice.classList.remove('hidden');
  }

  document.body.addEventListener('click', function(evt) {
    const dismiss = evt.target.closest('[data-dismiss-saved-notice]');
    if (dismiss) {
      const notice = document.getElementById('saved-local-notice');
      if (notice) { notice.classList.add('hidden'); }
      try { localStorage.setItem(KEY + ':noticeSeen', '1'); } catch (err) { /* ignore */ }
      return;
    }

    // Only the signed-out variant carries this attribute; a signed-in user's button posts to
    // the server through HTMX instead, so this handler must not intercept it.
    const button = evt.target.closest('[data-save-toggle]');
    if (!button) { return; }
    evt.preventDefault();

    const id = Number.parseInt(button.dataset.saveToggle, 10);
    const ids = read();
    const at = ids.indexOf(id);
    if (at === -1) { ids.push(id); } else { ids.splice(at, 1); }
    write(ids);
    paint();
    if (at === -1) { showLocalOnlyNotice(); }
  });

  if (isAuthenticated) {
    mergeIntoAccount();
  } else {
    // HTMX fires this on every newly-inserted fragment, so a swapped-in talk list gets its
    // bookmark state back. Scoped to the inserted node to avoid re-walking the whole page on
    // each ten-second poll.
    document.body.addEventListener('htmx:load', function(evt) { paint(evt.detail.elt); });
    paint();
  }
})();
