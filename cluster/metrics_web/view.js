'use strict';
const latest = new Map();
const connection = document.getElementById('connection');
const hosts = document.getElementById('hosts');
const stream = new EventSource('/events');
const labels = {load1:'Load (1m)',load5:'Load (5m)',mem_used_gb:'Memory used (GiB)',swap_used_gb:'Swap used (GiB)',disk_free_gb:'Disk free (GiB)',queue_depth:'Queue depth',tokens_today:'Tokens today',codex_weekly_pct:'Codex weekly %',gemini_usd_today:'Gemini $ today'};
function paint() {
  hosts.replaceChildren();
  for (const host of ['mini', 'nucbox']) {
    const data = latest.get(host);
    const section = document.createElement('section');
    const heading = document.createElement('h2');
    const stale = !data || Date.now()/1000 - data.ts > 6;
    heading.textContent = host + (stale ? ' — stale / unknown' : ' — live');
    section.append(heading);
    const table = document.createElement('table');
    for (const [key, label] of Object.entries(labels)) {
      const row = table.insertRow();
      row.insertCell().textContent = label;
      row.insertCell().textContent = data?.[key] ?? 'unknown';
    }
    for (const p of ['codex','gemini','cursor','claude','minimax']) {
      const row = table.insertRow();
      row.insertCell().textContent = p + ' active lanes';
      row.insertCell().textContent = data?.lanes?.[p] ?? 'unknown';
    }
    section.append(table);
    hosts.append(section);
  }
}
stream.addEventListener('metrics', event => {
  const data = JSON.parse(event.data);
  latest.set(data.host, data);
  paint();
});
stream.onopen = () => { connection.textContent = 'Connected'; };
stream.onerror = () => { connection.textContent = 'Disconnected — reconnecting; values may be stale'; };
setInterval(paint, 1000); // Only ages the display; never fetches or polls.
paint();
