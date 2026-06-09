#!/usr/bin/env python3
"""
ui.py — NUCLEUS terminal UI
============================
A rich terminal interface that shows:
  - Live activity log (what nucleus is doing)
  - Goal queue with status
  - Skill map (which tools are winning)
  - Chat/input panel with predictive suggestions
  - Suggestion bar: "did you mean to...?" based on context

Run:
    python3 ui.py

Predictions are based on:
  - Time of day patterns  (morning → open browser, check email)
  - What you just did     (built code → "run it?" "commit it?")
  - Goal history          (frequent goals rise to top)
  - Sequence patterns     (A usually follows B)
"""

import os, sys, json, time, re, threading
from pathlib   import Path
from datetime  import datetime
from collections import Counter, defaultdict

from textual.app        import App, ComposeResult
from textual.widgets    import (Header, Footer, Input, RichLog,
                                Static, Button, Label)
from textual.containers import Horizontal, Vertical, ScrollableContainer
from textual.reactive   import reactive
from textual.binding    import Binding
from textual            import events

# Import nucleus components
sys.path.insert(0, str(Path(__file__).parent))
from skills  import SkillMap, PatternMemory, GoalQueue, Goal
from tools   import ToolExecutor, CloudRouter, C
from nucleus import Nucleus, ConvMemory


# ═══════════════════════════════════════════════
#  PREDICTOR
#  Watches what you do and suggests what's next
# ═══════════════════════════════════════════════
class Predictor:
    """
    Learns sequences: if you do A, what do you usually do next?

    Three signal sources:
      1. Sequence memory  — A → B patterns from history
      2. Time patterns    — morning/afternoon/evening habits
      3. Context signals  — just built code → suggest git/run/test

    Suggestions are short, actionable, tappable.
    Nucleus asks "did you mean to do X?" — you press a key to confirm.
    """

    SEQUENCE_PATH = Path("memory/sequences.json")

    # Hard-coded context rules — instant, no learning needed
    CONTEXT_RULES = [
        # After building code
        {"trigger": r"build|create|write|make",
         "goal_type": "code",
         "suggestions": [
             "run the code",
             "test it",
             "git add . && git commit -m 'new build'",
             "open the output folder",
         ]},
        # After opening browser
        {"trigger": r"open chrome|open firefox|open browser",
         "goal_type": "open_app",
         "suggestions": [
             "search for documentation",
             "open github",
             "check email",
         ]},
        # After checking system
        {"trigger": r"disk|ram|cpu|memory|status",
         "goal_type": "system",
         "suggestions": [
             "clean up disk",
             "kill heavy processes",
             "check what's running",
         ]},
        # After network check
        {"trigger": r"ip|network|ping|curl",
         "goal_type": "shell",
         "suggestions": [
             "test internet speed",
             "check open ports",
             "ssh into server",
         ]},
        # After any search
        {"trigger": r"search|find|what is|who is",
         "goal_type": "search",
         "suggestions": [
             "save this to a file",
             "search for more",
             "open in browser",
         ]},
    ]

    # Time-of-day suggestions
    TIME_SUGGESTIONS = {
        "morning":   ["open chrome", "check system status",
                      "what's the weather", "check email"],
        "afternoon": ["build something", "search for documentation",
                      "run my scripts", "check git status"],
        "evening":   ["git add . && git commit", "clean up disk",
                      "check what's running", "shutdown in 1 hour"],
        "night":     ["shutdown", "save all work",
                      "run overnight tasks"],
    }

    def __init__(self, conv: ConvMemory, queue: GoalQueue):
        self.conv    = conv
        self.queue   = queue
        self._seqs   = self._load_seqs()
        self._last   = None   # last completed goal

    def _load_seqs(self) -> dict:
        try:
            return json.loads(self.SEQUENCE_PATH.read_text())
        except:
            return {}

    def _save_seqs(self):
        self.SEQUENCE_PATH.parent.mkdir(exist_ok=True)
        self.SEQUENCE_PATH.write_text(json.dumps(self._seqs, indent=2))

    def record(self, user_input: str, goal_type: str, success: bool):
        """Record what you just did — update sequence memory."""
        if not success:
            return
        key = goal_type
        if self._last:
            # Record: after _last, you did key
            pair = f"{self._last}→{key}"
            self._seqs[pair] = self._seqs.get(pair, 0) + 1
            self._save_seqs()
        self._last = key

    def predict(self, last_input: str = "", last_type: str = "") -> list[dict]:
        """
        Generate up to 4 suggestions for what to do next.
        Returns list of {text, reason, confidence}
        """
        suggestions = []

        # 1. Context rules — what usually follows what you just did
        if last_input:
            for rule in self.CONTEXT_RULES:
                if (re.search(rule["trigger"], last_input, re.I) or
                        rule["goal_type"] == last_type):
                    for s in rule["suggestions"][:2]:
                        suggestions.append({
                            "text":       s,
                            "reason":     "after this",
                            "confidence": 0.75,
                        })
                    break

        # 2. Sequence memory — learned patterns
        if last_type and last_type in [s.split("→")[0] for s in self._seqs]:
            for pair, count in sorted(self._seqs.items(),
                                      key=lambda x: x[1], reverse=True):
                before, after = pair.split("→")
                if before == last_type and count >= 2:
                    # Map goal_type back to a human suggestion
                    type_to_text = {
                        "code":     "build something else",
                        "shell":    "run another command",
                        "search":   "search for more",
                        "open_app": "open another app",
                        "system":   "check system again",
                    }
                    text = type_to_text.get(after, after)
                    suggestions.append({
                        "text":       text,
                        "reason":     f"you do this {count}x after",
                        "confidence": min(0.95, 0.5 + count * 0.05),
                    })

        # 3. Time-of-day suggestions — fill remaining slots
        hour = datetime.now().hour
        period = ("morning"   if 5 <= hour < 12 else
                  "afternoon" if 12 <= hour < 17 else
                  "evening"   if 17 <= hour < 22 else "night")
        for text in self.TIME_SUGGESTIONS[period]:
            suggestions.append({
                "text":       text,
                "reason":     period,
                "confidence": 0.40,
            })

        # 4. Frequent past goals from history
        recent = self.conv.recent(20)
        if recent:
            user_msgs = [m["content"] for m in recent
                         if m["role"] == "user"]
            counts = Counter(user_msgs)
            for text, count in counts.most_common(3):
                if count >= 2 and text != last_input:
                    suggestions.append({
                        "text":       text,
                        "reason":     f"you do this often ({count}x)",
                        "confidence": min(0.90, 0.5 + count * 0.08),
                    })

        # Deduplicate + sort by confidence + limit to 4
        seen = set()
        unique = []
        for s in sorted(suggestions, key=lambda x: x["confidence"], reverse=True):
            key = s["text"].lower().strip()
            if key not in seen:
                seen.add(key)
                unique.append(s)
            if len(unique) >= 4:
                break

        return unique


# ═══════════════════════════════════════════════
#  CSS STYLES
# ═══════════════════════════════════════════════
CSS = """
Screen {
    background: #0d0f14;
    layers: base;
}

Header {
    background: #534AB7;
    color: white;
    height: 1;
    dock: top;
}

Footer {
    background: #1a1f35;
    color: #4a5270;
    height: 1;
    dock: bottom;
}

#input-area {
    dock: top;
    height: 3;
    background: #111320;
    border-bottom: solid #534AB7;
    padding: 0 1;
    layout: horizontal;
    align: left middle;
}

#prompt-label {
    color: #534AB7;
    text-style: bold;
    width: 3;
    content-align: left middle;
}

#user-input {
    width: 1fr;
    background: #0d0f14;
    color: #c0cce8;
    border: solid #252d50;
}

#user-input:focus {
    border: solid #534AB7;
}

#main-layout {
    layout: horizontal;
    height: 1fr;
}

#left-panel {
    width: 65%;
    layout: vertical;
    border-right: solid #1e2030;
}

#log-area {
    height: 1fr;
    padding: 0 1;
    background: #0d0f14;
}

#suggestions-area {
    height: 7;
    background: #111320;
    border-top: solid #1e2030;
    padding: 1 1 0 1;
}

#suggestion-label {
    color: #4a5270;
    height: 1;
    margin-bottom: 1;
}

#suggestion-buttons {
    layout: horizontal;
    height: 3;
}

.suggestion-btn {
    background: #1a1f35;
    color: #7a8ab0;
    border: solid #252d50;
    margin-right: 1;
    min-width: 20;
    height: 3;
}

.suggestion-btn:hover {
    background: #252d50;
    color: #a0b0e0;
}

#right-panel {
    width: 35%;
    layout: vertical;
}

#goals-panel {
    height: 50%;
    border-bottom: solid #1e2030;
    padding: 1;
}

#skills-panel {
    height: 50%;
    padding: 1;
}

.panel-title {
    color: #534AB7;
    text-style: bold;
    height: 1;
    margin-bottom: 1;
}

#goals-list {
    height: 1fr;
    color: #6a7a9a;
    overflow-y: auto;
}

#skills-list {
    height: 1fr;
    color: #6a7a9a;
    overflow-y: auto;
}
"""


# ═══════════════════════════════════════════════
#  THE APP
# ═══════════════════════════════════════════════
class NucleusUI(App):
    """NUCLEUS terminal UI — adaptive autonomous agent"""

    CSS = CSS
    TITLE = "NUCLEUS  ·  adaptive autonomous agent"

    BINDINGS = [
        Binding("ctrl+c",  "quit",         "Quit",         show=True),
        Binding("ctrl+g",  "show_goals",   "Goals",        show=True),
        Binding("ctrl+s",  "show_skills",  "Skills",       show=True),
        Binding("ctrl+l",  "clear_log",    "Clear",        show=True),
        Binding("f1",      "suggest_0",    "Suggest 1",    show=False),
        Binding("f2",      "suggest_1",    "Suggest 2",    show=False),
        Binding("f3",      "suggest_2",    "Suggest 3",    show=False),
        Binding("f4",      "suggest_3",    "Suggest 4",    show=False),
    ]

    _suggestions: reactive = reactive([])
    _last_input:  str      = ""
    _last_type:   str      = ""

    def __init__(self):
        super().__init__()
        self.nucleus   = None
        self.predictor = None
        self._is_ready = False

    def compose(self) -> ComposeResult:
        yield Header()
        # Input docked at bottom — must be before main content
        with Horizontal(id="input-area"):
            yield Label("▸", id="prompt-label")
            yield Input(placeholder="tell me what to do...",
                        id="user-input")
        with Horizontal(id="main-layout"):
            # Left: log + suggestions
            with Vertical(id="left-panel"):
                yield RichLog(id="log-area", highlight=True,
                              markup=True, wrap=True)
                with Vertical(id="suggestions-area"):
                    yield Label("  suggestions appear here after first action",
                                id="suggestion-label")
                    with Horizontal(id="suggestion-buttons"):
                        for i in range(4):
                            yield Button(f"F{i+1}: —",
                                         id=f"suggestion-{i}",
                                         classes="suggestion-btn")
            # Right: goals + skills
            with Vertical(id="right-panel"):
                with Vertical(id="goals-panel"):
                    yield Label("◆  GOALS", classes="panel-title")
                    yield Static("loading...", id="goals-list")
                with Vertical(id="skills-panel"):
                    yield Label("≋  SKILLS", classes="panel-title")
                    yield Static("loading...", id="skills-list")
        yield Footer()

    def on_mount(self) -> None:
        """Boot nucleus in background thread."""
        self._boot_log("Starting NUCLEUS...")
        thread = threading.Thread(target=self._boot_nucleus, daemon=True)
        thread.start()

    def _boot_nucleus(self):
        """Boot nucleus components — runs in background."""
        try:
            self.nucleus   = Nucleus()
            self.predictor = Predictor(self.nucleus.conv, self.nucleus.queue)
            self._is_ready = True

            self.call_from_thread(self._boot_done)
        except Exception as e:
            self.call_from_thread(self._boot_log, f"[red]Boot error: {e}[/red]")

    def _boot_done(self):
        self._boot_log("[green]✓ NUCLEUS ready[/green]")
        try:
            self._refresh_panels()
        except Exception as e:
            self._boot_log(f"[yellow]Panel init: {e}[/yellow]")
        try:
            self._update_suggestions("", "")
        except Exception as e:
            self._boot_log(f"[yellow]Suggestions init: {e}[/yellow]")
        self.query_one("#user-input", Input).focus()

    def _boot_log(self, msg: str):
        self.query_one("#log-area", RichLog).write(
            f"[dim]{datetime.now().strftime('%H:%M:%S')}[/dim]  {msg}"
        )

    # ── Input handling ────────────────────────
    def on_input_submitted(self, event: Input.Submitted) -> None:
        user_input = event.value.strip()
        if not user_input:
            return
        event.input.value = ""

        self._log_user(user_input)

        if not self._is_ready:
            self._boot_log("[yellow]Still booting...[/yellow]")
            return

        # Handle built-in UI commands
        lower = user_input.lower()
        if lower in ("quit","exit","q"):
            self.exit()
            return
        if lower == "help":
            self._show_help()
            return

        # Run in background so UI stays responsive
        thread = threading.Thread(
            target=self._run_input,
            args=(user_input,),
            daemon=True
        )
        thread.start()

    def _run_input(self, user_input: str):
        """Process input in background thread."""
        try:
            # Observe
            intent = self.nucleus.observe(user_input)

            # Internal commands
            if intent["goal_type"] == "_cmd":
                self.call_from_thread(
                    self._handle_cmd, intent["action"])
                return

            # Chat (no action)
            if (intent["goal_type"] == "chat" and
                    not intent.get("template")):
                self.call_from_thread(
                    self._log_system, "[dim]thinking...[/dim]")
                answer = self.nucleus._handle_chat_return(user_input)
                self.call_from_thread(self._log_nucleus, answer)
                self.call_from_thread(
                    self._update_suggestions, user_input, "chat")
                return

            # Action
            goal   = self.nucleus.decide(intent)
            self.call_from_thread(
                self._log_system,
                f"[yellow]▶[/yellow] {goal.goal_type} → {goal._best_tool}"
            )

            result   = self.nucleus.act(goal)
            response = self.nucleus.learn(intent, goal, result)

            self.call_from_thread(self._log_nucleus, response)
            self.call_from_thread(self._refresh_panels)
            self.call_from_thread(
                self._update_suggestions,
                user_input,
                intent.get("goal_type","unknown")
            )

            # Record for predictor
            if self.predictor:
                self.predictor.record(
                    user_input,
                    intent.get("goal_type","unknown"),
                    result.get("ok", False)
                )

        except Exception as e:
            self.call_from_thread(
                self._log_system,
                f"[red]Error: {e}[/red]"
            )

    # ── Logging helpers ───────────────────────
    def _log_user(self, text: str):
        log = self.query_one("#log-area", RichLog)
        log.write(
            f"\n[dim]{datetime.now().strftime('%H:%M:%S')}[/dim]  "
            f"[yellow bold]you ▸[/yellow bold]  {text}"
        )

    def _log_nucleus(self, text: str):
        log = self.query_one("#log-area", RichLog)
        log.write(
            f"[dim]{datetime.now().strftime('%H:%M:%S')}[/dim]  "
            f"[purple bold]◎[/purple bold]  {text}"
        )

    def _log_system(self, text: str):
        log = self.query_one("#log-area", RichLog)
        log.write(
            f"[dim]{datetime.now().strftime('%H:%M:%S')}[/dim]  {text}"
        )

    # ── Panel updates ─────────────────────────
    def _refresh_panels(self):
        """Update goals and skills panels."""
        if not self.nucleus:
            return

        # Goals
        try:
            goals = list(self.nucleus.queue._goals)[-12:]
            icons = {"done":"[green]✓[/green]","failed":"[red]✗[/red]",
                     "active":"[yellow]▶[/yellow]","pending":"[dim]○[/dim]",
                     "skipped":"[dim]—[/dim]"}
            goal_lines = []
            for g in reversed(goals):
                icon  = icons.get(g.status, "○")
                title = g.title[:38]
                goal_lines.append(f"{icon} {title}")
            self.query_one("#goals-list", Static).update(
                "\n".join(goal_lines) or "[dim]empty[/dim]"
            )
        except Exception as e:
            self.query_one("#goals-list", Static).update(f"[red]{e}[/red]")

        # Skills
        try:
            skill_lines = []
            for gtype, tools in self.nucleus.skills._map.items():
                if not tools:
                    continue
                best  = max(tools.items(), key=lambda x: x[1]["score"])
                bar_w = int(best[1]["score"] * 10)
                bar   = "█" * bar_w + "░" * (10 - bar_w)
                skill_lines.append(
                    f"[dim]{gtype:<10}[/dim] [cyan]{bar}[/cyan] "
                    f"[green]{best[1]['score']:.0%}[/green] "
                    f"[dim]{best[0][:8]}[/dim]"
                )
            self.query_one("#skills-list", Static).update(
                "\n".join(skill_lines) or "[dim]no data yet[/dim]"
            )
        except Exception as e:
            self.query_one("#skills-list", Static).update(f"[red]{e}[/red]")

    def _update_suggestions(self, last_input: str, last_type: str):
        """Refresh the suggestion buttons."""
        if not self.predictor:
            return

        self._last_input = last_input
        self._last_type  = last_type

        preds = self.predictor.predict(last_input, last_type)

        # Update label
        label = self.query_one("#suggestion-label", Label)
        if preds:
            label.update(
                f"  [dim]suggested — press F1-F4 or click:[/dim]"
            )
        else:
            label.update("  [dim]type anything to get started[/dim]")

        # Update buttons
        for i in range(4):
            btn = self.query_one(f"#suggestion-{i}", Button)
            if i < len(preds):
                p     = preds[i]
                short = p["text"][:20]
                conf  = int(p["confidence"] * 100)
                btn.label  = f"F{i+1}: {short} [{conf}%]"
                btn.tooltip = f"{p['text']}  ({p['reason']})"
                btn.disabled = False
                btn.styles.color = "#a0b8e0" if conf >= 70 else "#6a7a9a"
            else:
                btn.label    = f"F{i+1}: —"
                btn.disabled = True

        self._suggestions = preds

    # ── Suggestion actions ────────────────────
    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id and btn_id.startswith("suggestion-"):
            idx = int(btn_id.split("-")[1])
            self._fire_suggestion(idx)

    def action_suggest_0(self): self._fire_suggestion(0)
    def action_suggest_1(self): self._fire_suggestion(1)
    def action_suggest_2(self): self._fire_suggestion(2)
    def action_suggest_3(self): self._fire_suggestion(3)

    def _fire_suggestion(self, idx: int):
        if not self._suggestions or idx >= len(self._suggestions):
            return
        text = self._suggestions[idx]["text"]
        inp  = self.query_one("#user-input", Input)
        inp.value = text
        inp.focus()
        # Auto-submit
        self.call_later(self._submit_input, text)

    def _submit_input(self, text: str):
        self.query_one("#user-input", Input).value = ""
        self._log_user(text)
        if self._is_ready:
            thread = threading.Thread(
                target=self._run_input, args=(text,), daemon=True)
            thread.start()

    # ── Built-in commands ─────────────────────
    def _handle_cmd(self, action: str):
        if action == "show_goals":
            self._log_system(
                f"[bold]Goals:[/bold]\n{self.nucleus.queue.display()}")
        elif action == "show_skills":
            self._log_system(
                f"[bold]Skills:[/bold]\n{self.nucleus.skills.summary()}")
        elif action == "show_memory":
            patterns = self.nucleus.patterns.recall(8)
            lines = [f"  ✓ {p['input'][:45]} → {p['action']}"
                     for p in patterns]
            self._log_system(
                "[bold]Learned patterns:[/bold]\n" + "\n".join(lines))
        elif action == "clear_done":
            self.nucleus.queue.clear_done()
            self._log_system("[dim]Done goals cleared.[/dim]")
            self._refresh_panels()
        elif action == "forget":
            self.nucleus.conv.clear()
            self._log_system("[dim]Conversation cleared.[/dim]")
        elif action == "show_status":
            r = self.nucleus.tools.run("system_check","system")
            self._log_system(f"[bold]Status:[/bold] {r.get('output','?')}")
        elif action == "show_help":
            self._show_help()
        elif action == "quit":
            self.exit()
        else:
            self.nucleus._handle_command(action)

    def action_show_goals(self):
        self._handle_cmd("show_goals")

    def action_show_skills(self):
        self._handle_cmd("show_skills")

    def action_clear_log(self):
        self.query_one("#log-area", RichLog).clear()

    def _show_help(self):
        self._log_system("""[bold]NUCLEUS UI[/bold]

[yellow]Type anything naturally.[/yellow]  Examples:
  open chrome          → opens chrome browser
  build a snake game   → cloud writes + launches it
  what time is it      → instant answer
  search for X         → web search
  install pygame       → install package
  disk space           → show disk usage
  git status           → run git command

[yellow]Suggestions:[/yellow]
  F1-F4               → accept a suggestion
  Click any button    → same thing

[yellow]Commands:[/yellow]
  goals   skills   memory   status   forget   help   quit

[yellow]Keyboard:[/yellow]
  Ctrl+G  goals     Ctrl+S  skills
  Ctrl+L  clear     Ctrl+C  quit""")


# ═══════════════════════════════════════════════
#  PATCH: add _handle_chat_return to Nucleus
#  (returns string instead of printing)
# ═══════════════════════════════════════════════
def _patch_nucleus():
    """Add helper method to Nucleus for UI use."""
    def _handle_chat_return(self, user_input: str) -> str:
        history = self.conv.recent(10)
        context = "\n".join(
            f"{m['role'].upper()}: {m['content']}"
            for m in history[-6:]
        )
        prompt = (
            f"You are ARIA, an autonomous agent on a Linux laptop. "
            f"Be concise. One paragraph max. No lists.\n\n"
            f"Conversation:\n{context}\n\nUSER: {user_input}"
        )
        answer = self.cloud.ask(prompt)
        self.conv.add("user",      user_input)
        self.conv.add("assistant", answer)
        return answer

    Nucleus._handle_chat_return = _handle_chat_return


# ═══════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════
def main():
    _patch_nucleus()
    app = NucleusUI()
    app.run()


if __name__ == "__main__":
    main()
