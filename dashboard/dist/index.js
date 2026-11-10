/**
 * Hermes Time Machine — Web Dashboard Bundle (dashboard/dist/index.js)
 * Standalone bundle registered with window.__HERMES_PLUGINS__.register('time-machine', Component)
 * Consumes window.__HERMES_PLUGIN_SDK__ and mounts seamlessly in the Hermes Web Dashboard tab.
 */
(function () {
  var SDK = window.__HERMES_PLUGIN_SDK__ || {};
  var React = SDK.React || window.React;
  if (!React) {
    if (typeof console !== 'undefined' && console.warn) {
      console.warn('Hermes Time Machine: React not found on window.__HERMES_PLUGIN_SDK__');
    }
    return;
  }

  var hooks = SDK.hooks || {};
  var useState = hooks.useState || React.useState;
  var useEffect = hooks.useEffect || React.useEffect;
  var useCallback = hooks.useCallback || React.useCallback;

  var components = SDK.components || {};
  var Card = components.Card || function (props) {
    return React.createElement('div', { className: 'rounded-lg border border-border bg-card p-4 ' + (props.className || '') }, props.children);
  };
  var CardHeader = components.CardHeader || function (props) {
    return React.createElement('div', { className: 'mb-3 ' + (props.className || '') }, props.children);
  };
  var CardTitle = components.CardTitle || function (props) {
    return React.createElement('h3', { className: 'text-base font-semibold leading-none tracking-tight ' + (props.className || '') }, props.children);
  };
  var CardContent = components.CardContent || function (props) {
    return React.createElement('div', { className: 'text-sm ' + (props.className || '') }, props.children);
  };
  var Badge = components.Badge || function (props) {
    return React.createElement('span', { className: 'inline-flex items-center px-2 py-0.5 rounded text-xs font-mono font-medium ' + (props.className || '') }, props.children);
  };
  var Button = components.Button || function (props) {
    return React.createElement('button', {
      type: 'button',
      onClick: props.onClick,
      disabled: props.disabled,
      className: 'inline-flex items-center justify-center rounded-md text-xs font-medium px-3 py-1.5 transition-colors cursor-pointer border border-border bg-secondary text-secondary-foreground hover:bg-secondary/80 ' + (props.className || '')
    }, props.children);
  };

  var API = '/api/plugins/time-machine';

  function fetchJSON(url, opts) {
    if (SDK.fetchJSON) return SDK.fetchJSON(url, opts);
    return fetch(url, opts).then(function (res) {
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return res.json();
    });
  }

  function formatTime(epochSeconds) {
    if (!epochSeconds) return 'Just now';
    var d = new Date(epochSeconds * 1000);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  }

  function formatBytes(bytes) {
    if (!bytes || bytes <= 0) return '0 B';
    var units = ['B', 'KB', 'MB', 'GB'];
    var i = Math.floor(Math.log(bytes) / Math.log(1024));
    return (bytes / Math.pow(1024, i)).toFixed(1) + ' ' + units[i];
  }

  function fileChangeLabel(snap) {
    var fileCount = snap.files_changed != null ? snap.files_changed : (snap.modified_files_count != null ? snap.modified_files_count : 0);
    return fileCount + ' file(s) +' + (snap.insertions || 0) + '/-' + (snap.deletions || 0);
  }

  function diffUrl(id, mode) {
    var base = API + '/snapshots/' + encodeURIComponent(id) + '/diff';
    if (mode === 'working_tree') return base + '?compare_to=WORKING_TREE';
    return base;
  }

  function TimeMachineDashboard() {
    var statusState = useState(null);
    var status = statusState[0];
    var setStatus = statusState[1];

    var snapshotsState = useState([]);
    var snapshots = snapshotsState[0];
    var setSnapshots = snapshotsState[1];

    var selectedSnapState = useState(null);
    var selectedSnap = selectedSnapState[0];
    var setSelectedSnap = selectedSnapState[1];

    var diffState = useState(null);
    var diff = diffState[0];
    var setDiff = diffState[1];

    var loadingState = useState(true);
    var loading = loadingState[0];
    var setLoading = loadingState[1];

    var noticeState = useState(null);
    var notice = noticeState[0];
    var setNotice = noticeState[1];

    var diffModeState = useState('turn');
    var diffMode = diffModeState[0];
    var setDiffMode = diffModeState[1];

    var searchState = useState('');
    var searchQuery = searchState[0];
    var setSearchQuery = searchState[1];

    var matchState = useState(null);
    var matchIds = matchState[0];
    var setMatchIds = matchState[1];

    var planState = useState(null);
    var restorePlan = planState[0];
    var setRestorePlan = planState[1];

    var loadData = useCallback(function () {
      setLoading(true);
      fetchJSON(API + '/status')
        .then(function (stat) {
          setStatus(stat);
          return fetchJSON(API + '/snapshots?limit=50');
        })
        .then(function (snapRes) {
          var list = (snapRes && snapRes.snapshots) || [];
          setSnapshots(list);
          if (list.length > 0 && !selectedSnap) {
            setSelectedSnap(list[0]);
          }
        })
        .catch(function (err) {
          setNotice({ kind: 'error', message: err.message || 'Failed to load Time Machine data' });
        })
        .finally(function () {
          setLoading(false);
        });
    }, [selectedSnap]);

    useEffect(function () {
      loadData();
    }, [loadData]);

    useEffect(function () {
      if (!selectedSnap || !selectedSnap.id) {
        setDiff(null);
        return;
      }
      fetchJSON(diffUrl(selectedSnap.id, diffMode))
        .then(function (res) {
          setDiff(res.diff || 'No filesystem differences recorded vs workspace.');
        })
        .catch(function () {
          setDiff('Diff unavailable.');
        });
    }, [selectedSnap, diffMode]);

    function onSearch(event) {
      var q = event && event.target ? event.target.value : '';
      setSearchQuery(q);
      if (!q || !q.trim()) {
        setMatchIds(null);
        return;
      }
      fetchJSON(API + '/snapshots/search?q=' + encodeURIComponent(q.trim()))
        .then(function (res) {
          var ids = ((res && res.matches) || []).map(function (m) { return m.snapshot_id || m.id; }).filter(Boolean);
          setMatchIds(ids);
        })
        .catch(function (err) {
          setMatchIds([]);
          setNotice({ kind: 'error', message: err.message || 'Search failed' });
        });
    }

    function onPreview() {
      if (!selectedSnap || !selectedSnap.id) return;
      fetchJSON(API + '/snapshots/' + encodeURIComponent(selectedSnap.id) + '/restore', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ dry_run: true, user_approved: false })
      }).then(function (res) {
        var restored = (res.planned_restored_files || []).filter(function (f) { return f && f !== '(all files)'; });
        var unlinked = (res.planned_unlinked_files || res.planned_deleted_stray_files || []).filter(Boolean);
        setRestorePlan({
          snapshotId: selectedSnap.id,
          step: 'preview',
          plannedRestored: restored,
          plannedUnlinked: unlinked,
          fullWorkspace: (res.planned_restored_files || []).indexOf('(all files)') !== -1,
          selected: restored.concat(unlinked),
          backupId: null
        });
        setNotice(null);
      }).catch(function (err) {
        setNotice({ kind: 'error', message: err.message || 'Preview restore failed' });
      });
    }

    function toggleFile(file) {
      if (!restorePlan) return;
      var selected = restorePlan.selected.slice();
      var idx = selected.indexOf(file);
      if (idx === -1) selected.push(file);
      else selected.splice(idx, 1);
      setRestorePlan(Object.assign({}, restorePlan, { selected: selected }));
    }

    function onConfirm() {
      if (!restorePlan || !restorePlan.snapshotId) return;
      var selected = restorePlan.selected || [];
      if (!restorePlan.fullWorkspace && selected.length === 0) {
        setNotice({ kind: 'error', message: 'Select at least one file, or check Restore entire workspace.' });
        return;
      }
      var payload = { dry_run: false, user_approved: true };
      if (!restorePlan.fullWorkspace) payload.file_paths = selected;
      fetchJSON(API + '/snapshots/' + encodeURIComponent(restorePlan.snapshotId) + '/restore', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      }).then(function (res) {
        var backupId = res.pre_restore_backup_id || null;
        setRestorePlan(Object.assign({}, restorePlan, { step: 'done', backupId: backupId }));
        setNotice({
          kind: 'info',
          message: "Restored snapshot '" + restorePlan.snapshotId + "'. Safety backup: " + (backupId || 'n/a') + '.'
        });
        loadData();
      }).catch(function (err) {
        setNotice({ kind: 'error', message: err.message || 'Restore failed' });
      });
    }

    var visible = matchIds == null
      ? snapshots
      : snapshots.filter(function (snap) { return matchIds.indexOf(snap.id) !== -1; });

    var fileChoices = [];
    if (restorePlan && restorePlan.step === 'preview') {
      (restorePlan.plannedRestored || []).forEach(function (f) { fileChoices.push({ file: f, kind: 'restore' }); });
      (restorePlan.plannedUnlinked || []).forEach(function (f) { fileChoices.push({ file: f, kind: 'unlink' }); });
    }

    return React.createElement(
      'div',
      { className: 'space-y-6 p-6 max-w-6xl mx-auto' },

      React.createElement(
        'div',
        { className: 'flex flex-wrap items-center justify-between gap-4 border-b border-border pb-4' },
        React.createElement(
          'div',
          null,
          React.createElement('h1', { className: 'text-2xl font-bold tracking-tight' }, 'Hermes Time Machine'),
          React.createElement(
            'p',
            { className: 'text-sm text-muted-foreground mt-1' },
            'Session timeline scrubber, surgical rollback, and autonomous self-healing.'
          )
        ),
        React.createElement(
          'div',
          { className: 'flex items-center gap-2' },
          React.createElement('input', {
            type: 'search',
            placeholder: 'Search checkpoints',
            value: searchQuery,
            onChange: onSearch,
            className: 'h-8 rounded-md border border-border bg-background px-2 text-xs'
          }),
          React.createElement(Button, { onClick: loadData, disabled: loading }, loading ? 'Refreshing...' : 'Refresh')
        )
      ),

      notice &&
        React.createElement(
          'div',
          {
            className:
              'p-4 rounded-md border text-sm ' +
              (notice.kind === 'error'
                ? 'border-destructive/40 bg-destructive/10 text-destructive'
                : 'border-blue-500/40 bg-blue-500/10 text-blue-400')
          },
          notice.message
        ),

      React.createElement(
        'div',
        { className: 'grid grid-cols-2 sm:grid-cols-4 gap-4' },
        React.createElement(
          Card,
          null,
          React.createElement(CardContent, { className: 'p-4' },
            React.createElement('div', { className: 'text-2xl font-bold' }, (status && status.snapshot_count) || snapshots.length),
            React.createElement('div', { className: 'text-xs text-muted-foreground mt-1 uppercase tracking-wider' }, 'Snapshots')
          )
        ),
        React.createElement(
          Card,
          null,
          React.createElement(CardContent, { className: 'p-4' },
            React.createElement('div', { className: 'text-2xl font-bold' }, formatBytes(status && status.store_size_bytes)),
            React.createElement('div', { className: 'text-xs text-muted-foreground mt-1 uppercase tracking-wider' }, 'Private Store')
          )
        ),
        React.createElement(
          Card,
          null,
          React.createElement(CardContent, { className: 'p-4' },
            React.createElement(
              'div',
              { className: 'text-2xl font-bold ' + (status && status.self_heal_available ? 'text-destructive' : 'text-emerald-500') },
              status && status.self_heal_available ? 'Alert' : 'Nominal'
            ),
            React.createElement('div', { className: 'text-xs text-muted-foreground mt-1 uppercase tracking-wider' }, 'Self-Heal Status')
          )
        ),
        React.createElement(
          Card,
          null,
          React.createElement(CardContent, { className: 'p-4' },
            React.createElement('div', { className: 'text-2xl font-bold font-mono text-xs truncate', title: (status && status.active_session_id) || 'None' },
              (status && status.active_session_id) || 'Default'
            ),
            React.createElement('div', { className: 'text-xs text-muted-foreground mt-1 uppercase tracking-wider' }, 'Active Session')
          )
        )
      ),

      React.createElement(
        'div',
        { className: 'grid grid-cols-1 md:grid-cols-2 gap-6' },

        React.createElement(
          Card,
          null,
          React.createElement(
            CardHeader,
            null,
            React.createElement(CardTitle, null, 'Session Checkpoints')
          ),
          React.createElement(
            CardContent,
            { className: 'space-y-2 max-h-[500px] overflow-y-auto pr-1' },
            visible.length === 0 &&
              React.createElement('div', { className: 'p-8 text-center text-muted-foreground text-sm' }, 'No checkpoints recorded yet.'),
            visible.map(function (snap) {
              var isSelected = selectedSnap && selectedSnap.id === snap.id;
              var isAlert = snap.self_heal_candidate || snap.status === 'error';
              var highlighted = matchIds != null && matchIds.indexOf(snap.id) !== -1;
              return React.createElement(
                'div',
                {
                  key: snap.id,
                  onClick: function () { setSelectedSnap(snap); setRestorePlan(null); },
                  className:
                    'p-3 rounded-md border cursor-pointer transition-all ' +
                    (isSelected
                      ? 'border-primary bg-primary/10'
                      : 'border-border/60 hover:border-border hover:bg-muted/50'),
                  style: highlighted ? { boxShadow: 'inset 0 0 0 1px rgba(250, 204, 21, 0.8)' } : undefined
                },
                React.createElement(
                  'div',
                  { className: 'flex items-center justify-between gap-2' },
                  React.createElement(
                    Badge,
                    { className: isAlert ? 'bg-destructive/20 text-destructive' : 'bg-secondary text-secondary-foreground' },
                    snap.tool_name || 'checkpoint'
                  ),
                  React.createElement('span', { className: 'text-xs text-muted-foreground font-mono' }, formatTime(snap.created_at))
                ),
                React.createElement(
                  'div',
                  { className: 'mt-2 text-xs font-mono text-muted-foreground flex items-center justify-between gap-2' },
                  React.createElement('span', { className: 'truncate max-w-[200px]', title: snap.id }, snap.id),
                  React.createElement('span', null, fileChangeLabel(snap))
                )
              );
            })
          )
        ),

        React.createElement(
          Card,
          null,
          React.createElement(
            CardHeader,
            { className: 'flex flex-row items-center justify-between pb-2 gap-2' },
            React.createElement(CardTitle, null, selectedSnap ? 'Diff: ' + selectedSnap.id : 'Inspection'),
            selectedSnap && React.createElement(
              'div',
              { className: 'flex items-center gap-2' },
              React.createElement(Button, {
                onClick: function () { setDiffMode('turn'); },
                className: diffMode === 'turn' ? 'bg-primary/20' : ''
              }, 'Turn Diff'),
              React.createElement(Button, {
                onClick: function () { setDiffMode('working_tree'); },
                className: diffMode === 'working_tree' ? 'bg-primary/20' : ''
              }, 'Live Working Tree Drift'),
              React.createElement(Button, { onClick: onPreview }, 'Preview Restore')
            )
          ),
          React.createElement(
            CardContent,
            null,
            restorePlan && restorePlan.step === 'preview' && React.createElement(
              'div',
              { className: 'mb-3 rounded-md border border-blue-500/30 bg-blue-500/5 p-3 space-y-2' },
              React.createElement('div', { className: 'text-xs font-semibold' }, 'Step 1 — choose files, then confirm'),
              React.createElement(
                'label',
                { className: 'flex items-center gap-2 text-xs' },
                React.createElement('input', {
                  type: 'checkbox',
                  checked: !!restorePlan.fullWorkspace,
                  onChange: function () {
                    setRestorePlan(Object.assign({}, restorePlan, { fullWorkspace: !restorePlan.fullWorkspace }));
                  }
                }),
                'Restore entire workspace'
              ),
              fileChoices.map(function (item) {
                var checked = (restorePlan.selected || []).indexOf(item.file) !== -1;
                return React.createElement(
                  'label',
                  { key: item.kind + ':' + item.file, className: 'flex items-center gap-2 text-xs font-mono' },
                  React.createElement('input', {
                    type: 'checkbox',
                    checked: checked,
                    onChange: function () { toggleFile(item.file); }
                  }),
                  (item.kind === 'unlink' ? 'unlink ' : 'restore ') + item.file
                );
              }),
              React.createElement(
                'div',
                { className: 'flex items-center gap-2 pt-1' },
                React.createElement(Button, { onClick: onConfirm }, 'Confirm & Restore Selected Files'),
                React.createElement(Button, { onClick: function () { setRestorePlan(null); } }, 'Cancel')
              )
            ),
            restorePlan && restorePlan.step === 'done' && React.createElement(
              'div',
              { className: 'mb-3 text-xs text-emerald-500' },
              'Safety backup ' + (restorePlan.backupId || 'recorded') + '.'
            ),
            React.createElement(
              'pre',
              { className: 'p-3 rounded-md bg-muted font-mono text-xs overflow-auto max-h-[440px] whitespace-pre text-foreground/90' },
              diff || 'Select a checkpoint on the left to inspect its unified diff.'
            )
          )
        )
      ),

      React.createElement(
        'div',
        { className: 'p-4 rounded-md border border-border/40 text-xs text-muted-foreground space-y-1' },
        React.createElement('div', { className: 'font-semibold text-foreground/80' }, 'Safety & Slash Commands'),
        React.createElement('div', null, '• Use ', React.createElement('code', { className: 'font-mono text-foreground' }, '/tm list'), ' to view session snapshots.'),
        React.createElement('div', null, '• Use ', React.createElement('code', { className: 'font-mono text-foreground' }, '/tm restore <snapshot_id>'), ' to restore files after human review.'),
        React.createElement('div', null, '• Use ', React.createElement('code', { className: 'font-mono text-foreground' }, '/tm recover'), ' to diagnose test failures and generate recovery plans.'),
        React.createElement('div', null, '• Preview Restore stays a dry-run until Confirm is clicked. /tm commit, comment, clone, push, and pull never touch the project .git. v0.4.1')
      )
    );
  }

  var plugins = window.__HERMES_PLUGINS__;
  if (plugins && typeof plugins.register === 'function') {
    plugins.register('time-machine', TimeMachineDashboard);
  }
})();
