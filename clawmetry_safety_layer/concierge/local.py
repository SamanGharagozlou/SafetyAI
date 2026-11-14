"""Local concierge fallback (edge, no network): keyword answers from the venue facts, in EN/DE/FR/IT, used when the
brain or the Anthropic API is unreachable. It can still guide (through the same skills / local policy) and refuse
no-go places with the nearest allowed alternative. Replies are short and canned on purpose."""

from __future__ import annotations

import re
import unicodedata

from venue.facts import Venue

WORDS = {
    "de": (
        "wie",
        "viel",
        "kostet",
        "wo",
        "ist",
        "bitte",
        "danke",
        "ich",
        "möchte",
        "zum",
        "zur",
        "eintritt",
        "preis",
        "kinder",
        "öffnet",
        "geöffnet",
        "bringen",
        "führen",
    ),
    "fr": (
        "combien",
        "coûte",
        "où",
        "est",
        "merci",
        "je",
        "voudrais",
        "prix",
        "entrée",
        "enfants",
        "ouvert",
        "piscine",
        "s'il",
        "vous",
        "emmener",
    ),
    "it": (
        "quanto",
        "costa",
        "dove",
        "grazie",
        "vorrei",
        "prezzo",
        "ingresso",
        "bambini",
        "aperto",
        "piscina",
        "portare",
        "per favore",
    ),
}
TEMPLATES = {
    "price": {
        "en": "Thermal bath for adults (16+): CHF 33 for 3 hours, CHF 39 for a day; children (6+) CHF 20 / 23; under 6 free.",
        "de": "Thermalbad Erwachsene (ab 16): CHF 33 für 3 Stunden, CHF 39 für den Tag; Kinder (ab 6) CHF 20 / 23; unter 6 gratis.",
        "fr": "Bains thermaux adultes (16+) : CHF 33 pour 3 heures, CHF 39 la journée ; enfants (6+) CHF 20 / 23 ; moins de 6 ans gratuit.",
        "it": "Terme adulti (16+): CHF 33 per 3 ore, CHF 39 per il giorno; bambini (6+) CHF 20 / 23; sotto i 6 anni gratis.",
    },
    "hours": {
        "en": "The pools are open daily 08:00-20:00; Sauna World and the waterslides 10:00-19:00.",
        "de": "Die Becken sind täglich 08:00-20:00 offen, Saunawelt und Rutschen 10:00-19:00.",
        "fr": "Les bassins sont ouverts tous les jours de 08:00 à 20:00 ; saunas et toboggans de 10:00 à 19:00.",
        "it": "Le piscine sono aperte tutti i giorni 08:00-20:00; saune e scivoli 10:00-19:00.",
    },
    "rules": {
        "en": "Please no photos or videos anywhere in the complex; the sauna is textile-free and 16+.",
        "de": "Bitte keine Fotos oder Videos in der ganzen Anlage; die Sauna ist textilfrei und ab 16.",
        "fr": "Pas de photos ni de vidéos dans tout le complexe ; le sauna est sans textile et dès 16 ans.",
        "it": "Niente foto né video in tutto il complesso; la sauna è senza costume e dai 16 anni.",
    },
    "rent": {
        "en": "You can rent a swimsuit for CHF 8, a towel for CHF 5 and a bathrobe for CHF 12 at the Kasse.",
        "de": "An der Kasse mieten Sie Badekleid für CHF 8, Tuch für CHF 5 und Bademantel für CHF 12.",
        "fr": "À la caisse : maillot CHF 8, serviette CHF 5, peignoir CHF 12.",
        "it": "Alla cassa: costume CHF 8, asciugamano CHF 5, accappatoio CHF 12.",
    },
    "unknown": {
        "en": "I can help with prices, opening hours and the way to the Kasse, shop, café or Therme entrance. For anything else, the Kasse staff are happy to help.",
        "de": "Ich helfe bei Preisen, Öffnungszeiten und dem Weg zu Kasse, Shop, Café oder Eingang. Für alles andere hilft Ihnen die Kasse gern.",
        "fr": "Je peux vous aider pour les prix, les horaires et le chemin vers la caisse, la boutique, le café ou l'entrée. Pour le reste, la caisse vous aidera volontiers.",
        "it": "Posso aiutarla con prezzi, orari e la strada per cassa, negozio, caffè o ingresso. Per il resto la cassa la aiuta volentieri.",
    },
    "medical": {
        "en": "I can't give medical advice; please ask the staff at the Kasse or, in an emergency, call 144.",
        "de": "Ich kann keine medizinischen Ratschläge geben; bitte fragen Sie an der Kasse oder rufen Sie im Notfall 144 an.",
        "fr": "Je ne peux pas donner de conseil médical ; demandez à la caisse ou, en urgence, appelez le 144.",
        "it": "Non posso dare consigli medici; chieda alla cassa o, in emergenza, chiami il 144.",
    },
}
GO_WORDS = (
    "take me",
    "bring me",
    "show me",
    "where is",
    "where's",
    "way to",
    "guide",
    "bringen sie",
    "zeigen sie",
    "wo ist",
    "wo sind",
    "führen",
    "emmenez",
    "où est",
    "où sont",
    "conduire",
    "portami",
    "dov'è",
    "dove sono",
    "accompagn",
)
MEDICAL = (
    "pregnan",
    "heart",
    "blood pressure",
    "medic",
    "doctor",
    "schwanger",
    "herz",
    "arzt",
    "enceinte",
    "cœur",
    "médecin",
    "incinta",
    "cuore",
    "medico",
)


def _n(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c))


def detect_lang(text: str) -> str:
    t = _n(text)
    toks = set(re.findall(r"[a-z']+", t))
    scores = {lang: sum(1 for w in ws if _n(w) in toks or (" " in w and _n(w) in t)) for lang, ws in WORDS.items()}
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] > 0 else "en"


class LocalConcierge:
    def __init__(self, venue: Venue | None = None):
        self.venue = venue or Venue()

    def find_poi(self, text: str) -> str | None:
        t = _n(text)
        words = {
            "pool_deck": ("pool", "becken", "piscine", "piscina", "bassin", "schwimm", "baden", "bath ", "water"),
            "sauna_world": ("sauna",),
            "pool_bar": ("bar",),
            "massage_beauty": ("massage", "beauty", "kosmetik", "massaggi"),
            "kasse": ("kasse", "ticket", "caisse", "cassa", "reception", "desk"),
            "shop": ("shop", "souvenir", "boutique", "negozio"),
            "cafe": ("cafe", "coffee", "kaffee", "caffe"),
            "bath_entrance": ("entrance", "eingang", "entree", "ingresso", "turnstile", "drehkreuz"),
            "relax_level": ("relax",),
            "parking": ("parking", "parkplatz", "parcheggio", "car"),
            "lost_property": ("lost", "fundb", "trouve", "smarrit"),
            "stairs": ("stairs", "treppe", "escalier", "scale"),
            "technik": ("technik", "technical"),
        }
        for pid, ws in words.items():
            if any(w in t for w in ws):
                return pid
        return None

    def ask(self, text: str, lang: str | None = None, guide=None) -> dict:
        """-> {reply, actions, lang, source: local}. guide(poi, lang) -> SkillResult runs the guide skill (or None)."""
        lang = lang if lang in TEMPLATES["price"] else detect_lang(text)
        t = _n(text)
        actions: list[dict] = []
        if any(w in t for w in MEDICAL):
            return {"reply": TEMPLATES["medical"][lang], "actions": [], "lang": lang, "source": "local"}
        poi = self.find_poi(text)
        wants_go = any(_n(w) in t for w in GO_WORDS)
        if poi and wants_go:
            p = self.venue.pois[poi]
            name = self.venue.name(poi, lang)
            if p.get("no_go"):
                near = p.get("nearest")
                reply = {
                    "en": f"I can't go to the {name}: {self.venue.text(poi, 'no_go', 'en')}. I can take you to the {self.venue.name(near, 'en')} instead.",
                    "de": f"Zum Ziel {name} kann ich nicht: {self.venue.text(poi, 'no_go', 'de')}. Ich bringe Sie gern zum Ziel {self.venue.name(near, 'de')}.",
                    "fr": f"Je ne peux pas aller vers {name} : {self.venue.text(poi, 'no_go', 'fr')}. Je peux vous conduire à {self.venue.name(near, 'fr')}.",
                    "it": f"Non posso andare a {name}: {self.venue.text(poi, 'no_go', 'it')}. Posso portarla a {self.venue.name(near, 'it')}.",
                }[lang]
                if guide is not None:
                    r = guide(poi, lang)  # refused at once by the skill; also files the staff-escort approval
                    actions.append({"type": "guide_to", "poi": poi, "ok": r.ok, "message": r.message[:160]})
                return {"reply": reply, "actions": actions, "lang": lang, "source": "local"}
            reply = (
                self.venue.text(poi, "intro", lang)
                or {
                    "en": f"Follow me to the {name}.",
                    "de": f"Folgen Sie mir: {name}.",
                    "fr": f"Suivez-moi : {name}.",
                    "it": f"Mi segua: {name}.",
                }[lang]
            )
            if guide is not None:
                actions.append({"type": "guide_to", "poi": poi, "started": True})
                guide(poi, lang)
            return {"reply": reply, "actions": actions, "lang": lang, "source": "local"}
        for key, ws in (
            (
                "rent",
                ("rent", "towel", "swimsuit", "robe", "miet", "tuch", "serviette", "louer", "noleggi", "asciugam"),
            ),
            (
                "price",
                (
                    "price",
                    "cost",
                    "how much",
                    "chf",
                    "ticket",
                    "preis",
                    "kostet",
                    "prix",
                    "combien",
                    "prezzo",
                    "quanto",
                ),
            ),
            ("hours", ("open", "hour", "close", "when", "offnung", "offen", "heure", "ouvert", "orari", "aperto")),
            ("rules", ("photo", "video", "rule", "allowed", "dog", "foto", "regel", "regle", "regola")),
        ):
            if any(_n(w) in t for w in ws):
                return {"reply": TEMPLATES[key][lang], "actions": [], "lang": lang, "source": "local"}
        if poi:  # a place was named without "take me": say where it is and offer to guide
            p = self.venue.pois[poi]
            if p.get("no_go"):
                near = p.get("nearest")
                return {
                    "reply": {
                        "en": f"The {self.venue.name(poi, 'en')} is somewhere I can't go: {self.venue.text(poi, 'no_go', 'en')}. The {self.venue.name(near, 'en')} is the closest I can show you.",
                        "de": f"Dorthin kann ich nicht: {self.venue.text(poi, 'no_go', 'de')}.",
                        "fr": f"Je ne peux pas y aller : {self.venue.text(poi, 'no_go', 'fr')}.",
                        "it": f"Lì non posso andare: {self.venue.text(poi, 'no_go', 'it')}.",
                    }[lang],
                    "actions": [],
                    "lang": lang,
                    "source": "local",
                }
            return {
                "reply": {
                    "en": f"Would you like me to take you to the {self.venue.name(poi, 'en')}?",
                    "de": f"Soll ich Sie zum Ziel {self.venue.name(poi, 'de')} bringen?",
                    "fr": f"Voulez-vous que je vous conduise à {self.venue.name(poi, 'fr')} ?",
                    "it": f"Vuole che la porti a {self.venue.name(poi, 'it')}?",
                }[lang],
                "actions": [],
                "lang": lang,
                "source": "local",
            }
        return {"reply": TEMPLATES["unknown"][lang], "actions": [], "lang": lang, "source": "local"}
