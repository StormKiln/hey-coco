"""Safety for general answers: this is a family kitchen device.

``SAFETY_RULE`` goes into both Qwen prompts: for food safety, pets, allergies, medicine and
health the model must give the cautious answer or say it isn't sure, never a permissive
guess. A small model is still a small model (qwen3:8b once said grapes are fine for dogs in
small amounts), so the commonest pet-poison questions never reach it: ``pet_food_answer``
answers them from a fixed, conservative table.
"""

from coco_hub.intents.normalize import tokenize

SAFETY_RULE = (
    "- Safety: for food safety (raw or undercooked meat, fish or eggs, how long food keeps), "
    "foods that are toxic to pets (grapes, raisins, chocolate, xylitol, onions, garlic and "
    "others), allergies, medicine or doses, and health or symptoms, give only the cautious, "
    'safe answer (for example "No, grapes are toxic to dogs.") or say you are not sure and '
    "suggest asking a vet, a doctor or pharmacist, or another trusted source. Never guess "
    'that something is safe, "fine in small amounts" or "probably okay", and never tell '
    "someone with an allergy that a food is safe for them."
)

_ANIMALS: dict[str, frozenset[str]] = {
    "dog": frozenset("dog dogs puppy puppies pup pups doggy doggie doggies doggo doggos".split()),
    "cat": frozenset("cat cats kitten kittens kitty kitties".split()),
}

# toxin -> (words that name it, how to say it, takes "are" rather than "is")
_TOXINS: dict[str, tuple[frozenset[str], str, bool]] = {
    "grapes": (
        frozenset("grape grapes raisin raisins sultana sultanas".split()),
        "Grapes and raisins",
        True,
    ),
    "chocolate": (frozenset("chocolate chocolates cocoa".split()), "Chocolate", False),
    "xylitol": (
        frozenset({"xylitol"}),
        "Xylitol, a sweetener in some sugar-free gum, sweets and peanut butter,",
        False,
    ),
    "onion": (frozenset({"onion", "onions"}), "Onions", True),
    "garlic": (frozenset({"garlic"}), "Garlic", False),
    "macadamia": (frozenset({"macadamia", "macadamias"}), "Macadamia nuts", True),
}

# Documented as toxic for both; for cats grapes, xylitol and macadamias are less well
# studied, so Coco says "can be toxic" there (still a firm no).
_WELL_DOCUMENTED = {
    ("dog", "grapes"),
    ("dog", "chocolate"),
    ("dog", "xylitol"),
    ("dog", "onion"),
    ("dog", "garlic"),
    ("dog", "macadamia"),
    ("cat", "chocolate"),
    ("cat", "onion"),
    ("cat", "garlic"),
}

# "is X bad for dogs" -> "Yes."; "can dogs have X" -> "No."; "my dog ate X" -> no lead word.
_HARM_WORDS = frozenset(
    "bad toxic poisonous poison poisoned dangerous harmful deadly unsafe kill".split()
)
_ATE_WORDS = frozenset("ate eaten eating swallowed licked".split())


def _first(words: list[str], table: dict[str, frozenset[str]]) -> str | None:
    for word in words:
        for key, names in table.items():
            if word in names:
                return key
    return None


def pet_food_answer(text: str) -> str | None:
    """A fixed, conservative answer if ``text`` asks about a dog or cat and a common pet
    poison; otherwise None (the model answers)."""
    words = tokenize(text)
    animal = _first(words, _ANIMALS)
    toxin = _first(words, {k: v[0] for k, v in _TOXINS.items()})
    if animal is None or toxin is None:
        return None
    _, subject, plural = _TOXINS[toxin]
    if (animal, toxin) in _WELL_DOCUMENTED:
        verb = "are" if plural else "is"
    else:
        verb = "can be"
    answer = (
        f"{subject} {verb} toxic to {animal}s — even small amounts. "
        f"If your {animal} ate some, call your vet."
    )
    if _HARM_WORDS.intersection(words):
        return f"Yes. {answer}"
    if _ATE_WORDS.intersection(words):
        return answer
    return f"No. {answer}"
