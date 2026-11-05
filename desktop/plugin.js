/**
 * Hermes Time Machine — Desktop Plugin (desktop/plugin.js)
 * Targets @hermes/plugin-sdk ctx.register({ area: 'statusBar.right' | 'rail.right' | 'pane' })
 */

const PLUGIN_ID = 'hermes-time-machine';
const API_BASE = `/api/plugins/${PLUGIN_ID}`;

async function apiCall(ctx, path, options = {}) {
  const cleanPath = path.startsWith('/') ? path : `/${path}`;
  if (ctx && typeof ctx.rest === 'function') {
    const method = (options.method || 'GET').toUpperCase();
    let bodyObj = options.body;
    if (typeof bodyObj === 'string') {
      try {
        bodyObj = JSON.parse(bodyObj);
      } catch (_) {}
    }
    return await ctx.rest(cleanPath, {
      ...options,
      method,
      body: options.body,
      json: bodyObj,
    });
  }

  const res = await fetch(`${API_BASE}${cleanPath}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
    body:
      options.body && typeof options.body !== 'string'
        ? JSON.stringify(options.body)
        : options.body,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.detail || data.error || `HTTP ${res.status}`);
  }
  return data;
}

function formatTime(epochSeconds) {
  if (!epochSeconds) return '';
  const d = new Date(epochSeconds * 1000);
  return d.toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function escapeHtml(str) {
  return String(str || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function renderColorizedDiff(diffText) {
  if (!diffText) {
    return '<div class="tm-empty">No filesystem differences vs current working directory.</div>';
  }
  const lines = String(diffText).split('\n');
  const htmlLines = lines.map((line) => {
    const esc = escapeHtml(line);
    if (line.startsWith('+++') || line.startsWith('---')) {
      return `<div class="tm-diff-line tm-diff-meta">${esc}</div>`;
    }
    if (line.startsWith('@@')) {
      return `<div class="tm-diff-line tm-diff-hunk">${esc}</div>`;
    }
    if (line.startsWith('+')) {
      return `<div class="tm-diff-line tm-diff-add">${esc}</div>`;
    }
    if (line.startsWith('-')) {
      return `<div class="tm-diff-line tm-diff-del">${esc}</div>`;
    }
    return `<div class="tm-diff-line">${esc}</div>`;
  });
  return `<div class="tm-diff-container">${htmlLines.join('')}</div>`;
}

const STUDIO_STYLES = `
  .tm-root {
    font-family: var(--font-sans, system-ui, -apple-system, sans-serif);
    color: var(--text-primary, #e6edf3);
    background: var(--bg-primary, #0d1117);
    height: 100%;
    width: 100%;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  .tm-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 14px;
    border-bottom: 1px solid var(--border-color, #30363d);
    background: var(--bg-secondary, #161b22);
    gap: 8px;
  }
  .tm-title {
    font-size: 13px;
    font-weight: 600;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .tm-actions {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .tm-btn {
    background: #21262d;
    color: #c9d1d9;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 4px 9px;
    font-size: 11px;
    cursor: pointer;
    transition: background 0.15s ease;
  }
  .tm-btn:hover { background: #30363d; }
  .tm-btn-primary {
    background: #238636;
    border-color: rgba(240,246,252,0.1);
    color: #ffffff;
  }
  .tm-btn-primary:hover { background: #2ea043; }
  .tm-btn-warn {
    background: #9e6a03;
    border-color: rgba(240,246,252,0.1);
    color: #ffffff;
  }
  .tm-btn-warn:hover { background: #bb8009; }
  .tm-btn-branch {
    background: #1f6feb;
    border-color: rgba(240,246,252,0.1);
    color: #ffffff;
  }
  .tm-btn-branch:hover { background: #388bfd; }
  .tm-body {
    display: grid;
    grid-template-columns: 320px 1fr;
    flex: 1;
    min-height: 0;
  }
  .tm-list {
    border-right: 1px solid var(--border-color, #30363d);
    overflow-y: auto;
    padding: 8px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .tm-card {
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 9px 10px;
    background: #161b22;
    cursor: pointer;
    transition: border-color 0.15s ease, background 0.15s ease;
  }
  .tm-card:hover, .tm-card.active {
    border-color: #58a6ff;
    background: #1c2128;
  }
  .tm-card-top {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 12px;
    font-weight: 600;
    margin-bottom: 4px;
  }
  .tm-badge {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 10px;
    padding: 1px 6px;
    border-radius: 4px;
    background: rgba(88,166,255,0.15);
    color: #58a6ff;
  }
  .tm-sub {
    font-size: 11px;
    color: #8b949e;
    word-break: break-all;
    margin-bottom: 6px;
  }
  .tm-detail {
    display: flex;
    flex-direction: column;
    min-height: 0;
    overflow: hidden;
  }
  .tm-detail-bar {
    padding: 10px 14px;
    border-bottom: 1px solid #30363d;
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
    background: #13181f;
  }
  .tm-diff-container, .tm-diff-scroll {
    flex: 1;
    overflow: auto;
    padding: 10px 14px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 11.5px;
    line-height: 1.5;
  }
  .tm-diff-line { white-space: pre-wrap; word-break: break-all; padding: 1px 4px; }
  .tm-diff-add { background: rgba(46, 160, 67, 0.18); color: #7ee787; }
  .tm-diff-del { background: rgba(248, 81, 73, 0.18); color: #ffa198; }
  .tm-diff-hunk { color: #79c0ff; background: rgba(56, 139, 253, 0.1); }
  .tm-diff-meta { color: #8b949e; font-weight: 600; }
  .tm-toast {
    margin: 8px 14px 0;
    padding: 7px 11px;
    border-radius: 6px;
    font-size: 11.5px;
    background: rgba(56, 139, 253, 0.15);
    border: 1px solid #388bfd;
    color: #c9d1d9;
  }
  .tm-empty {
    padding: 20px;
    text-align: center;
    color: #8b949e;
    font-size: 12px;
  }
`;

function mountTimeMachineDOM(container, ctx, mode = 'studio') {
  let disposed = false;
  let state = {
    snapshots: [],
    selectedId: null,
    selectedSnapshot: null,
    diffText: '',
    loading: false,
    toast: null,
    sessionFilter: '',
  };

  function setToast(msg) {
    if (disposed) return;
    state.toast = msg;
    render();
    setTimeout(() => {
      if (!disposed && state.toast === msg) {
        state.toast = null;
        render();
      }
    }, 5000);
  }

  async function loadSnapshots(selectFirst = false) {
    if (disposed) return;
    state.loading = true;
    render();
    try {
      const activeSession =
        state.sessionFilter ||
        (ctx && ctx.session && ctx.session.id) ||
        '';
      const q = activeSession
        ? `?session_id=${encodeURIComponent(activeSession)}&limit=50`
        : '?limit=50';
      const data = await apiCall(ctx, `/snapshots${q}`);
      state.snapshots = data.snapshots || [];
      if (state.snapshots.length === 0 && activeSession && !state.sessionFilter) {
        const allData = await apiCall(ctx, '/snapshots?limit=50');
        state.snapshots = allData.snapshots || [];
      }
      if ((selectFirst || !state.selectedId) && state.snapshots.length > 0) {
        await selectSnapshot(state.snapshots[0].id);
      }
    } catch (err) {
      setToast(`Failed to load snapshots: ${err.message}`);
    } finally {
      state.loading = false;
      render();
    }
  }

  async function selectSnapshot(snapshotId) {
    if (disposed) return;
    state.selectedId = snapshotId;
    state.selectedSnapshot =
      state.snapshots.find((s) => s.id === snapshotId) || null;
    state.diffText = 'Loading diff...';
    render();
    try {
      const detail = await apiCall(
        ctx,
        `/snapshots/${encodeURIComponent(snapshotId)}?include_diff=true`
      );
      state.selectedSnapshot = detail.snapshot || state.selectedSnapshot;
      state.diffText = detail.diff || '';
    } catch (err) {
      state.diffText = `Error loading diff: ${err.message}`;
    }
    render();
  }

  async function takeManualSnapshot() {
    try {
      setToast('Creating checkpoint...');
      await apiCall(ctx, '/snapshots', {
        method: 'POST',
        body: JSON.stringify({
          session_id: (ctx && ctx.session && ctx.session.id) || undefined,
          reason: 'manual_desktop_snapshot',
        }),
      });
      setToast('Snapshot captured.');
      await loadSnapshots(true);
    } catch (err) {
      setToast(`Snapshot failed: ${err.message}`);
    }
  }

  async function triggerRestore(snapshotId, rewindConversation = false) {
    const label = rewindConversation
      ? 'Restore working tree files AND rewind conversation messages in state.db?'
      : 'Restore working tree files to this snapshot? (A safety checkpoint is created automatically first)';
    if (typeof window !== 'undefined' && !window.confirm(label)) {
      return;
    }
    try {
      const res = await apiCall(
        ctx,
        `/snapshots/${encodeURIComponent(snapshotId)}/restore`,
        {
          method: 'POST',
          body: JSON.stringify({
            restore_files: true,
            rewind_conversation: rewindConversation,
          }),
        }
      );
      setToast(
        rewindConversation
          ? `Restored files & rewound ${res.messages_deactivated || 0} message(s).`
          : `Restored files to ${(res.checkpoint_hash || '').slice(0, 8)}.`
      );
      await loadSnapshots(false);
      await selectSnapshot(snapshotId);
    } catch (err) {
      setToast(`Restore error: ${err.message}`);
    }
  }

  async function triggerBranch(snapshotId) {
    const customTitle =
      typeof window !== 'undefined'
        ? window.prompt('Optional title for branched session:', '')
        : '';
    if (customTitle === null) return;
    try {
      const res = await apiCall(
        ctx,
        `/snapshots/${encodeURIComponent(snapshotId)}/branch`,
        {
          method: 'POST',
          body: JSON.stringify({
            branch_title: customTitle || undefined,
            restore_files: true,
          }),
        }
      );
      setToast(
        `Forked session ${res.new_session_id} (${res.copied_messages} messages).`
      );
      await loadSnapshots(false);
    } catch (err) {
      setToast(`Branch error: ${err.message}`);
    }
  }

  function render() {
    if (disposed || !container) return;
    const isRail = mode === 'rail';
    const cardsHtml =
      state.snapshots.length === 0
        ? `<div class="tm-empty">${
            state.loading ? 'Loading snapshots...' : 'No snapshots recorded yet.'
          }</div>`
        : state.snapshots
            .map((s) => {
              const active = s.id === state.selectedId ? 'active' : '';
              const shortHash = (s.checkpoint_hash || '').slice(0, 8) || 'no-git';
              const stats =
                s.files_changed || s.insertions || s.deletions
                  ? `<span>${s.files_changed || 0}f +${s.insertions || 0}/-${
                      s.deletions || 0
                    }</span>`
                  : '';
              return `
                <div class="tm-card ${active}" data-snap-id="${escapeHtml(s.id)}">
                  <div class="tm-card-top">
                    <span>${escapeHtml(s.tool_name)}</span>
                    <span class="tm-badge">${escapeHtml(shortHash)}</span>
                  </div>
                  <div class="tm-sub">${escapeHtml(
                    s.tool_args_summary || s.reason || s.working_dir
                  )}</div>
                  <div style="display:flex;justify-content:space-between;align-items:center;font-size:11px;color:#8b949e;">
                    <span>${escapeHtml(formatTime(s.created_at))} · msg#${
                s.message_id_watermark ?? '—'
              }</span>
                    ${stats}
                  </div>
                  ${
                    isRail
                      ? `<div style="display:flex;gap:6px;margin-top:8px;">
                          <button class="tm-btn tm-btn-warn" data-action="restore-files" data-id="${escapeHtml(
                            s.id
                          )}">Restore</button>
                          <button class="tm-btn tm-btn-warn" data-action="restore-all" data-id="${escapeHtml(
                            s.id
                          )}">Rewind All</button>
                          <button class="tm-btn tm-btn-branch" data-action="branch" data-id="${escapeHtml(
                            s.id
                          )}">Branch</button>
                        </div>`
                      : ''
                  }
                </div>
              `;
            })
            .join('');

    const sel = state.selectedSnapshot;
    const detailHtml = sel
      ? `
        <div class="tm-detail-bar">
          <div>
            <strong>${escapeHtml(sel.tool_name)}</strong>
            <span class="tm-badge" style="margin-left:8px;">${escapeHtml(
              (sel.checkpoint_hash || '').slice(0, 12)
            )}</span>
            <span style="font-size:11px;color:#8b949e;margin-left:8px;">
              Session: ${escapeHtml(sel.session_id)} · Watermark msg #${
          sel.message_id_watermark ?? '—'
        }
            </span>
          </div>
          <div class="tm-actions">
            <button class="tm-btn tm-btn-warn" id="tm-restore-files-btn">Restore Files</button>
            <button class="tm-btn tm-btn-warn" id="tm-restore-all-btn">Rewind Files + Chat</button>
            <button class="tm-btn tm-btn-branch" id="tm-branch-btn">Fork Session Here</button>
          </div>
        </div>
        <div class="tm-diff-scroll">
          ${renderColorizedDiff(state.diffText)}
        </div>
      `
      : `<div class="tm-empty">Select a snapshot on the left to inspect its filesystem diff and restore/branch controls.</div>`;

    container.innerHTML = `
      <style>${STUDIO_STYLES}</style>
      <div class="tm-root">
        <div class="tm-header">
          <div class="tm-title">
            <span>⏱️ Time Machine</span>
            <span class="tm-badge">${state.snapshots.length}</span>
          </div>
          <div class="tm-actions">
            <button class="tm-btn tm-btn-primary" id="tm-snap-now-btn">+ Snap Now</button>
            <button class="tm-btn" id="tm-refresh-btn">↻</button>
          </div>
        </div>
        ${state.toast ? `<div class="tm-toast">${escapeHtml(state.toast)}</div>` : ''}
        ${
          isRail
            ? `<div class="tm-list" style="border-right:none;flex:1;">${cardsHtml}</div>
               ${
                 sel
                   ? `<div style="border-top:1px solid #30363d;max-height:42%;overflow:auto;" class="tm-diff-scroll">
                        ${renderColorizedDiff(state.diffText)}
                      </div>`
                   : ''
               }`
            : `<div class="tm-body">
                 <div class="tm-list">${cardsHtml}</div>
                 <div class="tm-detail">${detailHtml}</div>
               </div>`
        }
      </div>
    `;

    const refreshBtn = container.querySelector('#tm-refresh-btn');
    if (refreshBtn) refreshBtn.onclick = () => loadSnapshots(false);

    const snapNowBtn = container.querySelector('#tm-snap-now-btn');
    if (snapNowBtn) snapNowBtn.onclick = () => takeManualSnapshot();

    container.querySelectorAll('.tm-card').forEach((card) => {
      card.onclick = (e) => {
        const actionBtn = e.target.closest('button[data-action]');
        const snapId = card.getAttribute('data-snap-id');
        if (actionBtn) {
          e.stopPropagation();
          const act = actionBtn.getAttribute('data-action');
          const id = actionBtn.getAttribute('data-id');
          if (act === 'restore-files') triggerRestore(id, false);
          if (act === 'restore-all') triggerRestore(id, true);
          if (act === 'branch') triggerBranch(id);
          return;
        }
        if (snapId) selectSnapshot(snapId);
      };
    });

    const restoreFilesBtn = container.querySelector('#tm-restore-files-btn');
    if (restoreFilesBtn && sel) {
      restoreFilesBtn.onclick = () => triggerRestore(sel.id, false);
    }

    const restoreAllBtn = container.querySelector('#tm-restore-all-btn');
    if (restoreAllBtn && sel) {
      restoreAllBtn.onclick = () => triggerRestore(sel.id, true);
    }

    const branchBtn = container.querySelector('#tm-branch-btn');
    if (branchBtn && sel) {
      branchBtn.onclick = () => triggerBranch(sel.id);
    }
  }

  loadSnapshots(true);
  return () => {
    disposed = true;
  };
}

function mountStatusBarShield(container, ctx) {
  let disposed = false;
  let count = 0;
  let latestTool = 'ready';

  async function poll() {
    if (disposed) return;
    try {
      const status = await apiCall(ctx, '/status');
      count = status.snapshot_count || 0;
      latestTool = status.latest_snapshot?.tool_name || 'ready';
      render();
    } catch (_) {}
  }

  function render() {
    if (disposed || !container) return;
    container.innerHTML = `
      <button
        style="background:transparent;border:none;color:inherit;cursor:pointer;display:inline-flex;align-items:center;gap:6px;font-size:12px;padding:2px 6px;"
        title="Hermes Time Machine — Click to open timeline"
      >
        <span>🛡️ TM: <strong>${count}</strong></span>
        <span style="opacity:0.75;">(${escapeHtml(latestTool)})</span>
      </button>
    `;
    const btn = container.querySelector('button');
    if (btn && ctx) {
      btn.onclick = () => {
        if (typeof ctx.openPane === 'function') {
          ctx.openPane('time-machine-studio');
        } else if (typeof ctx.openView === 'function') {
          ctx.openView('time-machine-studio');
        } else if (typeof ctx.toggleRail === 'function') {
          ctx.toggleRail('time-machine-rail');
        }
      };
    }
  }

  render();
  poll();
  const timer = setInterval(poll, 8000);
  return () => {
    disposed = true;
    clearInterval(timer);
  };
}

function registerSlot(ctx, spec) {
  if (typeof ctx.register === 'function') {
    ctx.register({
      id: spec.id,
      area: spec.area,
      label: spec.label,
      title: spec.label,
      icon: spec.icon,
      priority: spec.priority,
      mount: (el) => spec.mount(el, ctx),
      render: (el) => {
        if (el && typeof el === 'object' && 'innerHTML' in el) {
          return spec.mount(el, ctx);
        }
        return null;
      },
    });
    return;
  }

  if (spec.area === 'statusBar.right' && typeof ctx.registerStatusBarItem === 'function') {
    ctx.registerStatusBarItem({
      id: spec.id,
      position: 'right',
      priority: spec.priority || 90,
      mount: (el) => spec.mount(el, ctx),
    });
  } else if (spec.area === 'rail.right' && typeof ctx.registerRailItem === 'function') {
    ctx.registerRailItem({
      id: spec.id,
      title: spec.label,
      icon: spec.icon,
      mount: (el) => spec.mount(el, ctx),
    });
  } else if (spec.area === 'pane' && typeof ctx.registerView === 'function') {
    ctx.registerView({
      id: spec.id,
      title: spec.label,
      icon: spec.icon,
      mount: (el) => spec.mount(el, ctx),
    });
  }
}

export default {
  id: PLUGIN_ID,
  name: 'Hermes Time Machine',
  version: '0.2.0',

  activate(ctx) {
    registerSlot(ctx, {
      id: 'time-machine-shield',
      area: 'statusBar.right',
      label: 'Time Machine',
      icon: 'shield',
      priority: 90,
      mount: mountStatusBarShield,
    });

    registerSlot(ctx, {
      id: 'time-machine-rail',
      area: 'rail.right',
      label: 'Time Machine',
      icon: 'history',
      priority: 80,
      mount: (el, c) => mountTimeMachineDOM(el, c, 'rail'),
    });

    registerSlot(ctx, {
      id: 'time-machine-studio',
      area: 'pane',
      label: 'Time Machine Studio',
      icon: 'history',
      priority: 70,
      mount: (el, c) => mountTimeMachineDOM(el, c, 'studio'),
    });
  },
};
