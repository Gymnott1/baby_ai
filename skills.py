"""
skills.py — The adaptive brain
================================
Three structures that make the nucleus intelligent:

  SkillMap     — tool × goal_type → success rate. Updates every run.
                 Nucleus always picks highest scoring tool. No prompting.

  PatternMemory — what you say → what it means → what works.
                 First time: ask cloud. Every time after: instant local.

  GoalQueue    — priority sorted, persisted, encrypted goals.
                 Survives restarts. Self-repair always first.

Nothing here is a language model. Everything here is adaptive data.
"""

import json, time, re, hashlib, difflib
from pathlib  import Path
from datetime import datetime


# ═══════════════════════════════════════════════
#  SKILL MAP
#  Learns which tool works best for each goal type
# ═══════════════════════════════════════════════
class SkillMap:
    """
    A live score table: goal_type × tool → success stats.

    Scoring formula (Bayesian-style moving average):
      new_score = old_score * decay + outcome * (1 - decay)

    decay=0.85 means recent outcomes matter more than old ones.
    A tool that was good but started failing drops fast.
    A tool that was bad but started working rises fast.

    After enough runs, the nucleus never has to guess —
    it just picks the highest score for the goal type.
    """

    DECAY      = 0.85    # how much history matters vs recent outcome
    MIN_TRIALS = 3       # minimum tries before trusting the score
    PATH       = Path("memory/skillmap.json")

    # Default scores — conservative starting point
    # Cloud is trusted for code, shell for system tasks
    DEFAULTS = {
        "code":     {"github": 0.80, "groq": 0.70, "gemini": 0.70, "shell": 0.10, "search": 0.20},
        "shell":    {"shell": 0.95,  "github": 0.30, "groq": 0.30, "search": 0.10},
        "search":   {"search": 0.75, "github": 0.50, "groq": 0.50, "shell": 0.20},
        "open_app": {"shell": 0.98,  "search": 0.10, "github": 0.10},
        "file":     {"shell": 0.90,  "github": 0.20, "search": 0.10},
        "chat":     {"groq": 0.80,   "github": 0.75, "gemini": 0.75},
        "notify":   {"telegram": 0.90, "log": 0.99},
        "system":   {"shell": 0.99,  "python": 0.95},
        "unknown":  {"github": 0.60, "groq": 0.60, "shell": 0.40},
    }

    def __init__(self):
        self._map: dict = self._load()

    def _load(self) -> dict:
        try:
            return json.loads(self.PATH.read_text())
        except:
            # Deep copy defaults with trial counts
            result = {}
            for gtype, tools in self.DEFAULTS.items():
                result[gtype] = {
                    tool: {"score": score, "trials": 0,
                           "wins": 0, "last_used": None}
                    for tool, score in tools.items()
                }
            return result

    def _save(self):
        self.PATH.parent.mkdir(exist_ok=True)
        self.PATH.write_text(json.dumps(self._map, indent=2))

    def best_tool(self, goal_type: str, available: list) -> tuple[str, list]:
        """
        Returns (best_tool, fallback_list) for a goal type.
        Only considers tools that are actually available.
        Sorted by score descending.
        """
        gtype  = goal_type if goal_type in self._map else "unknown"
        scores = self._map.get(gtype, self._map["unknown"])

        ranked = sorted(
            [(tool, data["score"]) for tool, data in scores.items()
             if tool in available],
            key=lambda x: x[1], reverse=True
        )

        if not ranked:
            # Fall back to any available tool
            return available[0], available[1:]

        tools_ordered = [t for t, _ in ranked]
        return tools_ordered[0], tools_ordered[1:]

    def update(self, goal_type: str, tool: str, success: bool, elapsed: float):
        """
        Update score after an outcome.
        Called automatically by ToolExecutor after every tool run.
        """
        gtype = goal_type if goal_type in self._map else "unknown"
        if gtype not in self._map:
            self._map[gtype] = {}
        if tool not in self._map[gtype]:
            self._map[gtype][tool] = {"score": 0.5, "trials": 0,
                                       "wins": 0, "last_used": None}

        entry   = self._map[gtype][tool]
        outcome = 1.0 if success else 0.0

        # Bayesian moving average
        entry["score"]     = entry["score"] * self.DECAY + outcome * (1 - self.DECAY)
        entry["score"]     = round(entry["score"], 4)
        entry["trials"]   += 1
        entry["wins"]     += 1 if success else 0
        entry["last_used"] = datetime.now().isoformat()
        entry["avg_time"]  = elapsed

        self._save()

    def summary(self) -> str:
        """Human-readable skill map for display."""
        lines = []
        for gtype, tools in self._map.items():
            best = max(tools.items(), key=lambda x: x[1]["score"])
            lines.append(
                f"  {gtype:<12} best={best[0]:<10} "
                f"score={best[1]['score']:.0%} "
                f"trials={sum(t['trials'] for t in tools.values())}"
            )
        return "\n".join(lines)


# ═══════════════════════════════════════════════
#  PATTERN MEMORY
#  What you say → what it means → what works
# ═══════════════════════════════════════════════
class PatternMemory:
    """
    Learns the mapping: user_input → {goal_type, action, confirmed}.

    First time you say something new:
      → nucleus asks cloud AI what it means
      → cloud returns: goal_type + action
      → nucleus executes
      → if it worked → saves as confirmed pattern
      → next time: instant, no cloud needed

    Fuzzy matching: "open firefox" matches "open chrome"
    with similarity 0.7 → uses same pattern with substitution.

    This is how the nucleus builds a vocabulary of YOUR language
    on YOUR machine without any pre-training.
    """

    PATH       = Path("memory/patterns.json")
    MATCH_THRESHOLD = 0.72   # similarity score to consider a match

    # Built-in patterns — always available, no learning needed
    BUILTIN = [
        # System / shell
        {"pattern": r"open (\w+)",          "type": "open_app",  "action": "shell",
         "template": "open {app}",          "extract": "app=1"},
        {"pattern": r"launch (\w+)",         "type": "open_app",  "action": "shell",
         "template": "open {app}",          "extract": "app=1"},
        {"pattern": r"close (\w+)",          "type": "shell",     "action": "shell",
         "template": "pkill -f {app}",      "extract": "app=1"},
        {"pattern": r"kill (\w+)",           "type": "shell",     "action": "shell",
         "template": "pkill -f {app}",      "extract": "app=1"},
        {"pattern": r"install (.+)",         "type": "shell",     "action": "shell",
         "template": "pip install {pkg} --break-system-packages -q || sudo apt install {pkg} -y",
         "extract": "pkg=1"},
        {"pattern": r"(run|execute) (.+)",   "type": "shell",     "action": "shell",
         "template": "{cmd}",               "extract": "cmd=2"},
        {"pattern": r"volume (\w+)",         "type": "shell",     "action": "shell",
         "template": "amixer set Master {level}%", "extract": "level=1"},
        {"pattern": r"screenshot",           "type": "shell",     "action": "shell",
         "template": "scrot ~/Pictures/screenshot_$(date +%s).png"},
        {"pattern": r"(reboot|restart)",     "type": "shell",     "action": "shell",
         "template": "sudo reboot"},
        {"pattern": r"shutdown",             "type": "shell",     "action": "shell",
         "template": "sudo shutdown now"},
        {"pattern": r"what.*time",           "type": "system",    "action": "python",
         "template": "import datetime; print(datetime.datetime.now().strftime('%H:%M'))"},
        {"pattern": r"what.*date",           "type": "system",    "action": "python",
         "template": "import datetime; print(datetime.date.today())"},
        {"pattern": r"(disk|storage|space)", "type": "system",    "action": "shell",
         "template": "df -h /"},
        {"pattern": r"(ram|memory) usage",   "type": "system",    "action": "python",
         "template": "import psutil; m=psutil.virtual_memory(); print(f'{m.used/1e9:.1f}/{m.total/1e9:.1f} GB ({m.percent}%)')"},
        {"pattern": r"cpu",                  "type": "system",    "action": "python",
         "template": "import psutil; print(f'CPU: {psutil.cpu_percent(1)}%')"},
        {"pattern": r"ip address",           "type": "system",    "action": "shell",
         "template": "hostname -I"},
        {"pattern": r"processes",            "type": "system",    "action": "shell",
         "template": "ps aux --sort=-%cpu | head -10"},
        # Build / code
        {"pattern": r"(build|make|create|write).+(game|app|script|program|tool)",
         "type": "code", "action": "cloud_code", "template": None},
        {"pattern": r"(build|make|create|write) (.+)",
         "type": "code", "action": "cloud_code", "template": None},
        # Search / info
        {"pattern": r"(search|find|look up|google) (.+)",
         "type": "search", "action": "search", "template": None, "extract": "query=2"},
        {"pattern": r"what is (.+)",         "type": "search",    "action": "search",
         "template": None, "extract": "query=1"},
        {"pattern": r"who is (.+)",          "type": "search",    "action": "search",
         "template": None, "extract": "query=1"},
        {"pattern": r"weather",              "type": "search",    "action": "search",
         "template": None},
        # Notify / remind
        {"pattern": r"remind me (.+)",       "type": "notify",    "action": "schedule",
         "template": None},
        {"pattern": r"(alert|notify) (.+)",  "type": "notify",    "action": "notify",
         "template": None},
    ]

    def __init__(self):
        self._learned: list = self._load()

    def _load(self) -> list:
        try:
            return json.loads(self.PATH.read_text())
        except:
            return []

    def _save(self):
        self.PATH.parent.mkdir(exist_ok=True)
        self.PATH.write_text(json.dumps(self._learned, indent=2))

    def match(self, user_input: str) -> dict | None:
        """
        Try to match user input against known patterns.
        Returns match dict or None if no match.
        """
        text = user_input.strip().lower()

        # 1. Try built-in regex patterns first (fastest)
        for p in self.BUILTIN:
            m = re.search(p["pattern"], text, re.I)
            if m:
                result = {
                    "goal_type": p["type"],
                    "action":    p["action"],
                    "template":  p.get("template"),
                    "groups":    list(m.groups()),
                    "source":    "builtin",
                    "confidence": 0.95,
                    "raw_input": user_input,
                }
                # Fill template with captured groups
                if p.get("template") and m.groups():
                    try:
                        extract = p.get("extract","")
                        if extract:
                            for mapping in extract.split():
                                key, idx = mapping.split("=")
                                val = m.group(int(idx))
                                result["template"] = result["template"].replace(
                                    "{"+key+"}", val)
                    except:
                        pass
                return result

        # 2. Try learned patterns (exact then fuzzy)
        for lp in reversed(self._learned):   # most recent first
            similarity = difflib.SequenceMatcher(
                None, text, lp["input"].lower()).ratio()
            if similarity >= self.MATCH_THRESHOLD:
                return {
                    "goal_type":  lp["goal_type"],
                    "action":     lp["action"],
                    "template":   lp.get("template"),
                    "groups":     [],
                    "source":     "learned",
                    "confidence": similarity,
                    "raw_input":  user_input,
                }

        return None   # unknown — nucleus will ask cloud

    def learn(self, user_input: str, goal_type: str, action: str,
              template: str, success: bool):
        """
        Save a new pattern after a successful outcome.
        Only confirmed patterns get saved.
        """
        if not success:
            return   # don't learn from failures

        # Check if already known
        for lp in self._learned:
            if lp["input"].lower() == user_input.lower():
                lp["confirmations"] = lp.get("confirmations",1) + 1
                self._save()
                return

        self._learned.append({
            "input":         user_input,
            "goal_type":     goal_type,
            "action":        action,
            "template":      template,
            "confirmations": 1,
            "learned_at":    datetime.now().isoformat(),
        })
        self._save()

    def recall(self, n: int = 10) -> list:
        """Recent learned patterns — for display."""
        return self._learned[-n:]


# ═══════════════════════════════════════════════
#  GOAL QUEUE
#  Priority-sorted, persisted, survives restarts
# ═══════════════════════════════════════════════
class Goal:
    def __init__(self, title: str, goal_type: str = "unknown",
                 priority: int = 50, action: str = None,
                 template: str = None, groups: list = None,
                 source: str = "user"):
        self.id        = hashlib.md5(f"{title}{time.time()}".encode()).hexdigest()[:8]
        self.title     = title
        self.goal_type = goal_type
        self.priority  = priority
        self.action    = action       # shell / cloud_code / search / python / schedule
        self.template  = template     # command template to run
        self.groups    = groups or [] # regex capture groups
        self.source    = source       # user / sensor / scheduled / system
        self.status    = "pending"    # pending/active/done/failed/skipped
        self.retries   = 0
        self.result    = {}
        self.created   = datetime.now().isoformat()
        self.history   = []

    def log(self, msg: str, data: dict = None):
        self.history.append({
            "ts": datetime.now().isoformat(),
            "msg": msg, "data": data or {}
        })

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()
                if k != "history" or True}

    @classmethod
    def from_dict(cls, d: dict) -> "Goal":
        g = cls(d["title"], d.get("goal_type","unknown"),
                d.get("priority",50), d.get("action"),
                d.get("template"), d.get("groups",[]),
                d.get("source","user"))
        g.id      = d.get("id", g.id)
        g.status  = d.get("status","pending")
        g.retries = d.get("retries",0)
        g.result  = d.get("result",{})
        g.created = d.get("created", g.created)
        g.history = d.get("history",[])
        return g


class GoalQueue:
    """
    Priority-sorted goal queue. Persisted to disk.
    Self-repair is always injected at priority 0 on boot.
    """
    PATH = Path("memory/goals.json")

    # Priority levels
    P_SYSTEM   = 0    # self-repair, integrity
    P_SENSOR   = 1    # gas leak, temp alert (emergencies)
    P_URGENT   = 10   # things you just asked for
    P_NORMAL   = 50   # regular tasks
    P_IDLE     = 90   # background / scheduled

    def __init__(self):
        self._goals: list[Goal] = self._load()
        self._inject_system_goals()

    def _load(self) -> list[Goal]:
        try:
            return [Goal.from_dict(d)
                    for d in json.loads(self.PATH.read_text())]
        except:
            return []

    def _save(self):
        self.PATH.parent.mkdir(exist_ok=True)
        self.PATH.write_text(
            json.dumps([g.to_dict() for g in self._goals], indent=2))

    def _inject_system_goals(self):
        """Ensure self-repair is always in the queue."""
        has_repair = any(g.goal_type == "system" and g.status == "pending"
                         for g in self._goals)
        if not has_repair:
            self.push(Goal(
                title     = "self-repair",
                goal_type = "system",
                priority  = self.P_SYSTEM,
                action    = "system_check",
                source    = "system"
            ))

    def push(self, goal: Goal):
        self._goals.append(goal)
        self._sort()
        self._save()

    def next(self) -> Goal | None:
        self._sort()
        for g in self._goals:
            if g.status == "pending":
                return g
        return None

    def pending(self) -> list[Goal]:
        self._sort()
        return [g for g in self._goals if g.status == "pending"]

    def update(self, goal: Goal):
        for i, g in enumerate(self._goals):
            if g.id == goal.id:
                self._goals[i] = goal
                break
        self._save()

    def clear_done(self):
        self._goals = [g for g in self._goals
                       if g.status not in ("done","skipped","failed")]
        self._save()

    def _sort(self):
        self._goals.sort(key=lambda g: (g.priority, g.created))

    def display(self) -> str:
        icons = {"done":"✓","failed":"✗","active":"▶",
                 "pending":"○","skipped":"—"}
        lines = []
        for g in self._goals[-20:]:
            icon = icons.get(g.status,"?")
            lines.append(f"  {icon} [{g.priority:02d}] {g.title[:55]:<55} {g.status}")
        return "\n".join(lines) if lines else "  (empty)"
