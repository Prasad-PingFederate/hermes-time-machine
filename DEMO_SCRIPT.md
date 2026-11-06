# 🎬 Hermes Time Machine — Official <90s Demo Video Script

**Title:** Hermes Time Machine: Autonomous Self-Healing & Visual Time Travel for Hermes Agent  
**Duration:** 88 Seconds  
**Hackathon:** Nous Research Hermes Agent Hackathon  
**Target Audience:** Hackathon Judges, Autonomous Agent Developers, AI Safety Engineers  

---

## ⏱️ Video Timeline & Screen Flow

```
+---------------------------------------------------------------------------------------------------+
| 0:00 - 0:15 | Introduction: The Broken Code Dilemma & Safe Mode Status Bar                        |
| 0:15 - 0:35 | The Incident: Agent Makes a Buggy Edit + Leaves a Stray Temp File                   |
| 0:35 - 0:55 | Autonomous Self-Healing: Culprit Detection & 1-Call Surgical Recovery               |
| 0:55 - 1:15 | Studio Deep-Dive: Interactive Timeline Scrubber & Side-by-Side Diff                  |
| 1:15 - 1:30 | The Verdict: Zero Data Loss, Green Tests, and True Zero-Risk Autonomy                |
+---------------------------------------------------------------------------------------------------+
```

---

### Segment 1: Introduction & Baseline (0:00 – 0:15)
- **Visual:** Hermes Agent CLI and Electron Desktop open side-by-side. The status bar in the bottom right displays `🛡️ Safe Mode • 4 snaps`. A Python payment service (`billing.py`, `tax.py`) is passing all unit tests.
- **Voiceover:**  
  *"Every developer knows the dread of an autonomous agent editing three files, breaking the build, and getting lost in hallucinations trying to fix it. Meet Hermes Time Machine — the first zero-risk, shadow-git time travel and self-healing engine built specifically for Hermes Agent."*
- **Action:** Open terminal and run `pytest`. Tests pass 100%. Show Time Machine status bar badge.

---

### Segment 2: The Failure & Culprit Flagging (0:15 – 0:35)
- **Visual:** Hermes implements multi-currency EUR tax in `tax.py`, but introduces a fatal `RuntimeError` in `billing.py` and creates a stray `debug_dump.tmp` file. Pytest runs and fails catastrophically.
- **Voiceover:**  
  *"Here, the agent successfully added a currency feature in tax.py, but broke billing.py and left an untracked temporary file. Pytest immediately fails."*
- **Action:** Terminal displays test failure output. Instantly, Time Machine's `transform_tool_result` middleware intercepts the error and injects an actionable notice directly into the context window:
  ```markdown
  [🛡️ Time Machine Self-Heal Notice & Recommendation]:
  Command failed after recent file mutations.
  • Identified culprit file(s): ['billing.py', 'debug_dump.tmp']
  • Recommended safe checkpoint: 'snap_feature' (Confidence: high)
  • Quick 1-call autonomous recovery:
    time_machine_recover(auto_apply=true, confirm=true)
  ```

---

### Segment 3: Autonomous Recovery (0:35 – 0:55)
- **Visual:** Hermes reads the recommendation and executes `time_machine_recover(auto_apply=true, confirm=true)`. 
- **Voiceover:**  
  *"Instead of blindly trying random edits, Hermes invokes Time Machine's autonomous recovery tool. Time Machine correlates the traceback, pinpoints billing.py and the stray file, takes an automatic Pre-Restore Safety Checkpoint, and surgically reverts only the broken pieces — while keeping the new tax feature completely intact!"*
- **Action:** 
  1. `billing.py` reverts to working code.
  2. `debug_dump.tmp` is unlinked from disk without git pathspec errors.
  3. `tax.py` keeps EUR multi-currency code.
  4. Pytest re-runs: **100% GREEN**.

---

### Segment 4: Desktop Timeline Scrubber & Diff View (0:55 – 1:15)
- **Visual:** Switch focus to Hermes Desktop Studio View (`desktop/plugin.js`). Show the new horizontal timeline scrubber at the top: `● ─── ● ─── ● ─── ⚠️ ─── ✅`.
- **Voiceover:**  
  *"For human-in-the-loop oversight, the Desktop interface features an interactive horizontal scrubber. Hover over any step for a mini-diff preview. Toggle between side-by-side split and unified diffs. Want to revert a single file or export a full debugging log? It's all just one click."*
- **Action:** 
  1. Hover over a node in the scrubber showing file tooltip.
  2. Click **"Compare Against: Live Working Tree"** dropdown.
  3. Click **"📸 Snapshot"** to capture an immediate manual checkpoint.
  4. Click **"📥 Export"** to generate instant Markdown timeline.

---

### Segment 5: The Winning Edge (1:15 – 1:30)
- **Visual:** Split screen showing the 18/18 Pytest suite passing and `hermes_cli.plugin_validate` with 16/16 checks green.
- **Voiceover:**  
  *"Time Machine pairs shadow-git commits with Hermes state.db turn watermarks. It gives agents the ultimate superpower: the fearlessness to innovate, knowing every rollback is itself reversible. That is Hermes Time Machine."*
- **Action:** Show final summary slide with GitHub link: `https://github.com/Prasad-PingFederate/hermes-time-machine`.

---

## 💡 Recording Tips & Commands
1. Run the live demo script directly to capture terminal footage:
   ```bash
   python scripts/demo_walkthrough.py
   ```
2. Or demonstrate the CLI slash command:
   ```bash
   /rewind demo
   /rewind list
   /rewind diff snap_xxx WORKING_TREE
   /rewind export
   ```
3. Run the full test validation:
   ```bash
   pytest tests/ -v
   python -m hermes_cli.plugin_validate .
   ```
