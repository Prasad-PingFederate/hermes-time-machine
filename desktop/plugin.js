/**
 * Hermes Time Machine — Desktop Plugin (desktop/plugin.js)
 * Adheres strictly to the Hermes Desktop SDK (@hermes/plugin-sdk) contribution model:
 *   - area: 'statusBar.right' (order: 85, render: () => <ReactNode>)
 *   - area: 'panes' (placement: 'right', render: () => <ReactNode>)
 *   - area: 'palette' (command palette entry)
 * No mount(el), no rail.right, no raw DOM injection, no eval.
 */

import { host, useValue } from '@hermes/plugin-sdk';
import { useState, useEffect, useCallback, useMemo } from 'react';
import { jsx, jsxs } from 'react/jsx-runtime';

const PLUGIN_ID = 'time-machine';
const API_BASE = `/api/plugins/${PLUGIN_ID}`;

export function initialPaneState() {
  return {
    diffMode: 'turn',
    searchQuery: '',
    matchIds: null,
    restore: null,
  };
}

export function diffEndpoint(snapshotId, mode) {
  const base = `/snapshots/${encodeURIComponent(snapshotId)}/diff`;
  if (mode === 'working_tree') return `${base}?compare_to=WORKING_TREE`;
  return base;
}

export function previewRestoreBody() {
  return { dry_run: true, user_approved: false };
}

export function confirmRestoreBody(selectedFiles, restoreWorkspace) {
  const body = { dry_run: false, user_approved: true };
  if (restoreWorkspace) return body;
  return {
    ...body,
    file_paths: Array.isArray(selectedFiles) ? selectedFiles : [],
  };
}

export function fileChangeLabel(snap) {
  if (!snap) return '';
  const fileCount = snap.files_changed ?? snap.modified_files_count ?? 0;
  const insertions = snap.insertions || 0;
  const deletions = snap.deletions || 0;
  return `${fileCount} file(s) +${insertions}/-${deletions}`;
}

export function reducePaneState(state, action) {
  const current = state || initialPaneState();
  switch (action && action.type) {
    case 'set_diff_mode':
      return {
        ...current,
        diffMode: action.mode === 'working_tree' ? 'working_tree' : 'turn',
      };
    case 'set_search_query':
      return { ...current, searchQuery: action.query || '' };
    case 'search_results': {
      const ids = Array.isArray(action.matches)
        ? action.matches.map((m) => m && (m.snapshot_id || m.id)).filter(Boolean)
        : [];
      return {
        ...current,
        searchQuery: action.query != null ? action.query : current.searchQuery,
        matchIds: ids,
      };
    }
    case 'clear_search':
      return { ...current, searchQuery: '', matchIds: null };
    case 'preview_loaded': {
      const restored = (action.plannedRestored || []).filter((f) => f && f !== '(all files)');
      const unlinked = (action.plannedUnlinked || []).filter(Boolean);
      return {
        ...current,
        restore: {
          snapshotId: action.snapshotId,
          step: 'preview',
          plannedRestored: restored,
          plannedUnlinked: unlinked,
          fullWorkspace: false,
          selected: restored.concat(unlinked),
          backupId: null,
        },
      };
    }
    case 'toggle_full_workspace':
      if (!current.restore) return current;
      return {
        ...current,
        restore: { ...current.restore, fullWorkspace: !current.restore.fullWorkspace },
      };
    case 'toggle_file': {
      if (!current.restore) return current;
      const has = current.restore.selected.indexOf(action.file) !== -1;
      const selected = has
        ? current.restore.selected.filter((f) => f !== action.file)
        : current.restore.selected.concat([action.file]);
      return { ...current, restore: { ...current.restore, selected } };
    }
    case 'cancel_restore':
      return { ...current, restore: null };
    case 'restore_confirmed':
      if (!current.restore) return current;
      return {
        ...current,
        restore: {
          ...current.restore,
          step: 'done',
          backupId: action.backupId || null,
        },
      };
    default:
      return current;
  }
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

function getBadgeColor(s) {
  if (!s) return 'var(--ui-text-tertiary, #888)';
  if (s.self_heal_candidate || s.status === 'error') return '#ef4444';
  if (s.tool_name === 'pre_restore_safety_backup') return '#10b981';
  if (['write_file', 'patch', 'edit_file', 'create_file'].includes(s.tool_name)) return '#3b82f6';
  if (['terminal', 'bash', 'execute_code', 'run_command'].includes(s.tool_name)) return '#8b5cf6';
  return 'var(--ui-text-tertiary, #888)';
}

async function pluginRest(ctx, path, options) {
  if (!ctx || typeof ctx.rest !== 'function') return null;
  if (options) return ctx.rest(path, options);
  return ctx.rest(path);
}

/**
 * Status bar chip component
 */
function TimeMachineStatusChip({ ctx }) {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(false);

  const fetchStatus = useCallback(async () => {
    try {
      if (ctx && typeof ctx.rest === 'function') {
        const data = await ctx.rest('/status');
        setStatus(data);
      }
    } catch (_) {}
  }, [ctx]);

  useEffect(() => {
    fetchStatus();
    const tick = () => {
      if (typeof document !== 'undefined' && document.visibilityState === 'hidden') {
        return;
      }
      fetchStatus();
    };
    if (ctx && typeof ctx.setInterval === 'function') {
      return ctx.setInterval(tick, 10000);
    }
    const timer = setInterval(tick, 10000);
    return () => clearInterval(timer);
  }, [fetchStatus, ctx]);

  const snapCount = status?.snapshot_count ?? 0;
  const hasSelfHeal = !!status?.self_heal_available;

  const handleClick = () => {
    if (typeof host?.openWorkspace === 'function') {
      host.openWorkspace('time-machine', {
        title: 'Time Machine',
        dock: { pane: 'workspace', pos: 'right' },
        render: () => jsx(TimeMachinePane, { ctx }),
      });
    } else if (typeof host?.notify === 'function') {
      host.notify({
        kind: hasSelfHeal ? 'warning' : 'info',
        message: hasSelfHeal
          ? 'Time Machine: Self-heal recommended! Run /tm recover in chat.'
          : `Time Machine: ${snapCount} snapshot(s) recorded in session.`,
      });
    }
  };

  const label = hasSelfHeal
    ? 'Time Machine: ⚡ Self-Heal'
    : snapCount > 0
    ? `Time Machine: ${snapCount}`
    : 'Time Machine';

  return jsx('button', {
    type: 'button',
    className: 'px-1.5 py-0.5 text-[0.6875rem] font-mono rounded flex items-center gap-1 cursor-pointer transition-colors hover:bg-white/10',
    style: {
      color: hasSelfHeal ? '#ef4444' : snapCount > 0 ? '#3b82f6' : 'var(--ui-text-tertiary, #888)',
    },
    onClick: handleClick,
    title: hasSelfHeal
      ? 'Time Machine: Self-heal recommended — click to inspect'
      : `Time Machine: ${snapCount} snapshots captured`,
    children: [
      jsx('span', {
        className: 'inline-block w-1.5 h-1.5 rounded-full',
        style: {
          backgroundColor: hasSelfHeal ? '#ef4444' : snapCount > 0 ? '#10b981' : '#6b7280',
        },
      }),
      label,
    ],
  });
}

/**
 * Main sidebar pane component
 */
function TimeMachinePane({ ctx }) {
  const [snapshots, setSnapshots] = useState(() => (ctx && ctx.initialSnapshots) || []);
  const [selectedSnapshot, setSelectedSnapshot] = useState(
    () => (ctx && (ctx.previewSnapshot || (ctx.initialSnapshots && ctx.initialSnapshots[0]))) || null,
  );
  const [diff, setDiff] = useState(null);
  const [loading, setLoading] = useState(true);
  const [diffLoading, setDiffLoading] = useState(false);
  const [error, setError] = useState(null);
  const [actionNotice, setActionNotice] = useState(null);
  const [paneState, setPaneState] = useState(() => {
    if (ctx && ctx.initialPaneState) return { ...initialPaneState(), ...ctx.initialPaneState };
    return initialPaneState();
  });

  const applyPane = (action) => {
    const next = reducePaneState(paneState, action);
    setPaneState(next);
    return next;
  };

  const loadSnapshots = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      if (ctx && typeof ctx.rest === 'function') {
        const data = await ctx.rest('/snapshots?limit=50');
        const list = Array.isArray(data?.snapshots) ? data.snapshots : [];
        setSnapshots(list);
        if (list.length > 0 && !selectedSnapshot) {
          setSelectedSnapshot(list[0]);
        }
      }
    } catch (err) {
      setError(err?.message || 'Failed to load snapshots');
    } finally {
      setLoading(false);
    }
  }, [ctx, selectedSnapshot]);

  const loadDiff = useCallback(async (id, mode) => {
    if (!id) {
      setDiff(null);
      return;
    }
    setDiffLoading(true);
    try {
      if (ctx && typeof ctx.rest === 'function') {
        const res = await ctx.rest(diffEndpoint(id, mode));
        setDiff(res?.diff || 'No filesystem differences vs current workspace.');
      }
    } catch (err) {
      setDiff(`Diff unavailable: ${err?.message || 'error'}`);
    } finally {
      setDiffLoading(false);
    }
  }, [ctx]);

  useEffect(() => {
    loadSnapshots();
  }, [loadSnapshots]);

  useEffect(() => {
    if (!selectedSnapshot?.id) {
      setDiff(null);
      return;
    }
    let cancelled = false;
    setDiffLoading(true);
    const fetchDiff = async () => {
      try {
        if (ctx && typeof ctx.rest === 'function') {
          const res = await ctx.rest(diffEndpoint(selectedSnapshot.id, paneState.diffMode));
          if (!cancelled) {
            setDiff(res?.diff || 'No filesystem differences vs current workspace.');
          }
        }
      } catch (err) {
        if (!cancelled) {
          setDiff(`Diff unavailable: ${err?.message || 'error'}`);
        }
      } finally {
        if (!cancelled) setDiffLoading(false);
      }
    };
    fetchDiff();
    return () => {
      cancelled = true;
    };
  }, [ctx, selectedSnapshot, paneState.diffMode]);

  const handleSearch = async (event) => {
    const q = event && event.target ? event.target.value : '';
    if (!q || !q.trim()) {
      applyPane({ type: 'clear_search' });
      return;
    }
    applyPane({ type: 'set_search_query', query: q });
    try {
      const res = await pluginRest(ctx, `/snapshots/search?q=${encodeURIComponent(q.trim())}`);
      applyPane({ type: 'search_results', query: q, matches: (res && res.matches) || [] });
    } catch (err) {
      applyPane({ type: 'search_results', query: q, matches: [] });
      setError(err?.message || 'Search failed');
    }
  };

  const handleDiffMode = (mode) => {
    const next = applyPane({ type: 'set_diff_mode', mode });
    if (selectedSnapshot?.id) loadDiff(selectedSnapshot.id, next.diffMode);
  };

  const handlePreviewRestore = async (snap) => {
    if (!snap?.id) return;
    try {
      const res = await pluginRest(ctx, `/snapshots/${encodeURIComponent(snap.id)}/restore`, {
        method: 'POST',
        body: previewRestoreBody(),
      });
      applyPane({
        type: 'preview_loaded',
        snapshotId: snap.id,
        plannedRestored: (res && res.planned_restored_files) || [],
        plannedUnlinked: (res && (res.planned_unlinked_files || res.planned_deleted_stray_files)) || [],
      });
      setActionNotice(null);
    } catch (err) {
      setActionNotice({ kind: 'warning', message: err?.message || 'Preview restore failed' });
    }
  };

  const handleToggleFile = (file) => {
    applyPane({ type: 'toggle_file', file });
  };

  const handleCancelRestore = () => {
    applyPane({ type: 'cancel_restore' });
  };

  const handleConfirmRestore = async () => {
    const plan = paneState.restore;
    if (!plan || !plan.snapshotId) return;
    const selected = plan.selected || [];
    if (!plan.fullWorkspace && selected.length === 0) {
      setActionNotice({ kind: 'warning', message: 'Select at least one file, or check Restore entire workspace.' });
      return;
    }
    try {
      const res = await pluginRest(ctx, `/snapshots/${encodeURIComponent(plan.snapshotId)}/restore`, {
        method: 'POST',
        body: confirmRestoreBody(selected, !!plan.fullWorkspace),
      });
      const backupId = res && res.pre_restore_backup_id;
      applyPane({ type: 'restore_confirmed', backupId });
      setActionNotice({
        kind: 'info',
        message: `Restored snapshot '${plan.snapshotId}'. Safety backup: ${backupId || 'n/a'}.`,
      });
      await loadSnapshots();
      if (selectedSnapshot?.id) await loadDiff(selectedSnapshot.id, paneState.diffMode);
    } catch (err) {
      setActionNotice({ kind: 'warning', message: err?.message || 'Restore failed' });
    }
  };

  const matchIds = paneState.matchIds;
  const visibleSnapshots = matchIds == null
    ? snapshots
    : snapshots.filter((snap) => matchIds.indexOf(snap.id) !== -1);
  const restorePlan = paneState.restore;
  const diffMode = paneState.diffMode || 'turn';

  const fileChoices = restorePlan
    ? (restorePlan.plannedRestored || []).map((f) => ({ file: f, kind: 'restore' }))
        .concat((restorePlan.plannedUnlinked || []).map((f) => ({ file: f, kind: 'unlink' })))
    : [];

  return jsxs('div', {
    className: 'flex h-full flex-col text-xs font-sans overflow-hidden select-none',
    style: {
      backgroundColor: 'var(--ui-background, #121316)',
      color: 'var(--ui-text, #e2e8f0)',
    },
    children: [
      jsxs('div', {
        className: 'flex items-center justify-between p-3 border-b border-white/10 shrink-0 gap-2',
        children: [
          jsxs('div', {
            className: 'flex items-center gap-2 min-w-0',
            children: [
              jsx('span', { className: 'text-sm font-semibold tracking-wide', children: 'Time Machine' }),
              jsx('span', {
                className: 'px-1.5 py-0.5 rounded text-[10px] font-mono bg-blue-500/20 text-blue-400',
                children: `${snapshots.length} snapshots`,
              }),
            ],
          }),
          jsx('input', {
            type: 'search',
            'data-action': 'search-checkpoints',
            placeholder: 'Search checkpoints',
            defaultValue: paneState.searchQuery || '',
            onChange: handleSearch,
            className: 'w-32 px-2 py-1 rounded bg-white/5 border border-white/10 text-[11px] text-white/80',
          }),
          jsx('button', {
            type: 'button',
            className: 'px-2 py-1 rounded bg-white/5 hover:bg-white/10 text-[11px] cursor-pointer transition-colors',
            onClick: loadSnapshots,
            children: loading ? 'Refreshing...' : 'Refresh',
          }),
        ],
      }),

      actionNotice &&
        jsxs('div', {
          className: 'p-2.5 mx-3 mt-3 rounded border text-[11px] leading-relaxed',
          style: {
            backgroundColor: actionNotice.kind === 'warning' ? 'rgba(239, 68, 68, 0.1)' : 'rgba(59, 130, 246, 0.1)',
            borderColor: actionNotice.kind === 'warning' ? '#ef4444' : '#3b82f6',
            color: actionNotice.kind === 'warning' ? '#fca5a5' : '#93c5fd',
          },
          children: [
            jsx('div', { className: 'font-semibold mb-0.5', children: actionNotice.kind === 'warning' ? 'Restore Error' : 'Restore Update' }),
            jsx('div', { children: actionNotice.message }),
          ],
        }),

      error &&
        jsx('div', { className: 'px-3 pt-2 text-[11px] text-red-300', children: error }),

      jsxs('div', {
        className: 'flex flex-1 min-h-0 divide-y divide-white/10 flex-col overflow-hidden',
        children: [
          jsxs('div', {
            className: 'flex-1 overflow-y-auto p-2 space-y-1.5 min-h-[140px]',
            children: [
              loading && snapshots.length === 0 &&
                jsx('div', { className: 'p-4 text-center text-white/40', children: 'Loading snapshots...' }),
              !loading && visibleSnapshots.length === 0 &&
                jsx('div', { className: 'p-4 text-center text-white/40', children: 'No snapshots recorded yet in this session.' }),
              visibleSnapshots.map((snap) => {
                const isSelected = selectedSnapshot?.id === snap.id;
                const badgeColor = getBadgeColor(snap);
                const highlighted = matchIds != null && matchIds.indexOf(snap.id) !== -1;
                return jsxs('div', {
                  key: snap.id,
                  onClick: () => setSelectedSnapshot(snap),
                  className: `p-2 rounded cursor-pointer transition-all border ${
                    isSelected ? 'bg-white/10 border-blue-500/50' : 'bg-white/5 border-transparent hover:bg-white/[0.07]'
                  }`,
                  style: highlighted ? { boxShadow: 'inset 0 0 0 1px rgba(250, 204, 21, 0.7)' } : undefined,
                  children: [
                    jsxs('div', {
                      className: 'flex items-center justify-between gap-1 mb-1',
                      children: [
                        jsx('span', {
                          className: 'px-1.5 py-0.5 rounded text-[10px] font-mono font-bold uppercase',
                          style: { backgroundColor: `${badgeColor}22`, color: badgeColor },
                          children: snap.tool_name || 'checkpoint',
                        }),
                        jsx('span', { className: 'text-[10px] text-white/40 font-mono', children: formatTime(snap.created_at) }),
                      ],
                    }),
                    jsxs('div', {
                      className: 'flex items-center justify-between text-[11px] text-white/70 font-mono truncate gap-2',
                      children: [
                        jsx('span', { className: 'truncate', title: snap.id, children: snap.id }),
                        jsx('span', { className: 'text-[10px] text-white/50 shrink-0', children: fileChangeLabel(snap) }),
                      ],
                    }),
                  ],
                });
              }),
            ],
          }),

          selectedSnapshot &&
            jsxs('div', {
              className: 'h-56 flex flex-col p-2 bg-black/30 shrink-0 border-t border-white/10',
              children: [
                jsxs('div', {
                  className: 'flex items-center justify-between pb-1.5 mb-1.5 border-b border-white/10 gap-1',
                  children: [
                    jsxs('span', {
                      className: 'font-mono text-[11px] text-white/60 truncate',
                      children: ['Diff: ', selectedSnapshot.id],
                    }),
                    jsxs('div', {
                      className: 'flex items-center gap-1',
                      children: [
                        jsx('button', {
                          type: 'button',
                          'data-action': 'diff-turn',
                          className: `px-1.5 py-0.5 rounded text-[10px] cursor-pointer ${diffMode === 'turn' ? 'bg-white/20 text-white' : 'bg-white/5 text-white/60'}`,
                          onClick: () => handleDiffMode('turn'),
                          children: 'Turn Diff',
                        }),
                        jsx('button', {
                          type: 'button',
                          'data-action': 'diff-working-tree',
                          className: `px-1.5 py-0.5 rounded text-[10px] cursor-pointer ${diffMode === 'working_tree' ? 'bg-white/20 text-white' : 'bg-white/5 text-white/60'}`,
                          onClick: () => handleDiffMode('working_tree'),
                          children: 'Live Working Tree Drift',
                        }),
                        jsx('button', {
                          type: 'button',
                          'data-action': 'preview-restore',
                          className: 'px-2 py-0.5 rounded bg-blue-600/30 text-blue-300 hover:bg-blue-600/50 text-[10px] cursor-pointer',
                          onClick: () => handlePreviewRestore(selectedSnapshot),
                          children: 'Preview Restore',
                        }),
                      ],
                    }),
                  ],
                }),
                restorePlan && restorePlan.step === 'preview' &&
                  jsxs('div', {
                    className: 'mb-1.5 rounded border border-blue-500/30 bg-blue-500/10 p-1.5 space-y-1',
                    children: [
                      jsx('div', { className: 'text-[10px] text-blue-200 font-semibold', children: 'Step 1 — choose files, then confirm' }),
                      jsx('label', {
                        className: 'flex items-center gap-1 text-[10px] text-blue-100',
                        children: [
                          jsx('input', {
                            type: 'checkbox',
                            'data-action': 'toggle-full-workspace',
                            checked: !!restorePlan.fullWorkspace,
                            onChange: () => applyPane({ type: 'toggle_full_workspace' }),
                          }),
                          jsx('span', { children: 'Restore entire workspace' }),
                        ],
                      }),
                      fileChoices.map((item) => jsxs('label', {
                        key: `${item.kind}:${item.file}`,
                        className: 'flex items-center gap-1 text-[10px] text-white/80 font-mono',
                        children: [
                          jsx('input', {
                            type: 'checkbox',
                            'data-action': 'toggle-restore-file',
                            'data-file': item.file,
                            checked: (restorePlan.selected || []).indexOf(item.file) !== -1,
                            onChange: () => handleToggleFile(item.file),
                          }),
                          jsx('span', { children: `${item.kind === 'unlink' ? 'unlink' : 'restore'} ${item.file}` }),
                        ],
                      })),
                      jsxs('div', {
                        className: 'flex items-center gap-1 pt-1',
                        children: [
                          jsx('button', {
                            type: 'button',
                            'data-action': 'confirm-restore',
                            className: 'px-2 py-0.5 rounded bg-emerald-600/40 text-emerald-200 text-[10px] cursor-pointer',
                            onClick: handleConfirmRestore,
                            children: 'Confirm & Restore Selected Files',
                          }),
                          jsx('button', {
                            type: 'button',
                            'data-action': 'cancel-restore',
                            className: 'px-2 py-0.5 rounded bg-white/10 text-white/70 text-[10px] cursor-pointer',
                            onClick: handleCancelRestore,
                            children: 'Cancel',
                          }),
                        ],
                      }),
                    ],
                  }),
                restorePlan && restorePlan.step === 'done' &&
                  jsx('div', {
                    className: 'mb-1 text-[10px] text-emerald-300',
                    children: `Safety backup ${restorePlan.backupId || 'recorded'}.`,
                  }),
                jsx('div', {
                  className: 'flex-1 overflow-auto font-mono text-[10px] leading-relaxed whitespace-pre text-white/80 select-text',
                  children: diffLoading ? 'Calculating diff...' : diff || 'No diff available.',
                }),
              ],
            }),
        ],
      }),

      jsxs('div', {
        className: 'p-2 border-t border-white/10 text-[10px] text-white/40 shrink-0 flex items-center justify-between',
        children: [
          jsx('span', { children: 'Slash commands: /tm list · /tm restore <id>' }),
          jsx('span', { className: 'font-mono', children: 'v0.4.1' }),
        ],
      }),
    ],
  });
}

const plugin = {
  id: PLUGIN_ID,
  name: 'Hermes Time Machine',
  description: 'Visual timeline scrubber, surgical file rollback, and autonomous self-healing for Hermes Agent & Desktop.',
  defaultEnabled: true,

  register(ctx) {
    return this.activate(ctx);
  },

  activate(ctx) {
    if (!ctx) return;

    const contributions = [
      {
        id: 'status',
        area: 'statusBar.right',
        order: 85,
        title: 'Time Machine',
        render: () => jsx(TimeMachineStatusChip, { ctx }),
      },
      {
        id: 'timeline-pane',
        area: 'panes',
        title: 'Time Machine',
        data: { placement: 'right', width: '340px' },
        render: () => jsx(TimeMachinePane, { ctx }),
      },
      {
        id: 'open-timeline',
        area: 'palette',
        data: {
          id: 'time-machine.open',
          label: 'Time Machine: Open timeline pane',
          keywords: ['time machine', 'snapshots', 'undo', 'rollback', 'restore', 'checkpoints'],
          run: () => {
            if (typeof host?.openWorkspace === 'function') {
              host.openWorkspace('time-machine', {
                title: 'Time Machine',
                dock: { pane: 'workspace', pos: 'right' },
                render: () => jsx(TimeMachinePane, { ctx }),
              });
            }
          },
        },
      },
    ];

    if (typeof ctx.registerMany === 'function') {
      ctx.registerMany(contributions);
    } else if (typeof ctx.register === 'function') {
      for (const c of contributions) {
        ctx.register(c);
      }
    }
  },
};

export default plugin;
