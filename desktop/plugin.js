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

function getBadgeColor(s) {
  if (s.self_heal_candidate || s.status === 'error') return '#ef4444';
  if (s.tool_name === 'pre_restore_safety_backup') return '#10b981';
  if (['write_file', 'patch', 'edit_file', 'create_file'].includes(s.tool_name)) return '#3b82f6';
  if (['terminal', 'bash', 'execute_code', 'run_command'].includes(s.tool_name)) return '#8b5cf6';
  if (s.tool_name === 'time_machine_branch' || s.parent_session_id) return '#f59e0b';
  return '#6b7280';
}

function renderColorizedDiff(diffText) {
  if (!diffText || !diffText.trim()) {
    return '<div class="tm-empty">No filesystem differences vs current working directory.</div>';
  }
  const lines = String(diffText).split('\n');
  const htmlLines = lines.map((line, idx) => {
    const esc = escapeHtml(line);
    const lineNum = `<span class="tm-diff-num">${idx + 1}</span>`;
    if (line.startsWith('+++') || line.startsWith('---')) {
      return `<div class="tm-diff-line tm-diff-meta">${lineNum}${esc}</div>`;
    }
    if (line.startsWith('@@')) {
      return `<div class="tm-diff-line tm-diff-hunk">${lineNum}${esc}</div>`;
    }
    if (line.startsWith('+')) {
      return `<div class="tm-diff-line tm-diff-add">${lineNum}${esc}</div>`;
    }
    if (line.startsWith('-')) {
      return `<div class="tm-diff-line tm-diff-del">${lineNum}${esc}</div>`;
    }
    return `<div class="tm-diff-line">${lineNum}${esc}</div>`;
  });
  return `<div class="tm-diff-content">${htmlLines.join('')}</div>`;
}

function renderSideBySideDiff(diffText) {
  if (!diffText || !diffText.trim()) {
    return '<div class="tm-empty">No filesystem differences to split.</div>';
  }
  const lines = String(diffText).split('\n');
  const rows = [];
  let leftNum = 1;
  let rightNum = 1;

  for (const line of lines) {
    if (line.startsWith('+++') || line.startsWith('---') || line.startsWith('diff --git') || line.startsWith('index ')) {
      continue;
    }
    if (line.startsWith('@@')) {
      rows.push(`
        <tr class="tm-split-hunk">
          <td colspan="4">${escapeHtml(line)}</td>
        </tr>
      `);
      continue;
    }
    if (line.startsWith('-')) {
      rows.push(`
        <tr class="tm-split-row">
          <td class="tm-split-num tm-split-del">${leftNum++}</td>
          <td class="tm-split-code tm-split-del">${escapeHtml(line.slice(1))}</td>
          <td class="tm-split-num tm-split-empty"></td>
          <td class="tm-split-code tm-split-empty"></td>
        </tr>
      `);
    } else if (line.startsWith('+')) {
      rows.push(`
        <tr class="tm-split-row">
          <td class="tm-split-num tm-split-empty"></td>
          <td class="tm-split-code tm-split-empty"></td>
          <td class="tm-split-num tm-split-add">${rightNum++}</td>
          <td class="tm-split-code tm-split-add">${escapeHtml(line.slice(1))}</td>
        </tr>
      `);
    } else {
      rows.push(`
        <tr class="tm-split-row">
          <td class="tm-split-num">${leftNum++}</td>
          <td class="tm-split-code">${escapeHtml(line.startsWith(' ') ? line.slice(1) : line)}</td>
          <td class="tm-split-num">${rightNum++}</td>
          <td class="tm-split-code">${escapeHtml(line.startsWith(' ') ? line.slice(1) : line)}</td>
        </tr>
      `);
    }
  }

  return `
    <table class="tm-split-table">
      <thead>
        <tr>
          <th colspan="2" style="width:50%;border-right:1px solid #30363d;">Original State</th>
          <th colspan="2" style="width:50%;">Current Working Tree</th>
        </tr>
      </thead>
      <tbody>
        ${rows.join('')}
      </tbody>
    </table>
  `;
}

const STYLES = `
  .tm-root {
    display: flex;
    flex-direction: column;
    height: 100%;
    width: 100%;
    background: #0d1117;
    color: #c9d1d9;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    overflow: hidden;
    box-sizing: border-box;
  }
  .tm-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 14px;
    border-bottom: 1px solid #30363d;
    background: #161b22;
    gap: 8px;
    flex-shrink: 0;
  }
  .tm-title {
    font-weight: 600;
    font-size: 13px;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .tm-badge {
    background: #21262d;
    border: 1px solid #30363d;
    border-radius: 12px;
    padding: 2px 8px;
    font-size: 11px;
    color: #8b949e;
  }
  .tm-badge-amber {
    background: rgba(245, 158, 11, 0.15);
    border-color: #f59e0b;
    color: #fbbf24;
    font-weight: 600;
  }
  .tm-badge-blue {
    background: rgba(56, 139, 253, 0.15);
    border-color: #388bfd;
    color: #58a6ff;
    font-weight: 500;
  }
  .tm-summary-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 6px 12px;
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 6px;
    margin-bottom: 10px;
    font-size: 11.5px;
  }
  
  .tm-scrubber-container {
    padding: 8px 14px;
    background: #0d1117;
    border-bottom: 1px solid #21262d;
    display: flex;
    align-items: center;
    gap: 8px;
    overflow-x: auto;
  }
  .tm-scrubber-track {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: max-content;
    padding: 4px 2px;
  }
  .tm-scrub-node {
    width: 14px;
    height: 14px;
    border-radius: 50%;
    border: 2px solid #30363d;
    background: #21262d;
    cursor: pointer;
    transition: transform 0.15s, border-color 0.15s, box-shadow 0.15s;
    position: relative;
    flex-shrink: 0;
  }
  .tm-scrub-node:hover {
    transform: scale(1.4);
  }
  .tm-scrub-node.active {
    border-color: #58a6ff;
    box-shadow: 0 0 8px rgba(88, 166, 255, 0.7);
  }
  .tm-scrub-line {
    height: 2px;
    width: 16px;
    background: #30363d;
    flex-shrink: 0;
  }
  .tm-scrub-tooltip {
    position: absolute;
    bottom: 22px;
    left: 50%;
    transform: translateX(-50%);
    background: #161b22;
    border: 1px solid #30363d;
    padding: 4px 8px;
    border-radius: 4px;
    font-size: 10px;
    white-space: nowrap;
    display: none;
    z-index: 100;
    pointer-events: none;
    color: #c9d1d9;
    box-shadow: 0 4px 12px rgba(0,0,0,0.5);
  }
  .tm-scrub-node:hover .tm-scrub-tooltip {
    display: block;
  }
  .tm-btn-retry {
    background: rgba(245, 158, 11, 0.2);
    border: 1px solid #f59e0b;
    color: #fbbf24;
    font-weight: 600;
  }
  .tm-btn-retry:hover {
    background: rgba(245, 158, 11, 0.35);
  }

  .tm-filter-bar {
    padding: 6px 14px;
    background: #13181f;
    border-bottom: 1px solid #21262d;
    display: flex;
    gap: 8px;
    align-items: center;
  }
  .tm-input {
    flex: 1;
    background: #0d1117;
    border: 1px solid #30363d;
    border-radius: 6px;
    color: #c9d1d9;
    padding: 4px 8px;
    font-size: 11.5px;
    outline: none;
  }
  .tm-input:focus { border-color: #58a6ff; }
  .tm-body {
    display: flex;
    flex: 1;
    overflow: hidden;
  }
  .tm-sidebar {
    width: 320px;
    min-width: 260px;
    max-width: 440px;
    border-right: 1px solid #30363d;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    background: #0d1117;
  }
  .tm-rail-root .tm-sidebar {
    width: 100%;
    max-width: 100%;
    border-right: none;
  }
  .tm-card-list {
    flex: 1;
    overflow-y: auto;
    padding: 8px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .tm-card {
    background: #161b22;
    border: 1px solid #21262d;
    border-radius: 6px;
    padding: 8px 10px;
    cursor: pointer;
    transition: all 0.15s ease;
    display: flex;
    flex-direction: column;
    gap: 4px;
    position: relative;
    border-left: 3px solid #30363d;
  }
  .tm-card:hover {
    background: #1c2128;
    border-color: #388bfd;
  }
  .tm-card.active {
    background: #1f242c;
    border-color: #58a6ff;
    box-shadow: 0 0 0 1px #58a6ff;
  }
  .tm-card-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    font-size: 12px;
    font-weight: 600;
  }
  .tm-sub {
    font-size: 11px;
    color: #8b949e;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .tm-btn {
    background: #21262d;
    border: 1px solid #30363d;
    color: #c9d1d9;
    border-radius: 5px;
    padding: 3px 8px;
    font-size: 11px;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 4px;
    transition: background 0.15s;
  }
  .tm-btn:hover { background: #30363d; }
  .tm-btn-primary { background: #238636; border-color: #2ea043; color: #fff; }
  .tm-btn-primary:hover { background: #2ea043; }
  .tm-btn-warn { background: #b62324; border-color: #da3633; color: #fff; }
  .tm-btn-warn:hover { background: #da3633; }
  .tm-btn-branch { background: #8957e5; border-color: #a371f7; color: #fff; }
  .tm-btn-branch:hover { background: #a371f7; }
  .tm-detail-pane {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    background: #0d1117;
  }
  .tm-detail-bar {
    padding: 10px 14px;
    border-bottom: 1px solid #30363d;
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
    background: #161b22;
  }
  .tm-diff-container {
    flex: 1;
    overflow: auto;
    padding: 10px 14px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 11.5px;
    line-height: 1.5;
  }
  .tm-diff-content { display: flex; flex-direction: column; }
  .tm-diff-line {
    white-space: pre-wrap;
    word-break: break-all;
    padding: 1px 4px;
    display: flex;
    gap: 8px;
  }
  .tm-diff-num {
    width: 32px;
    color: #484f58;
    text-align: right;
    user-select: none;
    flex-shrink: 0;
  }
  .tm-diff-add { background: rgba(46, 160, 67, 0.18); color: #7ee787; }
  .tm-diff-del { background: rgba(248, 81, 73, 0.18); color: #ffa198; }
  .tm-diff-hunk { color: #79c0ff; background: rgba(56, 139, 253, 0.1); font-weight: 600; }
  .tm-diff-meta { color: #8b949e; font-weight: 600; }
  .tm-file-card {
    border: 1px solid #30363d;
    border-radius: 6px;
    margin-bottom: 12px;
    background: #161b22;
    overflow: hidden;
  }
  .tm-file-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 6px 12px;
    background: #21262d;
    border-bottom: 1px solid #30363d;
    font-weight: 600;
    font-size: 12px;
  }
  .tm-file-stats {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 11px;
    color: #8b949e;
  }
  .tm-split-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 11px;
    line-height: 1.4;
  }
  .tm-split-table th {
    padding: 4px 8px;
    background: #21262d;
    border-bottom: 1px solid #30363d;
    text-align: left;
    font-weight: 600;
  }
  .tm-split-row:hover { background: #161b22; }
  .tm-split-num {
    width: 32px;
    color: #484f58;
    text-align: right;
    padding: 1px 6px;
    user-select: none;
    border-right: 1px solid #21262d;
  }
  .tm-split-code {
    padding: 1px 8px;
    white-space: pre-wrap;
    word-break: break-all;
    font-family: monospace;
  }
  .tm-split-del { background: rgba(248, 81, 73, 0.15); color: #ffa198; }
  .tm-split-add { background: rgba(46, 160, 67, 0.15); color: #7ee787; }
  .tm-split-empty { background: rgba(0, 0, 0, 0.2); }
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
    padding: 24px;
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
    files: [],
    loading: false,
    toast: null,
    sessionFilter: '',
    filterText: '',
    viewMode: 'unified', // 'unified' | 'split'
    compareTo: '',       // '' (parent) | 'WORKING_TREE' | snapshot ID
    storage: null,
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
      const [data, statusData] = await Promise.all([
        apiCall(ctx, `/snapshots${q}`),
        apiCall(ctx, `/status${activeSession ? `?session_id=${encodeURIComponent(activeSession)}` : ''}`).catch(() => null),
      ]);
      state.snapshots = data.snapshots || [];
      if (statusData) state.storage = statusData;
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

  async function selectSnapshot(snapshotId, compareTo = state.compareTo) {
    if (disposed) return;
    state.selectedId = snapshotId;
    state.compareTo = compareTo || '';
    state.selectedSnapshot =
      state.snapshots.find((s) => s.id === snapshotId) || null;
    state.diffText = 'Loading diff...';
    state.files = [];
    render();
    try {
      if (state.compareTo) {
        const diffData = await apiCall(
          ctx,
          `/snapshots/${encodeURIComponent(snapshotId)}/diff?compare_to=${encodeURIComponent(state.compareTo)}`
        );
        state.diffText = diffData.diff || '';
        state.files = diffData.files || [];
      } else {
        const detail = await apiCall(
          ctx,
          `/snapshots/${encodeURIComponent(snapshotId)}?include_diff=true`
        );
        state.selectedSnapshot = detail.snapshot || state.selectedSnapshot;
        state.diffText = detail.diff || '';
        state.files = detail.files || [];
      }
    } catch (err) {
      state.diffText = `Error loading diff: ${err.message}`;
    }
    render();
  }

  async function triggerRestore(snapshotId, rewindConversation = false, targetFiles = null) {
    const what = targetFiles && targetFiles.length > 0
      ? `surgically restore ${targetFiles.join(', ')}`
      : rewindConversation
      ? 'restore ALL workspace files AND rewind chat messages in state.db'
      : 'restore ALL workspace files to this snapshot state';

    const label = `${what.charAt(0).toUpperCase() + what.slice(1)}? (A pre-restore safety snapshot will be created automatically first)`;
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
            file_paths: targetFiles || undefined,
          }),
        }
      );
      const safetyMsg = res.pre_restore_backup_id ? ` (Safety backup: ${res.pre_restore_backup_id})` : '';
      setToast(
        targetFiles
          ? `Surgically restored ${targetFiles.join(', ')}.${safetyMsg}`
          : rewindConversation
          ? `Restored workspace & rewound ${res.messages_deactivated || 0} messages.${safetyMsg}`
          : `Restored workspace to ${(res.checkpoint_hash || '').slice(0, 8)}.${safetyMsg}`
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
        ? window.prompt('Title for branched session:', 'Branched from Time Machine')
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


  async function triggerSnapshotNow() {
    try {
      const activeSession = state.sessionFilter || (ctx && ctx.session && ctx.session.id) || 'manual';
      const res = await apiCall(ctx, '/snapshots', {
        method: 'POST',
        body: JSON.stringify({ session_id: activeSession, reason: 'Manual User Checkpoint' }),
      });
      setToast(`📸 Snapshot created: ${res.snapshot?.id || 'ok'}`);
      await loadSnapshots(false);
    } catch (err) {
      setToast(`Snapshot error: ${err.message}`);
    }
  }

  async function triggerRestoreAndRetry(snapshotId) {
    if (typeof window !== 'undefined' && !window.confirm(`Restore to ${snapshotId} and retry the failed command?`)) {
      return;
    }
    try {
      const res = await apiCall(ctx, `/snapshots/${encodeURIComponent(snapshotId)}/retry`, {
        method: 'POST',
        body: JSON.stringify({ restore_files: true }),
      });
      const cmd = res.retry_command || 'pytest';
      setToast(`✅ Restored to ${(res.checkpoint_hash || '').slice(0, 8)}! Suggested command: ${cmd}`);
      await loadSnapshots(false);
      await selectSnapshot(snapshotId);
    } catch (err) {
      setToast(`Restore & retry error: ${err.message}`);
    }
  }

  async function triggerExportMarkdown() {
    try {
      const activeSession = state.sessionFilter || (ctx && ctx.session && ctx.session.id) || '';
      const q = activeSession ? `?session_id=${encodeURIComponent(activeSession)}` : '';
      const res = await apiCall(ctx, `/snapshots/export${q}`);
      if (typeof navigator !== 'undefined' && navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(res.markdown || '');
        setToast('📥 Timeline markdown copied to clipboard!');
      } else {
        setToast('📥 Timeline markdown generated successfully.');
      }
    } catch (err) {
      setToast(`Export error: ${err.message}`);
    }
  }

  async function triggerPrune() {
    if (typeof window !== 'undefined' && !window.confirm('Prune snapshots older than the last 50? (Baselines and safety backups are always preserved)')) {
      return;
    }
    try {
      const activeSession = state.sessionFilter || (ctx && ctx.session && ctx.session.id) || '';
      const q = activeSession ? `?session_id=${encodeURIComponent(activeSession)}&keep_last=50` : '?keep_last=50';
      const res = await apiCall(ctx, `/snapshots/prune${q}`, { method: 'POST' });
      setToast(`Pruned ${res.pruned_count || 0} old snapshots (retained ${res.retained_count || 0}).`);
      await loadSnapshots(false);
    } catch (err) {
      setToast(`Prune error: ${err.message}`);
    }
  }

  function render() {
    if (disposed || !container) return;
    const isRail = mode === 'rail';

    const filteredSnaps = state.snapshots.filter((s) => {
      if (!state.filterText) return true;
      const ft = state.filterText.toLowerCase();
      const nameMatch = (s.tool_name || '').toLowerCase().includes(ft);
      const argsMatch = (s.tool_args_summary || '').toLowerCase().includes(ft);
      const wdirMatch = (s.working_dir || '').toLowerCase().includes(ft);
      const diffMatch = (s.unified_diff || '').toLowerCase().includes(ft);
      const filesMatch = (s.affected_files || '').toLowerCase().includes(ft);
      return nameMatch || argsMatch || wdirMatch || diffMatch || filesMatch;
    });

    const cardsHtml =
      filteredSnaps.length === 0
        ? `<div class="tm-empty">${
            state.loading ? 'Loading snapshots...' : 'No snapshots matching filter.'
          }</div>`
        : filteredSnaps
            .map((s) => {
              const active = s.id === state.selectedId ? 'active' : '';
              const borderCol = getBadgeColor(s);
              const shortHash = (s.checkpoint_hash || '').slice(0, 8) || 'no-git';
              const stats =
                s.files_changed || s.insertions || s.deletions
                  ? `<span>${s.files_changed || 0}f +${s.insertions || 0}/-${
                      s.deletions || 0
                    }</span>`
                  : '';
              const selfHealBadge = s.self_heal_candidate
                ? `<span class="tm-badge tm-badge-amber" style="margin-left:4px;">⚠️ Self-Heal</span>`
                : '';
              const isBranch = Boolean(s.parent_session_id || s.tool_name === 'time_machine_branch' || (s.reason && s.reason.includes('branch')));
              const branchBadge = isBranch
                ? `<span class="tm-badge" style="margin-left:4px;background:rgba(139,92,246,0.15);border-color:#8b5cf6;color:#c4b5fd;">🌿 Forked</span>`
                : '';
              return `
                <div class="tm-card ${active}" data-snap-id="${escapeHtml(s.id)}" style="border-left-color:${borderCol};">
                  <div class="tm-card-top">
                    <span>${escapeHtml(s.tool_name)}</span>
                    <div>
                      ${branchBadge}
                      ${selfHealBadge}
                      <span class="tm-badge">${escapeHtml(shortHash)}</span>
                    </div>
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
                          )}">Revert</button>
                          <button class="tm-btn tm-btn-branch" data-action="branch" data-id="${escapeHtml(
                            s.id
                          )}">Fork</button>
                        </div>`
                      : ''
                  }
                </div>
              `;
            })
            .join('');

    const sel = state.selectedSnapshot;
    let diffAreaHtml = '';

    if (sel) {
      if (state.files && state.files.length > 0) {
        const fileCards = state.files.map((f) => {
          const statusBadge =
            f.status === 'added'
              ? '<span class="tm-badge" style="background:#238636;color:#fff;">Added</span>'
              : f.status === 'deleted'
              ? '<span class="tm-badge" style="background:#da3633;color:#fff;">Deleted</span>'
              : '<span class="tm-badge" style="background:#1f6feb;color:#fff;">Modified</span>';

          const viewContent =
            state.viewMode === 'split'
              ? renderSideBySideDiff(f.diff_chunk)
              : renderColorizedDiff(f.diff_chunk);

          return `
            <div class="tm-file-card">
              <div class="tm-file-header">
                <div style="display:flex;align-items:center;gap:8px;">
                  ${statusBadge}
                  <span>${escapeHtml(f.path)}</span>
                </div>
                <div class="tm-file-stats">
                  <span>+${f.additions || 0} / -${f.deletions || 0}</span>
                  <button class="tm-btn tm-btn-warn" data-action="revert-file" data-snap="${escapeHtml(sel.id)}" data-file="${escapeHtml(f.path)}">
                    Revert File Only
                  </button>
                </div>
              </div>
              <div style="max-height:360px;overflow:auto;">
                ${viewContent}
              </div>
            </div>
          `;
        }).join('');

        diffAreaHtml = fileCards;
      } else {
        diffAreaHtml =
          state.viewMode === 'split'
            ? renderSideBySideDiff(state.diffText)
            : renderColorizedDiff(state.diffText);
      }
    } else {
      diffAreaHtml = '<div class="tm-empty">Select a snapshot from the timeline to inspect changes.</div>';
    }

    const detailHtml = sel
      ? `
        <div class="tm-detail-bar">
          <div>
            <strong>${escapeHtml(sel.tool_name)}</strong>
            <span class="tm-badge" style="margin-left:8px;">${escapeHtml(
              (sel.checkpoint_hash || '').slice(0, 12)
            )}</span>
            <span style="font-size:11px;color:#8b949e;margin-left:8px;">
              Session: ${escapeHtml(sel.session_id)} · msg #${sel.message_id_watermark ?? '—'}
            </span>
          </div>
          <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">
            <div style="display:flex;align-items:center;gap:4px;">
              <label style="font-size:11px;color:#8b949e;">Compare:</label>
              <select class="tm-input" id="tm-compare-select" style="padding:2px 8px;font-size:11px;height:26px;width:auto;cursor:pointer;">
                <option value="" ${state.compareTo === '' ? 'selected' : ''}>Previous Step</option>
                <option value="WORKING_TREE" ${state.compareTo === 'WORKING_TREE' ? 'selected' : ''}>Live Working Tree</option>
                ${state.snapshots
                  .filter((s) => s.id !== sel.id)
                  .map((s) => `<option value="${escapeHtml(s.id)}" ${state.compareTo === s.id ? 'selected' : ''}>Snapshot: ${escapeHtml(s.tool_name)} (${escapeHtml(s.id.slice(-8))})</option>`)
                  .join('')}
              </select>
            </div>
            <button class="tm-btn" id="tm-toggle-view-btn">
              ${state.viewMode === 'split' ? 'View: Split ◫' : 'View: Unified ☰'}
            </button>
            <button class="tm-btn tm-btn-retry" id="tm-retry-btn" title="Revert to this snapshot and retry command">⚡ Restore & Retry</button>
            <button class="tm-btn tm-btn-warn" id="tm-restore-files-btn">Restore Full Workspace</button>
            <button class="tm-btn tm-btn-warn" id="tm-restore-all-btn">Rewind Files + Chat</button>
            <button class="tm-btn tm-btn-branch" id="tm-branch-btn">Fork Session</button>
          </div>
        </div>
        <div class="tm-diff-container">
          <div class="tm-summary-bar">
            <span style="font-weight:600;color:#c9d1d9;">
              ${(state.files || []).length} files changed &nbsp;•&nbsp; 
              <span style="color:#3fb950;">+${(state.files || []).reduce((a, b) => a + (b.additions || 0), 0)} additions</span> &nbsp;•&nbsp; 
              <span style="color:#f85149;">-${(state.files || []).reduce((a, b) => a + (b.deletions || 0), 0)} deletions</span>
            </span>
            <span style="color:#8b949e;font-size:11px;">Diff against: <strong>${escapeHtml(state.compareTo ? (state.compareTo === 'WORKING_TREE' ? 'Live Working Tree' : state.compareTo) : 'Previous Step')}</strong></span>
          </div>
          ${diffAreaHtml}
        </div>
      `
      : '<div class="tm-empty" style="margin:auto;">Select a snapshot from the timeline to inspect changes.</div>';


    const scrubberNodes = state.snapshots.map((s, idx) => {
      const active = s.id === state.selectedId ? 'active' : '';
      const col = getBadgeColor(s);
      const shortId = (s.checkpoint_hash || '').slice(0, 7) || s.id.slice(-6);
      const isLast = idx === state.snapshots.length - 1;
      return `
        <div class="tm-scrub-node ${active}" data-snap-id="${escapeHtml(s.id)}" style="background:${col};border-color:${col};" title="${escapeHtml(s.tool_name)}">
          <div class="tm-scrub-tooltip">
            <strong>${escapeHtml(s.tool_name)}</strong> (${escapeHtml(shortId)})<br/>
            ${escapeHtml(formatTime(s.created_at))}<br/>
            +${s.insertions || 0} / -${s.deletions || 0}
          </div>
        </div>
        ${!isLast ? '<div class="tm-scrub-line"></div>' : ''}
      `;
    }).join('');

    const scrubberHtml = state.snapshots.length > 0
      ? `<div class="tm-scrubber-container">
           <span style="font-size:11px;color:#8b949e;flex-shrink:0;">Timeline:</span>
           <div class="tm-scrubber-track">${scrubberNodes}</div>
         </div>`
      : '';

    container.innerHTML = `
      <style>${STYLES}</style>
      <div class="tm-root ${isRail ? 'tm-rail-root' : ''}">
        <div class="tm-header">
          <div class="tm-title">
            <span>🛡️ Hermes Time Machine</span>
            <span class="tm-badge">${state.snapshots.length} snapshots</span>
            ${state.storage ? `<span class="tm-badge tm-badge-blue" title="Total snapshot storage">Storage: ${((state.storage.total_storage_bytes || 0) / (1024 * 1024)).toFixed(1)} MB</span>` : ''}
          </div>
          <div style="display:flex;gap:6px;flex-wrap:wrap;">
            <button class="tm-btn tm-btn-primary" id="tm-snap-now-btn" title="Capture immediate filesystem checkpoint">📸 Snapshot</button>
            <button class="tm-btn" id="tm-export-btn" title="Export timeline history as Markdown report">📥 Export</button>
            <button class="tm-btn" id="tm-prune-btn" title="Prune older snapshots keeping last 50">Prune Old</button>
            <button class="tm-btn" id="tm-refresh-btn">Refresh</button>
          </div>
        </div>
        ${scrubberHtml}
        <div class="tm-filter-bar">
          <input class="tm-input" id="tm-filter-input" placeholder="Search code symbols, diffs, tools, or files..." value="${escapeHtml(state.filterText)}" />
        </div>
        ${state.toast ? `<div class="tm-toast">${escapeHtml(state.toast)}</div>` : ''}
        <div class="tm-body">
          <div class="tm-sidebar">
            <div class="tm-card-list">
              ${cardsHtml}
            </div>
          </div>
          ${!isRail ? `<div class="tm-detail-pane">${detailHtml}</div>` : ''}
        </div>
      </div>
    `;

    // Event listeners
    const refreshBtn = container.querySelector('#tm-refresh-btn');
    if (refreshBtn) refreshBtn.onclick = () => loadSnapshots(false);

    const snapNowBtn = container.querySelector('#tm-snap-now-btn');
    if (snapNowBtn) snapNowBtn.onclick = () => triggerSnapshotNow();

    const exportBtn = container.querySelector('#tm-export-btn');
    if (exportBtn) exportBtn.onclick = () => triggerExportMarkdown();

    const retryBtn = container.querySelector('#tm-retry-btn');
    if (retryBtn && sel) retryBtn.onclick = () => triggerRestoreAndRetry(sel.id);

    const scrubNodes = container.querySelectorAll('.tm-scrub-node');
    scrubNodes.forEach((node) => {
      node.onclick = () => {
        const id = node.getAttribute('data-snap-id');
        if (id) selectSnapshot(id);
      };
    });

    const pruneBtn = container.querySelector('#tm-prune-btn');
    if (pruneBtn) pruneBtn.onclick = () => triggerPrune();

    const compareSelect = container.querySelector('#tm-compare-select');
    if (compareSelect && sel) {
      compareSelect.onchange = (e) => selectSnapshot(sel.id, e.target.value);
    }

    const filterInput = container.querySelector('#tm-filter-input');
    if (filterInput) {
      filterInput.oninput = (e) => {
        state.filterText = e.target.value;
        render();
      };
    }

    const toggleViewBtn = container.querySelector('#tm-toggle-view-btn');
    if (toggleViewBtn) {
      toggleViewBtn.onclick = () => {
        state.viewMode = state.viewMode === 'unified' ? 'split' : 'unified';
        render();
      };
    }

    const cards = container.querySelectorAll('.tm-card');
    cards.forEach((el) => {
      el.onclick = (e) => {
        if (e.target.tagName === 'BUTTON') return;
        const id = el.getAttribute('data-snap-id');
        if (id) selectSnapshot(id);
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

    const revertFileBtns = container.querySelectorAll('button[data-action="revert-file"]');
    revertFileBtns.forEach((btn) => {
      btn.onclick = () => {
        const snapId = btn.getAttribute('data-snap');
        const filePath = btn.getAttribute('data-file');
        if (snapId && filePath) {
          triggerRestore(snapId, false, [filePath]);
        }
      };
    });

    const actionBtns = container.querySelectorAll('button[data-action]');
    actionBtns.forEach((btn) => {
      btn.onclick = (e) => {
        e.stopPropagation();
        const action = btn.getAttribute('data-action');
        const id = btn.getAttribute('data-id');
        if (action === 'restore-files') triggerRestore(id, false);
        else if (action === 'restore-all') triggerRestore(id, true);
        else if (action === 'branch') triggerBranch(id);
      };
    });
  }

  loadSnapshots(true);

  return () => {
    disposed = true;
    container.innerHTML = '';
  };
}

function mountStatusBarItem(container, ctx) {
  let disposed = false;
  let interval = null;

  async function update() {
    if (disposed || !container) return;
    try {
      const activeSession = (ctx && ctx.session && ctx.session.id) || '';
      const data = await apiCall(
        ctx,
        `/status${activeSession ? `?session_id=${encodeURIComponent(activeSession)}` : ''}`
      );
      const count = data.snapshot_count || 0;
      const isSelfHeal = Boolean(data.self_heal_available);

      const badgeStyle = isSelfHeal
        ? 'background:rgba(245,158,11,0.2);color:#fbbf24;border:1px solid #f59e0b;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:600;display:inline-flex;align-items:center;gap:4px;cursor:pointer;'
        : 'background:#21262d;color:#8b949e;border:1px solid #30363d;padding:2px 8px;border-radius:12px;font-size:11px;display:inline-flex;align-items:center;gap:4px;cursor:pointer;';

      let label = `🛡️ Safe Mode • ${count} snaps`;
      if (isSelfHeal) {
        label = `⚠️ Self-Heal Available • Revert Ready`;
      } else if (data.latest_snapshot && data.latest_snapshot.tool_name === 'pre_restore_safety_backup') {
        label = `✅ Recovered • Workspace Clean`;
      }

  
    const scrubberNodes = state.snapshots.map((s, idx) => {
      const active = s.id === state.selectedId ? 'active' : '';
      const col = getBadgeColor(s);
      const shortId = (s.checkpoint_hash || '').slice(0, 7) || s.id.slice(-6);
      const isLast = idx === state.snapshots.length - 1;
      return `
        <div class="tm-scrub-node ${active}" data-snap-id="${escapeHtml(s.id)}" style="background:${col};border-color:${col};" title="${escapeHtml(s.tool_name)}">
          <div class="tm-scrub-tooltip">
            <strong>${escapeHtml(s.tool_name)}</strong> (${escapeHtml(shortId)})<br/>
            ${escapeHtml(formatTime(s.created_at))}<br/>
            +${s.insertions || 0} / -${s.deletions || 0}
          </div>
        </div>
        ${!isLast ? '<div class="tm-scrub-line"></div>' : ''}
      `;
    }).join('');

    const scrubberHtml = state.snapshots.length > 0
      ? `<div class="tm-scrubber-container">
           <span style="font-size:11px;color:#8b949e;flex-shrink:0;">Timeline:</span>
           <div class="tm-scrubber-track">${scrubberNodes}</div>
         </div>`
      : '';

    container.innerHTML = `
        <div style="display:flex;align-items:center;height:100%;padding:0 8px;">
          <span style="${badgeStyle}" title="${isSelfHeal ? 'Command failed after code edit - self-healing checkpoint available!' : 'Time Machine active: automatic shadow-git checkpoints enabled.'}">
            ${escapeHtml(label)}
          </span>
        </div>
      `;

      const chip = container.querySelector('span');
      if (chip) {
        chip.onclick = () => {
          if (typeof ctx.openView === 'function') {
            ctx.openView('hermes-time-machine-view');
          } else if (typeof ctx.openRail === 'function') {
            ctx.openRail('hermes-time-machine-rail');
          }
        };
      }
    } catch (_) {}
  }

  update();
  interval = setInterval(update, 8000);

  return () => {
    disposed = true;
    if (interval) clearInterval(interval);
    container.innerHTML = '';
  };
}

function registerCapability(ctx, spec) {
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
  activate(ctx) {
    registerCapability(ctx, {
      id: 'hermes-time-machine-status',
      area: 'statusBar.right',
      label: 'Time Machine Status',
      priority: 95,
      mount: (el, c) => mountStatusBarItem(el, c),
    });

    registerCapability(ctx, {
      id: 'hermes-time-machine-rail',
      area: 'rail.right',
      label: 'Time Machine',
      icon: 'history',
      mount: (el, c) => mountTimeMachineDOM(el, c, 'rail'),
    });

    registerCapability(ctx, {
      id: 'hermes-time-machine-view',
      area: 'pane',
      label: 'Time Machine Studio',
      icon: 'history',
      mount: (el, c) => mountTimeMachineDOM(el, c, 'studio'),
    });
  },
};
