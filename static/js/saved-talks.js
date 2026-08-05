// Bookmarks for visitors who are not signed in.
//
// A logged-out visitor has no SavedTalk row to toggle, so their bookmarks live in localStorage
// and are folded into the account on the next login. The server renders every button in the
// "not saved" state - it cannot know any better - and this corrects them after paint.
//
// Two things make that harder than it sounds. HTMX replaces button markup wholesale when a
// signed-in user toggles one, and it re-renders whole lists when filters change or a poll
// fires, so nothing may be bound to a button directly: the click handler is delegated from
// document.body, and state is re-applied on every htmx:load. And the translated labels come
// from data attributes rather than being written here, so this file stays language-agnostic.
(function() {
  'use strict';

  var KEY = 'savedTalks:v1';
  // Matches MAX_MERGE_IDS on the server. A cap keeps a runaway loop from filling storage and
  // then posting an enormous merge payload.
  var MAX_IDS = 500;

  function read() {
    try {
      var raw = localStorage.getItem(KEY);
      if (!raw) { return []; }
      var ids = JSON.parse(raw);
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
    var solid = node.querySelector('[data-save-icon-solid]');
    var outline = node.querySelector('[data-save-icon-outline]');
    var label = node.querySelector('[data-save-label]');
    var button = node.matches('button') ? node : node.querySelector('button');

    if (solid) { solid.classList.toggle('hidden', !isSaved); }
    if (outline) { outline.classList.toggle('hidden', isSaved); }
    if (label) {
      label.textContent = isSaved
        ? label.dataset.labelSaved
        : label.dataset.labelUnsaved;
    }
    if (button) {
      button.classList.toggle('text-yellow-500', isSaved);
      button.title = isSaved ? button.dataset.titleSaved : button.dataset.titleUnsaved;
    }
    node.dataset.saved = isSaved ? '1' : '0';
  }

  function paint(root) {
    var ids = read();
    var scope = root && root.querySelectorAll ? root : document;
    var nodes = scope.querySelectorAll('[data-save-talk]');
    for (var i = 0; i < nodes.length; i++) {
      applyState(nodes[i], ids.indexOf(parseInt(nodes[i].dataset.saveTalk, 10)) !== -1);
    }
  }

  function csrfToken() {
    // CSRF_COOKIE_HTTPONLY is on, so the cookie cannot be read here. The token comes from the
    // hidden form the base template renders, the same way the HTMX buttons get it.
    var input = document.querySelector('#csrf-form input[name="csrfmiddlewaretoken"]');
    return input ? input.value : '';
  }

  function mergeIntoAccount() {
    var ids = read();
    if (!ids.length) { return; }

    var url = document.body.dataset.mergeSavedUrl;
    if (!url) { return; }

    fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
      body: JSON.stringify({ ids: ids })
    }).then(function(response) {
      if (!response.ok) { return; }
      // Clear only on success, so a failed merge is retried on the next page view instead of
      // silently losing the visitor's picks.
      localStorage.removeItem(KEY);
      var notice = document.getElementById('saved-merged-notice');
      if (notice) { notice.classList.remove('hidden'); }
    }).catch(function() {
      // Offline or blocked. Keep the local copy and try again later.
    });
  }

  function showLocalOnlyNotice() {
    var notice = document.getElementById('saved-local-notice');
    if (!notice || localStorage.getItem(KEY + ':noticeSeen')) { return; }
    notice.classList.remove('hidden');
  }

  document.body.addEventListener('click', function(evt) {
    var dismiss = evt.target.closest('[data-dismiss-saved-notice]');
    if (dismiss) {
      var notice = document.getElementById('saved-local-notice');
      if (notice) { notice.classList.add('hidden'); }
      try { localStorage.setItem(KEY + ':noticeSeen', '1'); } catch (err) { /* ignore */ }
      return;
    }

    var button = evt.target.closest('[data-save-toggle]');
    if (!button) { return; }
    evt.preventDefault();

    var id = parseInt(button.dataset.saveToggle, 10);
    var ids = read();
    var at = ids.indexOf(id);
    if (at === -1) { ids.push(id); } else { ids.splice(at, 1); }
    write(ids);
    paint();
    if (at === -1) { showLocalOnlyNotice(); }
  });

  // HTMX fires this on every newly-inserted fragment, so a swapped-in talk list gets its
  // bookmark state back. Scoped to the inserted node to avoid re-walking the whole page on
  // each ten-second poll.
  document.body.addEventListener('htmx:load', function(evt) { paint(evt.detail.elt); });

  paint();
  if (document.body.dataset.userAuthenticated === '1') {
    mergeIntoAccount();
  }
})();
