"""
The one door to a hosted language model. Every model call in TrustLadder goes
through here: the vision reader, the evidence agent and the reason writer.

Why one module
--------------
Three different jobs ask a model for help, and all three must behave the same way
when the model is missing, slow or overloaded: the demo keeps working, the screen
says which path ran, and nothing is retried forever during a live presentation.
Keeping the HTTP, the retries and the fallback list in one place means those
guarantees are made once.

Provider
--------
Google's Gemini models through the Google AI Studio REST endpoint, called with the
standard library only (urllib). No SDK is installed, so the deployed app adds no
dependency and runs identically with or without a key. The key is read from the
environment variable GEMINI_API_KEY and is never written to disk, logged, or shown.

Model fallback
--------------
On 16 October 2026 the newest flash model answered "503, high demand" for most of
an afternoon while the previous flash model answered at once. A demo cannot depend
on one model's capacity, so a call tries each model in MODELS in turn and stops at
the first that answers. The name of the model that answered travels back with the
result and is shown on screen, so nobody has to guess which model wrote a sentence.

Switching models off
--------------------
TRUSTLADDER_MODELS=off disables every call even when a key is present. The test
suite sets it so no test ever touches the network, and it is the switch to flip if
a presentation must run entirely on the written fallbacks.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Tried in order. The first entry is the one that answered reliably when this was
# built; the rest are the newer and the lighter models, for when it is saturated.
# Fastest first. Measured on 20 Oct 2026 with the demo's key: gemini-3.1-flash-lite answers in
# about 9 s; gemini-3.5-flash returned no text; gemini-3.8-flash and gemini-flash-latest timed
# out at 60 s. With the lite model last, every reading waited two minutes for the fallbacks
# and the customer's screen fell back to the local OCR. The others stay as fallbacks.
MODELS = ["gemini-3.1-flash-lite", "gemini-3.5-flash", "gemini-3.8-flash", "gemini-flash-latest"]

TIMEOUT_SECONDS = 40          # one request; a bill image read takes 5-8 s in practice
RETRY_PAUSE_SECONDS = 1       # between a "high demand" answer and the next model

# These models think before they answer, and the thinking counts against the output
# limit. A small limit cut a reason off mid-sentence; the limit is generous for that
# reason, and a reply that still hits it is treated as no reply.
DEFAULT_MAX_TOKENS = 4096


def mode() -> str:
    """"on", "reader" or "off": the TRUSTLADDER_MODELS setting.

    "reader" switches the hosted model on for READING pictures only, and leaves the
    evidence agent and the reason writer on their written fallbacks. The free tier of a
    Google key allows twenty requests a day per model; a held claim's narration takes
    several of them (the agent loops over its tools), so on a free key a rehearsal that
    narrates two or three claims leaves nothing for the photos. Reader-only spends the
    allowance on the one job that has no written fallback.
    """
    v = os.environ.get("TRUSTLADDER_MODELS", "").strip().lower()
    if v == "off" or not os.environ.get("GEMINI_API_KEY", "").strip():
        return "off"
    return "reader" if v == "reader" else "on"


def available() -> bool:
    """Is a key configured and are models switched on? Never exposes the key."""
    return mode() != "off"


def narrates() -> bool:
    """May the agent and the reason writer call the model? Only in full "on" mode."""
    return mode() == "on"


# The last failure, kept so the screens can say "on, but the last call failed: quota"
# instead of a bare "on". On 16 October the free tier turned out to allow twenty
# requests a day per model; a presenter must be able to see that state at a glance.
_LAST_FAILURE: dict = {"when": "", "why": ""}


def status() -> tuple[str, str]:
    """("on" | "off", a short note). The note names the last failure, if any."""
    if not available():
        return "off", "written fallbacks in use"
    state = "on" if narrates() else "on (reader only)"
    if _LAST_FAILURE["why"]:
        return state, f"last call failed at {_LAST_FAILURE['when']}: {_LAST_FAILURE['why']}"
    return state, provider_label()


def _note_failure(why: str) -> None:
    _LAST_FAILURE["when"] = time.strftime("%H:%M")
    _LAST_FAILURE["why"] = why


def _note_success() -> None:
    _LAST_FAILURE["why"] = ""


def provider_label() -> str:
    """How the screens name the provider, without naming a model that may not run."""
    return "Google Gemini (hosted, via Google AI Studio)"


# --------------------------------------------------------------------------
# Request pieces
# --------------------------------------------------------------------------


def text_part(text: str) -> dict:
    return {"text": text}


def image_part(data: bytes, mime: str) -> dict:
    """An image for a multimodal prompt. JPEG and PNG are what the uploader accepts."""
    return {"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}}


def function_response_part(name: str, response: dict) -> dict:
    """What a tool returned, in the shape the model expects it back."""
    return {"functionResponse": {"name": name, "response": response}}


@dataclass
class Reply:
    """One answer from the model.

    `text` is the written answer, if any. `calls` are the tools the model asked to
    run, each as (name, args). `content` is the model's whole turn exactly as it
    came back, which must be appended to the conversation unchanged when a tool
    result is sent back: newer models attach a signature to their reasoning and
    refuse a continuation that drops it.
    """
    text: str
    calls: list[tuple[str, dict]]
    model: str
    content: dict = field(default_factory=dict)
    usage: dict = field(default_factory=dict)


class ModelUnavailable(RuntimeError):
    """Every model in the list declined, timed out or errored."""


def generate(contents: list[dict], *, system: str = "", json_mode: bool = False,
             tools: list[dict] | None = None, max_tokens: int = DEFAULT_MAX_TOKENS,
             temperature: float = 0.0) -> Reply:
    """Ask the first model that will answer. Raises ModelUnavailable if none will.

    `contents` is the conversation so far: a list of {"role": "user"|"model",
    "parts": [...]}. `tools` is a list of function declarations (name, description,
    parameters as a JSON schema); when given, the model may answer with calls
    instead of text.
    """
    if not available():
        raise ModelUnavailable("no key configured or models switched off")
    key = os.environ["GEMINI_API_KEY"].strip()

    body: dict = {"contents": contents,
                  "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens}}
    if system:
        body["system_instruction"] = {"parts": [{"text": system}]}
    if json_mode:
        body["generationConfig"]["response_mime_type"] = "application/json"
    if tools:
        body["tools"] = [{"function_declarations": tools}]
    payload = json.dumps(body).encode()

    last_error = "no model tried"
    first_error = ""                      # the first model's reason: the one worth showing
    # Two passes over the list: a "high demand" answer is usually gone a few seconds
    # later, and the second pass catches it without waiting long on any one model.
    for attempt in range(2):
        for model in MODELS:
            request = urllib.request.Request(
                API.format(model=model), data=payload, method="POST",
                headers={"content-type": "application/json", "x-goog-api-key": key})
            try:
                with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                    answer = json.loads(response.read())
                reply = _parse(answer, model)
                _note_success()
                return reply
            except urllib.error.HTTPError as e:
                # 503 "high demand" and 429 "quota" are the two answers worth moving on
                # from. A 400 or 404 is this request's or this model's fault and is
                # equally worth moving on from; nothing here is worth stopping the demo.
                try:
                    detail = json.loads(e.read()).get("error", {}).get("message", "")
                except Exception:
                    detail = ""
                last_error = f"{model}: HTTP {e.code} " + (
                    "quota exceeded (free tier allows 20 requests a day per model)"
                    if e.code == 429 else "high demand" if e.code == 503 else detail[:60])
                first_error = first_error or last_error
                if e.code in (429, 503):
                    time.sleep(RETRY_PAUSE_SECONDS * (attempt + 1))
                continue
            except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError) as e:
                last_error = f"{model}: {type(e).__name__}"
                first_error = first_error or last_error
                continue
    why = first_error if first_error == last_error else f"{first_error}; fallbacks: {last_error}"
    _note_failure(why)
    raise ModelUnavailable(why)


def _parse(answer: dict, model: str) -> Reply:
    """Pull the text and the tool calls out of the raw response."""
    candidates = answer.get("candidates") or []
    if not candidates:
        # A safety block or an empty answer. Treat it as "no answer" rather than
        # as something to show: the caller falls back to its written path.
        raise ValueError("no candidates")
    if candidates[0].get("finishReason") == "MAX_TOKENS":
        raise ValueError("reply cut off at the token limit")
    content = candidates[0].get("content") or {}
    parts = content.get("parts") or []
    text = "".join(p.get("text", "") for p in parts if "text" in p and not p.get("thought"))
    calls = [(p["functionCall"].get("name", ""), p["functionCall"].get("args") or {})
             for p in parts if "functionCall" in p]
    return Reply(text=" ".join(text.split()) if not text.startswith("{") else text.strip(),
                 calls=calls, model=model, content=content,
                 usage=answer.get("usageMetadata") or {})


def ask_json(prompt_parts: list[dict], *, system: str = "",
             max_tokens: int = DEFAULT_MAX_TOKENS) -> tuple[dict, str]:
    """One-shot: send parts, get a parsed JSON object and the model that wrote it."""
    reply = generate([{"role": "user", "parts": prompt_parts}], system=system,
                     json_mode=True, max_tokens=max_tokens)
    text = reply.text.strip()
    # Models occasionally wrap JSON in a code fence even in JSON mode.
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):text.rfind("}") + 1]
    return json.loads(text), reply.model
