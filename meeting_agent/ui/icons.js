const paths = {
  search: '<circle cx="10" cy="10" r="6"/><path d="m15 15 5 5"/>',
  arrowDown: '<path d="M12 4v15m-6-6 6 6 6-6"/>',
  quote: '<path d="M4 10h6v7H4v-5c0-4 2-6 5-7m5 5h6v7h-6v-5c0-4 2-6 5-7"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  x: '<path d="m6 6 12 12M18 6 6 18"/>',
  layout: '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M3 9h18M15 9v12"/>',
  panels: '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="M9 4v16"/>',
  library: '<path d="M4 4v16M8 4v16M12 4v16m3-15 5 14"/>',
  settings:
    '<path d="m9 3-.7 2.1-2 .9-2.1-.5-1.7 3 1.5 1.6v2.6l-1.5 1.6 1.7 3 2.1-.5 2 .9L9 20h4l.7-2.3 2-.9 2.2.5 1.7-3-1.6-1.6v-2.6l1.6-1.6-1.7-3-2.2.5-2-.9L13 3z"/><circle cx="11" cy="11.5" r="3"/>',
  headphones:
    '<path d="M3 15v-3a9 9 0 0 1 18 0v3"/><rect x="3" y="12" width="4" height="7" rx="2"/><rect x="17" y="12" width="4" height="7" rx="2"/>',
  chevronDown: '<path d="m6 9 6 6 6-6"/>',
  chevronRight: '<path d="m9 6 6 6-6 6"/>',
  sliders:
    '<path d="M4 7h8m4 0h4M4 17h3m4 0h9"/><circle cx="14" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
  pencil: '<path d="m14 5 5 5M4 20l5-1L20 8a2 2 0 0 0-5-5L4 15z"/>',
  activity: '<path d="M2 12h4l3-8 5 16 3-8h5"/>',
  screen: '<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M12 17v4M8 21h8"/>',
  messages:
    '<path d="M5 4h11a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H9l-5 4v-4H3V6a2 2 0 0 1 2-2z"/><path d="M9 17h6l5 3v-4h1V9"/>',
  paperclip:
    '<path d="m9 13 6.8-6.8a2 2 0 1 1 2.8 2.8l-8.1 8.1a4 4 0 1 1-5.7-5.7l8.5-8.5a5.6 5.6 0 0 1 8 8l-9 9"/>',
  scan: '<path d="M8 3H5a2 2 0 0 0-2 2v3m13-5h3a2 2 0 0 1 2 2v3M3 16v3a2 2 0 0 0 2 2h3m13-5v3a2 2 0 0 1-2 2h-3"/><circle cx="12" cy="12" r="4"/>',
  arrowUp: '<path d="M12 19V5m-6 6 6-6 6 6"/>',
  board: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 8v7m6-7v4"/>',
  calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4m10-4v4M3 11h18"/>',
  users:
    '<circle cx="9" cy="8" r="3"/><path d="M3 20v-2a6 6 0 0 1 12 0v2m2-15a3 3 0 0 1 0 6m1 3a5 5 0 0 1 3 4v2"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  mic: '<rect x="9" y="2" width="6" height="13" rx="3"/><path d="M5 11v1a7 7 0 0 0 14 0v-1m-7 8v3m-4 0h8"/>',
  download: '<path d="M12 3v12m-5-5 5 5 5-5M4 16v4h16v-4"/>',
  square: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
  check: '<path d="m4 12 5 5L20 6"/>',
  checkSquare: '<rect x="3" y="3" width="18" height="18" rx="4"/><path d="m7 12 3 3 7-7"/>',
  question:
    '<circle cx="12" cy="12" r="9"/><path d="M9.6 8.5a2.6 2.6 0 0 1 5 1c0 2-2.6 2-2.6 4m0 3h.01"/>',
  bulb: '<path d="M8 16c0-3-3-4-3-8a7 7 0 0 1 14 0c0 4-3 5-3 8zm1 3h6m-5 3h4"/>',
  flag: '<path d="M5 21V4c5-4 9 4 14 0v10c-5 4-9-4-14 0"/>',
  spark: '<path d="m12 3 2.1 6.9L21 12l-6.9 2.1L12 21l-2.1-6.9L3 12l6.9-2.1z"/>',
  more: '<circle cx="5" cy="12" r="1" fill="currentColor"/><circle cx="12" cy="12" r="1" fill="currentColor"/><circle cx="19" cy="12" r="1" fill="currentColor"/>',
  video: '<rect x="2" y="5" width="14" height="14" rx="2"/><path d="m16 10 6-4v12l-6-4"/>',
  import: '<path d="M12 16V3m-5 5 5-5 5 5M4 16v5h16v-5"/>',
  file: '<path d="M5 3h9l5 5v13H5zM14 3v6h5M8 14h8m-8 3h6"/>',
  book: '<path d="M4 5h8v15H4zM12 5l7-2v15l-7 2"/>',
  plug: '<path d="m9 8 7 7m-9-4 6 6-2 2a4 4 0 0 1-6-6zm3-3 4-4m0 8 4-4M3 21l3-3"/>',
  play: '<path d="m7 4 14 8-14 8z"/>',
  refresh: '<path d="M20 7v5h-5M4 17v-5h5"/><path d="M5 9a8 8 0 0 1 14-4l1 3M4 16l1 3a8 8 0 0 0 14-4"/>',
  chat: '<path d="M4 4h16v13H9l-5 4z"/>',
  layers: '<path d="m12 3 10 5-10 5L2 8zM2 12l10 5 10-5M2 16l10 5 10-5"/>',
  alert: '<path d="m12 3 10 17H2zM12 9v4m0 3v.1"/>',
  trash: '<path d="M3 6h18M9 6V3h6v3M6 6l1 15h10l1-15M10 10v7m4-7v7"/>',
  external: '<path d="M12 4h8v8m0-8L9 15M8 5H4v15h15v-5"/>',
  pin: '<path d="m8 3 11 6-3 2-2 5-3-1-4 7 2-8-4-2 4-4z"/>',
};
function icon(name, size = 18) {
  return (
    '<svg aria-hidden="true" width="' +
    size +
    '" height="' +
    size +
    '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round">' +
    (paths[name] || "") +
    "</svg>"
  );
}
function icons(root = document) {
  root.querySelectorAll("[data-icon]").forEach((e) => {
    e.innerHTML = icon(e.dataset.icon);
    e.removeAttribute("data-icon");
  });
}
