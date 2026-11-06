# 🎬 Hermes Time Machine — Official 75s Demo Video Script (Winning Pacing)

**Title:** Hermes Time Machine: Autonomous Self-Healing & Visual Time Travel for Hermes Agent  
**Duration:** 75 Seconds (Target: < 90 Seconds)  
**Hackathon:** Nous Research Hermes Agent Hackathon  
**Target Audience:** Hackathon Judges, Autonomous Agent Developers, AI Safety Engineers  

---

## ⏱️ High-Velocity 75-Second Pacing Chart

```
+---------------+-----------------------------------------------------------------------------------+
| 0:00 - 0:08   | The Cold Open Hook: Instant Test Failure & Flashing ⚠️ Self-Heal Status Badge      |
| 0:08 - 0:30   | The "Surgical vs. Blind Rollback" Judge Moment: 1-Call Autonomous Recovery        |
| 0:30 - 0:55   | Interactive Scrubber & "Rollback of a Rollback" Safety Backup                     |
| 0:55 - 1:15   | Session Branching (🌿 Forked) & "What Introduced This?" Diff Search               |
+---------------+-----------------------------------------------------------------------------------+
```

---

### Act 1: The Cold Open Hook (0:00 – 0:08)
- **Visual:** Open directly on Hermes Desktop with the Time Machine Right-Rail open. Hermes is working on a payment service. It edits `tax.py` (good multi-currency feature) and `billing.py` (broken runtime bug), leaving an untracked `debug_dump.tmp` file. Pytest runs -> **RED TEST FAILURE**.
- **Visual Alert:** In the bottom-right status bar, the badge instantly flashes:  
  `⚠️ Self-Heal Available • Snapshot snap_feature recommended`.
- **Voiceover:**  
  *"Every agent user has experienced this: an agent adds a feature in tax.py, accidentally breaks billing.py, and leaves stray files behind. Normally, you're stuck in hallucination loops. But watch what happens with Hermes Time Machine."*

---

### Act 2: The "Surgical vs. Blind Rollback" Judge Moment (0:08 – 0:30)
- **Visual:** Time Machine's `transform_tool_result` middleware intercepts the error and injects the actionable self-heal notice:
  ```markdown
  [🛡️ Time Machine Self-Heal Notice & Recommendation]:
  Command 'pytest -q' failed after file mutations.
  • Culprit files identified: ['billing.py', 'debug_dump.tmp']
  • Preserved files: ['tax.py']
  • Recommended safe snapshot: 'snap_feature' (Confidence: high)
  • 1-Call Recovery: time_machine_recover(auto_apply=true, confirm=true, auto_verify=true)
  ```
- **The Contrast:**  
  Point out on screen: *A blind `git reset --hard` would obliterate our new tax feature!*
- **Action:**  
  Hermes executes `time_machine_recover(auto_apply=true, confirm=true, auto_verify=true)`.
  In **ONE single tool call**:
  1. Correlates the error traceback to `billing.py`.
  2. Captures an automatic Pre-Restore Safety Checkpoint (`snap_pre_restore_backup`).
  3. Surgically reverts `billing.py`.
  4. Safely unlinks newly created `debug_dump.tmp` without git pathspec errors.
  5. **Keeps the good `tax.py` multi-currency feature 100% intact**.
  6. Auto-verifies by re-running `pytest` inside the same call: **`verification: {passed: true, exit_code: 0}`**!
- **Voiceover:**  
  *"In a single tool call, Time Machine surgically reverts only the broken file, unlinks the stray debug dump, preserves our brand-new tax code, and auto-verifies that tests are green — zero LLM round-trips wasted."*

---

### Act 3: Interactive Scrubber & "Rollback of a Rollback" (0:30 – 0:55)
- **Visual:** Switch to Hermes Desktop Studio View (`desktop/plugin.js`). Click along the horizontal timeline scrubber:
  `● ─── ● ─── ● ─── ⚠️ ─── 🌿 ─── ✅`
- **Actions:**
  1. Hover on nodes for instant mini-diff and file previews.
  2. Toggle between **Side-by-Side Split Diff** and **Unified Diff**.
  3. Inspect **"Compare to Live Working Tree"** to see net changes against baseline.
  4. Highlight the `pre_restore_safety_backup` snapshot in the ledger: **even rollbacks can be rolled back!** True zero data loss.
- **Voiceover:**  
  *"For human oversight, our Desktop Studio offers an interactive horizontal scrubber, side-by-side split diffs, and live working tree comparison. And because every restore captures a safety checkpoint first, even a rollback is reversible."*

---

### Act 4: Session Branching & Diff Search (0:55 – 1:15)
- **Visual:** Demonstrate the two killer developer tools:
  1. **Session Branching (`/rewind branch experiment-v2`):** Shows the new branch tree with the `🌿 Forked` pill in the UI.
  2. **Diff Search (`/rewind search get_currency_symbol`):** Instantly pinpoints the exact snapshot, turn, tool name, and `+` / `-` diff lines where that function was first introduced.
- **Voiceover:**  
  *"Need to explore a risky refactor? Fork your session with one click. Wondering when a bug crept in? Search snapshot history to pinpoint the exact turn and diff line that introduced it."*
- **Final Shot (1:15):**  
  Show the test and validator suite: **23/23 Pytest passing • 16/16 Hermes Validator checks green**.  
  *"Zero hallucination loops. Zero data loss. 100% autonomous recovery. That's Hermes Time Machine."*

---

## 💻 1-Command Live Demonstration
To run the automated, color-coded walkthrough in your terminal:
```bash
python scripts/demo_walkthrough.py
```
To run full admission validation:
```bash
pytest tests/ -v
python -c "from hermes_cli.plugin_validate import validate_plugin_dir; from pathlib import Path; print(validate_plugin_dir(Path('.')))"
```
