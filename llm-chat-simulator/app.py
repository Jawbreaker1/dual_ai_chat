from flask import Flask, render_template, request, session, redirect, url_for, jsonify
from flask_session import Session
import requests, re, os
from datetime import timedelta
from difflib import SequenceMatcher

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
#LMSTUDIO_URL = "http://192.168.50.212:1234/v1/completions"
#LMSTUDIO_MODEL = "openai/gpt-oss-120b"

# ---- Defaults for bot personas ----
DEFAULT_LLM1_CONTEXT = (
    "You are Bot A. You are concise, analytical, and focus on clear reasoning."
)
DEFAULT_LLM2_CONTEXT = (
    "You are Bot B. You challenge assumptions, add counterpoints, and expand on ideas."
)

# ---- Anti-repeat / history window ----
HISTORY_WINDOW = 12  # only include the last N messages in the prompt (user+bots)


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
        session["max_tokens"] = 420       # concise by default; allow longer when needed


def _similar_ratio(a: str, b: str) -> float:
    a = (a or "").strip()
    b = (b or "").strip()
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _too_similar(a: str, b: str, threshold: float = 0.92) -> bool:
    return _similar_ratio(a, b) >= threshold


def lm_studio_complete(
    prompt: str,
    speaker: str,
    temperature: float = 0.7,
    freq_pen: float = 0.6,
    pres_pen: float = 0.25,
):
    """
    Call LM Studio /v1/completions and return (text, finish_reason).
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
        "max_tokens": session.get("max_tokens", 300),
        "temperature": temperature,
        "top_p": 1.0,
        "top_k": 40,
        "stop": stops,
        # penalties help reduce repetition
        "frequency_penalty": freq_pen,
        "presence_penalty": pres_pen,
    }
    try:
        r = requests.post(LMSTUDIO_URL, json=payload, timeout=120)
        r.raise_for_status()
        data = r.json()
        text = data.get("choices", [{}])[0].get("text", "") or ""
        finish = data.get("choices", [{}])[0].get("finish_reason", None)

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

        return text.strip(), finish
    except Exception as e:
        return f"(Local LLM error: {e})", "error"


SENTENCE_END = (".", "!", "?", "…", "…", ")", "\"")

def needs_closure(text: str, finish_reason: str) -> bool:
    t = (text or "").rstrip()
    if finish_reason == "length":
        return True
    if not t:
        return False
    return not t.endswith(SENTENCE_END)


def finish_if_cut(base_prompt: str, speaker: str, partial: str) -> str:
    """Attempt to finish the current sentence with at most one short sentence."""
    # Same stops as main call
    stops = ["\nUser:", "\nBot B:"] if speaker == "botA" else ["\nUser:", "\nBot A:"]

    continuation_instruction = (
        "\n(Continue and finish the current sentence only. Output ONE short sentence. "
        "Do not introduce new points. Do not add any speaker labels or headings.)\n"
    )

    payload = {
        "model": LMSTUDIO_MODEL,
        "prompt": base_prompt + "\n" + partial + continuation_instruction,
        "max_tokens": 40,
        "temperature": 0.4,
        "top_p": 1.0,
        "top_k": 40,
        "stop": stops,
        "frequency_penalty": 0.0,
        "presence_penalty": 0.0,
    }
    try:
        r = requests.post(LMSTUDIO_URL, json=payload, timeout=60)
        r.raise_for_status()
        data = r.json()
        cont = data.get("choices", [{}])[0].get("text", "") or ""
        return (partial + " " + cont).strip()
    except Exception:
        # As a last resort, add a period if missing
        t = partial.rstrip()
        if t and not t.endswith(SENTENCE_END):
            return t + "."
        return t


def build_prompt(next_speaker: str) -> str:
    """Construct a single-prompt transcript for the next speaker (with sliding window and anti-repeat rule)."""
    llm1 = session["llm1_context"]
    llm2 = session["llm2_context"]

    msgs = session["messages"][-HISTORY_WINDOW:]  # sliding window
    bot_turns = sum(1 for m in msgs if m["role"] in ("botA", "botB"))
    round_no = bot_turns + 1

    lines = [
        "System:\nYou are simulating a debate between two expert assistants, Bot A and Bot B.",
        f"Bot A persona:\n{llm1}",
        f"Bot B persona:\n{llm2}",
        "Rules:",
        "- The USER always starts with the first message.",
        "- Then Bot A and Bot B take strict turns (one message each).",
        "- Be direct, avoid fluff. Cite assumptions; correct the other if needed.",
        "- Default to ≤150 words (or ≤8 bullets). If deeper reasoning is needed, you may go longer, but be purposeful. End with a complete thought.",
        "- Never impersonate the other speaker. Only write your own turn.",
        "- Do NOT repeat sentences or structure from earlier replies; add NEW information, examples, or angles.",
        "",
        "Transcript so far:",
    ]

    for m in msgs:
        if m["role"] == "user":
            lines.append(f"User: {m['text']}")
        elif m["role"] == "botA":
            lines.append(f"Bot A: {m['text']}")
        elif m["role"] == "botB":
            lines.append(f"Bot B: {m['text']}")

    # Highlight the opponent's last message to force engagement
    opponent = "botB" if next_speaker == "botA" else "botA"
    opponent_label = "Bot B" if opponent == "botB" else "Bot A"
    opponent_last = None
    for m in reversed(session["messages"]):
        if m["role"] == opponent:
            opponent_last = m["text"]
            break
    if opponent_last:
        lines.append("")
        lines.append(f"Opponent's last message ({opponent_label}):")
        lines.append(opponent_last)

    # Turn instruction: revised for both bots
    if next_speaker == "botA":
        lines.append(
            f"\nRound {round_no}. Now it is Bot A's turn.\n"
            "Your task: Respond to the opponent's last message by acknowledging, challenging, or building on it. "
            "You may quote or paraphrase a key point if helpful (optional). Add at least one new angle, example, or piece of evidence. "
            "Default to ≤150 words (or ≤8 bullets); go longer only if needed for clarity. End with a complete thought.\n"
            "Bot A:"
        )
    else:
        lines.append(
            f"\nRound {round_no}. Now it is Bot B's turn.\n"
            "Your task: Respond to the opponent's last message by acknowledging, challenging, or building on it. "
            "You may quote or paraphrase a key point if helpful (optional). Add at least one new angle, example, or piece of evidence. "
            "Default to ≤150 words (or ≤8 bullets); go longer only if needed for clarity. End with a complete thought.\n"
            "Bot B:"
        )

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

    # Last reply from the same speaker (for similarity check)
    last_same_text = None
    for m in reversed(msgs):
        if m["role"] == speaker:
            last_same_text = m["text"]
            break

    # First attempt (role-specific sampling for clearer contrast)
    if speaker == "botA":
        reply, finish = lm_studio_complete(
            prompt, speaker, temperature=0.6, freq_pen=0.55, pres_pen=0.25
        )
    else:  # botB
        reply, finish = lm_studio_complete(
            prompt, speaker, temperature=0.9, freq_pen=0.8, pres_pen=0.4
        )

    # If too similar to the previous from same bot, retry once with stronger diversification
    if last_same_text and _too_similar(reply, last_same_text):
        prompt_retry = (
            prompt
            + "\n\n(Important: Do not repeat your previous message. Provide new points, examples, or a different structure such as bullets if last time was paragraphs.)"
        )
        if speaker == "botA":
            retry, finish_retry = lm_studio_complete(
                prompt_retry, speaker, temperature=0.75, freq_pen=0.8, pres_pen=0.4
            )
        else:  # botB
            retry, finish_retry = lm_studio_complete(
                prompt_retry, speaker, temperature=1.0, freq_pen=1.0, pres_pen=0.6
            )
        # choose the less similar (or keep retry anyway for some diversity)
        if not _too_similar(retry, last_same_text):
            reply, finish = retry, finish_retry
        else:
            reply, finish = retry, finish_retry

    # If cut off or lacking terminal punctuation, try to finish the sentence once
    if needs_closure(reply, finish):
        reply = finish_if_cut(prompt, speaker, reply)

    session["messages"].append({"role": speaker, "text": reply})
    session["next_speaker"] = "botA" if speaker == "botB" else "botB"

    if session["auto_turns_left"] > 0:
        session["auto_turns_left"] -= 1

    session.modified = True
    return jsonify(
        {
            "ok": True,
            "role": "botA" if speaker == "botA" else "botB",
            "text": reply,
            "auto_left": session["auto_turns_left"],
        }
    )


@app.route("/edit_context", methods=["GET"])
def edit_context():
    ensure_state()
    return render_template(
        "edit_context.html",
        llm1_context=session["llm1_context"],
        llm2_context=session["llm2_context"],
    )


@app.route("/update_context", methods=["POST"])
def update_context():
    ensure_state()
    session["llm1_context"] = request.form.get("llm1_context", DEFAULT_LLM1_CONTEXT)
    session["llm2_context"] = request.form.get("llm2_context", DEFAULT_LLM2_CONTEXT)
    session.modified = True
    # For fetch() auto-save this 302s back to "/" which is fine; we don't need JSON
    return redirect(url_for("chat"))


@app.route("/reset", methods=["POST"])
def reset():
    session.clear()
    return redirect(url_for("chat"))


if __name__ == "__main__":
    # Run: python app.py  (from llm-chat-simulator/)
    app.run(host="0.0.0.0", port=48080, debug=True)