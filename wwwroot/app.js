const $ = id => document.getElementById(id);
const make = (tag, text, className) => {
  const node = document.createElement(tag);
  if (text) node.textContent = text;
  if (className) node.className = className;
  return node;
};
const icon = name => {
  const image = make('img');
  image.src = '/icons/' + name + '.svg';
  image.alt = '';
  return image;
};
const url = (route, values) => {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values))
    if (value) params.set(key, value);
  return route + (params.size ? '?' + params : '');
};
const categories = {
  Folders: [], Documents: ['doc', 'docx', 'md', 'txt', 'rtf', 'odt'],
  Spreadsheets: ['xls', 'xlsx', 'csv', 'ods'], Presentations: ['ppt', 'pptx', 'odp'],
  Images: ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'], Videos: ['mp4', 'mov', 'webm'],
  Forms: ['form', 'xsn'], PDFs: ['pdf'], Archives: ['zip', 'tar', 'gz', '7z'],
};
const labels = { today: 'Today', week: 'Last 7 days', month: 'Last 30 days',
  year: 'This year', last: 'Last year', custom: 'Custom date range' };
const join = (parent, name) => parent ? parent + '/' + name : name;
const parent = path => path.split('/').slice(0, -1).join('/');
const state = () => Object.fromEntries(new URLSearchParams(location.search));
let revision = 0;
let pending;
let timer;
let activeAction;
let menuTrigger;

async function request(route, options) {
  const response = await fetch(route, options);
  if (!response.ok) {
    const problem = await response.json().catch(() => ({}));
    const error = new Error(problem.title || 'Request failed (' + response.status + ').');
    error.status = response.status;
    throw error;
  }
  return response;
}
const list = async (path, q, signal, folderSizes) =>
  (await request(url(q ? '/api/search' : '/api/browse', { path, q, folderSizes }), { signal })).json();
const post = (route, data) => request(route, {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
});
function navigate(changes, reset = false) {
  const next = url('/', { ...(reset ? {} : state()), ...changes });
  if (next !== location.pathname + location.search) history.pushState({}, '', next);
  refresh();
}
function folderLink(path, name, current = false) {
  const link = make('a', name);
  link.href = url('/', { path });
  if (current) link.setAttribute('aria-current', 'page');
  link.addEventListener('click', event => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigate({ path }, true);
  });
  return link;
}
function showBreadcrumbs(path) {
  const crumbs = $('breadcrumbs');
  const parts = path ? path.split('/') : [];
  crumbs.replaceChildren(folderLink('', 'Files', !path));
  let current = '';
  for (const [i, name] of parts.entries()) {
    current = join(current, name);
    const divider = make('span', '›');
    divider.setAttribute('aria-hidden', 'true');
    crumbs.append(divider, folderLink(current, name, i === parts.length - 1));
  }
}
function showTree(path, levels) {
  const tree = $('tree');
  const root = folderLink('', 'Files', !path);
  root.prepend(icon('files'));
  tree.replaceChildren(root);
  const ancestors = path ? path.split('/') : [];
  function add(depth) {
    for (const entry of levels[depth].entries.filter(entry => entry.kind === 'folder')) {
      const link = folderLink(entry.path, entry.name, entry.path === path);
      link.prepend(icon('folder'));
      link.style.paddingInlineStart = Math.min(64, 20 + depth * 8) + 'px';
      tree.append(link);
      if (entry.name === ancestors[depth] && levels[depth + 1]) add(depth + 1);
    }
  }
  add(0);
  const active = tree.querySelector('[aria-current]');
  if (active && active.offsetLeft < tree.scrollLeft)
    tree.scrollLeft = active.offsetLeft;
  else if (active && active.offsetLeft + active.offsetWidth > tree.scrollLeft + tree.clientWidth)
    tree.scrollLeft = active.offsetLeft + active.offsetWidth - tree.clientWidth;
}
const extension = entry => entry.name.includes('.') ? entry.name.split('.').at(-1).toLowerCase() : '';
const category = entry => entry.kind === 'folder' ? 'Folders' :
  Object.keys(categories).find(key => categories[key].includes(extension(entry)));
const fileType = entry => entry.kind === 'folder' ? 'File folder' :
  extension(entry) ? extension(entry).toUpperCase() + ' file' : 'File';
function bytes(value) {
  if (value < 1024) return value + ' B';
  const units = ['KB', 'MB', 'GB', 'TB'];
  let index = 0;
  let size = value / 1024;
  while (size >= 1024 && index < units.length - 1) { size /= 1024; index++; }
  return size.toFixed(1) + ' ' + units[index];
}
function matchesDate(entry, s) {
  if (!s.date) return true;
  const value = new Date(entry.modifiedUtc);
  const now = new Date();
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  switch (s.date) {
    case 'today': return value >= start && value <= now;
    case 'week':
    case 'month':
      start.setDate(start.getDate() - (s.date === 'week' ? 6 : 29));
      return value >= start && value <= now;
    case 'year': return value.getFullYear() === now.getFullYear();
    case 'last': return value.getFullYear() === now.getFullYear() - 1;
    case 'custom': {
      const localDay = [value.getFullYear(), value.getMonth() + 1, value.getDate()]
        .map((part, index) => String(part).padStart(index ? 2 : 4, '0')).join('-');
      return (!s.from || localDay >= s.from) && (!s.to || localDay <= s.to);
    }
    default: return true;
  }
}
function highlighted(name, query) {
  const fragment = document.createDocumentFragment();
  if (!query) { fragment.append(name); return fragment; }
  const escaped = [...query].map(char => '\\^$.*+?()[]{}|'.includes(char) ? '\\' + char : char).join('');
  let position = 0;
  for (const match of name.matchAll(new RegExp(escaped, 'giu'))) {
    fragment.append(name.slice(position, match.index), make('mark', match[0]));
    position = match.index + match[0].length;
  }
  fragment.append(name.slice(position));
  return fragment;
}
function row(entry, s) {
  const item = make('article', '', 'entry columns');
  if (s.item === entry.path) item.classList.add('selected');
  const name = entry.kind === 'folder' ? folderLink(entry.path, '') : make('span');
  name.className = 'name';
  const label = make('span', '', 'name-label');
  label.append(highlighted(entry.name, s.q?.trim()));
  name.append(icon(entry.kind === 'folder' ? 'folder' : 'document'), label);
  if (s.q && parent(entry.path) !== (s.path || '')) name.title = entry.path;
  const more = make('button', '', 'icon-button item-action');
  more.append(icon('more'));
  more.setAttribute('aria-label', 'Actions for ' + entry.name);
  more.setAttribute('aria-expanded', 'false');
  more.addEventListener('click', () => openMenu(entry, more));
  item.append(name, make('span', new Date(entry.modifiedUtc).toLocaleString([], {
    dateStyle: 'short', timeStyle: 'short',
  }), 'meta modified'), make('span', fileType(entry), 'meta type'),
  make('span', entry.sizeBytes == null ? '—' : bytes(entry.sizeBytes), 'meta size'), more);
  return item;
}
function render(entries, s) {
  const types = (s.types || '').split(',').filter(Boolean);
  const shown = entries.filter(entry => (!types.length || types.includes(category(entry))) && matchesDate(entry, s));
  const descending = s.dir === 'desc';
  const sort = s.sort || 'name';
  const sortValue = entry => ({ name: entry.name, size: entry.sizeBytes ?? -1,
    type: fileType(entry), modified: entry.modifiedUtc })[sort] ?? entry.name;
  shown.sort((a, b) => {
    const x = sortValue(a), y = sortValue(b);
    return (typeof x === 'number' ? x - y : x.localeCompare(y)) * (descending ? -1 : 1);
  });
  const grid = s.view === 'grid';
  $('entries').classList.toggle('grid', grid);
  $('headings').hidden = grid;
  $('headings').replaceChildren(...Object.entries({ name: 'Name', modified: 'Modified', type: 'Type', size: 'Size' })
    .map(([key, label]) => {
      const button = make('button', label + (sort === key ? (descending ? ' ↓' : ' ↑') : ''), key);
      button.addEventListener('click', () => navigate({ sort: key, dir: sort === key && !descending ? 'desc' : '' }));
      return button;
    }));
  $('entries').replaceChildren(...shown.map(entry => row(entry, s)));
  $('empty').hidden = shown.length > 0;
  $('empty').textContent = s.q || types.length || s.date ? 'No matches. Try changing the search or filters.' : 'This folder is empty.';
  $('results-note').hidden = !s.q;
  $('results-note').replaceChildren(
    make('span', 'Results for "' + (s.q?.toLowerCase() || '') + '"'),
    make('span', shown.length + (shown.length === 1 ? ' item found' : ' items found')),
  );
  const folders = shown.filter(entry => entry.kind === 'folder').length;
  $('counts').textContent = folders + ' ' + (folders === 1 ? 'folder' : 'folders') + ' · ' +
    (shown.length - folders) + ' ' + (shown.length - folders === 1 ? 'file' : 'files');
  $('total').textContent = bytes(shown.reduce((sum, entry) => sum + (entry.sizeBytes || 0), 0)) + ' total';
}
async function refresh() {
  closeMenu();
  pending?.abort();
  const signal = (pending = new AbortController()).signal;
  const version = ++revision;
  const s = state();
  const path = s.path || '';
  const query = s.q?.trim() || '';
  $('query').value = s.q || '';
  showBreadcrumbs(path);
  showFilters(s);
  $('list-view').setAttribute('aria-pressed', s.view !== 'grid');
  $('grid-view').setAttribute('aria-pressed', s.view === 'grid');
  $('results').setAttribute('aria-busy', 'true');
  const ancestors = path ? ['', ...path.split('/').map((_, i, parts) => parts.slice(0, i + 1).join('/'))] : [''];
  const levels = Promise.all(ancestors.map(folder => list(folder, '', signal, !query && folder === path)));
  try {
    const [tree, view] = await Promise.all([levels, query ? list(path, query, signal) : levels.then(items => items.at(-1))]);
    if (version !== revision) return;
    showTree(path, tree);
    render(view.entries, { ...s, q: query });
  } catch (error) {
    if (error.name === 'AbortError' || version !== revision) return;
    render([], { ...s, q: query });
    $('empty').textContent = error.message;
    try { showTree(path, [await list('', '', signal)]); } catch { /* Navigation remains available through breadcrumbs. */ }
  } finally {
    if (version === revision) $('results').setAttribute('aria-busy', 'false');
  }
}
function showFilters(s) {
  const types = new Set((s.types || '').split(','));
  $('types').replaceChildren(...Object.keys(categories).map(type => {
    const label = make('label');
    const checkbox = make('input');
    checkbox.type = 'checkbox';
    checkbox.checked = types.has(type);
    checkbox.addEventListener('change', () => {
      if (checkbox.checked) types.add(type); else types.delete(type);
      navigate({ types: [...types].filter(Boolean).join(',') });
    });
    label.append(checkbox, type);
    return label;
  }));
  const date = $('dates');
  const year = new Date().getFullYear();
  date.replaceChildren(...Object.entries({ '': 'Any time', ...labels }).map(([value, label]) => {
    const button = make('button', label + (value === 'year' ? ' (' + year + ')' : value === 'last' ? ' (' + (year - 1) + ')' : ''));
    button.setAttribute('aria-pressed', (s.date || '') === value);
    button.addEventListener('click', () => {
      $('date-menu').open = value === 'custom';
      navigate({ date: value, from: '', to: '' });
      if (value === 'custom') $('from-date').focus();
    });
    return button;
  }));
  if (s.date === 'custom') {
    const form = make('form');
    for (const key of ['from', 'to']) {
      const label = make('label', key === 'from' ? 'From' : 'To');
      const input = make('input');
      input.type = 'date';
      input.id = key + '-date';
      input.value = s[key] || '';
      label.append(input);
      form.append(label);
    }
    const apply = make('button', 'Apply dates');
    form.append(apply);
    form.addEventListener('submit', event => {
      event.preventDefault();
      const from = $('from-date').value;
      const to = $('to-date').value;
      if (from && to && from > to) { notice('Choose an end date on or after the start date.', '', ''); return; }
      $('date-menu').open = false;
      navigate({ from, to });
    });
    date.append(form);
  }
}
function notice(title, detail, kind) {
  clearTimeout(timer);
  const box = $('notice');
  const symbol = kind === 'working' ? make('span', '', 'spin') : kind ? icon('completed') : null;
  if (symbol) symbol.setAttribute('aria-hidden', 'true');
  const text = make('div');
  text.append(make('strong', title));
  if (detail) text.append(make('div', detail));
  const close = make('button', '', 'icon-button notice-close');
  close.type = 'button';
  close.setAttribute('aria-label', 'Dismiss notification');
  close.append(icon('close-x'));
  close.onclick = () => {
    clearTimeout(timer);
    box.hidden = true;
  };
  box.replaceChildren(...(symbol ? [symbol, text, close] : [text, close]));
  box.hidden = false;
  if (kind !== 'working') timer = setTimeout(() => { box.hidden = true; }, 5500);
}
async function copyLink(path, item) {
  try {
    await navigator.clipboard.writeText(new URL(url('/', { path, item }), location.origin).href);
    notice('Link copied', item || path || 'Files', 'done');
  } catch (error) { notice('Could not copy the link', error.message); }
}
async function download(entry) {
  notice('Download started', entry.name, 'working');
  try {
    const response = await request(url('/api/download', { path: entry.path }));
    const data = await response.blob();
    const href = URL.createObjectURL(data);
    const link = make('a');
    link.href = href;
    link.download = entry.name;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(href), 60000);
    notice('Download complete', entry.name, 'done');
  } catch (error) { notice('Download failed', error.message); }
}
function closeMenu(restore = false) {
  $('item-menu').hidden = true;
  menuTrigger?.setAttribute('aria-expanded', 'false');
  if (restore) menuTrigger?.focus();
  menuTrigger = null;
}
function openMenu(entry, trigger) {
  if (trigger === menuTrigger) { closeMenu(true); return; }
  closeMenu();
  const menu = $('item-menu');
  const actions = [
    ...(entry.kind === 'file' ? [['Download', 'download', () => download(entry)]] : []),
    ['Copy link', 'copy_link', () => entry.kind === 'folder' ? copyLink(entry.path) : copyLink(parent(entry.path), entry.path)],
    ['Rename', 'rename', () => openDialog('rename', entry)],
    ['Make a copy', 'make-a-copy', () => openDialog('copy', entry)],
    ['Move to…', 'move-to', () => openDialog('move', entry)],
    ['Delete', 'delete', () => openDialog('delete', entry)],
  ];
  menu.replaceChildren(...actions.map(([label, symbol, click]) => {
    const button = make('button', label);
    button.prepend(icon(symbol));
    button.addEventListener('click', () => { closeMenu(true); click(); });
    return button;
  }));
  menu.hidden = false;
  menuTrigger = trigger;
  trigger.setAttribute('aria-expanded', 'true');
  const rect = trigger.getBoundingClientRect();
  menu.style.left = Math.max(8, rect.right - menu.offsetWidth) + 'px';
  menu.style.top = Math.max(8, Math.min(rect.bottom + 4, innerHeight - menu.offsetHeight - 8)) + 'px';
  menu.firstElementChild.focus({ focusVisible: trigger.matches(':focus-visible') });
}
function openDialog(operation, entry) {
  activeAction = { operation, entry, path: state().path || '' };
  const titles = { create: 'New folder', rename: 'Rename', copy: 'Make a copy', move: 'Move to…', delete: 'Delete' };
  const deleting = operation === 'delete';
  $('dialog-title').textContent = titles[operation];
  $('dialog-description').textContent = deleting ? 'Permanently delete “' + entry.name + '”' +
    (entry.kind === 'folder' ? ' and its contents?' : '?') :
    operation === 'move' ? 'Enter an existing destination folder relative to Files. Leave blank to move to Files.' : '';
  $('dialog-label').textContent = operation === 'move' ? 'Destination folder' : 'Name';
  $('dialog-label').hidden = $('destination').hidden = deleting;
  $('destination').required = !deleting && operation !== 'move';
  $('destination').value = operation === 'rename' ? entry.name :
    operation === 'copy' ? 'Copy of ' + entry.name : '';
  $('dialog-error').hidden = true;
  $('confirm-action').textContent = deleting ? 'Delete' : 'Save';
  $('action-dialog').showModal();
  if (deleting) $('cancel-action').focus(); else $('destination').select();
}
$('action-form').addEventListener('submit', async event => {
  event.preventDefault();
  const { operation, entry, path } = activeAction;
  const value = $('destination').value.trim();
  $('confirm-action').disabled = true;
  try {
    if (operation === 'create') {
      await post('/api/folders', { path, name: value });
    } else {
      if (['rename', 'copy'].includes(operation) && /[\\/]/.test(value))
        throw new Error('Use a name without directory separators.');
      const destination = operation === 'move' ? join(value, entry.name) : join(parent(entry.path), value);
      await post('/api/actions', { path: entry.path,
        operation: operation === 'rename' ? 'move' : operation, destination });
    }
    $('action-dialog').close();
    notice({ create: 'Folder created', rename: 'Renamed', copy: 'Copy created',
      move: 'Moved', delete: 'Deleted' }[operation], entry?.name || value, 'done');
    if (operation === 'create') refresh(); else navigate({ item: '' });
  } catch (error) {
    $('dialog-error').hidden = false;
    $('dialog-error').textContent = error.message;
  } finally { $('confirm-action').disabled = false; }
});
$('destination').oninput = () => { $('dialog-error').hidden = true; };
$('cancel-action').onclick = () => $('action-dialog').close();
async function upload(input) {
  const start = state().path || '';
  const known = new Set();
  const items = [...input.files];
  input.value = '';
  if (!items.length) return;
  let completed = 0;
  const failures = [];
  for (const file of items) {
    notice('Uploading ' + (completed + failures.length + 1) + ' of ' + items.length, file.name, 'working');
    let path = start;
    try {
      for (const name of file.webkitRelativePath.split('/').slice(0, -1)) {
        const next = join(path, name);
        if (!known.has(next)) {
          try { await post('/api/folders', { path, name }); }
          catch (error) { if (error.status !== 409) throw error; }
          known.add(next);
        }
        path = next;
      }
      const body = new FormData();
      body.append('file', file, file.name);
      await request(url('/api/upload', { path }), { method: 'POST', body });
      completed++;
    } catch (error) { failures.push(file.name + ' · ' + error.message); }
  }
  notice(failures.length ? completed + ' uploaded, ' + failures.length + ' failed' : 'Upload complete',
    failures[0] || completed + ' ' + (completed === 1 ? 'file' : 'files'), failures.length ? '' : 'done');
  refresh();
}
$('search').onsubmit = event => { event.preventDefault(); navigate({ q: $('query').value.trim(), item: '' }); };
$('query').addEventListener('search', () => { if (!$('query').value) navigate({ q: '', item: '' }); });
$('copy-location').onclick = () => copyLink(state().path || '');
$('new-folder').onclick = () => openDialog('create');
$('list-view').onclick = () => navigate({ view: '' });
$('grid-view').onclick = () => navigate({ view: 'grid' });
for (const [button, input] of [['upload-files', 'file-input'], ['upload-folder', 'folder-input']]) {
  $(button).onclick = () => { $('upload-menu').open = false; $(input).click(); };
  $(input).onchange = () => upload($(input));
}
const details = [...document.querySelectorAll('details')];
for (const panel of details) panel.querySelector('summary').addEventListener('click', () => {
  for (const other of details) if (other !== panel) other.open = false;
});
document.addEventListener('click', event => {
  if (!$('item-menu').contains(event.target) && !event.target.closest('.item-action')) closeMenu();
  for (const panel of details) if (!event.composedPath().includes(panel)) panel.open = false;
});
document.addEventListener('keydown', event => {
  if (event.key !== 'Escape') return;
  if (menuTrigger) closeMenu(true);
  for (const panel of details) if (panel.open) {
    panel.open = false;
    panel.querySelector('summary').focus();
  }
});
window.addEventListener('resize', () => closeMenu());
window.addEventListener('scroll', () => closeMenu(), { passive: true });
window.addEventListener('popstate', refresh);
refresh();
