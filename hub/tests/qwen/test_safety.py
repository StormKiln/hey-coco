from datetime import UTC, datetime

import pytest
from test_fallback import fallback, run

from coco_hub.intents.engine import IntentContext
from coco_hub.qwen import catalog as catalog_module
from coco_hub.qwen.fallback import ANSWER_PROMPT
from coco_hub.qwen.safety import SAFETY_RULE, pet_food_answer

from fake_ollama import FakeOllama, down_transport

DOG_GRAPES = (
    "Grapes and raisins are toxic to dogs — even small amounts. "
    "If your dog ate some, call your vet."
)
CAT_CHOC = "Chocolate is toxic to cats — even small amounts. If your cat ate some, call your vet."


@pytest.mark.parametrize(
    ("text", "answer"),
    [
        ("can dogs eat grapes", f"No. {DOG_GRAPES}"),
        ("Can my dog have a grape?", f"No. {DOG_GRAPES}"),
        ("are raisins ok for puppies", f"No. {DOG_GRAPES}"),
        ("is it safe to give the doggo a few sultanas", f"No. {DOG_GRAPES}"),
        ("my puppy ate some raisins, is that bad", f"Yes. {DOG_GRAPES}"),
        ("are grapes poisonous to dogs", f"Yes. {DOG_GRAPES}"),
        ("my dog just ate a grape", DOG_GRAPES),
        ("my dog ate a grape, is he ok", DOG_GRAPES),
        ("is chocolate ok for cats", f"No. {CAT_CHOC}"),
        (
            "can dogs have chocolate",
            "No. Chocolate is toxic to dogs — even small amounts. "
            "If your dog ate some, call your vet.",
        ),
        (
            "can dogs have peanut butter with xylitol",
            "No. Xylitol, a sweetener in some sugar-free gum, sweets and peanut butter, is "
            "toxic to dogs — even small amounts. If your dog ate some, call your vet.",
        ),
        (
            "is it safe to give my cat a bit of onion",
            "No. Onions are toxic to cats — even small amounts. "
            "If your cat ate some, call your vet.",
        ),
        (
            "can my kitten have garlic",
            "No. Garlic is toxic to cats — even small amounts. "
            "If your cat ate some, call your vet.",
        ),
        (
            "are macadamia nuts bad for dogs",
            "Yes. Macadamia nuts are toxic to dogs — even small amounts. "
            "If your dog ate some, call your vet.",
        ),
        # Less well documented for cats: still a firm no, but not overstated.
        (
            "can cats eat grapes",
            "No. Grapes and raisins can be toxic to cats — even small amounts. "
            "If your cat ate some, call your vet.",
        ),
        (
            "is xylitol ok for cats",
            "No. Xylitol, a sweetener in some sugar-free gum, sweets and peanut butter, can be "
            "toxic to cats — even small amounts. If your cat ate some, call your vet.",
        ),
        (
            "can cats eat macadamias",
            "No. Macadamia nuts can be toxic to cats — even small amounts. "
            "If your cat ate some, call your vet.",
        ),
    ],
)
def test_pet_food_table(text, answer):
    assert pet_food_answer(text) == answer


@pytest.mark.parametrize(
    "text",
    [
        "can I eat grapes",
        "what do dogs eat",
        "why do cats purr",
        "how do you make chocolate cake",
        "are grapes healthy",
        "can dogs eat carrots",
        "",
    ],
)
def test_no_pet_food_answer(text):
    assert pet_food_answer(text) is None


FIXED = datetime(2026, 11, 14, 20, 5, tzinfo=UTC)


@pytest.fixture
def ctx(settings) -> IntentContext:
    return IntentContext(settings=settings, clock=lambda: FIXED)


@pytest.mark.anyio
async def test_pet_food_question_is_answered_without_the_model(ctx):
    fake = FakeOllama()  # any /api/chat call fails the test
    action = await run(fallback(fake), "can dogs eat grapes", ctx)
    assert action.say == f"No. {DOG_GRAPES}"
    assert (action.intent, action.matched_by) == ("general.answer", "qwen")
    assert action.view is not None and action.view.text == action.say
    assert fake.chats == []


@pytest.mark.anyio
async def test_pet_food_question_is_answered_with_ollama_down(ctx):
    action = await run(fallback(transport=down_transport()), "is chocolate ok for cats", ctx)
    assert action.say == f"No. {CAT_CHOC}"


def test_safety_rule_is_in_both_prompts():
    for topic in ("food safety", "toxic to pets", "allergies", "medicine", "health"):
        assert topic in SAFETY_RULE
    assert "grapes are toxic to dogs" in SAFETY_RULE
    assert "Never guess" in SAFETY_RULE
    assert SAFETY_RULE in catalog_module.OUTPUT_RULES
    assert SAFETY_RULE in ANSWER_PROMPT
