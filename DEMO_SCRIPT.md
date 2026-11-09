# 🎬 Hermes Time Machine — Official Demo Script (v0.3.0 Architecture)

**Title:** Hermes Time Machine: Autonomous Self-Healing & Visual Time Travel for Hermes Agent  
**Duration:** 75 Seconds (Target: < 90 Seconds)  
**Target Audience:** Hackathon Judges, Autonomous Agent Developers, AI Safety Engineers  

---

## ⏱️ High-Velocity 75-Second Pacing Chart

```
+---------------+-----------------------------------------------------------------------------------+
| 0:00 - 0:08   | The Cold Open Hook: Instant Test Failure & Status Bar Chip                        |
| 0:08 - 0:30   | The "Surgical vs. Blind Rollback": Dry-Run Diagnosis & Human-Approved Restore     |
| 0:30 - 0:55   | Interactive Scrubber & "Rollback of a Rollback" Pre-Restore Safety Backup         |
| 0:55 - 1:15   | Diff Search (`/tm search`) & Full Validation Suite Passing                        |
+---------------+-----------------------------------------------------------------------------------+
```

---

### Act 1: The Cold Open Hook (0:00 – 0:08)
- **Visual:** Open directly on Hermes Desktop with the Time Machine timeline pane open. Hermes is working on a payment service. It edits `tax.py` (good multi-currency feature) and `billing.py` (broken runtime bug), leaving an untracked `debug_dump.tmp` file. Pytest runs -> **RED TEST FAILURE**.
- **Visual Alert:** In the bottom-right status bar, the chip reflects:  
  `⚠️ Self-Heal Available • Snapshot snap_feature recommended`.
- **Voiceover:**  
  *"Every agent user has experienced this: an agent adds a feature in tax.py, accidentally breaks billing.py, and leaves stray files behind. Normally, you're stuck in hallucination loops. But watch what happens with Hermes Time Machine."*

---

### Act 2: The "Surgical vs. Blind Rollback" Judge Moment (0:08 – 0:30)
- **Visual:** Time Machine's `transform_tool_result` hook intercepts the failure and injects the actionable self-heal notice:
  ```markdown
  [🛡️ Time Machine Self-Heal Notice & Recommendation]:
  Command 'pytest -q' failed after file mutations.
  • Culprit files identified: ['billing.py', 'debug_dump.tmp']
  • Preserved files: ['tax.py']
  • Recommended safe snapshot: 'snap_feature'
  • Diagnosis: time_machine_recover()
  • Suggested verify command: pytest -q
  • To apply: /tm restore snap_feature
  ```
- **The Contrast:**  
  Point out on screen: *A blind `git reset --hard` would obliterate our new tax feature!*
- **Action:**  
  1. Hermes calls `time_machine_recover(session_id=...)` in **dry-run mode** (never modifying disk without user approval). It returns:
     - Root cause diagnosis correlating traceback to `billing.py`.
     - Planned surgical restore for `billing.py`.
     - Planned unlinking for stray `debug_dump.tmp`.
     - Preserved files list keeping `tax.py` intact.
     - `suggested_verify_command: "pytest -q"`.
  2. The user executes `/tm restore snap_feature` (or approves restoration via Hermes approval prompt / Desktop UI).
  3. Time Machine captures an automatic `pre_restore_safety_backup` into the isolated plugin store, surgically restores `billing.py`, unlinks `debug_dump.tmp`, and leaves `tax.py` untouched.
  4. Hermes executes the `suggested_verify_command` through its native `terminal` tool (respecting Docker/SSH backends): **Tests turn GREEN!**
- **Voiceover:**  
  *"Time Machine diagnoses the culprit file in dry-run mode, preserves our brand-new tax code, and waits for user approval before surgically restoring billing.py and cleaning stray files. Hermes then verifies using its native terminal tool — zero hallucination loops, 100% user control."*

---

### Act 3: Interactive Scrubber & "Rollback of a Rollback" (0:30 – 0:55)
- **Visual:** Switch to Hermes Desktop Studio View (`desktop/plugin.js`). Click along the horizontal timeline scrubber:
  `● ─── ● ─── ● ─── ⚠️ ─── 🛡️ ─── ✅`
- **Actions:**
  1. Hover on nodes for instant mini-diff and file previews.
  2. Toggle between **Side-by-Side Split Diff** and **Unified Diff**.
  3. Inspect **"Compare to Live Working Tree"** to see net changes against baseline.
  4. Highlight the `pre_restore_safety_backup` snapshot in the ledger: **even rollbacks can be rolled back!** True zero data loss.
- **Voiceover:**  
  *"For human oversight, our Desktop Studio offers an interactive horizontal scrubber, side-by-side split diffs, and live working tree comparison. And because every restore captures an isolated safety checkpoint first, even a rollback is reversible."*

---

### Act 4: Diff Search & Verification Suite (0:55 – 1:15)
- **Visual:** Demonstrate the historical search capability:
  - **Diff Search (`/tm search get_currency_symbol` or `time_machine_search`):** Instantly pinpoints the exact snapshot, turn, tool name, and `+` / `-` diff lines where that function was first introduced.
- **Voiceover:**  
  *"Wondering when a bug crept in? Search snapshot history with /tm search to pinpoint the exact turn and diff line that introduced it."*
- **Final Shot (1:15):**  
  Show the test and validator suite: **33/33 Pytest passing • 16/16 Hermes Validator checks green**.  
  *"Zero hallucination loops. Zero data loss. 100% human-approved recovery. That's Hermes Time Machine."*

---

## 💻 Live Verification & Demonstration
To run the automated, color-coded walkthrough in your terminal:
```bash
python scripts/demo_walkthrough.py
```
To run full test discovery and admission validation:
```bash
python -m unittest discover -s tests -v
pytest -v
hermes plugins validate .
```
