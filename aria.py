#!/usr/bin/env python3
"""
ARIA v4 — Phi-3 Mini as the local brain
=========================================
Phi-3 runs locally and is the intelligence behind everything:
  - Chat with you naturally
  - Plan and prioritise goals on boot
  - Write code to achieve goals
  - Execute that code and check results
  - Route to cloud AIs when it needs more power
  - Learn from every outcome

Usage:
    python3 aria.py                  ← chat + agent mode
    python3 aria.py --agent          ← agent only (no chat prompt)
    python3 aria.py --add-goal "..."  ← inject a goal then run

Requirements:
    ollama running with phi3:mini pulled
    pip install ollama openai psutil
"""

import os, sys, json, time, re, ast, subprocess, hashlib, textwrap, argparse, traceback
from pathlib  import Path
from datetime import datetime


# ═══════════════════════════════════════════════
#  COLOURS
# ═══════════════════════════════════════════════
class C:
    R="\033[0m"; B="\033[1m"; DIM="\033[2m"
    GRN="\033[92m"; YLW="\033[93m"; CYN="\033[96m"
    PRP="\033[95m"; RED="\033[91m"; GRY="\033[90m"
    WHT="\033[97m"

def ts(): return f"{C.GRY}[{datetime.now().strftime('%H:%M:%S')}]{C.R}"
def log(lvl, msg, detail=""):
    icons = {"BOOT":f"{C.CYN}◈{C.R}","GOAL":f"{C.PRP}◆{C.R}",
             "STEP":f"{C.YLW}▸{C.R}","OK":f"{C.GRN}✓{C.R}",
             "FAIL":f"{C.RED}✗{C.R}","FIX":f"{C.YLW}⟳{C.R}",
             "THINK":f"{C.CYN}≋{C.R}","DONE":f"{C.GRN}★{C.R}",
             "BRAIN":f"{C.PRP}◎{C.R}","RUN":f"{C.YLW}▶{C.R}",
             "LEARN":f"{C.GRN}↺{C.R}","CLOUD":f"{C.CYN}↗{C.R}",
             "CHAT":f"{C.WHT}❯{C.R}"}
    icon = icons.get(lvl,"·")
    line = f"{ts()} {icon} {C.B}{msg}{C.R}"
    if detail: line += f"  {C.DIM}{detail}{C.R}"
    print(line)


# ═══════════════════════════════════════════════
#  MEMORY  — conversation + outcome history
# ═══════════════════════════════════════════════
class Memory:
    """
    Two stores:
      chat_history  — rolling conversation with Phi-3 (last 30 messages)
      outcome_log   — every goal attempt + result (used for learning)
    """
    def __init__(self, base="memory"):
        self.base = Path(base)
        self.base.mkdir(exist_ok=True)
        self._chat_path    = self.base / "chat.json"
        self._outcome_path = self.base / "outcomes.json"
        self._chat:    list = self._load(self._chat_path)
        self._outcomes:list = self._load(self._outcome_path)

    def _load(self, path) -> list:
        try: return json.loads(path.read_text())
        except: return []

    def _save(self, path, data):
        path.write_text(json.dumps(data, indent=2))

    # ── Chat history ──────────────────────────
    def add_chat(self, role: str, content: str):
        self._chat.append({"role": role, "content": content,
                           "ts": datetime.now().isoformat()})
        if len(self._chat) > 60:          # keep last 30 exchanges
            self._chat = self._chat[-60:]
        self._save(self._chat_path, self._chat)

    def get_chat(self, last_n=20) -> list:
        """Returns list of {role, content} for Ollama."""
        return [{"role": m["role"], "content": m["content"]}
                for m in self._chat[-last_n:]]

    def clear_chat(self):
        self._chat = []
        self._save(self._chat_path, self._chat)

    # ── Outcome log ───────────────────────────
    def log_outcome(self, goal: str, tool: str, success: bool,
                    result_summary: str, code_written: str = ""):
        self._outcomes.append({
            "ts":       datetime.now().isoformat(),
            "goal":     goal,
            "tool":     tool,
            "success":  success,
            "summary":  result_summary[:300],
            "had_code": bool(code_written),
        })
        self._save(self._outcome_path, self._outcomes)

    def recent_outcomes(self, n=10) -> list:
        return self._outcomes[-n:]

    def success_rate(self, tool: str) -> float:
        relevant = [o for o in self._outcomes if o["tool"] == tool]
        if not relevant: return 0.5   # unknown → neutral
        return sum(1 for o in relevant if o["success"]) / len(relevant)


# ═══════════════════════════════════════════════
#  GOAL  — the atomic unit of work
# ═══════════════════════════════════════════════
class Goal:
    STATUSES = ("pending","active","done","failed","skipped")

    def __init__(self, title, description="", priority=50,
                 goal_type="autonomous", steps=None):
        self.id          = hashlib.md5(f"{title}{time.time()}".encode()).hexdigest()[:8]
        self.title       = title
        self.description = description or title
        self.priority    = priority
        self.goal_type   = goal_type   # autonomous | human_needed | sensor
        self.status      = "pending"
        self.steps       = steps or []
        self.history     = []
        self.result      = {}
        self.retries     = 0
        self.code        = ""          # any code Phi-3 wrote for this goal

    def log(self, msg, data=None):
        self.history.append({"ts": datetime.now().isoformat(),
                             "msg": msg, "data": data or {}})

    def to_dict(self):
        return {"id":self.id,"title":self.title,"status":self.status,
                "priority":self.priority,"type":self.goal_type,
                "retries":self.retries,"history":self.history[-3:]}


# ═══════════════════════════════════════════════
#  GOAL STORE  — persisted to disk
# ═══════════════════════════════════════════════
class GoalStore:
    def __init__(self, path="memory/goals.json"):
        self.path = Path(path)
        self.path.parent.mkdir(exist_ok=True)
        self._goals: list[Goal] = self._load()

    def _load(self) -> list[Goal]:
        try:
            data = json.loads(self.path.read_text())
            goals = []
            for d in data:
                g = Goal(d["title"], d.get("description",""),
                         d.get("priority",50), d.get("type","autonomous"))
                g.id      = d.get("id", g.id)
                g.status  = d.get("status","pending")
                g.retries = d.get("retries", 0)
                g.history = d.get("history", [])
                goals.append(g)
            return goals
        except:
            return []

    def _save(self):
        self.path.write_text(json.dumps(
            [g.to_dict() for g in self._goals], indent=2))

    def add(self, goal: Goal):
        self._goals.append(goal)
        self._sort()
        self._save()

    def pending(self) -> list[Goal]:
        self._sort()
        return [g for g in self._goals if g.status == "pending"]

    def all(self) -> list[Goal]:
        return self._goals

    def _sort(self):
        self._goals.sort(key=lambda g: g.priority)

    def update(self, goal: Goal):
        for i, g in enumerate(self._goals):
            if g.id == goal.id:
                self._goals[i] = goal
                break
        self._save()

    def clear_done(self):
        self._goals = [g for g in self._goals
                       if g.status not in ("done","skipped")]
        self._save()


# ═══════════════════════════════════════════════
#  TOOL EXECUTOR  — every tool has a fallback chain
# ═══════════════════════════════════════════════
class ToolExecutor:
    """
    Tools ARIA can use. Each has a fallback list.
    Phi-3 decides which tool to call; this class runs it
    and falls back automatically on failure.
    """

    FALLBACKS = {
        "ai_code":   ["cloud_ai",  "local_phi3", "skip"],
        "ai_reason": ["local_phi3","cloud_ai",   "skip"],
        "search":    ["duckduckgo","wikipedia",   "local_cache", "skip"],
        "run_code":  ["subprocess","python_exec", "log_and_skip"],
        "file":      ["direct",    "copy_tmp",    "skip"],
        "notify":    ["telegram",  "local_log"],
        "shell":     ["subprocess","log_and_skip"],
    }

    def __init__(self, phi3: 'Phi3Brain', cloud: 'CloudRouter', memory: Memory):
        self.phi3   = phi3
        self.cloud  = cloud
        self.memory = memory
        self._cache = {}   # local search cache

    def run(self, tool: str, **kwargs) -> dict:
        """Run a tool with automatic fallback. Always returns {ok, result, tool_used}."""
        chain = self.FALLBACKS.get(tool, [tool, "skip"])
        last_error = ""

        for candidate in chain:
            try:
                result = self._run_one(candidate, **kwargs)
                self.memory.log_outcome(
                    kwargs.get("goal","?"), candidate, True, str(result)[:200])
                return {"ok": True, "result": result, "tool_used": candidate}
            except Exception as e:
                last_error = str(e)
                log("FIX", f"Tool '{candidate}' failed → trying fallback",
                    last_error[:60])
                self.memory.log_outcome(
                    kwargs.get("goal","?"), candidate, False, last_error[:200])
                continue

        return {"ok": False, "result": last_error, "tool_used": "none"}

    def _run_one(self, tool: str, **kwargs) -> str:
        # ── AI tools ─────────────────────────────
        if tool == "cloud_ai":
            prompt = kwargs.get("prompt","")
            return self.cloud.ask(prompt)

        if tool == "local_phi3":
            prompt = kwargs.get("prompt","")
            return self.phi3.ask(prompt)

        # ── Code execution ────────────────────────
        if tool == "subprocess":
            code    = kwargs.get("code","")
            timeout = kwargs.get("timeout", 30)
            path    = Path("output") / f"_run_{int(time.time())}.py"
            path.write_text(code)
            env = os.environ.copy()
            env["SDL_VIDEODRIVER"] = "dummy"
            env["SDL_AUDIODRIVER"] = "dummy"
            r = subprocess.run(
                [sys.executable, str(path)],
                capture_output=True, text=True,
                timeout=timeout, env=env
            )
            path.unlink(missing_ok=True)
            if r.returncode != 0:
                raise RuntimeError(r.stderr[:300])
            return r.stdout[:500] or "ran ok"

        if tool == "python_exec":
            # Safe eval of simple expressions
            code = kwargs.get("code","")
            try:
                result = eval(compile(code, "<aria>", "exec"))
                return str(result)
            except:
                exec(code, {})
                return "exec ok"

        if tool == "log_and_skip":
            log("FIX", "Skipping — logging instead")
            return "skipped — logged"

        # ── Search ────────────────────────────────
        if tool == "duckduckgo":
            query = kwargs.get("query","")
            q = query.replace(" ", "+")
            try:
                import urllib.request
                url = f"https://api.duckduckgo.com/?q={q}&format=json&no_html=1"
                with urllib.request.urlopen(url, timeout=10) as resp:
                    import json as _json
                    d = _json.loads(resp.read())
                    result2 = d.get("AbstractText","") or d.get("Answer","")
                    if not result2:
                        raise RuntimeError("no result")
                    self._cache[query] = result2
                    return result2[:400]
            except Exception as ex:
                raise RuntimeError(f"duckduckgo: {ex}")

        if tool == "wikipedia":
            query = kwargs.get("query","").replace(" ","_")
            r = subprocess.run(
                ["python3","-c",
                 f"import urllib.request; "
                 f"u='https://en.wikipedia.org/api/rest_v1/page/summary/{query}'; "
                 f"r=urllib.request.urlopen(u); "
                 f"import json; d=json.loads(r.read()); "
                 f"print(d.get('extract','')[:400])"],
                capture_output=True, text=True, timeout=10
            )
            result = r.stdout.strip()
            if result:
                self._cache[query] = result
                return result
            raise RuntimeError("no wikipedia result")

        if tool == "local_cache":
            query = kwargs.get("query","")
            cached = self._cache.get(query)
            if cached: return cached
            raise RuntimeError("not in cache")

        # ── File ─────────────────────────────────
        if tool == "direct":
            path = kwargs.get("path","")
            return Path(path).read_text()

        if tool == "copy_tmp":
            path = kwargs.get("path","")
            import shutil
            tmp = Path("/tmp") / Path(path).name
            shutil.copy2(path, tmp)
            return tmp.read_text()

        # ── Notify ────────────────────────────────
        if tool == "telegram":
            token   = os.environ.get("TELEGRAM_BOT_TOKEN","")
            chat_id = os.environ.get("TELEGRAM_CHAT_ID","")
            if not token or not chat_id:
                raise RuntimeError("no telegram config")
            import urllib.request
            msg = kwargs.get("message","ARIA notification")
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            data = json.dumps({"chat_id":chat_id,"text":msg}).encode()
            req  = urllib.request.Request(url, data=data,
                   headers={"Content-Type":"application/json"})
            urllib.request.urlopen(req, timeout=10)
            return "telegram sent"

        if tool == "local_log":
            msg  = kwargs.get("message","ARIA notification")
            path = Path("logs/notifications.log")
            path.parent.mkdir(exist_ok=True)
            with open(path,"a") as f:
                f.write(f"[{datetime.now().isoformat()}] {msg}\n")
            return "logged"

        # ── Shell ─────────────────────────────────
        if tool == "shell":
            cmd = kwargs.get("cmd","")
            r   = subprocess.run(cmd, shell=True, capture_output=True,
                                 text=True, timeout=30)
            if r.returncode != 0:
                raise RuntimeError(r.stderr[:200])
            return r.stdout[:500]

        if tool == "skip":
            return "skipped"

        raise RuntimeError(f"unknown tool: {tool}")


# ═══════════════════════════════════════════════
#  CLOUD ROUTER  — fallback when Phi-3 needs help
# ═══════════════════════════════════════════════
class CloudRouter:
    """
    Cloud AIs are tools, not the brain.
    Phi-3 calls them when a task is beyond its ability
    (e.g. writing 500 lines of complex code).
    Fallback: GitHub Models → Groq → skip
    """

    def __init__(self):
        self._providers = []
        self._setup()

    def _setup(self):
        store_path = Path("memory/.keys.json")
        keys = {}
        try: keys = json.loads(store_path.read_text())
        except: pass

        # GitHub Models (GPT-4o) — free
        gh = os.environ.get("GITHUB_TOKEN","") or keys.get("github_token","")
        if gh:
            try:
                from openai import OpenAI
                client = OpenAI(api_key=gh,
                    base_url="https://models.inference.ai.azure.com")
                self._providers.append(("GitHub/GPT-4o", client, "gpt-4o"))
                log("OK", "Cloud: GitHub Models ready")
            except: pass

        # Groq — free, fast
        gq = os.environ.get("GROQ_API_KEY","") or keys.get("groq_api_key","")
        if gq:
            try:
                from openai import OpenAI
                client = OpenAI(api_key=gq,
                    base_url="https://api.groq.com/openai/v1")
                self._providers.append(("Groq/Llama", client, "llama-3.3-70b-versatile"))
                log("OK", "Cloud: Groq ready")
            except: pass

        # Google Gemini
        gm = os.environ.get("GOOGLE_API_KEY","") or keys.get("google_api_key","")
        if gm:
            try:
                from google import genai
                from google.genai import types
                self._gem_client = genai.Client(api_key=gm)
                self._gem_types  = types
                self._providers.append(("Gemini", None, "gemini-2.0-flash"))
                log("OK", "Cloud: Gemini ready")
            except: pass

        if not self._providers:
            log("FIX", "No cloud providers found — Phi-3 works alone")

    def ask(self, prompt: str, system: str = None) -> str:
        sys_msg = system or (
            "You are a powerful AI assistant helping ARIA complete goals. "
            "Be precise. When asked for code output ONLY code, no explanation."
        )
        for name, client, model in self._providers:
            try:
                log("CLOUD", f"Calling {name}...", prompt[:60].replace("\n"," "))
                if name == "Gemini":
                    resp = self._gem_client.models.generate_content(
                        model=model,
                        contents=[self._gem_types.Content(
                            role="user",
                            parts=[self._gem_types.Part(text=prompt)]
                        )],
                        config=self._gem_types.GenerateContentConfig(
                            system_instruction=sys_msg,
                            temperature=0.3, max_output_tokens=8192)
                    )
                    return resp.text or ""
                else:
                    resp = client.chat.completions.create(
                        model=model, temperature=0.3, max_tokens=8192,
                        messages=[{"role":"system","content":sys_msg},
                                  {"role":"user",  "content":prompt}]
                    )
                    return resp.choices[0].message.content or ""
            except Exception as e:
                msg = str(e)
                if "429" in msg or "quota" in msg.lower() or "rate" in msg.lower():
                    log("FIX", f"{name} quota hit → next provider")
                else:
                    log("FIX", f"{name} error → next provider", msg[:60])
                continue
        raise RuntimeError("All cloud providers unavailable")

    def available(self) -> bool:
        return len(self._providers) > 0


# ═══════════════════════════════════════════════
#  PHI-3 BRAIN  — the local intelligence
# ═══════════════════════════════════════════════
class Phi3Brain:
    """
    Phi-3 Mini running via Ollama.
    This is ARIA's permanent local brain.
    It plans, routes, writes small code, checks results,
    and holds conversations. It uses cloud AIs as tools
    when a task is too large for it.
    """

    MODEL   = "phi3:mini"
    PERSONA = """You are ARIA, an autonomous agent running on this Linux laptop.
You are concise and action-driven. Never give speeches or bullet-pointed instructions.
When the user asks you to DO something (open an app, run a command, build something,
find something, fix something) you act immediately — you do not explain how they could do it.
When the user wants to chat or asks a question, answer briefly and naturally.
One short paragraph maximum. No lists. No headers."""

    # Fast intent classifier — no Phi-3 needed, runs in microseconds
    ACTION_PATTERNS = [
        # shell / system actions
        (r"open\s+\w+",                    "shell",   70),
        (r"run\s+.+",                        "shell",   70),
        (r"start\s+\w+",                    "shell",   70),
        (r"launch\s+\w+",                   "shell",   70),
        (r"close\s+\w+",                    "shell",   60),
        (r"kill\s+.+",                       "shell",   60),
        (r"install\s+.+",                    "shell",   80),
        (r"update\s+.+",                     "shell",   70),
        (r"create\s+(?:a\s+)?(?:file|dir)", "shell",   70),
        (r"delete\s+.+",                     "shell",   75),
        (r"move\s+.+to\s+.+",              "shell",   70),
        # build / code tasks
        (r"build\s+.+",                      "code",    85),
        (r"make\s+(?:a\s+)?\w+\s+game",  "code",    90),
        (r"write\s+(?:a\s+)?(?:script|program|code)", "code", 85),
        (r"create\s+(?:a\s+)?(?:app|game|script)",    "code", 85),
        # search / find
        (r"search\s+(?:for\s+)?.+",         "search",  75),
        (r"find\s+.+",                        "search",  65),
        (r"look\s+up\s+.+",                 "search",  70),
        # file ops
        (r"read\s+.+\.\w+",               "file",    70),
        (r"show\s+(?:me\s+)?(?:the\s+)?.+\.\w+", "file", 65),
        # notify
        (r"remind\s+me",                     "notify",  70),
        (r"send\s+(?:a\s+)?(?:message|alert|notification)", "notify", 80),
    ]

    def __init__(self):
        import ollama as _ollama
        self._ollama = _ollama
        self._verify()

    def _verify(self):
        try:
            self._ollama.chat(model=self.MODEL,
                messages=[{"role":"user","content":"reply: READY"}])
            log("OK", f"Phi-3 Mini online")
        except Exception as e:
            log("FAIL", f"Phi-3 not responding: {e}")
            log("FIX",  "Start Ollama with: ollama serve  (in a separate terminal)")
            sys.exit(1)

    def classify_intent(self, user_input: str) -> dict:
        """
        Classify user input in microseconds using regex patterns.
        Returns: {intent: "action"|"chat", tool: str, confidence: int, goal: str}
        No Phi-3 call needed — fast local pattern matching.
        """
        import re as _re
        text = user_input.lower().strip()

        # Hard chat signals — never treat these as actions
        chat_signals = [
            r"^(hi|hey|hello|sup|yo)\b",
            r"^what (is|are|do|does|can|time)",
            r"^(who|why|how does|explain|tell me about)",
            r"^(thanks|thank you|ok|okay|got it|nice|cool|great)",
            r"^(yes|no|maybe|sure|nope)\b",
            r"\?$",   # ends with question mark → probably chat
        ]
        for pattern in chat_signals:
            if _re.search(pattern, text):
                return {"intent": "chat", "tool": None,
                        "confidence": 80, "goal": user_input}

        # Check action patterns
        best = None
        best_conf = 0
        best_tool = None
        for pattern, tool, confidence in self.ACTION_PATTERNS:
            if _re.search(pattern, text, _re.I):
                if confidence > best_conf:
                    best_conf = confidence
                    best_tool = tool
                    best = pattern

        if best and best_conf >= 65:
            return {"intent": "action", "tool": best_tool,
                    "confidence": best_conf, "goal": user_input}

        # Ambiguous — default to chat (safer than acting on wrong intent)
        return {"intent": "chat", "tool": None,
                "confidence": 50, "goal": user_input}

    def ask(self, prompt: str, history: list = None,
            system: str = None) -> str:
        """Raw call to Phi-3. Returns text."""
        messages = []
        if history:
            messages = list(history)
        messages.append({"role":"user","content":prompt})

        resp = self._ollama.chat(
            model   = self.MODEL,
            messages= messages,
            options = {"temperature": 0.4, "num_ctx": 4096},
            stream  = False
        )
        return resp["message"]["content"].strip()

    def ask_stream(self, prompt: str, history: list = None,
                   system: str = None):
        """Streaming call — yields text chunks for live display."""
        messages = []
        if history:
            messages = list(history)
        messages.append({"role":"user","content":prompt})

        for chunk in self._ollama.chat(
            model   = self.MODEL,
            messages= messages,
            options = {"temperature": 0.5, "num_ctx": 4096},
            stream  = True
        ):
            yield chunk["message"]["content"]

    def plan_goal(self, goal_title: str, available_tools: list,
                  past_outcomes: list) -> dict:
        """
        Short, fast prompt — Phi-3 responds in seconds not minutes.
        """
        # Build a one-line history hint
        hint = ""
        if past_outcomes:
            last = past_outcomes[-1]
            hint = f" (last: {last['tool']} {'ok' if last['success'] else 'fail'})"

        prompt = (
            f'Goal: "{goal_title}"{hint}\n'
            f'Tools: {", ".join(available_tools[:4])}\n'
            f'Reply JSON only, no explanation:\n'
            f'{{"needs_code":true/false,"primary_tool":"name","fallback_tool":"name",'
            f'"approach":"one sentence","needs_cloud":true/false}}'
        )

        raw = self.ask(prompt)
        try:
            # Extract JSON even if Phi-3 adds surrounding text
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if match:
                return json.loads(match.group())
        except:
            pass
        # Fallback plan if JSON parsing fails
        return {
            "needs_code":    False,
            "primary_tool":  "cloud_ai" if available_tools else "skip",
            "fallback_tool": "local_phi3",
            "approach":      f"attempt goal directly",
            "steps":         [goal_title],
            "needs_cloud":   True,
            "reason":        "default plan"
        }

    def write_code(self, task: str, context: str = "",
                   cloud: 'CloudRouter' = None) -> str:
        """
        Phi-3 writes code for a task.
        If the task is complex, it delegates to cloud AI
        and then reviews the result.
        """
        # Estimate complexity: if task mentions game/full app → use cloud
        complex_keywords = ["game","tetris","snake","full","complete",
                            "app","pygame","gui","database"]
        is_complex = any(k in task.lower() for k in complex_keywords)

        if is_complex and cloud and cloud.available():
            log("THINK", "Complex code task → delegating to cloud AI")
            prompt = f"""Write complete Python code for: {task}
{('Context: ' + context) if context else ''}
Output ONLY the Python code. No explanation. No markdown fences."""
            code = cloud.ask(prompt)
        else:
            log("THINK", f"Phi-3 writing code for: {task[:60]}")
            prompt = f"""Write Python code for: {task}
{('Context: ' + context) if context else ''}
Output ONLY working Python code. No explanation."""
            code = self.ask(prompt)

        # Strip markdown fences if present
        code = re.sub(r'^```python\n?', '', code.strip())
        code = re.sub(r'\n?```$',       '', code.strip())
        return code.strip()

    def check_result(self, goal: str, result: str) -> dict:
        """
        Quick success check — short prompt for fast response.
        Falls back to heuristic if Phi-3 is slow.
        """
        # Heuristic first — fast and good enough for most cases
        result_lower = result.lower()
        error_words  = ["error","traceback","exception","failed","no result",
                        "not found","refused","denied"]
        ok_words     = ["ok","success","done","complete","ran ok","wrote","created"]

        auto_fail = any(w in result_lower for w in error_words)
        auto_ok   = any(w in result_lower for w in ok_words)

        if auto_fail and not auto_ok:
            return {"success": False, "confidence": 0.8, "notes": "error detected"}
        if auto_ok and not auto_fail:
            return {"success": True,  "confidence": 0.8, "notes": "success detected"}

        # Ambiguous — ask Phi-3 briefly
        prompt = (
            f'Goal: "{goal[:60]}"\n'
            f'Result: "{result[:120]}"\n'
            f'Reply JSON: {{"success":true/false,"confidence":0.0-1.0,"notes":"reason"}}'
        )
        try:
            raw = self.ask(prompt)
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if match:
                return json.loads(match.group())
        except:
            pass
        return {"success": not auto_fail, "confidence": 0.5, "notes": "heuristic"}

    def generate_subgoals(self, main_goal: str) -> list[str]:
        """Break a big goal into smaller sub-goals."""
        prompt = f"""Break this goal into 3-5 concrete sub-goals:
"{main_goal}"
Reply ONLY with a JSON array of strings:
["sub-goal 1", "sub-goal 2", "sub-goal 3"]"""

        raw = self.ask(prompt)
        try:
            match = re.search(r'\[.*\]', raw, re.DOTALL)
            if match:
                return json.loads(match.group())
        except:
            pass
        return [main_goal]


# ═══════════════════════════════════════════════
#  AGENT  — the execution engine
# ═══════════════════════════════════════════════
class Agent:
    """
    Runs the goal queue. For each goal:
      1. Phi-3 plans the approach
      2. Phi-3 writes code if needed
      3. ToolExecutor runs with fallbacks
      4. Phi-3 checks the result
      5. Outcome logged → learning
    """

    AVAILABLE_TOOLS = ["cloud_ai","local_phi3","search",
                       "run_code","shell","file","notify"]

    def __init__(self, brain: Phi3Brain, cloud: CloudRouter,
                 memory: Memory, store: GoalStore):
        self.brain   = brain
        self.cloud   = cloud
        self.memory  = memory
        self.store   = store
        self.tools   = ToolExecutor(brain, cloud, memory)

    def run_all(self):
        """Process all pending goals in priority order."""
        pending = self.store.pending()
        if not pending:
            log("DONE", "No pending goals.")
            return

        log("BOOT", f"{len(pending)} goal(s) to process")
        for goal in pending:
            self._run_goal(goal)

    def _run_goal(self, goal: Goal):
        goal.status = "active"
        self.store.update(goal)
        log("GOAL", f"Working on: {goal.title}")

        # Fast path: system goals run concrete checks, no Phi-3 needed
        if goal.goal_type == "system":
            ok = self_repair()
            goal.status = "done" if ok else "failed"
            goal.result = {"ok": ok}
            goal.log("system check complete")
            self.store.update(goal)
            log("OK" if ok else "FAIL", "System check complete")
            return

        try:
            # ── 1. Plan — use hint if available (fast path) ──
            hint = getattr(goal, '_hint_tool', None)
            if hint == "shell":
                # Shell action — Phi-3 writes the command, no full planning needed
                log("THINK", "Shell action — Phi-3 writing command...")
                cmd_prompt = (
                    f'Write a single Linux shell command to: {goal.title}\n'
                    f'Reply with ONLY the command, nothing else. No explanation.' 
                )
                cmd = self.brain.ask(cmd_prompt).strip().strip('`').strip()
                # Strip markdown if Phi-3 wraps it
                import re as _re
                cmd = _re.sub(r'^```\w*\n?','',cmd).strip()
                cmd = _re.sub(r'\n?```$','',cmd).strip()
                log("RUN", f"Command: {cmd}")
                result = self.tools.run("shell", cmd=cmd, goal=goal.title)
                out = result.get("result","")
                goal.status = "done" if result["ok"] else "failed"
                goal.result = {"output": out,
                               "check": {"success": result["ok"],
                                         "confidence": 0.9,
                                         "notes": "shell ran" if result["ok"] else out[:80]}}
                goal.log("shell complete", {"cmd": cmd, "ok": result["ok"]})
                self.store.update(goal)
                self.memory.log_outcome(goal.title, "shell", result["ok"], out[:200])
                return

            log("THINK", "Phi-3 planning approach...")
            plan = self.brain.plan_goal(
                goal.title,
                self.AVAILABLE_TOOLS,
                self.memory.recent_outcomes(5)
            )
            log("STEP", f"Plan: {plan.get('approach','?')}",
                f"tool={plan.get('primary_tool')} cloud={plan.get('needs_cloud')}")
            goal.log("planned", plan)

            # ── 2. Write code if needed ─────────
            code_result = ""
            if plan.get("needs_code"):
                log("THINK", "Phi-3 writing code...")
                context = "\n".join(plan.get("steps",[]))
                code = self.brain.write_code(
                    goal.title, context,
                    cloud=self.cloud if plan.get("needs_cloud") else None
                )
                goal.code = code

                # Save the code
                filename  = re.sub(r'[^a-z0-9]+','_', goal.title.lower())[:30]
                code_path = Path("output") / f"{filename}.py"
                code_path.write_text(code)
                log("OK", f"Code written → {code_path.name}",
                    f"{len(code.splitlines())} lines")

                # Validate syntax
                ok, err = self._syntax_check(code)
                if not ok:
                    log("FIX", "Syntax error — asking Phi-3 to fix...")
                    fix_prompt = f"""Fix this Python syntax error:
Error: {err}
Code:
{code}
Output ONLY the fixed Python code."""
                    code = self.brain.ask(fix_prompt)
                    code = re.sub(r'^```python\n?','',code.strip())
                    code = re.sub(r'\n?```$','',code.strip())
                    code_path.write_text(code)
                    goal.code = code

                # Run it
                log("RUN", f"Running {code_path.name}...")
                run_result = self.tools.run("run_code",
                    code=code, goal=goal.title, timeout=15)
                code_result = run_result.get("result","")
                if run_result["ok"]:
                    log("OK", "Code ran successfully", code_result[:60])
                else:
                    log("FIX", "Code error — asking Phi-3 to diagnose...",
                        code_result[:60])
                    # One auto-fix attempt
                    fix_prompt = f"""Fix this Python error:
Error: {code_result}
Code:
{code}
Output ONLY the fixed Python code."""
                    fixed_code = self.brain.ask(fix_prompt)
                    fixed_code = re.sub(r'^```python\n?','',fixed_code.strip())
                    fixed_code = re.sub(r'\n?```$','',fixed_code.strip())
                    code_path.write_text(fixed_code)
                    goal.code = fixed_code
                    retry = self.tools.run("run_code",
                        code=fixed_code, goal=goal.title, timeout=15)
                    code_result = retry.get("result","")
                    if retry["ok"]:
                        log("OK", "Fixed and ran successfully")
                    else:
                        log("FIX","Still failing — continuing anyway",
                            code_result[:40])

            else:
                # ── 3. Use a tool directly ───────
                primary = plan.get("primary_tool","cloud_ai")
                prompt  = f"Complete this goal: {goal.title}\n{goal.description}"

                if primary in ("cloud_ai","local_phi3"):
                    result = self.tools.run("ai_reason",
                        prompt=prompt, goal=goal.title)
                else:
                    result = self.tools.run(primary,
                        prompt=prompt, goal=goal.title)

                code_result = result.get("result","")
                log("OK" if result["ok"] else "FIX",
                    f"Tool result via {result['tool_used']}",
                    code_result[:80])

            # ── 4. Phi-3 checks result ──────────
            log("THINK", "Phi-3 reviewing result...")
            check = self.brain.check_result(goal.title, code_result)
            success = check.get("success", True)
            log("OK" if success else "FAIL",
                f"Result: {'success' if success else 'partial'}",
                f"confidence={check.get('confidence',0):.0%} — {check.get('notes','')[:60]}")

            # ── 5. Done ──────────────────────────
            goal.status = "done" if success else "failed"
            goal.result = {"output": code_result[:500], "check": check}
            goal.log("completed", check)
            self.store.update(goal)

            self.memory.log_outcome(
                goal.title,
                plan.get("primary_tool","?"),
                success,
                code_result[:200],
                goal.code
            )

            # Learn: if goal produced working code → save as example
            if success and goal.code:
                self._save_example(goal)

        except Exception as e:
            goal.status = "failed"
            goal.retries += 1
            goal.log(f"error: {e}")
            self.store.update(goal)
            log("FAIL", f"Goal failed: {e}", traceback.format_exc()[-200:])

            if goal.retries <= 2:
                log("FIX", f"Retrying goal (attempt {goal.retries}/2)...")
                goal.status = "pending"
                self.store.update(goal)

    def _syntax_check(self, code: str) -> tuple[bool, str]:
        try:
            ast.parse(code)
            return True, ""
        except SyntaxError as e:
            return False, f"line {e.lineno}: {e.msg}"

    def _save_example(self, goal: Goal):
        """Save successful code as a training example for future reference."""
        ex_dir = Path("memory/examples")
        ex_dir.mkdir(exist_ok=True)
        name = re.sub(r'[^a-z0-9]+','_', goal.title.lower())[:30]
        (ex_dir / f"{name}.py").write_text(goal.code)
        log("LEARN", f"Saved example: {name}.py")


# ═══════════════════════════════════════════════
#  CHAT MODE  — talk to Phi-3 directly
# ═══════════════════════════════════════════════
class Chat:
    """
    Natural conversation with Phi-3.
    Phi-3 remembers the conversation and can:
      - answer questions
      - add goals ("add goal: build tetris")
      - show goal queue ("goals")
      - clear memory ("forget")
      - run agent ("run goals")
    """

    COMMANDS = {
        "goals":      "show current goal queue",
        "run goals":  "execute all pending goals now",
        "clear done": "remove completed goals",
        "forget":     "clear conversation memory",
        "help":       "show this help",
        "quit":       "exit ARIA",
    }

    def __init__(self, brain: Phi3Brain, agent: Agent,
                 store: GoalStore, memory: Memory):
        self.brain  = brain
        self.agent  = agent
        self.store  = store
        self.memory = memory

    def run(self):
        self._banner()
        print(f"{C.PRP}ARIA{C.R} Online. What do you need?\n")

        while True:
            try:
                user = input(f"{C.YLW}you ▸ {C.R}").strip()
            except (KeyboardInterrupt, EOFError):
                print(f"\n{C.GRY}Goodbye.{C.R}")
                break

            if not user:
                continue
            lower = user.lower()

            # ── Hard commands (instant, no AI) ──
            if lower in ("quit","exit","bye","q"):
                print(f"{C.GRY}Goodbye.{C.R}"); break
            if lower == "goals":
                self._show_goals(); continue
            if lower == "run goals":
                self.agent.run_all(); continue
            if lower == "clear done":
                self.store.clear_done()
                print(f"{C.GRY}Done goals cleared.{C.R}\n"); continue
            if lower == "forget":
                self.memory.clear_chat()
                print(f"{C.GRY}Memory cleared.{C.R}\n"); continue
            if lower == "help":
                self._show_help(); continue

            # ── Intent classification (microseconds, no Phi-3) ──
            intent = self.brain.classify_intent(user)

            if intent["intent"] == "action":
                # ACTION PATH — create goal and run it immediately
                g = Goal(user, description=user,
                         priority=10, goal_type="autonomous")
                g._hint_tool = intent["tool"]
                self.store.add(g)
                self.memory.add_chat("user", user)
                self.memory.add_chat("assistant", f"Working on it...")

                print(f"{C.PRP}ARIA{C.R} On it.\n")
                log("GOAL", f"Action → goal: {user[:60]}",
                    f"tool={intent['tool']} conf={intent['confidence']}%")

                self.agent._run_goal(g)
                self.store.update(g)

                out     = g.result.get("output","done")[:200]
                success = g.result.get("check",{}).get("success", True)
                status  = f"{C.GRN}Done.{C.R}" if success else f"{C.YLW}Partial.{C.R}"
                print(f"\n{C.PRP}ARIA{C.R} {status} {out}\n")

            else:
                # CHAT PATH — stream Phi-3 response naturally
                self.memory.add_chat("user", user)
                history = self.memory.get_chat(last_n=16)

                print(f"{C.PRP}ARIA{C.R} ", end="", flush=True)
                full = ""
                for chunk in self.brain.ask_stream(user, history=history[:-1]):
                    print(chunk, end="", flush=True)
                    full += chunk
                print("\n")
                self.memory.add_chat("assistant", full)

    def _show_goals(self):
        goals = self.store.all()
        if not goals:
            print(f"  {C.GRY}No goals.{C.R}\n")
            return
        print(f"\n  {C.B}Goal queue:{C.R}")
        for g in goals:
            icon = {"done":f"{C.GRN}✓{C.R}","failed":f"{C.RED}✗{C.R}",
                    "active":f"{C.YLW}▶{C.R}","pending":f"{C.GRY}○{C.R}",
                    "skipped":f"{C.GRY}—{C.R}"}.get(g.status,"○")
            print(f"  {icon} [{g.priority:02d}] {g.title}")
        print()

    def _show_help(self):
        print(f"\n  {C.B}Commands:{C.R}")
        for cmd, desc in self.COMMANDS.items():
            print(f"  {C.YLW}{cmd:<16}{C.R} {desc}")
        print(f"  {C.YLW}{'add goal: ...':<16}{C.R} add a goal to the queue")
        print(f"  {C.YLW}{'goal: ...':<16}{C.R} shorthand for above\n")

    def _banner(self):
        print(f"\n{C.PRP}{C.B}")
        print("  ╔══════════════════════════════════════════╗")
        print("  ║  ARIA v4  —  Phi-3 Mini local brain     ║")
        print("  ║  type 'help' for commands                ║")
        print(f"  ╚══════════════════════════════════════════╝{C.R}\n")


# ═══════════════════════════════════════════════
#  SELF REPAIR  — always first goal
# ═══════════════════════════════════════════════
def self_repair() -> bool:
    """
    Concrete system check — no AI needed, runs fast.
    Checks packages, directories, disk, and Ollama reachability.
    """
    log("BOOT","Self-repair: checking system...")
    ok = True

    # 1. Required packages
    required = ["ollama","psutil","openai"]
    for pkg in required:
        try:
            __import__(pkg.replace("-","_"))
            log("OK", f"Package: {pkg}")
        except ImportError:
            log("FIX", f"Installing: {pkg}")
            r = subprocess.run([sys.executable,"-m","pip","install",
                                pkg,"-q","--break-system-packages"],
                               capture_output=True)
            ok = ok and r.returncode == 0

    # 2. Directories
    for d in ["output","logs","memory","memory/examples"]:
        Path(d).mkdir(exist_ok=True)
    log("OK","Directories ready")

    # 3. Disk space (warn if < 1GB free)
    import shutil
    free_gb = shutil.disk_usage(".").free / (1024**3)
    if free_gb < 1.0:
        log("FIX", f"Low disk: {free_gb:.1f}GB free")
    else:
        log("OK", f"Disk: {free_gb:.1f}GB free")

    # 4. RAM
    import psutil
    ram_free = psutil.virtual_memory().available / (1024**3)
    log("OK" if ram_free > 1.0 else "FIX",
        f"RAM: {ram_free:.1f}GB available")

    # 5. Ollama reachable (quick HTTP check, no model load)
    try:
        import urllib.request
        urllib.request.urlopen("http://localhost:11434", timeout=3)
        log("OK","Ollama: reachable")
    except:
        log("FIX","Ollama: not reachable — run: ollama serve")
        ok = False

    return ok


# ═══════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(
        description="ARIA v4 — Phi-3 Mini local brain + cloud tools")
    parser.add_argument("--agent",    action="store_true",
                        help="Run agent mode (process goals) without chat")
    parser.add_argument("--add-goal", metavar="GOAL",
                        help="Add a goal then run agent")
    parser.add_argument("--goals",    action="store_true",
                        help="Show current goal queue and exit")
    args = parser.parse_args()

    # ── Self repair first ─────────────────────
    self_repair()

    # ── Boot core components ──────────────────
    log("BOOT","Starting Phi-3 Mini...")
    brain  = Phi3Brain()
    cloud  = CloudRouter()
    memory = Memory()
    store  = GoalStore()

    # Self-repair runs as concrete code, not a Phi-3 question
    # Remove any old pending self-repair goals first to avoid duplicates
    store._goals = [g for g in store._goals
                    if "self-repair" not in g.title.lower()
                    or g.status == "done"]
    store._save()
    store.add(Goal("self-repair: verify system",
                   "Check packages, disk, RAM, and Ollama",
                   priority=0, goal_type="system"))

    agent = Agent(brain, cloud, memory, store)

    # ── Modes ─────────────────────────────────
    if args.goals:
        for g in store.all():
            print(f"[{g.status:8}] [{g.priority:02}] {g.title}")
        return

    if args.add_goal:
        g = Goal(args.add_goal, priority=50)
        store.add(g)
        log("GOAL", f"Added: {args.add_goal}")
        agent.run_all()
        return

    if args.agent:
        agent.run_all()
        return

    # ── Default: chat + agent ─────────────────
    # Run any pending goals first, then open chat
    pending = store.pending()
    if len(pending) > 1:   # more than just self-repair
        log("BOOT", f"Running {len(pending)} pending goals before chat...")
        agent.run_all()

    chat = Chat(brain, agent, store, memory)
    chat.run()


if __name__ == "__main__":
    main()