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
      fetchJSON(API + '/snapshots/' + encodeURIComponent(selectedSnap.id) + '/diff')
        .then(function (res) {
          setDiff(res.diff || 'No filesystem differences recorded vs workspace.');
        })
        .catch(function () {
          setDiff('Diff unavailable.');
        });
    }, [selectedSnap]);

    return React.createElement(
      'div',
      { className: 'space-y-6 p-6 max-w-6xl mx-auto' },

      // Header Bar
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
          React.createElement(Button, { onClick: loadData, disabled: loading }, loading ? 'Refreshing...' : 'Refresh')
        )
      ),

      // Notice / Safety Banner
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

      // Stat Cards Grid
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

      // Main Grid: Timeline + Diff
      React.createElement(
        'div',
        { className: 'grid grid-cols-1 md:grid-cols-2 gap-6' },

        // Snapshot Timeline Card
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
            snapshots.length === 0 &&
              React.createElement('div', { className: 'p-8 text-center text-muted-foreground text-sm' }, 'No checkpoints recorded yet.'),
            snapshots.map(function (snap) {
              var isSelected = selectedSnap && selectedSnap.id === snap.id;
              var isAlert = snap.self_heal_candidate || snap.status === 'error';
              return React.createElement(
                'div',
                {
                  key: snap.id,
                  onClick: function () { setSelectedSnap(snap); },
                  className:
                    'p-3 rounded-md border cursor-pointer transition-all ' +
                    (isSelected
                      ? 'border-primary bg-primary/10'
                      : 'border-border/60 hover:border-border hover:bg-muted/50')
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
                  { className: 'mt-2 text-xs font-mono text-muted-foreground flex items-center justify-between' },
                  React.createElement('span', { className: 'truncate max-w-[200px]', title: snap.id }, snap.id),
                  snap.modified_files_count !== undefined &&
                    React.createElement('span', null, snap.modified_files_count + ' file(s)')
                )
              );
            })
          )
        ),

        // Diff & Inspection Card
        React.createElement(
          Card,
          null,
          React.createElement(
            CardHeader,
            { className: 'flex flex-row items-center justify-between pb-2' },
            React.createElement(CardTitle, null, selectedSnap ? 'Diff: ' + selectedSnap.id : 'Inspection'),
            selectedSnap &&
              React.createElement(
                Button,
                {
                  onClick: function () {
                    setNotice({
                      kind: 'info',
                      message: "To restore snapshot '" + selectedSnap.id + "', run '/tm restore " + selectedSnap.id + "' in chat or review the diff. Restorations require explicit user approval."
                    });
                  }
                },
                'Preview Restore'
              )
          ),
          React.createElement(
            CardContent,
            null,
            React.createElement(
              'pre',
              { className: 'p-3 rounded-md bg-muted font-mono text-xs overflow-auto max-h-[440px] whitespace-pre text-foreground/90' },
              diff || 'Select a checkpoint on the left to inspect its unified diff.'
            )
          )
        )
      ),

      // Footer Explanations
      React.createElement(
        'div',
        { className: 'p-4 rounded-md border border-border/40 text-xs text-muted-foreground space-y-1' },
        React.createElement('div', { className: 'font-semibold text-foreground/80' }, 'Safety & Slash Commands'),
        React.createElement('div', null, '• Use ', React.createElement('code', { className: 'font-mono text-foreground' }, '/tm list'), ' to view session snapshots.'),
        React.createElement('div', null, '• Use ', React.createElement('code', { className: 'font-mono text-foreground' }, '/tm restore <snapshot_id>'), ' to restore files after human review.'),
        React.createElement('div', null, '• Use ', React.createElement('code', { className: 'font-mono text-foreground' }, '/tm recover'), ' to diagnose test failures and generate recovery plans.'),
        React.createElement('div', null, '• For conversation tree branching and rewind, use Hermes core ', React.createElement('code', { className: 'font-mono text-foreground' }, '/rewind'), ' and ', React.createElement('code', { className: 'font-mono text-foreground' }, '/branch'), '.')
      )
    );
  }

  var plugins = window.__HERMES_PLUGINS__;
  if (plugins && typeof plugins.register === 'function') {
    plugins.register('time-machine', TimeMachineDashboard);
  }
})();
