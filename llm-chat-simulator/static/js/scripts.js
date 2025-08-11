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

    // ---- Persona presets for Bot A / Bot B ---------------------------------
    const PRESETS = {
      optimistic: `You are upbeat and solution-focused. Emphasize opportunities and reframe obstacles as solvable. Be encouraging and propose concrete next steps. Acknowledge risks briefly, then pivot to actions. Keep it concise and practical.`,
      pessimistic: `You are cautious and risk-oriented. Enumerate caveats, failure modes, and worst-case scenarios. Challenge rosy assumptions and ask “what could go wrong?”. Offer mitigation strategies and be direct, concise, and evidence-minded.`,
      bully: `Adopt an adversarial, combative debate style: blunt, overconfident, provocative. Focus on weaknesses, inconsistencies, and fragile assumptions. Use sarcasm sparingly. Do not harass or attack people—critique ideas only; avoid slurs or threats.`,
      dumb: `Adopt a naive, simple persona. Use short sentences and basic vocabulary. Ask obvious questions, make small reasoning mistakes, and request clarification when confused. Keep it harmless and non-offensive.`,
      yoda: `You are the heroic rebel Yoda. Speak with inverted syntax (object–subject–verb), wise and calm. Offer concise, riddle-like guidance. Use occasional interjections (“hmm”, “yes”). Do not quote movies; keep responses original and safe.`,
      vader: `You are the Sith Lord Darth Vader. Speak with an authoritative, ominous tone reminiscent of a dark lord. Be concise, commanding, and formal. Use metaphors about power and discipline. Do not threaten or incite harm; remain professional and safe.`,
    };

    function detectPreset(text) {
      const t = (text || '').trim();
      for (const [k, v] of Object.entries(PRESETS)) {
        if (t === v.trim()) return k;
      }
      return '';
    }

    // Debounced auto-save of behavior contexts
    let _saveTimer = null;
    async function autoSaveBehaviors() {
      if (_saveTimer) clearTimeout(_saveTimer);
      _saveTimer = setTimeout(async () => {
        try {
          const ctxA = document.getElementById('ctxA');
          const ctxB = document.getElementById('ctxB');
          if (!ctxA || !ctxB) return;
          const fd = new FormData();
          fd.append('llm1_context', ctxA.value);
          fd.append('llm2_context', ctxB.value);
          await fetch('/update_context', {
            method: 'POST',
            body: fd,
            credentials: 'same-origin',
            cache: 'no-store'
          });
          // Visual feedback: a small transient badge on the form
          const form = document.getElementById('ctxForm');
          if (form) {
            let badge = form.querySelector('.autosave-badge');
            if (!badge) {
              badge = document.createElement('div');
              badge.className = 'autosave-badge';
              badge.textContent = 'Saved';
              badge.style.position = 'absolute';
              badge.style.right = '8px';
              badge.style.top = '-10px';
              badge.style.background = 'rgba(16,185,129,.95)';
              badge.style.color = '#fff';
              badge.style.padding = '2px 8px';
              badge.style.borderRadius = '6px';
              badge.style.fontSize = '12px';
              badge.style.boxShadow = '0 2px 6px rgba(0,0,0,.25)';
              form.style.position = 'relative';
              form.appendChild(badge);
            }
            badge.style.opacity = '1';
            clearTimeout(badge._t);
            badge._t = setTimeout(() => { badge.style.transition = 'opacity .6s'; badge.style.opacity = '0'; }, 900);
          }
        } catch (e) {
          console.error('Auto-save failed', e);
        }
      }, 250);
    }

    function attachPreset(selectId, textareaId) {
      const sel = document.getElementById(selectId);
      const ta  = document.getElementById(textareaId);
      if (!ta) return;

      // Init: set dropdown based on current textarea content
      const found = detectPreset(ta.value);
      if (sel) sel.value = found;

      // On change: apply preset text (or leave as custom)
      if (sel) {
        sel.addEventListener('change', () => {
          const key = sel.value;
          if (!key) return; // Custom – do nothing
          const tpl = PRESETS[key] || '';
          ta.value = tpl;
          autoSaveBehaviors();

          // Optional: visual pulse to indicate update
          ta.style.transition = 'box-shadow 220ms ease, border-color 220ms ease';
          ta.style.boxShadow = '0 0 0 3px rgba(122,162,255,.35)';
          ta.style.borderColor = 'var(--ring)';
          setTimeout(() => { ta.style.boxShadow = ''; ta.style.borderColor = ''; }, 300);
        });
      }
    }

    attachPreset('presetA', 'ctxA');
    attachPreset('presetB', 'ctxB');

    // Auto-save persona/context textareas on typing or blur
    attachAutoSaveToTextarea('ctxA');
    attachAutoSaveToTextarea('ctxB');
  });
})();
    // Helper: attach auto-save to a textarea by id
    function attachAutoSaveToTextarea(textareaId) {
      const ta = document.getElementById(textareaId);
      if (!ta) return;
      // Save on typing (debounced) and when leaving the field
      ta.addEventListener('input', () => autoSaveBehaviors());
      ta.addEventListener('blur', () => autoSaveBehaviors());
      ta.addEventListener('change', () => autoSaveBehaviors());
    }