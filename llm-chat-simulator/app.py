from flask import Flask, render_template, request, session, redirect, url_for, jsonify
from flask_session import Session
import requests, re, os
from datetime import timedelta

# ---- Flask setup ----
app = Flask(__name__, template_folder="templates", static_folder="static")

# --- Server-side session (fix 4KB cookie limit) ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SESSION_DIR = os.path.join(os.path.dirname(BASE_DIR), ".flask_session")
os.makedirs(SESSION_DIR, exist_ok=True)

app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "dev-secret-change-me"),
    PERMANENT_SESSION_LIFETIME=timedelta(hours=6),
    SESSION_TYPE="filesystem",
    SESSION_FILE_DIR=SESSION_DIR,
    SESSION_PERMANENT=False,   # we'll set session.permanent=True per request when needed
    SESSION_USE_SIGNER=True,
)

Session(app)

# ---- LM Studio config ----
LMSTUDIO_URL = "http://localhost:1234/v1/completions"
LMSTUDIO_MODEL = "openai/gpt-oss-20b"

# ---- Defaults for bot personas ----
DEFAULT_LLM1_CONTEXT = (
    "You are Bot A. You are concise, analytical, and focus on clear reasoning."
)
DEFAULT_LLM2_CONTEXT = (
    "You are Bot B. You challenge assumptions, add counterpoints, and expand on ideas."
)

# ---- Helpers ----
def ensure_state():
    """Initialize session state if missing."""
    session.permanent = True
    if "messages" not in session:
        session["messages"] = []  # list of dicts: {"role": "user|botA|botB", "text": "..."}
    if "llm1_context" not in session:
        session["llm1_context"] = DEFAULT_LLM1_CONTEXT
    if "llm2_context" not in session:
        session["llm2_context"] = DEFAULT_LLM2_CONTEXT
    if "next_speaker" not in session:
        session["next_speaker"] = "botA"  # who replies next after the user's first prompt
    if "auto_turns_left" not in session:
        session["auto_turns_left"] = 0    # how many bot messages remain (client can set)
    if "max_tokens" not in session:
        session["max_tokens"] = 600       # just a sensible default

def lm_studio_complete(prompt: str, speaker: str) -> str:
    """
    Call LM Studio /v1/completions and return text.
    `speaker` is 'botA' or 'botB' so we can set safe stop sequences.
    """
    # Stop when the NEXT turn starts, not on the current label.
    if speaker == "botA":
        stops = ["\nUser:", "\nBot B:"]
    else:
        stops = ["\nUser:", "\nBot A:"]

    payload = {
        "model": LMSTUDIO_MODEL,
        "prompt": prompt,
        "max_tokens": session.get("max_tokens", 600),
        "temperature": 0.6,
        "top_p": 1.0,
        "top_k": 40,
        "stop": stops,
    }
    try:
        r = requests.post(LMSTUDIO_URL, json=payload, timeout=120)
        r.raise_for_status()
        data = r.json()
        text = data["choices"][0]["text"] or ""

        # --- Strip analysis→assistantfinal header artifacts (if present) ---
        ts = text.lstrip()
        low = ts.lower()
        if low.startswith("analysis"):
            idx = low.find("assistantfinal")
            if idx != -1:
                ts = ts[idx + len("assistantfinal"):]
        text = ts

        # --- Remove meta tags some models emit ---
        text = re.sub(r"<\|channel\|>analysis<\|message\|>.*?<\|channel\|>final<\|message\|>", "", text, flags=re.DOTALL)
        text = re.sub(r"<\|.*?\|>", "", text)

        return text.strip()
    except Exception as e:
        return f"(Local LLM error: {e})"

def build_prompt(next_speaker: str) -> str:
    """Construct a single-prompt transcript for the next speaker."""
    llm1 = session["llm1_context"]
    llm2 = session["llm2_context"]

    lines = [
        "System:\nYou are simulating a debate between two expert assistants, Bot A and Bot B.",
        f"Bot A persona:\n{llm1}",
        f"Bot B persona:\n{llm2}",
        "Rules:",
        "- The USER always starts with the first message.",
        "- Then Bot A and Bot B take strict turns (one message each).",
        "- Be direct, avoid fluff. Cite assumptions; correct the other if needed.",
        "- Never impersonate the other speaker. Only write your own turn.",
        "- Keep answers under ~200 words unless deeper reasoning is crucial.",
        "",
        "Transcript so far:",
    ]

    for m in session["messages"]:
        if m["role"] == "user":
            lines.append(f"User: {m['text']}")
        elif m["role"] == "botA":
            lines.append(f"Bot A: {m['text']}")
        elif m["role"] == "botB":
            lines.append(f"Bot B: {m['text']}")

    # Instruction for the next turn
    if next_speaker == "botA":
        lines.append("\nNow it is Bot A's turn. Write ONLY Bot A's reply:\nBot A:")
    else:
        lines.append("\nNow it is Bot B's turn. Write ONLY Bot B's reply:\nBot B:")

    return "\n".join(lines)

# ---- Routes ----
@app.route("/", methods=["GET"])
def chat():
    ensure_state()
    return render_template(
        "index.html",
        messages=session["messages"],
        llm1_context=session["llm1_context"],
        llm2_context=session["llm2_context"],
        next_speaker=session["next_speaker"],
    )

@app.route("/send", methods=["POST"])
def send():
    """User sends the FIRST message (or another, if you want to re-seed)."""
    ensure_state()
    text = request.form.get("user_input", "").strip()
    turns = int(request.form.get("turns", "4") or 4)  # number of individual bot replies

    if not text:
        return redirect(url_for("chat"))

    session["messages"].append({"role": "user", "text": text})
    session["next_speaker"] = "botA"
    session["auto_turns_left"] = max(0, turns)

    session.modified = True
    return redirect(url_for("chat"))

@app.route("/tick", methods=["POST"])
def tick():
    """
    Produce exactly ONE bot message (for polling/autoplay from the client).
    Returns JSON with the new message, and whether more auto turns remain.
    """
    ensure_state()

    if not session["messages"]:
        return jsonify({"ok": False, "reason": "no_messages"}), 400

    # Determine next speaker from history to avoid desync if session flag is off
    msgs = session["messages"]
    last_role = None
    for m in reversed(msgs):
        if m["role"] in ("botA", "botB", "user"):
            last_role = m["role"]
            break

    if last_role == "botA":
        speaker = "botB"
    elif last_role == "botB":
        speaker = "botA"
    else:
        # No bot messages yet (or last was user): start with Bot A
        speaker = "botA"

    prompt = build_prompt(speaker)
    reply = lm_studio_complete(prompt, speaker)

    session["messages"].append({"role": speaker, "text": reply})
    session["next_speaker"] = "botA" if speaker == "botB" else "botB"

    if session["auto_turns_left"] > 0:
        session["auto_turns_left"] -= 1

    session.modified = True
    return jsonify({
        "ok": True,
        "role": "botA" if speaker == "botA" else "botB",
        "text": reply,
        "auto_left": session["auto_turns_left"]
    })

@app.route("/edit_context", methods=["GET"])
def edit_context():
    ensure_state()
    return render_template(
        "edit_context.html",
        llm1_context=session["llm1_context"],
        llm2_context=session["llm2_context"]
    )

@app.route("/update_context", methods=["POST"])
def update_context():
    ensure_state()
    session["llm1_context"] = request.form.get("llm1_context", DEFAULT_LLM1_CONTEXT)
    session["llm2_context"] = request.form.get("llm2_context", DEFAULT_LLM2_CONTEXT)
    session.modified = True
    return redirect(url_for("chat"))

@app.route("/reset", methods=["POST"])
def reset():
    session.clear()
    return redirect(url_for("chat"))

if __name__ == "__main__":
    # Run: python app.py  (from llm-chat-simulator/)
    app.run(host="0.0.0.0", port=48080, debug=True)