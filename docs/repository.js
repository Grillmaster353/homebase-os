// Resolve public download links from GitHub Pages without any network request.
// Custom-domain publishers can set the homebase-repository meta value.
(() => {
  const valid = value => /^[A-Za-z0-9_.-]+$/.test(value);
  let repository = null;
  const configured = document.querySelector('meta[name="homebase-repository"]')?.content;
  if (configured) {
    try {
      const url = new URL(configured);
      const parts = url.pathname.split('/').filter(Boolean);
      if (url.protocol === 'https:' && url.hostname === 'github.com' && !url.username && !url.password && !url.search && !url.hash && parts.length === 2 && parts.every(valid)) {
        repository = `https://github.com/${parts[0]}/${parts[1]}`;
      }
    } catch {}
  } else if (/^[A-Za-z0-9-]+\.github\.io$/.test(location.hostname)) {
    const owner = location.hostname.split('.')[0];
    const name = location.pathname.split('/').filter(Boolean)[0] || `${owner}.github.io`;
    if (valid(owner) && valid(name)) repository = `https://github.com/${owner}/${name}`;
  }
  if (!repository) return;
  for (const link of document.querySelectorAll('[data-repository-link]')) {
    link.href = repository + (link.dataset.repositoryLink === 'download' ? '/archive/refs/heads/main.zip' : '');
    link.hidden = false;
  }
})();
