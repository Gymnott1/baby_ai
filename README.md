# baby_ai
## make ai live


---

## Setup (first time only)

```bash
pip install google-genai openai pygame psutil

python3 aria.py --setup
```

ARIA will walk you through adding keys one by one. Each is optional except at least one must work:

---

## The 3 providers — all free

| Provider | Free limit | Key format | Get it |
|---|---|---|---|
| **Google Gemini** | 1,500 req/day | `AIza...` or `AQ.Ab8...` | aistudio.google.com/app/apikey |
| **GitHub Models** | Generous free tier, GPT-4o | `ghp_...` or `github_pat_...` | github.com/settings/tokens |
| **Groq** | 14,400 req/day, ultra fast | `gsk_...` | console.groq.com/keys |

For GitHub: go to github.com/settings/tokens → Generate new token (classic) → no special scopes needed, just generate it and paste it.

---

## How the fallback works

```
ARIA calls Gemini
  → 429 RESOURCE_EXHAUSTED
  → [FIX] Provider fallback: Google Gemini → GitHub Models
  → continues the same goal, same context, no interruption
  → if GitHub also hits limits:
  → [FIX] Provider fallback: GitHub Models → Groq
```

The memory/conversation context is copied across providers when switching — so ARIA doesn't lose track of what it was building mid-task.

---

## Running

```bash
# First time — setup keys
python3 aria.py --setup

# After that — just run tasks directly
python3 aria.py "snake game"
python3 aria.py "tetris"
python3 aria.py "pong"

# Add/change keys any time
python3 aria.py --setup

# Chat mode
python3 aria.py --interactive
```