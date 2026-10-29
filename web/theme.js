// Browser-local appearance only. Runs before styles to restore the saved theme.
(() => {
  const key = 'homebase.theme';
  const root = document.documentElement;
  let current = 'dark';
  try {
    const saved = localStorage.getItem(key);
    if (saved === 'light' || saved === 'dark') current = saved;
  } catch { /* Storage can be unavailable in private or restricted browsers. */ }

  function sync() {
    for (const button of document.querySelectorAll('[data-theme-toggle]')) {
      const label = `Switch to ${current === 'dark' ? 'light' : 'dark'} mode`;
      button.setAttribute('aria-label', label);
      button.setAttribute('title', label);
      button.setAttribute('aria-pressed', String(current === 'light'));
    }
    for (const image of document.querySelectorAll('[data-light-src][data-dark-src]')) {
      const source = image.getAttribute(current === 'light' ? 'data-light-src' : 'data-dark-src');
      if (source && image.getAttribute('src') !== source) {
        image.hidden = false;
        const fallback = image.parentElement?.querySelector('.brand-logo-fallback');
        if (fallback) fallback.hidden = true;
        image.setAttribute('src', source);
      }
    }
  }

  function apply(value, persist = false) {
    current = value === 'light' ? 'light' : 'dark';
    root.dataset.theme = current;
    root.style.colorScheme = current;
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', current === 'light' ? '#f5f7fb' : '#0c0f17');
    if (persist) {
      try { localStorage.setItem(key, current); } catch { /* Appearance still works. */ }
    }
    sync();
  }

  window.HomeBaseTheme = Object.freeze({
    sync,
    toggle() { apply(current === 'dark' ? 'light' : 'dark', true); },
    get current() { return current; },
  });
  window.addEventListener('storage', event => {
    if (event.key === key || event.key === null) apply(event.key === null ? 'dark' : event.newValue);
  });
  apply(current);
})();
