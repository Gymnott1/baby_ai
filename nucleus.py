#!/usr/bin/env python3
"""
nucleus.py — The adaptive agent
=================================
No language model as the brain.
No pre-trained weights making decisions.
Just a tight observe → decide → act → learn loop
that gets smarter every single run.

Boot:   loads skill map + pattern memory + goal queue
Loop:   reads input → matches pattern (instant)
                    → if unknown: asks cloud once, saves answer
        decides:    which tool has highest success rate for this goal type
        acts:       runs tool with automatic fallback
        learns:     updates skill scores, saves pattern if it worked

The nucleus gets smarter because it remembers everything.
Not because it has a big model. Because it has a good memory.

Usage:
    python3 nucleus.py              ← interactive mode
    python3 nucleus.py --goals      ← show goal queue
    python3 nucleus.py --skills     ← show skill map
    python3 nucleus.py --run        ← process queue silently
"""

import os, sys, json, re, time, argparse, threading
from pathlib  import Path
from datetime import datetime

from skills import SkillMap, PatternMemory, GoalQueue, Goal
from tools  import ToolExecutor, CloudRouter, C, log, ts


# ═══════════════════════════════════════════════
#  CONVERSATION MEMORY
#  Rolling window of what was said
# ═══════════════════════════════════════════════
class ConvMemory:
    PATH = Path("memory/conversation.json")

    def __init__(self, max_turns: int = 40):
        self.max   = max_turns
        self._hist = self._load()

    def _load(self) -> list:
        try: return json.loads(self.PATH.read_text())
        except: return []

    def _save(self):
        self.PATH.parent.mkdir(exist_ok=True)
        self.PATH.write_text(json.dumps(self._hist[-self.max:], indent=2))

    def add(self, role: str, content: str):
        self._hist.append({
            "role":    role,
            "content": content,
            "ts":      datetime.now().isoformat()
        })
        if len(self._hist) > self.max:
            self._hist = self._hist[-self.max:]
        self._save()

    def recent(self, n: int = 10) -> list:
        return [{"role":m["role"],"content":m["content"]}
                for m in self._hist[-n:]]

    def clear(self):
        self._hist = []
        self._save()

    def summary(self) -> str:
        if not self._hist:
            return "No conversation history."
        lines = []
        for m in self._hist[-6:]:
            role = "you" if m["role"] == "user" else "ARIA"
            lines.append(f"  {role}: {m['content'][:80]}")
        return "\n".join(lines)


# ═══════════════════════════════════════════════
#  THE NUCLEUS
#  Observe → Decide → Act → Learn
# ═══════════════════════════════════════════════
class Nucleus:
    """
    The intelligent self.

    Three key properties that make it adaptive:

    1. It never asks the same question twice.
       Pattern memory means every solved problem
       is solved instantly forever after.

    2. It always picks the best tool.
       Skill map tracks success rates per goal type.
       Bad tools get demoted. Good tools get promoted.
       Automatically. Every run.

    3. It survives restarts.
       Goals, patterns, skills — all persisted to disk.
       Boot → continue exactly where it left off.
    """

    VERSION = "1.0"

    def __init__(self):
        log("SYS", f"Nucleus v{self.VERSION} booting...")
        for d in ["output","memory","logs"]:
            Path(d).mkdir(exist_ok=True)

        self.skills   = SkillMap()
        self.patterns = PatternMemory()
        self.queue    = GoalQueue()
        self.conv     = ConvMemory()
        self.cloud    = CloudRouter(self.skills)
        self.tools    = ToolExecutor(self.cloud, self.skills)

        log("SYS", "All systems ready")
        self._print_status()

    def _print_status(self):
        providers = len(self.cloud._providers)
        pending   = len(self.queue.pending())
        learned   = len(self.patterns._learned)
        print(f"\n{C.GRY}  cloud={providers} providers  "
              f"goals={pending} pending  "
              f"patterns={learned} learned{C.R}\n")

    # ════════════════════════════════════════════
    #  OBSERVE  — understand what was said
    # ════════════════════════════════════════════
    def observe(self, user_input: str) -> dict:
        """
        Turn raw user input into a structured intent.

        Step 1: check pattern memory (instant, no network)
        Step 2: if no match → ask cloud to classify (one time only)
                              save result to pattern memory
                              next time → step 1 will match
        """
        text = user_input.strip()

        # Hard commands — handled before any classification
        lower = text.lower()
        hard_commands = {
            "goals":       {"goal_type":"_cmd","action":"show_goals"},
            "skills":      {"goal_type":"_cmd","action":"show_skills"},
            "memory":      {"goal_type":"_cmd","action":"show_memory"},
            "history":     {"goal_type":"_cmd","action":"show_history"},
            "clear done":  {"goal_type":"_cmd","action":"clear_done"},
            "forget":      {"goal_type":"_cmd","action":"forget"},
            "run":         {"goal_type":"_cmd","action":"run_queue"},
            "status":      {"goal_type":"_cmd","action":"show_status"},
            "help":        {"goal_type":"_cmd","action":"show_help"},
            "quit":        {"goal_type":"_cmd","action":"quit"},
            "exit":        {"goal_type":"_cmd","action":"quit"},
            "q":           {"goal_type":"_cmd","action":"quit"},
        }
        if lower in hard_commands:
            return {**hard_commands[lower], "raw": text, "confidence": 1.0,
                    "source": "command"}

        # Pattern match (instant)
        match = self.patterns.match(text)
        if match and match["confidence"] >= 0.75:
            return {**match, "raw": text}

        # Unknown — cloud classifies once
        log("CLOUD", "Unknown input — asking cloud to classify...",
            text[:60])
        classification = self.cloud.classify(text)
        classification["raw"]        = text
        classification["confidence"] = 0.70
        classification["source"]     = "cloud_classified"
        classification["groups"]     = []

        return classification

    # ════════════════════════════════════════════
    #  DECIDE  — which tool, which approach
    # ════════════════════════════════════════════
    def decide(self, intent: dict) -> Goal:
        """
        Turn an intent into a Goal with the best tool selected.
        Skill map picks the tool. Not a prompt. Not a model.
        Just: what has worked best for this goal type before?
        """
        goal_type = intent.get("goal_type","unknown")
        action    = intent.get("action","cloud_ask")
        template  = intent.get("template")
        groups    = intent.get("groups",[])
        raw       = intent.get("raw","")

        # Map action to available tools with fallbacks
        TOOL_CHAINS = {
            "shell":      ["shell",      "python",    "cloud_ask"],
            "python":     ["python",     "shell",     "cloud_ask"],
            "cloud_code": ["cloud_code", "cloud_ask"],
            "cloud_ask":  ["cloud_ask"],
            "search":     ["search",     "cloud_ask"],
            "file_read":  ["file_read",  "cloud_ask"],
            "file_write": ["file_write"],
            "notify":     ["notify"],
            "schedule":   ["schedule"],
            "system_check":["system_check"],
        }

        available = TOOL_CHAINS.get(action, ["cloud_ask"])
        best, fallbacks = self.skills.best_tool(goal_type, available)

        g = Goal(
            title     = raw[:80],
            goal_type = goal_type,
            priority  = GoalQueue.P_URGENT,
            action    = action,
            template  = template,
            groups    = groups,
            source    = intent.get("source","user")
        )
        g._best_tool  = best
        g._fallbacks  = fallbacks
        g._intent     = intent
        return g

    # ════════════════════════════════════════════
    #  ACT  — run the goal
    # ════════════════════════════════════════════
    def act(self, goal: Goal) -> dict:
        """
        Execute the goal using the best tool.
        If it fails → try fallbacks in skill-map order.
        Reports every attempt back to SkillMap.
        """
        goal.status = "active"
        self.queue.update(goal)

        tool_chain = [goal._best_tool] + goal._fallbacks
        last_result = {}

        for tool in tool_chain:
            kwargs = self._build_kwargs(tool, goal)
            result = self.tools.run(tool, goal.goal_type, **kwargs)
            last_result = result

            if result["ok"]:
                goal.status = "done"
                goal.result = result
                goal.log("success", {"tool": tool, "output": result["output"][:200]})
                self.queue.update(goal)
                return result

            log("FIX", f"{tool} failed → trying fallback",
                result["output"][:60])

        # All tools failed
        goal.status = "failed"
        goal.result = last_result
        goal.log("all tools failed")
        self.queue.update(goal)
        return last_result

    def _build_kwargs(self, tool: str, goal: Goal) -> dict:
        """Build the right kwargs for each tool type."""
        raw      = goal.title
        template = goal.template
        groups   = goal.groups

        if tool == "shell":
            cmd = template or raw
            return {"cmd": cmd}

        if tool == "python":
            code = template or f"print('done: {raw}')"
            return {"code": code}

        if tool == "cloud_code":
            return {"task": raw, "context": ""}

        if tool == "cloud_ask":
            # Build a context-aware prompt using conversation history
            history = self.conv.recent(6)
            ctx = "\n".join(f"{m['role']}: {m['content']}"
                            for m in history[-4:]) if history else ""
            prompt = f"{ctx}\nuser: {raw}" if ctx else raw
            return {"prompt": prompt}

        if tool == "search":
            # Extract query from groups or use raw input
            query = groups[1] if len(groups) > 1 else (
                    groups[0] if groups else raw)
            # Remove search verb
            query = re.sub(r'^(search|find|look up|google|what is|who is)\s+',
                           '', query, flags=re.I).strip()
            return {"query": query}

        if tool == "file_read":
            parts = raw.split()
            path  = next((p for p in parts if "/" in p or "." in p), raw)
            return {"path": path}

        if tool == "notify":
            return {"message": raw}

        if tool == "schedule":
            # Simple time extraction
            time_match = re.search(r'at (\d+(?::\d+)?(?:am|pm)?)', raw, re.I)
            when = time_match.group(1) if time_match else None
            task = re.sub(r'remind me\s*', '', raw, flags=re.I).strip()
            return {"task": task, "when": when}

        if tool == "system_check":
            return {}

        return {"prompt": raw}

    # ════════════════════════════════════════════
    #  LEARN  — update memory from outcome
    # ════════════════════════════════════════════
    def learn(self, intent: dict, goal: Goal, result: dict):
        """
        After every action — learn from what happened.
        Successful patterns get saved. Bad outcomes demote tools.
        This is called automatically after every act().
        """
        success = result.get("ok", False)
        output  = result.get("output","")

        # Save pattern if it worked and came from cloud classification
        if success and intent.get("source") == "cloud_classified":
            self.patterns.learn(
                user_input = intent["raw"],
                goal_type  = intent["goal_type"],
                action     = intent["action"],
                template   = intent.get("template"),
                success    = True
            )
            log("LEARN", f"New pattern saved: {intent['raw'][:50]}")

        # Save conversation turn
        response = self._format_response(goal, result)
        self.conv.add("user",      intent["raw"])
        self.conv.add("assistant", response)

        return response

    def _format_response(self, goal: Goal, result: dict) -> str:
        """Turn a tool result into a natural response."""
        ok     = result.get("ok", False)
        output = result.get("output","").strip()
        tool   = result.get("tool_used","")

        if not ok:
            return f"Couldn't do that. {output[:120]}"

        # Suppress raw output for simple shell commands that worked silently
        if tool == "shell" and not output:
            return "Done."

        # For searches and questions — return the answer directly
        if goal.goal_type in ("search","chat") or tool in ("cloud_ask","search"):
            return output[:600]

        # For code tasks
        if goal.goal_type == "code" or tool == "cloud_code":
            name = re.sub(r'[^a-z0-9]+','_', goal.title.lower())[:25]
            return f"Done. Built and saved → output/{name}.py"

        # For system info
        if goal.goal_type == "system" or tool in ("python","system_check"):
            return output[:300] or "Done."

        # Generic
        return f"Done." + (f" {output[:100]}" if output else "")

    # ════════════════════════════════════════════
    #  MAIN LOOP
    # ════════════════════════════════════════════
    def run_interactive(self):
        """The main chat + action loop."""
        self._banner()

        # Run self-repair / system goals on every boot (concrete, fast)
        for g in self.queue.pending():
            if g.goal_type == "system":
                log("SYS", f"Running: {g.title}")
                r = self.tools.run("system_check", "system")
                g.status = "done"
                g.result = r
                self.queue.update(g)
                if r["ok"]:
                    log("OK", r["output"][:80])

        while True:
            try:
                user = input(f"\n{C.YLW}▸ {C.R}").strip()
            except (KeyboardInterrupt, EOFError):
                print(f"\n{C.GRY}Nucleus shutting down. Memory saved.{C.R}")
                break

            if not user:
                continue

            # ── Observe ──────────────────────────
            intent = self.observe(user)

            # ── Handle built-in commands ─────────
            if intent["goal_type"] == "_cmd":
                self._handle_command(intent["action"])
                continue

            # ── Decide ───────────────────────────
            goal = self.decide(intent)

            # ── Chat: no goal needed, stream answer
            if intent["goal_type"] == "chat" and not intent.get("template"):
                self._handle_chat(user)
                continue

            # ── Act ──────────────────────────────
            print(f"{C.GRY}  working...{C.R}", flush=True)
            result = self.act(goal)

            # ── Learn ────────────────────────────
            response = self.learn(intent, goal, result)

            # ── Respond ──────────────────────────
            print(f"\n{C.PRP}◎{C.R} {response}\n")

    def _handle_chat(self, user_input: str):
        """Pure conversation — cloud answers, nucleus remembers."""
        history = self.conv.recent(10)
        context = "\n".join(f"{m['role'].upper()}: {m['content']}"
                            for m in history[-6:])
        prompt  = (
            f"You are ARIA, an autonomous agent on a Linux laptop. "
            f"Be concise. One paragraph max. No lists.\n\n"
            f"Conversation so far:\n{context}\n\n"
            f"USER: {user_input}"
        )
        answer = self.cloud.ask(prompt)
        self.conv.add("user",      user_input)
        self.conv.add("assistant", answer)
        print(f"\n{C.PRP}◎{C.R} {answer}\n")

    def _handle_command(self, action: str):
        if action == "show_goals":
            print(f"\n{C.B}Goals:{C.R}\n{self.queue.display()}\n")

        elif action == "show_skills":
            print(f"\n{C.B}Skill map:{C.R}\n{self.skills.summary()}\n")

        elif action == "show_memory":
            patterns = self.patterns.recall(10)
            print(f"\n{C.B}Learned patterns ({len(self.patterns._learned)} total):{C.R}")
            for p in patterns:
                print(f"  ✓ {p['input'][:50]:<50} → {p['action']}")
            print()

        elif action == "show_history":
            print(f"\n{C.B}Recent conversation:{C.R}\n{self.conv.summary()}\n")

        elif action == "clear_done":
            self.queue.clear_done()
            print(f"{C.GRY}  Done goals cleared.{C.R}")

        elif action == "forget":
            self.conv.clear()
            print(f"{C.GRY}  Conversation memory cleared.{C.R}")

        elif action == "run_queue":
            pending = self.queue.pending()
            if not pending:
                print(f"{C.GRY}  No pending goals.{C.R}")
                return
            log("SYS", f"Running {len(pending)} pending goal(s)...")
            for g in pending:
                if g.goal_type == "system":
                    r = self.tools.run("system_check","system")
                    g.status = "done"
                    self.queue.update(g)
                    print(f"  ✓ system: {r.get('output','ok')}")
                    continue
                intent = {"goal_type": g.goal_type, "action": g.action or "cloud_ask",
                          "template": g.template, "groups": g.groups or [],
                          "raw": g.title, "source": "queue", "confidence": 1.0}
                g._best_tool, g._fallbacks = self.skills.best_tool(
                    g.goal_type,["shell","cloud_code","cloud_ask","search"])
                g._intent = intent
                result = self.act(g)
                response = self.learn(intent, g, result)
                print(f"\n{C.PRP}◎{C.R} {response}")

        elif action == "show_status":
            r = self.tools.run("system_check","system")
            print(f"\n{C.B}System:{C.R} {r.get('output','?')}")
            print(f"{C.B}Cloud:{C.R}  {len(self.cloud._providers)} providers")
            print(f"{C.B}Goals:{C.R}  {len(self.queue.pending())} pending")
            print(f"{C.B}Patterns:{C.R} {len(self.patterns._learned)} learned")
            print(f"{C.B}Skills:{C.R}\n{self.skills.summary()}\n")

        elif action == "show_help":
            print(f"""
{C.B}Commands:{C.R}
  goals          show goal queue
  skills         show skill map (what tools work best)
  memory         show learned patterns
  history        show recent conversation
  status         full system status
  run            process pending goals
  clear done     remove completed goals
  forget         clear conversation history
  help           this screen
  quit / q       exit

{C.B}Just talk naturally:{C.R}
  open chrome              → opens chrome
  build a snake game       → cloud writes, runs it
  what is machine learning → searches + answers
  search for python tips   → web search
  what time is it          → instant answer
  install pygame           → runs apt/pip
  disk space               → shows disk usage
  remind me to call mum    → schedules reminder
  who are you              → ARIA introduces itself
""")

        elif action == "quit":
            print(f"{C.GRY}Nucleus shutting down. Memory saved.{C.R}")
            sys.exit(0)

    def _banner(self):
        pending  = len(self.queue.pending())
        learned  = len(self.patterns._learned)
        providers= len(self.cloud._providers)
        print(f"""
{C.PRP}{C.B}
  ╔═══════════════════════════════════════════════╗
  ║  NUCLEUS  —  adaptive autonomous agent v{self.VERSION}   ║
  ║  observe · decide · act · learn               ║
  ╚═══════════════════════════════════════════════╝{C.R}
  {C.GRY}cloud={providers}  patterns={learned}  goals={pending}  type 'help'{C.R}
""")

    # ════════════════════════════════════════════
    #  BACKGROUND: process scheduled goals
    # ════════════════════════════════════════════
    def _background_tick(self):
        """Runs in a background thread. Checks scheduled goals."""
        while True:
            time.sleep(60)
            try:
                p = Path("memory/scheduled.json")
                if not p.exists():
                    continue
                jobs = json.loads(p.read_text())
                now  = datetime.now()
                remaining = []
                for job in jobs:
                    if job.get("when"):
                        # Simple time check — "5pm", "17:00", etc.
                        when = job["when"].lower().replace(" ","")
                        try:
                            if ":" in when:
                                h, m = map(int, re.findall(r'\d+', when)[:2])
                            else:
                                h = int(re.search(r'\d+', when).group())
                                m = 0
                            if "pm" in when and h < 12:
                                h += 12
                            if now.hour == h and now.minute == m:
                                self._notify_scheduled(job["task"])
                                continue   # don't re-add
                        except:
                            pass
                    remaining.append(job)
                p.write_text(json.dumps(remaining, indent=2))
            except:
                pass

    def _notify_scheduled(self, task: str):
        self.tools.run("notify","notify",
                       message=f"⏰ ARIA reminder: {task}")
        print(f"\n{C.YLW}⏰ Reminder: {task}{C.R}")


# ═══════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(
        description="NUCLEUS — adaptive autonomous agent")
    parser.add_argument("--goals",  action="store_true",
                        help="Show goal queue and exit")
    parser.add_argument("--skills", action="store_true",
                        help="Show skill map and exit")
    parser.add_argument("--run",    action="store_true",
                        help="Process pending goals and exit")
    parser.add_argument("--add",    metavar="GOAL",
                        help="Add a goal to the queue")
    args = parser.parse_args()

    nucleus = Nucleus()

    if args.goals:
        print(nucleus.queue.display()); return

    if args.skills:
        print(nucleus.skills.summary()); return

    if args.add:
        intent = nucleus.observe(args.add)
        goal   = nucleus.decide(intent)
        nucleus.queue.push(goal)
        print(f"Added: {goal.title}")
        return

    if args.run:
        nucleus._handle_command("run_queue"); return

    # Start background scheduler
    import threading
    t = threading.Thread(target=nucleus._background_tick, daemon=True)
    t.start()

    nucleus.run_interactive()


if __name__ == "__main__":
    main()
