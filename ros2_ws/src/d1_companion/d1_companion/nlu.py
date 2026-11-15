"""Tiny rule-based intent parser for guest messages (typed or from speech). No ML, no network, deterministic.

parse(text, places) -> (kind, dest)   kind: select | home | stop | end | retry | ask
Places can have `aliases`; matching is on whole words / phrases, lower-cased, longest alias first. Anything else is an `ask`
(a question for the answerer). A real language understanding service (Vapi / an LLM) can replace this: only this signature matters.
"""
import re

STOP = ('stop', 'halt', 'wait', 'freeze', 'hold on', 'hold still', 'not so fast')
END = ('finish', 'finished', 'i am done', "i'm done", 'that is all', "that's all", 'goodbye', 'bye', 'end session', 'no thanks', 'no thank you', 'quit')
RETRY = ('try again', 'retry', 'again please', 'once more')
HOME = ('take me home', 'go home', 'back to the start', 'back to start', 'go back', 'return', 'home')
GO = ('take me', 'bring me', 'lead me', 'go to', 'walk me', 'show me', 'navigate', 'i want to go', "i'd like to go", 'let us go', "let's go", 'go ')


def _norm(text):
    return ' ' + re.sub(r'[^a-z0-9äöüß ]+', ' ', text.lower()).strip() + ' '


def _has(t, phrase):
    return (' ' + phrase.strip() + ' ') in t


def parse(text, places):
    t = _norm(text)
    if any(_has(t, w) for w in STOP):
        return 'stop', None
    if any(_has(t, w) for w in RETRY):
        return 'retry', None
    # destination aliases (longest first so "right 2 m" beats "right")
    cands = []
    for name, p in places.items():
        names = {name.replace('_', ' '), (p.get('label') or '').lower()} | {a.lower() for a in (p.get('aliases') or [])}
        for a in names:
            a = re.sub(r'[^a-z0-9äöüß ]+', ' ', a).strip()
            if a:
                cands.append((len(a), a, name))
    for _, a, name in sorted(cands, reverse=True):
        if _has(t, a):
            if name == 'home':
                return 'home', None
            return 'select', name
    if any(_has(t, w) for w in HOME):
        return 'home', None
    if any(_has(t, w) for w in END):
        return 'end', None
    return 'ask', None


def default_answer(text, places):
    """Honest fallback: only what the robot knows (its places). A curated knowledge base replaces this (FR-I1)."""
    t = _norm(text)
    names = [p.get('label') or n for n, p in places.items() if n != 'home']
    if any(_has(t, w) for w in ('where', 'places', 'destinations', 'what can you', 'help', 'options', 'show')):
        return ('I can take you to: ' + ', '.join(names) + '. Tap a place on the map or tell me where you would like to go.') if names \
            else 'I do not know any places yet.'
    if any(_has(t, w) for w in ('who are you', 'what are you', 'your name', 'hello', 'hi', 'hey')):
        return "I'm your robot companion. I can walk you to places in this area. Where would you like to go?"
    return ("I don't have an answer to that yet. I can take you to: " + ', '.join(names) + '.') if names else "I don't have an answer to that yet."
