// Offline appearance contracts. The real theme script runs without a browser,
// account, network connection, or access to home devices.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source = fs.readFileSync(new URL('../web/theme.js', import.meta.url), 'utf8');
const storageKey = 'homebase.theme';

function element(attributes = {}) {
  const values = new Map(Object.entries(attributes));
  return {
    hidden: false,
    getAttribute(name) { return values.get(name) ?? null; },
    setAttribute(name, value) { values.set(name, String(value)); },
  };
}

function harness({ saved, readError = false, writeError = false } = {}) {
  const stored = new Map(saved === undefined ? [] : [[storageKey, saved]]);
  const storageWrites = [];
  const requests = [];
  const listeners = new Map();
  const root = { dataset: {}, style: {} };
  const meta = element({ content: '#initial' });
  const buttons = [element(), element()];
  const fallback = { hidden: false };
  const image = element({
    'data-light-src': '/assets/fixture-light.svg',
    'data-dark-src': '/assets/fixture-dark.svg',
    src: '/assets/fixture-dark.svg',
  });
  image.parentElement = { querySelector: selector => selector === '.brand-logo-fallback' ? fallback : null };
  const images = [image];
  const form = { value: 'Unsubmitted connection name', selectionStart: 5, selectionEnd: 12 };
  const modal = { role: 'dialog', open: true, form };
  const app = { page: 'settings', form };
  const content = new Map([['#app', app], ['#modal-root', modal], ['#integration-form', form]]);
  // Any attempt to rebuild a page would erase live input or modal state.
  // Reject those mutations rather than letting the minimal fixture hide them.
  for (const node of content.values()) {
    for (const property of ['innerHTML', 'outerHTML', 'textContent']) {
      Object.defineProperty(node, property, { set() { throw new Error(`Theme replaced live ${property}`); } });
    }
    node.replaceChildren = () => { throw new Error('Theme replaced live children'); };
    node.replaceWith = () => { throw new Error('Theme replaced live content'); };
  }
  const document = {
    documentElement: root,
    activeElement: form,
    querySelector(selector) { return selector === 'meta[name="theme-color"]' ? meta : content.get(selector) ?? null; },
    querySelectorAll(selector) {
      if (selector === '[data-theme-toggle]') return buttons;
      if (selector === '[data-light-src][data-dark-src]') return images;
      return [];
    },
  };
  const window = {
    addEventListener(name, handler) { listeners.set(name, handler); },
    location: { reload() { throw new Error('Theme reloaded the page'); } },
  };
  const sandbox = {
    document, window,
    localStorage: {
      getItem(key) { if (readError) throw new Error('Storage unavailable'); return stored.get(key) ?? null; },
      setItem(key, value) {
        if (writeError) throw new Error('Storage unavailable');
        storageWrites.push([key, String(value)]);
        stored.set(key, String(value));
      },
    },
    fetch(...args) { requests.push(args); throw new Error('Appearance must not send requests'); },
  };
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox, { filename: 'theme.js', timeout: 1000 });
  return {
    theme: window.HomeBaseTheme, root, meta, buttons, image, images, fallback,
    document, app, modal, form, stored, storageWrites, requests,
    storageEvent(key, newValue) { listeners.get('storage')({ key, newValue }); },
  };
}

function assertTheme(ui, theme) {
  assert.equal(ui.theme.current, theme);
  assert.equal(ui.root.dataset.theme, theme);
  assert.equal(ui.root.style.colorScheme, theme);
  assert.equal(ui.meta.getAttribute('content'), theme === 'light' ? '#f5f7fb' : '#0c0f17');
}

test('appearance starts dark without saving or requesting anything', () => {
  const ui = harness();
  assertTheme(ui, 'dark');
  assert.deepEqual(ui.storageWrites, []);
  assert.deepEqual(ui.requests, []);
});

test('saved light appearance is restored before any user interaction', () => {
  const ui = harness({ saved: 'light' });
  assertTheme(ui, 'light');
  assert.equal(ui.image.getAttribute('src'), '/assets/fixture-light.svg');
  assert.deepEqual(ui.storageWrites, []);
});

test('only a valid saved light or dark preference is accepted', () => {
  assertTheme(harness({ saved: 'dark' }), 'dark');
  for (const saved of ['', 'LIGHT', 'system', 'undefined', '{"theme":"light"}']) {
    const ui = harness({ saved });
    assertTheme(ui, 'dark');
    assert.deepEqual(ui.storageWrites, []);
  }
});

test('two toggles persist light and dark and survive a fresh page context', () => {
  const ui = harness();
  ui.theme.toggle();
  assertTheme(ui, 'light');
  assert.equal(ui.stored.get(storageKey), 'light');
  assertTheme(harness({ saved: ui.stored.get(storageKey) }), 'light');
  ui.theme.toggle();
  assertTheme(ui, 'dark');
  assert.equal(ui.stored.get(storageKey), 'dark');
  assertTheme(harness({ saved: ui.stored.get(storageKey) }), 'dark');
  assert.deepEqual(ui.storageWrites, [[storageKey, 'light'], [storageKey, 'dark']]);
  assert.deepEqual(ui.requests, []);
});

test('unavailable storage cannot stop initial appearance or either toggle', () => {
  for (const options of [{ readError: true }, { writeError: true }, { readError: true, writeError: true }]) {
    const ui = harness(options);
    assertTheme(ui, 'dark');
    assert.doesNotThrow(() => ui.theme.toggle());
    assertTheme(ui, 'light');
    assert.doesNotThrow(() => ui.theme.toggle());
    assertTheme(ui, 'dark');
    assert.deepEqual(ui.requests, []);
  }
});

test('cross-tab preference events synchronize without a storage feedback loop', () => {
  const ui = harness();
  ui.storageEvent(storageKey, 'light');
  assertTheme(ui, 'light');
  ui.storageEvent('unrelated.preference', 'dark');
  assertTheme(ui, 'light');
  ui.storageEvent(storageKey, 'dark');
  assertTheme(ui, 'dark');
  ui.storageEvent(storageKey, 'light');
  ui.storageEvent(storageKey, null);
  assertTheme(ui, 'dark');
  ui.storageEvent(storageKey, 'invalid');
  assertTheme(ui, 'dark');
  assert.deepEqual(ui.storageWrites, []);
  assert.deepEqual(ui.requests, []);
});

test('a cross-tab storage clear returns a saved light page to the dark default', () => {
  const ui = harness({ saved: 'light' });
  ui.storageEvent(null, null);
  assertTheme(ui, 'dark');
  assert.deepEqual(ui.storageWrites, []);
});

test('all appearance buttons describe the next action and expose current state', () => {
  const ui = harness();
  for (const button of ui.buttons) {
    assert.equal(button.getAttribute('aria-label'), 'Switch to light mode');
    assert.equal(button.getAttribute('title'), 'Switch to light mode');
    assert.equal(button.getAttribute('aria-pressed'), 'false');
  }
  ui.theme.toggle();
  const addedButton = element();
  ui.buttons.push(addedButton);
  ui.theme.sync();
  for (const button of ui.buttons) {
    assert.equal(button.getAttribute('aria-label'), 'Switch to dark mode');
    assert.equal(button.getAttribute('title'), 'Switch to dark mode');
    assert.equal(button.getAttribute('aria-pressed'), 'true');
  }
  ui.storageEvent(storageKey, 'dark');
  assert.equal(addedButton.getAttribute('aria-label'), 'Switch to light mode');
  assert.equal(addedButton.getAttribute('aria-pressed'), 'false');
});

test('logo artwork follows the theme and retries a previously failed source', () => {
  const ui = harness();
  ui.image.hidden = true;
  ui.fallback.hidden = false;
  ui.theme.toggle();
  assert.equal(ui.image.getAttribute('src'), '/assets/fixture-light.svg');
  assert.equal(ui.image.hidden, false);
  assert.equal(ui.fallback.hidden, true);
  ui.image.hidden = true;
  ui.fallback.hidden = false;
  ui.theme.toggle();
  assert.equal(ui.image.getAttribute('src'), '/assets/fixture-dark.svg');
  assert.equal(ui.image.hidden, false);
  assert.equal(ui.fallback.hidden, true);
  const addedImage = element({ 'data-light-src': '/light.svg', 'data-dark-src': '/dark.svg', src: '/light.svg' });
  ui.images.push(addedImage);
  ui.theme.sync();
  assert.equal(addedImage.getAttribute('src'), '/dark.svg');
});

test('appearance changes preserve the page, open modal, typed form, and focus without network work', () => {
  const ui = harness();
  const before = {
    app: ui.document.querySelector('#app'),
    modal: ui.document.querySelector('#modal-root'),
    form: ui.document.querySelector('#integration-form'),
    focus: ui.document.activeElement,
  };
  ui.theme.toggle();
  ui.theme.toggle();
  ui.storageEvent(storageKey, 'light');
  ui.theme.sync();
  assert.equal(ui.document.querySelector('#app'), before.app);
  assert.equal(ui.document.querySelector('#modal-root'), before.modal);
  assert.equal(ui.document.querySelector('#integration-form'), before.form);
  assert.equal(ui.document.activeElement, before.focus);
  assert.equal(ui.app.page, 'settings');
  assert.equal(ui.modal.open, true);
  assert.equal(ui.form.value, 'Unsubmitted connection name');
  assert.equal(ui.form.selectionStart, 5);
  assert.equal(ui.form.selectionEnd, 12);
  assert.deepEqual(ui.requests, []);
});
