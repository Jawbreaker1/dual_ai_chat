// Dual LLM Chat – frontend logic
// Renders Markdown via marked.js, sanitizes via DOMPurify, and strips analysis→assistantfinal artifacts.
(function () {
  'use strict';

  function ready(fn) {
    if (document.readyState !== 'loading') fn();
    else document.addEventListener('DOMContentLoaded', fn);
  }

  ready(function () {
    const chatEl = document.getElementById('chat');
    const tickBtn = document.getElementById('tickBtn');
    const autoBtn = document.getElementById('autoBtn');
    const turnsInput = document.querySelector('input[name="turns"]');

    if (!chatEl) return; // page without chat

    // --- Helpers -------------------------------------------------------------

    function stripArtifacts(text) {
      const s = (text || '').trimStart();
      const low = s.toLowerCase();
      if (low.startsWith('analysis')) {
        const key = 'assistantfinal';
        const idx = low.indexOf(key);
        if (idx !== -1) {
          return s.slice(idx + key.length).trim();
        }
      }
      return text || '';
    }

    function renderMarkdown(text) {
      try {
        const cleaned = stripArtifacts(text);
        const html = marked.parse(cleaned);
        return DOMPurify.sanitize(html);
      } catch (e) {
        console.error('Markdown render error', e);
        const esc = (text || '').replace(/[&<>]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[ch]));
        return esc;
      }
    }

    function append(role, text) {
      const div = document.createElement('div');
      div.className = 'msg ' + (role === 'botA' ? 'a' : role === 'botB' ? 'b' : 'user');
      const label = (role === 'botA') ? 'Bot A' : (role === 'botB') ? 'Bot B' : 'User';
      div.innerHTML = `<div class="meta">${label}</div><div class="msg-content"></div>`;
      const content = div.querySelector('.msg-content');
      content.innerHTML = renderMarkdown(text);
      chatEl.appendChild(div);
      chatEl.scrollTop = chatEl.scrollHeight;
    }

    // Who should speak next, based on the last bubble in the DOM (mirrors server logic)
    function computeNextSpeakerFromDOM() {
      const items = chatEl.querySelectorAll('.msg');
      const last = items[items.length - 1];
      if (!last) return 'botA';
      if (last.classList.contains('a')) return 'botB';
      if (last.classList.contains('b')) return 'botA';
      return 'botA'; // last was user or unknown
    }

    // Show a temporary "thinking…" bubble for A or B and return the placeholder element
    function showTyping(role) {
      const isA = (role === 'botA');
      const div = document.createElement('div');
      div.className = 'msg ' + (isA ? 'a' : 'b') + ' typing';
      const label = isA ? 'Bot A' : 'Bot B';
      div.innerHTML = `
        <div class="meta">${label}</div>
        <div class="msg-content"><span class="dots"><span></span><span></span><span></span></span> thinking…</div>
      `;
      chatEl.appendChild(div);
      chatEl.scrollTop = chatEl.scrollHeight;
      return div;
    }

    // --- Chat ticking --------------------------------------------------------

    async function tickOnce() {
      if (tickBtn) tickBtn.disabled = true;

      // show typing indicator for the predicted next speaker
      const next = computeNextSpeakerFromDOM();
      const placeholder = showTyping(next);

      try {
        const res = await fetch('/tick', { method: 'POST', credentials: 'same-origin', cache: 'no-store' });
        const data = await res.json();
        if (data && data.ok) {
          // ensure placeholder matches the actual role returned by server
          const expectedRole = (data.role === 'botA') ? 'a' : (data.role === 'botB') ? 'b' : 'user';
          const metaEl = placeholder.querySelector('.meta');
          if (expectedRole === 'a') {
            placeholder.classList.remove('b', 'user');
            placeholder.classList.add('a');
            if (metaEl) metaEl.textContent = 'Bot A';
          } else if (expectedRole === 'b') {
            placeholder.classList.remove('a', 'user');
            placeholder.classList.add('b');
            if (metaEl) metaEl.textContent = 'Bot B';
          } else {
            placeholder.classList.remove('a', 'b');
            placeholder.classList.add('user');
            if (metaEl) metaEl.textContent = 'User';
          }

          placeholder.classList.remove('typing');
          const content = placeholder.querySelector('.msg-content');
          if (content) content.innerHTML = renderMarkdown(data.text);
          chatEl.scrollTop = chatEl.scrollHeight;
          return (typeof data.auto_left === 'number') ? data.auto_left : 0;
        }
        console.warn('Tick failed', data);
        placeholder.classList.remove('typing');
        const content = placeholder.querySelector('.msg-content');
        if (content) content.textContent = '(No response)';
        return 0;
      } catch (e) {
        console.error(e);
        placeholder.classList.remove('typing');
        const content = placeholder.querySelector('.msg-content');
        if (content) content.textContent = '(Network error)';
        return 0;
      } finally {
        if (tickBtn) tickBtn.disabled = false;
      }
    }

    async function autoPlayServer(initialLeft) {
      // Uses server-maintained auto_left counter (set during Send & Start)
      let left = typeof initialLeft === 'number' ? initialLeft : 1;
      if (autoBtn) autoBtn.disabled = true;
      while (left > 0) {
        left = await tickOnce();
        if (left <= 0) break;
      }
      if (autoBtn) autoBtn.disabled = false;
    }

    async function autoPlayLocal(count) {
      // Runs exactly `count` ticks client-side (when clicking Auto-play manually)
      const n = Math.max(1, parseInt(count, 10) || 1);
      if (autoBtn) autoBtn.disabled = true;
      for (let i = 0; i < n; i++) {
        await tickOnce();
      }
      if (autoBtn) autoBtn.disabled = false;
    }

    // --- Events --------------------------------------------------------------

    if (tickBtn) {
      tickBtn.addEventListener('click', function () {
        tickOnce();
      });
    }

    if (autoBtn) {
      autoBtn.addEventListener('click', function () {
        // Manual autoplay uses local counter from the input field
        const desired = turnsInput ? parseInt(turnsInput.value, 10) : 0;
        autoPlayLocal(desired > 0 ? desired : 1);
      });
    }

    // Enhance pre-rendered messages from the server
    const existing = chatEl.querySelectorAll('.msg .msg-content');
    existing.forEach(node => {
      const raw = node.textContent; // plain text from server
      node.innerHTML = renderMarkdown(raw);
    });

    // If the server set a counter (after Send & Start), kick off autoplay automatically
    if (typeof window.AUTO_TURNS_LEFT === 'number' && window.AUTO_TURNS_LEFT > 0) {
      autoPlayServer(window.AUTO_TURNS_LEFT);
    }

    // --- Theme switcher ------------------------------------------------------
    const themeSelect = document.getElementById('themeSelect');
    const THEMES = ['', 'theme-orchid', 'theme-emerald', 'theme-ember', 'theme-slate'];

    function applyTheme(themeClass) {
      // Remove any previously applied theme class from <body>
      THEMES.forEach(t => { if (t) document.body.classList.remove(t); });
      if (themeClass) document.body.classList.add(themeClass);
    }

    // Load saved theme from localStorage
    try {
      const saved = localStorage.getItem('themeClass');
      if (saved && THEMES.includes(saved)) {
        applyTheme(saved);
        if (themeSelect) themeSelect.value = saved;
      }
    } catch (e) {
      // ignore storage errors
    }

    // React to user changes
    if (themeSelect) {
      themeSelect.addEventListener('change', () => {
        const value = themeSelect.value;
        applyTheme(value);
        try { localStorage.setItem('themeClass', value); } catch (e) {}
      });
    }
  });
})();