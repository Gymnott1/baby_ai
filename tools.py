"""
tools.py — Every tool the nucleus can use
==========================================
Each tool:
  - has a fallback chain (nothing hard-crashes)
  - reports success/failure back to SkillMap automatically
  - is stateless — nucleus decides WHICH tool, tools just run

Tools:
  shell       — run any shell command
  python      — run Python code directly
  cloud_code  — ask cloud AI to write and return code
  cloud_ask   — ask cloud AI a question, get text back
  search      — DuckDuckGo → Wikipedia → local cache
  file_read   — read a file
  file_write  — write a file
  notify      — Telegram → local log
  system_check— concrete system health check (no AI)
"""

import os, sys, json, re, time, subprocess, ast, shutil
from pathlib  import Path
from datetime import datetime


# ═══════════════════════════════════════════════
#  TERMINAL COLOURS  (shared with nucleus.py)
# ═══════════════════════════════════════════════
class C:
    R="\033[0m"; B="\033[1m"; DIM="\033[2m"
    GRN="\033[92m"; YLW="\033[93m"; CYN="\033[96m"
    PRP="\033[95m"; RED="\033[91m"; GRY="\033[90m"

def ts(): return f"{C.GRY}[{datetime.now().strftime('%H:%M:%S')}]{C.R}"
def log(lvl, msg, detail=""):
    icons = {
        "RUN":   f"{C.YLW}▶{C.R}", "OK":   f"{C.GRN}✓{C.R}",
        "FAIL":  f"{C.RED}✗{C.R}", "FIX":  f"{C.YLW}⟳{C.R}",
        "CLOUD": f"{C.CYN}↗{C.R}", "TOOL": f"{C.CYN}⬡{C.R}",
        "LEARN": f"{C.GRN}↺{C.R}", "SYS":  f"{C.PRP}◈{C.R}",
    }
    icon = icons.get(lvl, "·")
    line = f"{ts()} {icon} {C.B}{msg}{C.R}"
    if detail: line += f"  {C.DIM}{detail}{C.R}"
    print(line)


# ═══════════════════════════════════════════════
#  CLOUD ROUTER
#  GitHub → Groq → Gemini with auto-fallback
# ═══════════════════════════════════════════════
class CloudRouter:
    """
    The nucleus calls this when it needs outside intelligence.
    Two modes:
      ask(prompt)   — get a text answer
      code(task)    — get complete Python code

    Providers tried in skill-map order (highest success rate first).
    On quota/error: instant fallback to next provider.
    """

    def __init__(self, skill_map):
        self.skills    = skill_map
        self._providers = []
        self._setup()

    def _setup(self):
        store = {}
        try:
            store = json.loads(Path("memory/.keys.json").read_text())
        except:
            pass

        # GitHub Models — GPT-4o free
        gh = os.environ.get("GITHUB_TOKEN","") or store.get("github_token","")
        if gh:
            try:
                from openai import OpenAI
                self._providers.append({
                    "name":   "github",
                    "client": OpenAI(api_key=gh,
                                     base_url="https://models.inference.ai.azure.com"),
                    "model":  "gpt-4o",
                    "type":   "openai"
                })
                log("OK", "Cloud: GitHub/GPT-4o ready")
            except: pass

        # Groq — fast free Llama
        gq = os.environ.get("GROQ_API_KEY","") or store.get("groq_api_key","")
        if gq:
            try:
                from openai import OpenAI
                self._providers.append({
                    "name":   "groq",
                    "client": OpenAI(api_key=gq,
                                     base_url="https://api.groq.com/openai/v1"),
                    "model":  "llama-3.3-70b-versatile",
                    "type":   "openai"
                })
                log("OK", "Cloud: Groq/Llama ready")
            except: pass

        # Google Gemini
        gm = os.environ.get("GOOGLE_API_KEY","") or store.get("google_api_key","")
        if gm:
            try:
                from google import genai
                from google.genai import types
                self._providers.append({
                    "name":   "gemini",
                    "client": genai.Client(api_key=gm),
                    "types":  types,
                    "model":  "gemini-2.0-flash",
                    "type":   "gemini"
                })
                log("OK", "Cloud: Gemini ready")
            except: pass

        if not self._providers:
            log("FIX", "No cloud providers — nucleus runs offline")

    def _call(self, provider: dict, system: str, prompt: str) -> str:
        if provider["type"] == "openai":
            resp = provider["client"].chat.completions.create(
                model       = provider["model"],
                temperature = 0.2,
                max_tokens  = 8192,
                messages    = [{"role":"system","content":system},
                               {"role":"user",  "content":prompt}]
            )
            return resp.choices[0].message.content or ""

        if provider["type"] == "gemini":
            t    = provider["types"]
            resp = provider["client"].models.generate_content(
                model    = provider["model"],
                contents = [t.Content(role="user",
                            parts=[t.Part(text=prompt)])],
                config   = t.GenerateContentConfig(
                    system_instruction=system,
                    temperature=0.2, max_output_tokens=8192)
            )
            return resp.text or ""

        return ""

    def _ranked_providers(self) -> list:
        """Sort providers by their current skill score."""
        scored = []
        for p in self._providers:
            score = self.skills._map.get("chat",{}).get(
                p["name"],{}).get("score", 0.6)
            scored.append((score, p))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [p for _, p in scored]

    def ask(self, prompt: str,
            system: str = "Be concise and precise.") -> str:
        """Ask a question, get a text answer."""
        for p in self._ranked_providers():
            t0 = time.time()
            try:
                log("CLOUD", f"Asking {p['name']}...", prompt[:60].replace("\n"," "))
                result = self._call(p, system, prompt)
                self.skills.update("chat", p["name"], True, time.time()-t0)
                return result
            except Exception as e:
                msg = str(e)
                is_quota = any(w in msg.lower() for w in
                               ["429","quota","rate","limit","token"])
                log("FIX", f"{p['name']} {'quota' if is_quota else 'error'} → next",
                    msg[:50])
                self.skills.update("chat", p["name"], False, time.time()-t0)
                continue

        return "All cloud providers unavailable."

    def code(self, task: str, context: str = "") -> str:
        """Ask cloud to write complete Python code."""
        system = (
            "You are a Python expert. Output ONLY complete working Python code. "
            "No explanation. No markdown fences. No comments unless critical. "
            "Use pygame for games. The code must be immediately runnable."
        )
        prompt = (
            f"Write complete Python code for: {task}\n"
            f"{('Context: ' + context) if context else ''}"
        )
        for p in self._ranked_providers():
            t0 = time.time()
            try:
                log("CLOUD", f"Coding with {p['name']}...", task[:60])
                raw = self._call(p, system, prompt)
                # Strip markdown
                raw = re.sub(r'^```python\n?','', raw.strip())
                raw = re.sub(r'^```\n?',       '', raw.strip())
                raw = re.sub(r'\n?```$',       '', raw.strip())
                self.skills.update("code", p["name"], True, time.time()-t0)
                return raw.strip()
            except Exception as e:
                msg = str(e)
                log("FIX", f"{p['name']} error → next", msg[:50])
                self.skills.update("code", p["name"], False, time.time()-t0)
                continue

        return ""

    def fix(self, code: str, error: str) -> str:
        """Fix broken code — cloud only."""
        system = (
            "Fix the Python error. Return ONLY the complete fixed file. "
            "No explanation. No markdown."
        )
        prompt = (
            f"Error: {error[:400]}\n\n"
            f"Code:\n{chr(10).join(code.splitlines()[-80:])}"
        )
        result = self.ask(prompt, system)
        result = re.sub(r'^```python\n?','', result.strip())
        result = re.sub(r'^```\n?',       '', result.strip())
        result = re.sub(r'\n?```$',       '', result.strip())
        return result.strip()

    def classify(self, user_input: str) -> dict:
        """
        Ask cloud to classify an unknown input.
        Returns {goal_type, action, template, explanation}
        Called ONLY when pattern memory has no match.
        Result gets saved to pattern memory so it's never asked again.
        """
        system = (
            "You are classifying user requests for an autonomous agent on Linux. "
            "Reply ONLY with valid JSON, no explanation."
        )
        prompt = (
            f'Classify this user request: "{user_input}"\n\n'
            f'Reply with JSON only:\n'
            f'{{"goal_type":"shell|code|search|system|chat|notify",'
            f'"action":"shell|python|cloud_code|cloud_ask|search|schedule",'
            f'"template":"the exact linux command or null if code/search",'
            f'"explanation":"one sentence"}}'
        )
        raw = self.ask(prompt, system)
        try:
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if match:
                return json.loads(match.group())
        except:
            pass
        return {"goal_type":"chat","action":"cloud_ask",
                "template": None,"explanation":"fallback to chat"}

    def available(self) -> bool:
        return len(self._providers) > 0


# ═══════════════════════════════════════════════
#  TOOL EXECUTOR
#  Runs tools, reports outcomes to SkillMap
# ═══════════════════════════════════════════════
class ToolExecutor:
    """
    Stateless tool runner. Nucleus decides which tool.
    This just runs it and reports the outcome.

    Every run → SkillMap.update() called automatically.
    That's how the nucleus learns which tools work.
    """

    def __init__(self, cloud: CloudRouter, skill_map):
        self.cloud  = cloud
        self.skills = skill_map
        self._search_cache: dict = {}
        self._load_app_cache()

    def run(self, tool: str, goal_type: str, **kwargs) -> dict:
        """
        Run a tool. Returns {ok, output, tool_used, elapsed}.
        Never raises — errors are captured and returned.
        """
        t0 = time.time()
        try:
            output    = self._dispatch(tool, **kwargs)
            out_str   = str(output)
            elapsed   = time.time() - t0
            # Treat "not found" as failure even if no exception was raised
            bad_words = ["not found","no such file","command not found"]
            if any(w in out_str.lower() for w in bad_words):
                raise RuntimeError(out_str)
            self.skills.update(goal_type, tool, True, elapsed)
            log("OK", f"{tool} succeeded", out_str[:80].replace("\n"," "))
            return {"ok": True, "output": out_str, "tool_used": tool,
                    "elapsed": elapsed}
        except Exception as e:
            elapsed = time.time() - t0
            self.skills.update(goal_type, tool, False, elapsed)
            log("FAIL", f"{tool} failed", str(e)[:80])
            return {"ok": False, "output": str(e), "tool_used": tool,
                    "elapsed": elapsed}

    def _dispatch(self, tool: str, **kw) -> str:
        if tool == "shell":        return self._shell(**kw)
        if tool == "python":       return self._python(**kw)
        if tool == "cloud_code":   return self._cloud_code(**kw)
        if tool == "cloud_ask":    return self._cloud_ask(**kw)
        if tool == "search":       return self._search(**kw)
        if tool == "file_read":    return self._file_read(**kw)
        if tool == "file_write":   return self._file_write(**kw)
        if tool == "notify":       return self._notify(**kw)
        if tool == "system_check": return self._system_check(**kw)
        if tool == "schedule":     return self._schedule(**kw)
        raise ValueError(f"Unknown tool: {tool}")

    # ── App name resolver ─────────────────────
    APP_ALIASES = {
        "chrome":    ["google-chrome","google-chrome-stable","chromium-browser","chromium"],
        "firefox":   ["firefox","firefox-esr"],
        "terminal":  ["gnome-terminal","xterm","konsole","xfce4-terminal","lxterminal"],
        "files":     ["nautilus","thunar","nemo","dolphin","pcmanfm"],
        "calculator":["gnome-calculator","kcalc","xcalc","galculator"],
        "editor":    ["gedit","kate","mousepad","leafpad","xed","nano"],
        "vscode":    ["code","code-oss","codium"],
        "spotify":   ["spotify","flatpak run com.spotify.Client"],
        "discord":   ["discord","flatpak run com.discordapp.Discord"],
        "vlc":       ["vlc"],
        "slack":     ["slack","flatpak run com.slack.Slack"],
        "winbox":    ["/home/gymnott/Desktop/WinBox_Linux/WinBox","winbox","WinBox"],
        "mikrotik":  ["/home/gymnott/Desktop/WinBox_Linux/WinBox"],
    }

    # Learned app locations — persists across restarts
    _app_cache: dict = {}

    def _resolve_app(self, name: str) -> str:
        """
        4-strategy resolver — never fails on the same app twice:
        1. Cache  (instant, learned from previous runs)
        2. Aliases (known alternatives like google-chrome for chrome)
        3. PATH   (which command)
        4. Filesystem search (Desktop, Downloads, /opt, etc.)
        """
        import glob, os as _os
        name_lower = name.lower().strip()

        # 1. Cache
        if name_lower in self._app_cache:
            return self._app_cache[name_lower]

        # 2. Aliases + 3. PATH
        candidates = self.APP_ALIASES.get(name_lower, []) + [name_lower, name]
        for cmd in candidates:
            r = subprocess.run(f"which {cmd.split()[0]}",
                               shell=True, capture_output=True)
            if r.returncode == 0:
                self._learn_app(name_lower, cmd)
                return cmd

        # 4. Filesystem search — case-insensitive, deep search
        # Build search dirs including all /home/*/Desktop etc.
        home = _os.path.expanduser("~")
        search_dirs = [
            _os.path.join(home, "Desktop"),
            _os.path.join(home, "Downloads"),
            _os.path.join(home, ".local", "bin"),
            _os.path.join(home, "Applications"),
            "/opt", "/usr/local/bin", "/usr/games",
        ]
        # Add all users' home dirs
        try:
            for entry in _os.scandir("/home"):
                if entry.is_dir():
                    for sub in ["Desktop","Downloads","bin","Applications"]:
                        search_dirs.append(_os.path.join(entry.path, sub))
        except:
            pass

        for d in search_dirs:
            if not _os.path.exists(d):
                continue
            # Walk directory tree manually for case-insensitive match
            for root, dirs, files in _os.walk(d):
                for fname in files:
                    if name_lower in fname.lower():
                        full = _os.path.join(root, fname)
                        if _os.access(full, _os.X_OK):
                            log("LEARN", f"Found {name} → {full}")
                            self._learn_app(name_lower, full)
                            return full

        # 5. Last resort — find across home dir and /home/* users
        home = _os.path.expanduser("~")
        search_roots = [home]
        # Also check /home/username dirs in case ~ resolves wrong
        try:
            for entry in _os.scandir("/home"):
                if entry.is_dir() and entry.path not in search_roots:
                    search_roots.append(entry.path)
        except:
            pass

        for root in search_roots:
            try:
                r = subprocess.run(
                    f'find "{root}" -iname "*{name_lower}*" -type f 2>/dev/null | head -5',
                    shell=True, capture_output=True, text=True, timeout=8
                )
                for hit in r.stdout.strip().splitlines():
                    hit = hit.strip()
                    if hit and _os.access(hit, _os.X_OK):
                        log("LEARN", f"Found {name} → {hit}")
                        self._learn_app(name_lower, hit)
                        return hit
            except:
                continue

        return name_lower   # let shell handle it

    def _learn_app(self, name: str, path: str):
        """Remember where an app lives — survives restarts."""
        self._app_cache[name] = path
        try:
            p = Path("memory/app_cache.json")
            cache = json.loads(p.read_text()) if p.exists() else {}
            cache[name] = path
            p.write_text(json.dumps(cache, indent=2))
        except:
            pass

    def _load_app_cache(self):
        """Load previously learned locations on startup."""
        try:
            cache = json.loads(Path("memory/app_cache.json").read_text())
            self._app_cache.update(cache)
        except:
            pass

    # ── Shell ─────────────────────────────────
    def _shell(self, cmd: str, timeout: int = 15) -> str:
        # Resolve open/launch commands to real app binaries
        import re as _re
        open_match = _re.match(r"^(open|launch|start)\s+(.+?)\s*&?$",
                               cmd.strip(), _re.I)
        if open_match:
            app_name = open_match.group(2).strip()
            real_cmd = self._resolve_app(app_name)
            cmd = f"{real_cmd} &"
        log("RUN", f"$ {cmd[:70]}")
        r = subprocess.run(
            cmd, shell=True, capture_output=True,
            text=True, timeout=timeout
        )
        out = (r.stdout + r.stderr).strip()
        # Not found — search filesystem and retry once
        if r.returncode != 0 and "not found" in out.lower():
            failed_bin = cmd.split()[0].rstrip("&").strip()
            found = self._resolve_app(failed_bin)
            if found != failed_bin:
                retry_cmd = cmd.replace(failed_bin, f'"{found}"', 1)
                log("FIX", f"Retrying with: {found}")
                r2 = subprocess.run(retry_cmd, shell=True,
                                    capture_output=True, text=True, timeout=timeout)
                out2 = (r2.stdout + r2.stderr).strip()
                if r2.returncode == 0 or r2.stdout:
                    return out2 or "done"
                out = out2 or out

        if r.returncode != 0 and not r.stdout:
            raise RuntimeError(out or f"exit code {r.returncode}")
        return out or "done"

    # ── Python exec ───────────────────────────
    def _python(self, code: str) -> str:
        log("RUN", f"python: {code[:60]}")
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            exec(compile(code, "<nucleus>", "exec"), {})
        return buf.getvalue().strip() or "ok"

    # ── Cloud code ────────────────────────────
    def _cloud_code(self, task: str, context: str = "") -> str:
        code = self.cloud.code(task, context)
        if not code:
            raise RuntimeError("Cloud returned empty code")

        # Save it
        name = re.sub(r'[^a-z0-9]+','_', task.lower())[:30]
        path = Path("output") / f"{name}.py"
        path.write_text(code)
        log("OK", f"Code written → {path.name}",
            f"{len(code.splitlines())} lines")

        # Syntax check
        try:
            ast.parse(code)
        except SyntaxError as e:
            log("FIX", f"Syntax error → cloud fixing...", str(e)[:60])
            fixed = self.cloud.fix(code, str(e))
            if fixed:
                path.write_text(fixed)
                code = fixed

        # Run it (with display suppressed for test)
        env = os.environ.copy()
        env.update({"SDL_VIDEODRIVER":"dummy","SDL_AUDIODRIVER":"dummy",
                    "DISPLAY": os.environ.get("DISPLAY",":0")})

        # Actually launch games properly — restore display
        is_game = any(w in task.lower()
                      for w in ["game","snake","tetris","pong","breakout"])
        if is_game:
            log("RUN", f"Launching {path.name}...")
            subprocess.Popen([sys.executable, str(path)])
            return f"Game launched: {path.name}"

        # For scripts — run and capture output
        r = subprocess.run(
            [sys.executable, str(path)],
            capture_output=True, text=True,
            timeout=20, env=env
        )
        out = (r.stdout + r.stderr).strip()
        if r.returncode != 0 and r.stderr and not r.stdout:
            # Try one fix
            log("FIX", "Runtime error → cloud fixing...")
            fixed = self.cloud.fix(code, r.stderr[:300])
            if fixed:
                path.write_text(fixed)
                r2 = subprocess.run(
                    [sys.executable, str(path)],
                    capture_output=True, text=True, timeout=20, env=env
                )
                out = (r2.stdout + r2.stderr).strip()
        return out or f"Script ran: {path.name}"

    # ── Cloud ask ─────────────────────────────
    def _cloud_ask(self, prompt: str) -> str:
        return self.cloud.ask(prompt)

    # ── Search ────────────────────────────────
    def _search(self, query: str) -> str:
        # Cache check
        key = query.lower().strip()
        if key in self._search_cache:
            log("TOOL", f"Search cache hit: {key[:40]}")
            return self._search_cache[key]

        # DuckDuckGo instant answer
        try:
            import urllib.request
            q   = query.replace(" ","+")
            url = f"https://api.duckduckgo.com/?q={q}&format=json&no_html=1&skip_disambig=1"
            with urllib.request.urlopen(url, timeout=8) as resp:
                d = json.loads(resp.read())
            result = (d.get("AbstractText") or d.get("Answer") or
                      d.get("Definition") or "")
            if result:
                self._search_cache[key] = result[:500]
                return result[:500]
        except:
            pass

        # Wikipedia fallback
        try:
            import urllib.request, urllib.parse
            q   = urllib.parse.quote(query)
            url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{q}"
            with urllib.request.urlopen(url, timeout=8) as resp:
                d = json.loads(resp.read())
            result = d.get("extract","")[:500]
            if result:
                self._search_cache[key] = result
                return result
        except:
            pass

        # Cloud AI fallback — ask from knowledge
        result = self.cloud.ask(f"Answer briefly: {query}")
        self._search_cache[key] = result
        return result

    # ── File ops ──────────────────────────────
    def _file_read(self, path: str) -> str:
        return Path(path).read_text()[:2000]

    def _file_write(self, path: str, content: str) -> str:
        Path(path).write_text(content)
        return f"Written: {path}"

    # ── Notify ────────────────────────────────
    def _notify(self, message: str) -> str:
        token   = os.environ.get("TELEGRAM_BOT_TOKEN","")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID","")
        if token and chat_id:
            import urllib.request
            data = json.dumps({"chat_id":chat_id,"text":message}).encode()
            req  = urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=data, headers={"Content-Type":"application/json"})
            urllib.request.urlopen(req, timeout=10)
            return "Telegram sent"
        # Fallback: log file
        p = Path("logs/notifications.log")
        p.parent.mkdir(exist_ok=True)
        with open(p,"a") as f:
            f.write(f"[{datetime.now().isoformat()}] {message}\n")
        return f"Logged: {message}"

    # ── Schedule ──────────────────────────────
    def _schedule(self, task: str, when: str = None) -> str:
        p = Path("memory/scheduled.json")
        try:
            jobs = json.loads(p.read_text())
        except:
            jobs = []
        jobs.append({"task":task,"when":when,
                     "created":datetime.now().isoformat()})
        p.write_text(json.dumps(jobs,indent=2))
        return f"Scheduled: {task}" + (f" at {when}" if when else "")

    # ── System check ──────────────────────────
    def _system_check(self) -> str:
        import psutil, shutil
        results = []

        # RAM
        m = psutil.virtual_memory()
        results.append(f"RAM: {m.used/1e9:.1f}/{m.total/1e9:.1f}GB ({m.percent}%)")

        # Disk
        d = shutil.disk_usage(".")
        results.append(f"Disk: {d.free/1e9:.1f}GB free of {d.total/1e9:.1f}GB")

        # CPU
        results.append(f"CPU: {psutil.cpu_percent(interval=0.5)}%")

        # Directories
        for dr in ["output","memory","logs"]:
            Path(dr).mkdir(exist_ok=True)

        log("SYS", "System check passed")
        return " | ".join(results)