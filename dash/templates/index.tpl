% import json
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>G-ENV</title>
  <link rel="icon" type="image/svg+xml" href="/static/favicon.svg">
  <link rel="stylesheet" href="/static/penman-base.css">
  <link rel="stylesheet" href="/static/dash.css">
</head>
<body>
  <header class="site-header">
    <span class="brand">G-ENV</span>
    <nav>
      <a href="#workspaces">workspaces</a>
      <a href="#keys">keys</a>
      <a href="#mcps">mcps</a>
      <a href="#env">env</a>
      <a href="#config">config</a>
      % if command_url:
      <a href="{{command_url}}" rel="noopener">command ↗</a>
      % end
    </nav>
  </header>

  <main class="site-main">
    <h1>G-ENV <em>dashboard</em></h1>

    <!-- WORKSPACES -->
    <section id="workspaces" class="section">
      <h2>Workspaces</h2>
      <div class="ws-grid">
        % for w in workspaces:
        <div class="ws-card {{'ws-active' if w['active'] else ''}}" data-ws="{{w['ws']}}" style="border-left: 3px solid {{w['accent']}}; background: var(--bg);">
          <div class="ws-card-top">
            <div class="ws-num" style="color: color-mix(in oklch, {{w['accent']}} 60%, var(--fg-faint));">{{w['ws']}}</div>
            <input type="color" class="ws-color-pick" value="{{w['accent']}}" data-field="accent" data-ws="{{w['ws']}}" title="workspace colour">
          </div>
          <input class="ws-label-input" value="{{w['label']}}" data-field="label" data-ws="{{w['ws']}}">
          <select class="ws-ssh-select" data-field="ssh_host" data-ws="{{w['ws']}}">
            <option value="">Local</option>
            % for alias, name in ssh_names.items():
            <option value="{{alias}}" {{'selected' if w['ssh_host'] == alias else ''}}>{{name}}</option>
            % end
          </select>
          <input class="ws-path-input" value="{{w['start_path']}}" placeholder="start path…" data-field="start_path" data-ws="{{w['ws']}}">
          % if w['active']:
          <div class="ws-wins" style="color: {{w['accent']}};">{{w['windows']}} window{{'s' if w['windows'] != 1 else ''}}</div>
          % end
        </div>
        % end
      </div>
      <p class="sublabel">Changes take effect on next CMD+N or new terminal.</p>

      % if ssh_config:
      <h2>SSH hosts</h2>
      <table class="bind-table">
        <thead><tr><th>alias</th><th>hostname</th><th>user</th><th>key</th></tr></thead>
        <tbody>
          % for h in ssh_config:
          % if h['host'] != '*':
          <tr>
            <td class="key mono">{{h['host']}}</td>
            <td class="action mono">{{h['options'].get('hostname', '')}}</td>
            <td class="mono muted">{{h['options'].get('user', '')}}</td>
            <td class="mono muted">{{h['options'].get('identityfile', '').replace('/Users/gabriel/', '~/')}}</td>
          </tr>
          % end
          % end
        </tbody>
      </table>
      % end
    </section>

    <!-- KEYBINDINGS -->
    <section id="keys" class="section">
      <h2>Aerospace keybindings</h2>
      <table class="bind-table">
        <tbody>
          % for b in aerospace:
          <tr>
            <td class="key"><kbd>{{b['key']}}</kbd></td>
            <td class="action">{{b['action']}}</td>
          </tr>
          % end
        </tbody>
      </table>

      <p class="callout">cmd+shift+4/5 are used for window moving. If macOS screenshot shortcuts are still on those keys, disable them in <strong>System Settings → Keyboard Shortcuts → Screenshots</strong>.</p>

      <h2>Ghostty keybindings</h2>
      % if ghostty:
      <table class="bind-table">
        <tbody>
          % for b in ghostty:
          <tr>
            <td class="key"><kbd>{{b['key']}}</kbd></td>
            <td class="action">{{b['action']}}</td>
          </tr>
          % end
        </tbody>
      </table>
      % else:
      <p class="muted">No custom keybinds configured.</p>
      % end

      <h2>tmux keybindings</h2>
      % if tmux:
      <table class="bind-table">
        <tbody>
          % for b in tmux:
          <tr>
            <td class="key"><kbd>{{b['key']}}</kbd></td>
            <td class="action">{{b['action']}}</td>
          </tr>
          % end
        </tbody>
      </table>
      % else:
      <p class="muted">No bind entries found.</p>
      % end
    </section>

    <!-- MCPs -->
    <section id="mcps" class="section">
      <h2>MCP servers</h2>
      % if mcps:
      <table class="bind-table">
        <thead>
          <tr><th>name</th><th>command</th><th>args</th></tr>
        </thead>
        <tbody>
          % for m in mcps:
          <tr>
            <td class="key">{{m['name']}}</td>
            <td class="action mono">{{m['command']}}</td>
            <td class="mono muted">{{m['args']}}</td>
          </tr>
          % end
        </tbody>
      </table>
      % else:
      <p class="muted">No MCP servers configured in settings.json.</p>
      % end

      <h2>Claude permissions (allowed)</h2>
      % allowed = claude_settings.get('permissions', {}).get('allow', [])
      % if allowed:
      <ul class="perm-list">
        % for p in allowed:
        <li class="mono">{{p}}</li>
        % end
      </ul>
      % else:
      <p class="muted">None.</p>
      % end
    </section>

    <!-- ENV PASSPORT -->
    <section id="env" class="section">
      <h2>.env passport <button class="reveal-btn" id="reveal-toggle">reveal all</button></h2>
      % if env_passport:
      <table class="bind-table" id="env-table">
        <tbody>
          % for e in env_passport:
          <tr>
            <td class="key mono">{{e['key']}}</td>
            <td class="action">
              % if e['set']:
              <span class="dot dot-green"></span>
              <span class="masked" data-key="{{e['key']}}">{{e['masked']}}</span>
              % else:
              <span class="dot dot-red"></span> <span class="muted">(empty)</span>
              % end
            </td>
          </tr>
          % end
        </tbody>
      </table>
      % else:
      <p class="muted">No .env file found.</p>
      % end
    </section>

    <!-- RAW CONFIGS -->
    <section id="config" class="section">
      <h2>Claude settings.json</h2>
      <pre class="codeblock">{{!json.dumps(claude_settings, indent=2)}}</pre>
    </section>

  </main>
<script>
let envRevealed = false;
let envData = null;
document.getElementById('reveal-toggle').addEventListener('click', async function() {
  if (!envData) {
    const r = await fetch('/api/env');
    envData = await r.json();
  }
  envRevealed = !envRevealed;
  this.textContent = envRevealed ? 'hide' : 'reveal all';
  document.querySelectorAll('[data-key]').forEach(el => {
    const key = el.dataset.key;
    el.textContent = envRevealed ? (envData[key] || '?') : el.dataset.masked || '••••••••••••';
    if (!el.dataset.masked) el.dataset.masked = '••••••••••••';
  });
});

function saveWs(ws, field, value) {
  fetch('/api/workspace', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ws, [field]: value})
  });
}

document.addEventListener('change', e => {
  const el = e.target;
  if (el.dataset.field) saveWs(el.dataset.ws, el.dataset.field, el.value);
});
document.addEventListener('blur', e => {
  const el = e.target;
  if (el.classList.contains('ws-label-input')) saveWs(el.dataset.ws, 'label', el.value);
}, true);
</script>
</body>
</html>
