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
    const timer = setInterval(fetchStatus, 10000);
    return () => clearInterval(timer);
  }, [fetchStatus]);

  const snapCount = status?.snapshot_count ?? 0;
  const hasSelfHeal = !!status?.self_heal_available;

  const handleClick = () => {
    if (typeof host?.openWorkspace === 'function') {
      host.openWorkspace('time-machine', {
        title: 'Time Machine',
        dock: { pane: 'workspace', pos: 'right' },
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
  const [snapshots, setSnapshots] = useState([]);
  const [selectedSnapshot, setSelectedSnapshot] = useState(null);
  const [diff, setDiff] = useState(null);
  const [loading, setLoading] = useState(true);
  const [diffLoading, setDiffLoading] = useState(false);
  const [error, setError] = useState(null);
  const [actionNotice, setActionNotice] = useState(null);

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
          const res = await ctx.rest(`/snapshots/${encodeURIComponent(selectedSnapshot.id)}/diff`);
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
  }, [ctx, selectedSnapshot]);

  const handlePreviewRestore = (snap) => {
    setActionNotice({
      kind: 'info',
      message: `To restore to snapshot '${snap.id}', run '/tm restore ${snap.id}' in chat or request approval. Direct mutations default to dry-run mode for safety.`,
    });
  };

  return jsxs('div', {
    className: 'flex h-full flex-col text-xs font-sans overflow-hidden select-none',
    style: {
      backgroundColor: 'var(--ui-background, #121316)',
      color: 'var(--ui-text, #e2e8f0)',
    },
    children: [
      // Header
      jsxs('div', {
        className: 'flex items-center justify-between p-3 border-b border-white/10 shrink-0',
        children: [
          jsxs('div', {
            className: 'flex items-center gap-2',
            children: [
              jsx('span', { className: 'text-sm font-semibold tracking-wide', children: 'Time Machine' }),
              jsx('span', {
                className: 'px-1.5 py-0.5 rounded text-[10px] font-mono bg-blue-500/20 text-blue-400',
                children: `${snapshots.length} snapshots`,
              }),
            ],
          }),
          jsx('button', {
            type: 'button',
            className: 'px-2 py-1 rounded bg-white/5 hover:bg-white/10 text-[11px] cursor-pointer transition-colors',
            onClick: loadSnapshots,
            children: loading ? 'Refreshing...' : 'Refresh',
          }),
        ],
      }),

      // Action notice banner (e.g. safety notice)
      actionNotice &&
        jsxs('div', {
          className: 'p-2.5 mx-3 mt-3 rounded border text-[11px] leading-relaxed',
          style: {
            backgroundColor: actionNotice.kind === 'warning' ? 'rgba(239, 68, 68, 0.1)' : 'rgba(59, 130, 246, 0.1)',
            borderColor: actionNotice.kind === 'warning' ? '#ef4444' : '#3b82f6',
            color: actionNotice.kind === 'warning' ? '#fca5a5' : '#93c5fd',
          },
          children: [
            jsx('div', { className: 'font-semibold mb-0.5', children: 'Safety Guard Notice' }),
            jsx('div', { children: actionNotice.message }),
          ],
        }),

      // Main content: split between snapshot list and diff
      jsxs('div', {
        className: 'flex flex-1 min-h-0 divide-y divide-white/10 flex-col overflow-hidden',
        children: [
          // Timeline list
          jsxs('div', {
            className: 'flex-1 overflow-y-auto p-2 space-y-1.5 min-h-[140px]',
            children: [
              loading && snapshots.length === 0 &&
                jsx('div', { className: 'p-4 text-center text-white/40', children: 'Loading snapshots...' }),
              !loading && snapshots.length === 0 &&
                jsx('div', { className: 'p-4 text-center text-white/40', children: 'No snapshots recorded yet in this session.' }),
              snapshots.map((snap) => {
                const isSelected = selectedSnapshot?.id === snap.id;
                const badgeColor = getBadgeColor(snap);
                return jsxs('div', {
                  key: snap.id,
                  onClick: () => setSelectedSnapshot(snap),
                  className: `p-2 rounded cursor-pointer transition-all border ${
                    isSelected ? 'bg-white/10 border-blue-500/50' : 'bg-white/5 border-transparent hover:bg-white/[0.07]'
                  }`,
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
                      className: 'flex items-center justify-between text-[11px] text-white/70 font-mono truncate',
                      children: [
                        jsx('span', { className: 'truncate', title: snap.id, children: snap.id }),
                        snap.modified_files_count !== undefined &&
                          jsx('span', { className: 'text-[10px] text-white/40', children: `${snap.modified_files_count} file(s)` }),
                      ],
                    }),
                  ],
                });
              }),
            ],
          }),

          // Diff preview panel
          selectedSnapshot &&
            jsxs('div', {
              className: 'h-48 flex flex-col p-2 bg-black/30 shrink-0 border-t border-white/10',
              children: [
                jsxs('div', {
                  className: 'flex items-center justify-between pb-1.5 mb-1.5 border-b border-white/10',
                  children: [
                    jsxs('span', {
                      className: 'font-mono text-[11px] text-white/60 truncate',
                      children: ['Diff: ', selectedSnapshot.id],
                    }),
                    jsx('button', {
                      type: 'button',
                      className: 'px-2 py-0.5 rounded bg-blue-600/30 text-blue-300 hover:bg-blue-600/50 text-[10px] cursor-pointer',
                      onClick: () => handlePreviewRestore(selectedSnapshot),
                      children: 'Preview Restore',
                    }),
                  ],
                }),
                jsx('div', {
                  className: 'flex-1 overflow-auto font-mono text-[10px] leading-relaxed whitespace-pre text-white/80 select-text',
                  children: diffLoading ? 'Calculating diff...' : diff || 'No diff available.',
                }),
              ],
            }),
        ],
      }),

      // Footer
      jsxs('div', {
        className: 'p-2 border-t border-white/10 text-[10px] text-white/40 shrink-0 flex items-center justify-between',
        children: [
          jsx('span', { children: 'Slash commands: /tm list · /tm restore <id>' }),
          jsx('span', { className: 'font-mono', children: 'v0.3.0' }),
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
