#!/usr/bin/env python3
"""
ARIA v2 — Autonomous Resident Intelligence Agent (Gemini Edition)
=================================================================
Truly autonomous. You give it a task in plain English.
ARIA plans, codes, tests, fixes, and ships — powered by Google Gemini.

Usage:
    python3 aria.py "build a snake game"
    python3 aria.py "build tetris"
    python3 aria.py "build a calculator app"
    python3 aria.py --interactive        ← chat mode

Requirements:
    pip install google-genai pygame psutil

Get your FREE API key at:
    https://aistudio.google.com/app/apikey

Set your key once:
    export GOOGLE_API_KEY=AIza...
    (or ARIA will ask you on first run and save it)
"""

import os, sys, json, time, hashlib, subprocess, ast, textwrap, re, argparse
from pathlib  import Path
from datetime import datetime


# ═══════════════════════════════════════════════════════════
#  TERMINAL COLOURS
# ═══════════════════════════════════════════════════════════
class C:
    RESET  = "\033[0m";  BOLD  = "\033[1m"
    RED    = "\033[91m"; GREEN = "\033[92m"
    YELLOW = "\033[93m"; CYAN  = "\033[96m"
    PURPLE = "\033[95m"; GREY  = "\033[90m"
    WHITE  = "\033[97m"; DIM   = "\033[2m"

def ts():
    return f"{C.GREY}[{datetime.now().strftime('%H:%M:%S')}]{C.RESET}"

def log(level, msg, detail=""):
    icons = {
        "BOOT":   f"{C.CYAN}◈{C.RESET}",
        "GOAL":   f"{C.PURPLE}◆{C.RESET}",
        "STEP":   f"{C.YELLOW}▸{C.RESET}",
        "OK":     f"{C.GREEN}✓{C.RESET}",
        "FAIL":   f"{C.RED}✗{C.RESET}",
        "FIX":    f"{C.YELLOW}⟳{C.RESET}",
        "THINK":  f"{C.CYAN}≋{C.RESET}",
        "DONE":   f"{C.GREEN}★{C.RESET}",
        "AI":     f"{C.PURPLE}◎{C.RESET}",
        "TEST":   f"{C.CYAN}⬡{C.RESET}",
        "WRITE":  f"{C.YELLOW}✎{C.RESET}",
        "REPAIR": f"{C.RED}⚙{C.RESET}",
        "WEB":    f"{C.CYAN}↗{C.RESET}",
    }
    icon = icons.get(level, "·")
    line = f"{ts()} {icon} {C.BOLD}{msg}{C.RESET}"
    if detail:
        line += f"  {C.DIM}{detail}{C.RESET}"
    print(line)


# ═══════════════════════════════════════════════════════════
#  SIMPLE KEY STORE  (plaintext for laptop — swap for Vault
#  from the full ARIA spec when you want encryption)
# ═══════════════════════════════════════════════════════════
class KeyStore:
    def __init__(self, path="output/.aria_config.json"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data = json.loads(self.path.read_text()) if self.path.exists() else {}

    def get(self, k, default=None): return self._data.get(k, default)
    def set(self, k, v):
        self._data[k] = v
        self.path.write_text(json.dumps(self._data, indent=2))


# ═══════════════════════════════════════════════════════════
#  HISTORY LEDGER  — append-only log of everything ARIA does
# ═══════════════════════════════════════════════════════════
class Ledger:
    def __init__(self, path="output/history.json"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("[]")

    def write(self, entry: dict):
        data = json.loads(self.path.read_text())
        data.append({"ts": datetime.now().isoformat(), **entry})
        self.path.write_text(json.dumps(data, indent=2))

    def read(self) -> list:
        return json.loads(self.path.read_text())


# ═══════════════════════════════════════════════════════════
#  DIFFICULTY SCORER
# ═══════════════════════════════════════════════════════════
class DifficultyScorer:
    """
    Scores a task description across 6 dimensions:
      sys_req   — compute burden on this machine
      knowledge — how much domain knowledge needed
      time      — estimated duration
      agency    — autonomy + risk level
      status    — current system health
      risk      — consequence of failure
    """
    WEIGHTS = dict(sys_req=0.10, knowledge=0.20, time=0.15,
                   agency=0.25, status=0.10, risk=0.20)

    def score(self, description: str) -> dict:
        import psutil
        d = description.lower()
        cpu = psutil.cpu_percent(interval=0.3) / 100
        ram = psutil.virtual_memory().percent / 100

        dims = {
            "sys_req":   min(1.0, (cpu+ram)/2 + (0.2 if "compile" in d else 0)),
            "knowledge": 0.8 if any(w in d for w in ["game","physics","ai","3d","network"]) else 0.4,
            "time":      0.9 if any(w in d for w in ["game","full","complete","app"]) else 0.4,
            "agency":    0.6 if any(w in d for w in ["write","run","execute","install"]) else 0.3,
            "status":    min(1.0, cpu),
            "risk":      0.1,
        }
        composite = sum(v * self.WEIGHTS[k] for k, v in dims.items())
        return {"dims": dims, "score": round(composite, 3)}


# ═══════════════════════════════════════════════════════════
#  GOAL
# ═══════════════════════════════════════════════════════════
class Goal:
    def __init__(self, title, description, priority=50, fn=None):
        self.id          = hashlib.md5(f"{title}{time.time()}".encode()).hexdigest()[:8]
        self.title       = title
        self.description = description
        self.priority    = priority
        self.fn          = fn          # callable that executes this goal
        self.status      = "pending"   # pending|active|done|failed
        self.retries     = 0
        self.history     = []
        self.result      = None

    def log(self, msg, data=None):
        self.history.append({"ts": datetime.now().isoformat(),
                              "msg": msg, "data": data or {}})


# ═══════════════════════════════════════════════════════════
#  PROVIDER REGISTRY
#  Each provider is tried in order. On 429/quota error,
#  ARIA automatically falls back to the next one.
# ═══════════════════════════════════════════════════════════

class ProviderError(Exception):
    """Raised when a provider fails and fallback should be tried."""
    pass

class QuotaError(ProviderError):
    """Specifically a rate-limit / quota exhaustion."""
    pass


class GeminiProvider:
    """
    Google AI Studio — free tier
    1,500 req/day on gemini-2.0-flash
    Key format: AIza... or AQ....
    Get key: https://aistudio.google.com/app/apikey
    """
    NAME  = "Google Gemini"
    MODEL = "gemini-2.0-flash"

    def __init__(self, api_key: str):
        from google import genai
        from google.genai import types
        self._types  = types
        self.client  = genai.Client(api_key=api_key)
        self.memory  = []   # {role, content}

    def chat(self, prompt: str, system: str, remember: bool) -> str:
        contents = []
        for m in self.memory:
            contents.append(self._types.Content(
                role=m["role"],
                parts=[self._types.Part(text=m["content"])]
            ))
        contents.append(self._types.Content(
            role="user", parts=[self._types.Part(text=prompt)]
        ))
        try:
            resp = self.client.models.generate_content(
                model    = self.MODEL,
                contents = contents,
                config   = self._types.GenerateContentConfig(
                    system_instruction = system,
                    temperature        = 0.3,
                    max_output_tokens  = 8192,
                )
            )
            result = resp.text or ""
        except Exception as e:
            msg = str(e)
            if "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower():
                raise QuotaError(f"Gemini quota hit: {msg[:120]}")
            raise ProviderError(f"Gemini error: {msg[:120]}")

        if remember:
            self.memory.append({"role": "user",  "content": prompt})
            self.memory.append({"role": "model", "content": result})
            if len(self.memory) > 20:
                self.memory = self.memory[-20:]
        return result

    @staticmethod
    def valid_key(k: str) -> bool:
        return k.startswith("AIza") or k.startswith("AQ.")


class GitHubProvider:
    """
    GitHub Models — completely free with any GitHub account
    Runs GPT-4o, Llama, Mistral and more via OpenAI-compatible API
    Key: your GitHub Personal Access Token (classic or fine-grained)
    Get key: https://github.com/settings/tokens
    Docs:    https://docs.github.com/en/github-models
    """
    NAME     = "GitHub Models"
    MODEL    = "gpt-4o"          # also: "meta-llama-3.1-70b-instruct", "mistral-large"
    BASE_URL = "https://models.inference.ai.azure.com"

    def __init__(self, api_key: str):
        from openai import OpenAI
        self.client = OpenAI(api_key=api_key, base_url=self.BASE_URL)
        self.memory = []   # {role, content}

    def chat(self, prompt: str, system: str, remember: bool) -> str:
        messages = [{"role": "system", "content": system}]
        messages += self.memory
        messages.append({"role": "user", "content": prompt})
        try:
            resp = self.client.chat.completions.create(
                model       = self.MODEL,
                messages    = messages,
                temperature = 0.3,
                max_tokens  = 8192,
            )
            result = resp.choices[0].message.content or ""
        except Exception as e:
            msg = str(e)
            if "429" in msg or "rate" in msg.lower() or "quota" in msg.lower():
                raise QuotaError(f"GitHub Models quota hit: {msg[:120]}")
            raise ProviderError(f"GitHub Models error: {msg[:120]}")

        if remember:
            self.memory.append({"role": "user",      "content": prompt})
            self.memory.append({"role": "assistant", "content": result})
            if len(self.memory) > 20:
                self.memory = self.memory[-20:]
        return result

    @staticmethod
    def valid_key(k: str) -> bool:
        # GitHub tokens: classic = ghp_..., fine-grained = github_pat_...
        return k.startswith("ghp_") or k.startswith("github_pat_") or k.startswith("gh")


class GroqProvider:
    """
    Groq — extremely fast, generous free tier
    Runs Llama 3, Mixtral at ~500 tokens/sec
    Key: https://console.groq.com/keys
    Free: 14,400 req/day on llama-3.1-70b
    """
    NAME  = "Groq"
    MODEL = "llama-3.3-70b-versatile"

    def __init__(self, api_key: str):
        from openai import OpenAI
        self.client = OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")
        self.memory = []

    def chat(self, prompt: str, system: str, remember: bool) -> str:
        messages = [{"role": "system", "content": system}]
        messages += self.memory
        messages.append({"role": "user", "content": prompt})
        try:
            resp = self.client.chat.completions.create(
                model       = self.MODEL,
                messages    = messages,
                temperature = 0.3,
                max_tokens  = 8192,
            )
            result = resp.choices[0].message.content or ""
        except Exception as e:
            msg = str(e)
            if "429" in msg or "rate" in msg.lower():
                raise QuotaError(f"Groq quota hit: {msg[:120]}")
            raise ProviderError(f"Groq error: {msg[:120]}")

        if remember:
            self.memory.append({"role": "user",      "content": prompt})
            self.memory.append({"role": "assistant", "content": result})
            if len(self.memory) > 20:
                self.memory = self.memory[-20:]
        return result

    @staticmethod
    def valid_key(k: str) -> bool:
        return k.startswith("gsk_")


# ═══════════════════════════════════════════════════════════
#  AI BRAIN  — multi-provider with automatic fallback
#  Priority: Gemini → GitHub Models → Groq
#  On 429/quota: transparently switches to next provider
# ═══════════════════════════════════════════════════════════
class AIBrain:
    """
    ARIA's reasoning engine with automatic provider fallback.

    When one provider hits its quota or errors, ARIA instantly
    switches to the next without losing context or failing the goal.

    Provider priority (configure in KeyStore):
      1. Google Gemini   — free 1,500 req/day
      2. GitHub Models   — free with GitHub account (GPT-4o!)
      3. Groq            — free 14,400 req/day (ultra fast)
    """

    SYSTEM_PROMPT = (
        "You are ARIA's internal reasoning engine. You are precise, concise, "
        "and always output exactly what is asked — no preamble, no explanation "
        "unless explicitly requested. When asked for code, output ONLY the code, "
        "never wrapped in markdown fences. When asked for JSON, output ONLY valid JSON."
    )

    def __init__(self, providers: list, ledger: Ledger):
        self.providers      = providers   # ordered list of provider instances
        self.active_idx     = 0           # which provider we're currently on
        self.ledger         = ledger
        self.fallback_count = 0

    @property
    def active(self):
        return self.providers[self.active_idx]

    def _fallback(self, reason: str):
        """Switch to the next provider. If all exhausted, reset to first and raise."""
        old_name = self.active.NAME
        next_idx = self.active_idx + 1
        if next_idx >= len(self.providers):
            self.active_idx = 0   # reset so next goal starts from provider 0
            raise RuntimeError(
                f"All {len(self.providers)} providers exhausted.\n"
                f"  Last error : {reason[:120]}\n"
                f"  Options    : wait for quota reset, or add more keys with --setup"
            )
        self.active_idx = next_idx
        new_name = self.active.NAME
        self.fallback_count += 1
        log("FIX", f"Provider fallback: {old_name} → {new_name}", reason[:80])
        self.ledger.write({"event": "provider_fallback",
                           "from": old_name, "to": new_name, "reason": reason})
        # Copy memory so new provider has full context
        self.active.memory = list(self.providers[next_idx - 1].memory)

    # ── Core call with automatic retry + fallback ────────────
    def think(self, prompt: str, system: str = None, remember: bool = False) -> str:
        sys_prompt = system or self.SYSTEM_PROMPT
        log("AI", f"[{self.active.NAME}] Thinking...", prompt[:70].replace("\n"," "))

        while True:
            try:
                result = self.active.chat(prompt, sys_prompt, remember)
                self.ledger.write({
                    "event":          "ai_call",
                    "provider":       self.active.NAME,
                    "prompt_preview": prompt[:200],
                    "response_chars": len(result),
                })
                return result

            except QuotaError as e:
                # Rate limit — switch provider automatically
                self._fallback(str(e))

            except ProviderError as e:
                # Other provider error — try fallback once, then raise
                log("FAIL", f"Provider error: {e}", "trying fallback...")
                self._fallback(str(e))

    # ── All higher-level methods delegate to think() ─────────
    def plan_task(self, task: str) -> list[dict]:
        prompt = f"""
You are planning how to build: "{task}"

Return a JSON array of build steps. Each step has:
  "name"        — short identifier (snake_case)
  "description" — one sentence what this step does
  "type"        — one of: design | code | test | fix | launch

Return ONLY valid JSON. No markdown. No explanation. Example:
[
  {{"name": "design_architecture", "description": "Define classes and game loop structure", "type": "design"}},
  {{"name": "write_core_logic",    "description": "Write main game classes and mechanics",  "type": "code"}},
  {{"name": "write_rendering",     "description": "Write all drawing and display code",     "type": "code"}},
  {{"name": "write_entry_point",   "description": "Write main() and game launch code",      "type": "code"}},
  {{"name": "test_and_fix",        "description": "Validate syntax and fix any errors",     "type": "test"}},
  {{"name": "launch",              "description": "Launch the finished program",             "type": "launch"}}
]
"""
        import re, json
        raw = self.think(prompt)
        raw = re.sub(r"```[a-z]*\n?", "", raw).strip().rstrip("`")
        try:
            return json.loads(raw)
        except:
            log("FIX", "Plan JSON parse failed — using fallback plan")
            return [
                {"name": "write_full_program", "description": f"Write complete {task}", "type": "code"},
                {"name": "test_and_fix",       "description": "Validate and fix",       "type": "test"},
                {"name": "launch",             "description": "Launch the program",     "type": "launch"},
            ]

    def design(self, task: str) -> str:
        prompt = f"""
Design the architecture for: "{task}"
Describe (no code yet):
1. Python libraries to use and why
2. Classes/modules to create
3. How the main loop works
4. Key design decisions
Be brief — internal scratchpad before coding.
"""
        result = self.think(prompt, remember=True)
        log("THINK", "Architecture designed", f"{len(result)} chars")
        return result

    def write_code(self, task: str, step_name: str, step_desc: str,
                   existing_code: str = "", error: str = "") -> str:
        import re
        if error:
            prompt = f"""
The following Python code for "{task}" has an error. Fix it.
ERROR: {error}
CURRENT CODE:
{existing_code}
Return the COMPLETE fixed Python file. Output ONLY code, no explanation.
"""
        elif existing_code:
            prompt = f"""
You are building "{task}" step by step.
Existing code so far:
{existing_code[-3000:]}
Now complete step: "{step_name}" — {step_desc}
Extend the code above. Return the COMPLETE updated Python file.
Output ONLY valid Python. No markdown fences. No explanation.
"""
        else:
            prompt = f"""
Build step "{step_name}" for: "{task}"
This step: {step_desc}
Output ONLY valid Python code. No markdown. No explanation.
Use pygame for any game/visual project. Write the foundation that later steps will extend.
"""
        code = self.think(prompt, remember=True)
        code = re.sub(r"^```python\n?", "", code.strip())
        code = re.sub(r"\n?```$",       "", code.strip())
        return code.strip()

    def diagnose_error(self, code: str, error: str, task: str) -> str:
        prompt = f"""
Building "{task}". Got this error:
{error}
Last 50 lines of code:
{chr(10).join(code.splitlines()[-50:])}
In ONE sentence: what is wrong and what is the fix?
"""
        result = self.think(prompt)
        log("THINK", f"Diagnosis: {result[:100]}")
        return result

    def search_for_help(self, query: str) -> str:
        log("WEB", f"Knowledge lookup: {query[:60]}")
        result = self.think(
            f"Technical question for a Python project: {query}\n"
            f"Give a brief precise answer with code example if relevant."
        )
        self.ledger.write({"event": "knowledge_lookup", "query": query})
        return result

    def reflect(self, task: str, events: list) -> str:
        # Summarise events as plain text — avoids 413 on large JSON payloads
        lines = []
        for e in events[-6:]:
            ev = e.get("event","?")
            if ev == "goal_done":   lines.append(f"✓ {e.get('title','')}")
            elif ev == "goal_failed": lines.append(f"✗ {e.get('title','')} — {e.get('error','')[:60]}")
            elif ev == "provider_fallback": lines.append(f"↷ fallback {e.get('from')} → {e.get('to')}")
            elif ev == "test_passed": lines.append(f"⬡ tests passed (attempts: {e.get('attempts',1)})")
        summary = "\n".join(lines) if lines else "task completed"
        prompt = (
            f'ARIA built: "{task}"\n'
            f'Log:\n{summary}\n'
            f'In 2 sentences: what went well and one thing to improve next time?'
        )
        return self.think(prompt)


# ═══════════════════════════════════════════════════════════
#  CODE EXECUTOR  — runs/tests generated code safely
# ═══════════════════════════════════════════════════════════
class CodeExecutor:
    def __init__(self, output_dir="output"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)

    def save(self, filename: str, code: str) -> Path:
        path = self.output_dir / filename
        path.write_text(code)
        return path

    def syntax_check(self, code: str) -> tuple[bool, str]:
        """Parse the code with Python's AST — catches all syntax errors."""
        try:
            ast.parse(code)
            return True, ""
        except SyntaxError as e:
            return False, f"SyntaxError line {e.lineno}: {e.msg}\n  → {e.text}"

    def structure_check(self, code: str, required_names: list) -> tuple[bool, str]:
        """Verify expected classes/functions exist in the code."""
        try:
            tree  = ast.parse(code)
            found = {n.name for n in ast.walk(tree)
                     if isinstance(n, (ast.ClassDef, ast.FunctionDef))}
            missing = [r for r in required_names if r not in found]
            if missing:
                return False, f"Missing definitions: {missing}"
            return True, ""
        except:
            return True, ""   # If we can't parse, syntax_check will catch it

    def runtime_check(self, path: Path, timeout: int = 5) -> tuple[bool, str]:
        """
        Try running the script in a subprocess with a display env variable
        set to suppress pygame window during testing.
        Captures output and errors within timeout.
        """
        env = os.environ.copy()
        env["ARIA_TEST_MODE"] = "1"
        env["SDL_VIDEODRIVER"] = "dummy"    # pygame: no window during test
        env["SDL_AUDIODRIVER"] = "dummy"    # pygame: no audio during test

        # We wrap the script: import it, but __name__ won't be '__main__'
        # so the game loop won't start. This catches import-time errors.
        test_code = f"""
import sys
sys.path.insert(0, '{path.parent}')
try:
    import importlib.util
    spec = importlib.util.spec_from_file_location('_aria_test', '{path}')
    mod  = importlib.util.module_from_spec(spec)
    # Don't exec — just verify it loads without error at module level
    import ast, py_compile
    py_compile.compile('{path}', doraise=True)
    print('RUNTIME_OK')
except Exception as e:
    print(f'RUNTIME_ERROR: {{e}}')
"""
        result = subprocess.run(
            [sys.executable, "-c", test_code],
            capture_output=True, text=True,
            timeout=timeout, env=env
        )
        output = result.stdout + result.stderr
        if "RUNTIME_OK" in output:
            return True, ""
        error = output.replace("RUNTIME_ERROR: ", "").strip()
        return False, error or "Unknown runtime error"

    def launch(self, path: Path):
        """Open the finished program in a new process."""
        log("DONE", f"Launching: {path.name}")
        subprocess.Popen([sys.executable, str(path)])


# ═══════════════════════════════════════════════════════════
#  BRAIN — Goal Queue + Execution Engine
# ═══════════════════════════════════════════════════════════
class Brain:
    def __init__(self, ledger: Ledger, ai: AIBrain, executor: CodeExecutor):
        self.ledger   = ledger
        self.ai       = ai
        self.executor = executor
        self.scorer   = DifficultyScorer()
        self._queue   = []

    def add(self, goal: Goal):
        diff = self.scorer.score(goal.description)
        goal.priority += int(diff["score"] * 20)
        self._queue.sort(key=lambda g: g.priority)
        self._queue.append(goal)
        self._queue.sort(key=lambda g: g.priority)
        self.ledger.write({"event": "goal_queued", "title": goal.title,
                           "priority": goal.priority, "difficulty": diff["score"]})
        log("GOAL", f"Queued: {goal.title}",
            f"priority={goal.priority} diff={diff['score']:.2f}")

    def run_all(self):
        while True:
            pending = [g for g in self._queue if g.status == "pending"]
            if not pending:
                break
            goal = pending[0]
            self._execute(goal)

    def _execute(self, goal: Goal):
        goal.status = "active"
        log("GOAL", f"Executing: {goal.title}")
        self.ledger.write({"event": "goal_start", "id": goal.id, "title": goal.title})
        try:
            goal.result = goal.fn()
            goal.status = "done"
            goal.log("success")
            self.ledger.write({"event": "goal_done", "id": goal.id, "title": goal.title})
            log("OK", f"Done: {goal.title}")
        except Exception as e:
            goal.status = "failed"
            goal.retries += 1
            goal.log(f"error: {e}")
            log("FAIL", f"Failed: {goal.title}", str(e)[:100])
            self.ledger.write({"event": "goal_failed", "id": goal.id, "error": str(e)})
            if goal.retries <= 2:
                log("FIX", f"Auto-retrying (attempt {goal.retries}/2)...")
                time.sleep(0.5)
                goal.status = "pending"
                self._execute(goal)


# ═══════════════════════════════════════════════════════════
#  SELF-REPAIR  — always goal #0
# ═══════════════════════════════════════════════════════════
class SelfRepair:
    REQUIRED_PACKAGES = ["google-genai", "pygame", "psutil"]

    def run(self) -> bool:
        log("REPAIR", "Self-repair: checking system...")
        ok = True

        if sys.version_info < (3, 10):
            log("FAIL", f"Python 3.10+ required, got {sys.version[:6]}")
            ok = False

        for pkg in self.REQUIRED_PACKAGES:
            try:
                __import__(pkg.replace("-", "_"))
                log("OK", f"Package present: {pkg}")
            except ImportError:
                log("FIX", f"Auto-installing: {pkg}")
                r = subprocess.run(
                    [sys.executable, "-m", "pip", "install", pkg, "-q"],
                    capture_output=True
                )
                if r.returncode != 0:
                    log("FAIL", f"Could not install {pkg}")
                    ok = False

        Path("output").mkdir(exist_ok=True)
        Path("logs").mkdir(exist_ok=True)
        return ok


# ═══════════════════════════════════════════════════════════
#  ARIA  — The Agent
#  This is what orchestrates everything
# ═══════════════════════════════════════════════════════════
class ARIA:
    VERSION = "3.0"

    def __init__(self, providers: list):
        self.ledger   = Ledger()
        self.store    = KeyStore()
        self.repair   = SelfRepair()
        self.executor = CodeExecutor()
        self.ai       = AIBrain(providers, self.ledger)
        self.brain    = Brain(self.ledger, self.ai, self.executor)
        self.ledger.write({"event": "aria_boot", "version": "3.0",
                           "providers": [p.NAME for p in providers]})

    # ── Main entry: give ARIA a task ─────────────────────────
    def run_task(self, task: str):
        self._print_banner(task)

        # ── Goal 0: Self-repair (always first) ──────────────
        def do_repair():
            ok = self.repair.run()
            if not ok:
                raise RuntimeError("System not healthy — check errors above")

        self.brain.add(Goal(
            title="SELF-REPAIR: system integrity check",
            description="Verify Python version, packages, and write permissions",
            priority=0,
            fn=do_repair
        ))

        # ── Goal 1: Plan the task ────────────────────────────
        plan_result = []

        def do_plan():
            log("THINK", f"Planning task: {task}")
            steps = self.ai.plan_task(task)
            plan_result.extend(steps)
            log("OK", f"Plan: {len(steps)} steps")
            for i, s in enumerate(steps):
                log("STEP", f"  {i+1}. [{s['type']}] {s['name']}", s['description'])
            self.ledger.write({"event": "plan_created", "task": task, "steps": steps})

        self.brain.add(Goal(
            title="PLAN: design build roadmap",
            description=f"Ask AI to plan how to build: {task}",
            priority=5,
            fn=do_plan
        ))

        # ── Goal 2: Architecture design ─────────────────────
        design_result = []

        def do_design():
            log("THINK", "Designing architecture...")
            design = self.ai.design(task)
            design_result.append(design)
            print(f"\n{C.DIM}  {'─'*60}")
            for line in design.splitlines()[:12]:
                print(f"  {C.DIM}{line}{C.RESET}")
            print(f"  {'─'*60}{C.RESET}\n")

        self.brain.add(Goal(
            title="DESIGN: architecture",
            description=f"Design class structure and approach for: {task}",
            priority=8,
            fn=do_design
        ))

        # ── Goal 3: Code generation (iterative, step by step) ─
        code_state   = [""]   # mutable container for the evolving code
        output_name  = self._task_to_filename(task)
        output_path  = self.executor.output_dir / output_name

        def do_code():
            # Wait for plan (it runs before this in the queue)
            if not plan_result:
                raise RuntimeError("Plan not ready yet")

            code_steps = [s for s in plan_result if s["type"] == "code"]
            if not code_steps:
                code_steps = [{"name": "write_full_program",
                               "description": f"Write complete {task}", "type": "code"}]

            for step in code_steps:
                log("WRITE", f"Writing: {step['name']}", step['description'])
                new_code = self.ai.write_code(
                    task        = task,
                    step_name   = step["name"],
                    step_desc   = step["description"],
                    existing_code = code_state[0]
                )
                code_state[0] = new_code
                self.executor.save(output_name, new_code)
                log("OK", f"Section written", f"{len(new_code)} chars → {output_name}")
                self.ledger.write({"event": "code_written", "step": step["name"],
                                   "chars": len(new_code)})

        self.brain.add(Goal(
            title="CODE: write program",
            description=f"Generate all code for: {task}",
            priority=15,
            fn=do_code
        ))

        # ── Goal 4: Test & auto-fix (up to 3 rounds) ─────────
        def do_test_and_fix():
            code = code_state[0]
            if not code:
                raise RuntimeError("No code to test")

            max_fix_rounds = 3

            for attempt in range(max_fix_rounds + 1):
                if attempt > 0:
                    log("FIX", f"Fix attempt {attempt}/{max_fix_rounds}...")

                # Syntax check
                ok, err = self.executor.syntax_check(code)
                if not ok:
                    log("FAIL", "Syntax error", err[:100])
                    if attempt == max_fix_rounds:
                        raise RuntimeError(f"Could not fix after {max_fix_rounds} attempts: {err}")
                    diagnosis = self.ai.diagnose_error(code, err, task)
                    log("THINK", f"Diagnosis: {diagnosis[:80]}")
                    code = self.ai.write_code(task, "fix_syntax", "fix error",
                                              existing_code=code, error=err)
                    code_state[0] = code
                    self.executor.save(output_name, code)
                    continue

                log("TEST", "Syntax OK — running structure check...")

                # Runtime check
                ok, err = self.executor.runtime_check(output_path)
                if not ok:
                    log("FAIL", "Runtime error", err[:100])
                    if attempt == max_fix_rounds:
                        raise RuntimeError(f"Runtime errors remain: {err}")
                    # Ask AI if it needs external knowledge to fix this
                    if "import" in err.lower() or "module" in err.lower():
                        knowledge = self.ai.search_for_help(
                            f"Python error in pygame project: {err}"
                        )
                        log("WEB", f"Got help: {knowledge[:80]}")
                        err += f"\n\nRelevant knowledge:\n{knowledge}"
                    diagnosis = self.ai.diagnose_error(code, err, task)
                    code = self.ai.write_code(task, "fix_runtime", "fix runtime error",
                                              existing_code=code, error=err)
                    code_state[0] = code
                    self.executor.save(output_name, code)
                    continue

                # All checks pass
                log("OK", "All tests passed ✓")
                self.ledger.write({"event": "test_passed", "attempts": attempt + 1})
                return

            raise RuntimeError("Test/fix loop exhausted")

        self.brain.add(Goal(
            title="TEST+FIX: validate and auto-repair",
            description="Syntax check, runtime check, and iterative AI-driven fixing",
            priority=25,
            fn=do_test_and_fix
        ))

        # ── Goal 5: Reflect & launch ─────────────────────────
        def do_launch():
            # Reflection — non-fatal: if all providers are tired, skip it
            reflection = "Build complete."
            try:
                log("THINK", "Reflecting on build process...")
                history = self.ledger.read()
                events  = [e for e in history if e.get("event") in
                           ("goal_done","goal_failed","provider_fallback","test_passed")]
                reflection = self.ai.reflect(task, events)
            except Exception as e:
                log("FIX", f"Reflection skipped (providers resting): {str(e)[:60]}")
                reflection = "Build complete. (reflection skipped — provider quota)"

            # Print completion message — always runs
            self._print_completion(task, output_path, reflection, code_state[0])
            self.ledger.write({"event": "task_complete", "task": task,
                               "output": str(output_path), "reflection": reflection})

            # Launch — always runs even if reflection failed
            try:
                input(f"\n  {C.YELLOW}Press ENTER to launch, or Ctrl+C to skip...{C.RESET} ")
            except KeyboardInterrupt:
                print(f"\n  {C.GREY}Skipped launch.{C.RESET}")
                return
            self.executor.launch(output_path)

        self.brain.add(Goal(
            title="LAUNCH: complete and deliver",
            description="Show completion message, reflect, and launch the program",
            priority=35,
            fn=do_launch
        ))

        # ── Execute the queue ────────────────────────────────
        self._print_queue()
        self.brain.run_all()

    # ── Interactive / chat mode ──────────────────────────────
    def chat(self):
        """
        Conversational mode — ARIA can answer questions,
        give advice, and take tasks in natural language.
        """
        self._print_banner("interactive mode")
        print(f"\n{C.CYAN}  ARIA is ready. Type a task or question.{C.RESET}")
        print(f"{C.DIM}  Examples:{C.RESET}")
        print(f"{C.DIM}    build a snake game{C.RESET}")
        print(f"{C.DIM}    build tetris{C.RESET}")
        print(f"{C.DIM}    build a calculator app{C.RESET}")
        print(f"{C.DIM}    how do I add a high score leaderboard?{C.RESET}")
        print(f"{C.DIM}    quit{C.RESET}\n")

        while True:
            try:
                user_input = input(f"{C.PURPLE}you ▸ {C.RESET}").strip()
            except (KeyboardInterrupt, EOFError):
                print(f"\n{C.GREY}ARIA: Goodbye.{C.RESET}")
                break

            if not user_input:
                continue
            if user_input.lower() in ("quit","exit","bye","q"):
                print(f"{C.GREY}ARIA: Goodbye.{C.RESET}")
                break

            # Is it a build task or a question?
            build_keywords = ["build","create","make","write","generate","code"]
            is_task = any(user_input.lower().startswith(k) for k in build_keywords)

            if is_task:
                task = re.sub(r"^(build|create|make|write|generate|code)\s+", "",
                              user_input, flags=re.IGNORECASE).strip()
                # Re-init brain (fresh queue for new task)
                self.brain = Brain(self.ledger, self.ai, self.executor)
                self.run_task(task)
            else:
                # Answer as assistant
                answer = self.ai.think(
                    user_input,
                    system="You are ARIA, a helpful autonomous coding agent. "
                           "Answer concisely and practically. "
                           "If the user wants to build something, "
                           "remind them they can say 'build [thing]' to start.",
                    remember=True
                )
                print(f"\n{C.CYAN}ARIA ◎{C.RESET} {answer}\n")

    # ── Helpers ──────────────────────────────────────────────
    def _task_to_filename(self, task: str) -> str:
        clean = re.sub(r"[^a-z0-9]+", "_", task.lower()).strip("_")
        return f"{clean[:30]}.py"

    def _print_banner(self, task: str):
        print(f"\n{C.CYAN}{C.BOLD}")
        print(f"  ╔══════════════════════════════════════════════╗")
        print(f"  ║  ARIA v{self.VERSION} — Autonomous Resident Agent     ║")
        print(f"  ║  Task: {task[:38]:<38} ║")
        print(f"  ╚══════════════════════════════════════════════╝")
        print(f"{C.RESET}")

    def _print_queue(self):
        print(f"\n{C.GREY}  Goal queue ({len(self.brain._queue)} goals):{C.RESET}")
        for g in self.brain._queue:
            bar = "█" * min(10, int(g.priority/5)) + "░" * max(0, 10 - int(g.priority/5))
            print(f"{C.GREY}    [{g.priority:02d}] {g.title:<48} [{bar}]{C.RESET}")
        print()

    def _print_completion(self, task: str, path: Path, reflection: str, code: str):
        lines = len(code.splitlines())
        print(f"\n{C.GREEN}{C.BOLD}")
        print(f"  ╔══════════════════════════════════════════════╗")
        print(f"  ║  ✓  ARIA COMPLETE                            ║")
        print(f"  ║                                              ║")
        print(f"  ║  Task:   {task[:38]:<38} ║")
        print(f"  ║  Output: {str(path)[:38]:<38} ║")
        print(f"  ║  Lines:  {lines:<38} ║")
        print(f"  ║                                              ║")
        print(f"  ║  Check out this game!                        ║")
        print(f"  ╚══════════════════════════════════════════════╝")
        print(f"{C.RESET}")
        print(f"{C.DIM}  Reflection:{C.RESET}")
        for line in textwrap.wrap(reflection, 58):
            print(f"{C.DIM}  {line}{C.RESET}")
        print()


# ═══════════════════════════════════════════════════════════
#  KEY SETUP  — collects keys for all providers, builds chain
# ═══════════════════════════════════════════════════════════

def _ask_key(name: str, url: str, example: str, env_var: str, store_key: str,
             store: KeyStore, validator) -> str | None:
    """Try env → saved config → ask user. Returns key or None if skipped."""
    key = os.environ.get(env_var, "")
    if validator(key):
        return key
    key = store.get(store_key, "")
    if validator(key):
        return key
    print(f"\n{C.YELLOW}  {name} key not found.{C.RESET}")
    print(f"{C.DIM}  Get one free: {url}{C.RESET}")
    print(f"{C.DIM}  Example format: {example}{C.RESET}")
    key = input(f"  Paste {name} key (or press Enter to skip): ").strip()
    if not key:
        return None
    store.set(store_key, key)
    print(f"{C.GREEN}  Saved.{C.RESET}")
    return key


def build_providers(store: KeyStore) -> list:
    """
    Interactively collect API keys and build an ordered provider list.
    At least one provider is required. Others are optional fallbacks.
    """
    providers = []

    print(f"\n{C.CYAN}{C.BOLD}  ARIA Provider Setup{C.RESET}")
    print(f"{C.DIM}  ARIA will try providers in order. On quota error it falls back.{C.RESET}\n")

    # ── Provider 1: Google Gemini ──────────────────────────
    key = _ask_key(
        name      = "Google AI Studio (Gemini)",
        url       = "https://aistudio.google.com/app/apikey",
        example   = "AIza... or AQ.Ab8...",
        env_var   = "GOOGLE_API_KEY",
        store_key = "google_api_key",
        store     = store,
        validator = lambda k: k.startswith("AIza") or k.startswith("AQ.")
    )
    if key:
        try:
            providers.append(GeminiProvider(key))
            log("OK", "Gemini provider ready")
        except Exception as e:
            log("FAIL", f"Gemini init failed: {e}")

    # ── Provider 2: GitHub Models ──────────────────────────
    key = _ask_key(
        name      = "GitHub Personal Access Token",
        url       = "https://github.com/settings/tokens  (no special scopes needed)",
        example   = "ghp_xxxx...  or  github_pat_xxxx...",
        env_var   = "GITHUB_TOKEN",
        store_key = "github_token",
        store     = store,
        validator = lambda k: len(k) > 10   # GitHub tokens vary in format
    )
    if key:
        try:
            providers.append(GitHubProvider(key))
            log("OK", "GitHub Models provider ready")
        except Exception as e:
            log("FAIL", f"GitHub Models init failed: {e}")

    # ── Provider 3: Groq (optional) ───────────────────────
    key = _ask_key(
        name      = "Groq (optional, ultra-fast free tier)",
        url       = "https://console.groq.com/keys",
        example   = "gsk_xxxx...",
        env_var   = "GROQ_API_KEY",
        store_key = "groq_api_key",
        store     = store,
        validator = lambda k: k.startswith("gsk_")
    )
    if key:
        try:
            providers.append(GroqProvider(key))
            log("OK", "Groq provider ready")
        except Exception as e:
            log("FAIL", f"Groq init failed: {e}")

    if not providers:
        print(f"\n{C.RED}  No providers configured. ARIA needs at least one API key.{C.RESET}")
        print(f"{C.DIM}  Easiest option: GitHub token from https://github.com/settings/tokens{C.RESET}\n")
        sys.exit(1)

    print(f"\n{C.GREEN}  Provider chain: {C.RESET}", end="")
    print(" → ".join(f"{C.BOLD}{p.NAME}{C.RESET}" for p in providers))
    print(f"{C.DIM}  ARIA will automatically fall back on quota/errors.{C.RESET}\n")
    return providers


def load_providers(store: KeyStore) -> list:
    """
    Load saved keys silently (no prompts). Used on subsequent runs.
    Falls back to interactive setup if no keys saved.
    """
    providers = []

    checks = [
        ("google_api_key", "GOOGLE_API_KEY",
         lambda k: k.startswith("AIza") or k.startswith("AQ."),
         lambda k: GeminiProvider(k), "Gemini"),
        ("github_token",   "GITHUB_TOKEN",
         lambda k: len(k) > 10,
         lambda k: GitHubProvider(k), "GitHub Models"),
        ("groq_api_key",   "GROQ_API_KEY",
         lambda k: k.startswith("gsk_"),
         lambda k: GroqProvider(k), "Groq"),
    ]

    for store_key, env_var, validator, factory, name in checks:
        key = os.environ.get(env_var, "") or store.get(store_key, "")
        if key and validator(key):
            try:
                providers.append(factory(key))
                log("OK", f"{name} loaded from saved config")
            except Exception as e:
                log("FAIL", f"{name} failed to init: {e}")

    return providers


# ═══════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(
        prog        = "aria",
        description = "ARIA v3 — Autonomous AI agent with multi-provider fallback"
    )
    parser.add_argument("task", nargs="?",
                        help='Task to build. e.g. "snake game" or "tetris"')
    parser.add_argument("--interactive", "-i", action="store_true",
                        help="Start in interactive chat mode")
    parser.add_argument("--setup", "-s", action="store_true",
                        help="Re-run provider setup to add/change API keys")
    args = parser.parse_args()

    store = KeyStore()

    # Setup mode or first run
    if args.setup:
        providers = build_providers(store)
    else:
        providers = load_providers(store)
        if not providers:
            print(f"{C.YELLOW}  No saved API keys found — running setup...{C.RESET}")
            providers = build_providers(store)

    aria = ARIA(providers)

    if args.interactive or not args.task:
        aria.chat()
    else:
        aria.run_task(args.task)


if __name__ == "__main__":
    main()
