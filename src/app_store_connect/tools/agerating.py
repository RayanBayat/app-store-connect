"""Age rating: read the questionnaire answers and (with writes on) change them.

Apple requires every question of the age-rating questionnaire to be answered before an update
can be submitted. A missing answer is returned as a 409 ("You must provide a value for the
attribute 'socialMedia'"), so these tools show which answers are missing and fill them in.

Spec 4.5.1: `GET /v1/appInfos/{id}/ageRatingDeclaration` and
`PATCH /v1/ageRatingDeclarations/{id}`; the app info's state comes from `GET /v1/appInfos/{id}`.
"""

from __future__ import annotations

from typing import Any, Final

from app_store_connect.tools.base import (
    ToolContext,
    ToolInputError,
    ToolRefusedError,
    ToolSpec,
    require_id,
    resource_of,
    string_prop,
    to_text,
)
from app_store_connect.tools.confirm import confirm_change

NOT_ANSWERED: Final = "not answered"

# Yes/no questions: attribute -> plain label.
BOOLEAN_QUESTIONS: Final[dict[str, str]] = {
    "advertising": "Advertising",
    "gambling": "Gambling",
    "healthOrWellnessTopics": "Health or wellness topics",
    "lootBox": "Loot boxes",
    "messagingAndChat": "Messaging and chat",
    "parentalControls": "Parental controls",
    "ageAssurance": "Age assurance",
    "socialMedia": "Social media",
    "socialMediaAgeRestricted": "Social media: age restricted",
    "unrestrictedWebAccess": "Unrestricted web access",
    "userGeneratedContent": "User-generated content",
}
# Frequency questions: answered NONE / INFREQUENT_OR_MILD / FREQUENT_OR_INTENSE (the plain
# INFREQUENT and FREQUENT values also exist in the spec and are accepted).
FREQUENCY_QUESTIONS: Final[dict[str, str]] = {
    "alcoholTobaccoOrDrugUseOrReferences": "Alcohol, tobacco or drug use or references",
    "contests": "Contests",
    "gamblingSimulated": "Simulated gambling",
    "gunsOrOtherWeapons": "Guns or other weapons",
    "medicalOrTreatmentInformation": "Medical or treatment information",
    "profanityOrCrudeHumor": "Profanity or crude humor",
    "sexualContentGraphicAndNudity": "Sexual content: graphic and nudity",
    "sexualContentOrNudity": "Sexual content or nudity",
    "horrorOrFearThemes": "Horror or fear themes",
    "matureOrSuggestiveThemes": "Mature or suggestive themes",
    "violenceCartoonOrFantasy": "Cartoon or fantasy violence",
    "violenceRealisticProlongedGraphicOrSadistic": "Prolonged graphic or sadistic violence",
    "violenceRealistic": "Realistic violence",
}
FREQUENCY_VALUES: Final[dict[str, str]] = {
    "NONE": "None",
    "INFREQUENT_OR_MILD": "Infrequent or mild",
    "FREQUENT_OR_INTENSE": "Frequent or intense",
    "INFREQUENT": "Infrequent",
    "FREQUENT": "Frequent",
}
# Optional choices with their own values. `ageRatingOverride` is deprecated (use the V2 one).
CHOICE_QUESTIONS: Final[dict[str, tuple[str, tuple[str, ...]]]] = {
    "kidsAgeBand": ("Kids age band", ("FIVE_AND_UNDER", "SIX_TO_EIGHT", "NINE_TO_ELEVEN")),
    "ageRatingOverrideV2": (
        "Age rating override",
        ("NONE", "NINE_PLUS", "THIRTEEN_PLUS", "SIXTEEN_PLUS", "EIGHTEEN_PLUS", "UNRATED"),
    ),
    "koreaAgeRatingOverride": (
        "Korea age rating override",
        ("NONE", "ALL", "TWELVE_PLUS", "FIFTEEN_PLUS", "NINETEEN_PLUS"),
    ),
}
# Other details we only read.
INFO_FIELDS: Final = ("gracRatingClassificationNumber", "developerAgeRatingInfoUrl")
DECLARATION_FIELDS: Final = (
    *BOOLEAN_QUESTIONS, *FREQUENCY_QUESTIONS, *CHOICE_QUESTIONS, *INFO_FIELDS
)  # fmt: skip
LABELS: Final[dict[str, str]] = {
    **BOOLEAN_QUESTIONS,
    **FREQUENCY_QUESTIONS,
    **{name: label for name, (label, _) in CHOICE_QUESTIONS.items()},
}

# App info states in which Apple lets the developer edit the answers (the rest are in review,
# approved or live). Names are from `AppInfo.attributes.state` in spec 4.5.1.
EDITABLE_STATES: Final = frozenset({"PREPARE_FOR_SUBMISSION", "DEVELOPER_REJECTED", "REJECTED"})
ALL_OTHERS: Final = "all_others"
LEGAL_NOTE: Final = (
    "These answers are your legal declaration to Apple as the developer, and they set the "
    "app's App Store age rating. Only press Accept if every answer is true."
)


def show_answer(name: str, value: Any) -> str:
    """Plain-English form of a stored or new answer; None means the question is unanswered."""
    if value is None:
        return NOT_ANSWERED
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if name in FREQUENCY_QUESTIONS:
        return FREQUENCY_VALUES.get(str(value), str(value))
    return str(value)


def is_editable(state: object) -> bool:
    """True when the app info is in a state where the answers may be changed."""
    return state in EDITABLE_STATES


async def get_age_rating(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Return the answers (and unanswered questions) for each app info of an app."""
    infos = await ctx.client.paginate("/v1/apps/{id}/appInfos", item_id=require_id(args, "app_id"))
    result: list[dict[str, Any]] = []
    for info in infos:
        info_id = str(info["id"])
        response = await ctx.client.request(
            "GET", "/v1/appInfos/{id}/ageRatingDeclaration", item_id=info_id
        )
        data = resource_of(response)
        attributes = data.get("attributes") or {}
        state = (info.get("attributes") or {}).get("state")
        questions = (*BOOLEAN_QUESTIONS, *FREQUENCY_QUESTIONS)
        result.append(
            {
                "app_info_id": info_id,
                "state": state,
                "editable": is_editable(state),
                "declaration_id": data.get("id"),
                "answers": {
                    name: show_answer(name, attributes.get(name))
                    if name in questions
                    else (attributes.get(name) or NOT_ANSWERED)
                    for name in DECLARATION_FIELDS
                },
                "not_answered": [LABELS[n] for n in questions if attributes.get(n) is None],
            }
        )
    return to_text(result)


def check_value(name: str, value: Any) -> None:
    """Refuse a value Apple's spec does not allow for question `name`."""
    if name in BOOLEAN_QUESTIONS:
        if not isinstance(value, bool):
            raise ToolInputError(f"{name} must be true or false.")
    elif name in FREQUENCY_QUESTIONS:
        if value not in FREQUENCY_VALUES:
            raise ToolInputError(f"{name} must be one of: {', '.join(FREQUENCY_VALUES)}.")
    else:
        allowed = CHOICE_QUESTIONS[name][1]
        if value not in allowed:
            raise ToolInputError(f"{name} must be one of: {', '.join(allowed)}.")


def validate_answers(raw: Any) -> tuple[dict[str, Any], bool]:
    """Check the `answers` object. Returns (explicit answers, fill_others)."""
    if not isinstance(raw, dict) or not raw:
        raise ToolInputError("Argument 'answers' must be a non-empty object.")
    fill_others = False
    explicit: dict[str, Any] = {}
    for name, value in raw.items():
        if name == ALL_OTHERS:
            if value != "none":
                raise ToolInputError('all_others only accepts the value "none".')
            fill_others = True
        elif name == "ageRatingOverride":
            raise ToolInputError(
                "ageRatingOverride is deprecated by Apple; use ageRatingOverrideV2."
            )
        elif name not in LABELS:
            raise ToolInputError(f"Unknown question '{name}'. Known: {', '.join(LABELS)}.")
        else:
            check_value(name, value)
            explicit[name] = value
    return explicit, fill_others


def with_all_others(explicit: dict[str, Any]) -> dict[str, Any]:
    """Add 'No' / 'None' for every yes/no and frequency question not given explicitly."""
    wanted: dict[str, Any] = dict.fromkeys(BOOLEAN_QUESTIONS, False)
    wanted.update(dict.fromkeys(FREQUENCY_QUESTIONS, "NONE"))
    wanted.update(explicit)
    return wanted


async def update_age_rating(ctx: ToolContext, args: dict[str, Any]) -> str:
    """Show old -> new for every changed answer, ask the human, then send one PATCH."""
    info_id = require_id(args, "app_info_id")
    explicit, fill_others = validate_answers(args.get("answers"))
    wanted = with_all_others(explicit) if fill_others else explicit
    info = resource_of(await ctx.client.request("GET", "/v1/appInfos/{id}", item_id=info_id))
    state = (info.get("attributes") or {}).get("state")
    if not is_editable(state):
        raise ToolRefusedError(
            f"This app info is in state {state}; answers can only change in: "
            f"{', '.join(sorted(EDITABLE_STATES))}. Nothing was changed."
        )
    declaration_response = await ctx.client.request(
        "GET", "/v1/appInfos/{id}/ageRatingDeclaration", item_id=info_id
    )
    declaration = resource_of(declaration_response)
    old = declaration.get("attributes") or {}
    changed = {n: v for n, v in wanted.items() if old.get(n) != v}
    if not changed:
        return "Nothing to change: the answers are already the same."
    lines = [
        f"{LABELS[n]}: {show_answer(n, old.get(n))}  ->  {show_answer(n, v)}"
        for n, v in changed.items()
    ]
    await confirm_change(ctx, "Change the age rating answers", [*lines, "", LEGAL_NOTE])
    declaration_id = str(declaration["id"])
    body = {"data": {"type": "ageRatingDeclarations", "id": declaration_id, "attributes": changed}}
    await ctx.client.request(
        "PATCH", "/v1/ageRatingDeclarations/{id}", item_id=declaration_id, body=body
    )
    return "Updated: " + to_text(changed)


_ANSWERS_HELP = (
    "Object of question -> answer. Yes/no questions take true or false: "
    + ", ".join(BOOLEAN_QUESTIONS)
    + ". Frequency questions take NONE, INFREQUENT_OR_MILD or FREQUENT_OR_INTENSE: "
    + ", ".join(FREQUENCY_QUESTIONS)
    + ". Choices: "
    + "; ".join(f"{n} = {' / '.join(v)}" for n, (_, v) in CHOICE_QUESTIONS.items())
    + ". Special key 'all_others': \"none\" answers every yes/no question you did not list "
    "with false and every frequency question you did not list with NONE (it replaces existing "
    "answers too; overrides and kidsAgeBand are left alone). Use it for 'no to everything "
    "except X'."
)


READ_TOOLS: Final[list[ToolSpec]] = [
    ToolSpec(
        name="get_age_rating",
        description=(
            "Show the age rating questionnaire answers for each app info of an app (the editable "
            "one and the live one): its state, whether it is editable, every answer in plain "
            "words, and a list of questions that are 'not answered' (Apple blocks submitting "
            "while any is missing)."
        ),
        properties={"app_id": string_prop("The app's ID.")},
        required=("app_id",),
        handler=get_age_rating,
    ),
]


WRITE_TOOLS: Final[list[ToolSpec]] = [
    ToolSpec(
        name="update_age_rating",
        description="Answer or change the age rating questionnaire of an editable app info (state "
        "PREPARE_FOR_SUBMISSION, DEVELOPER_REJECTED or REJECTED; others are refused). Unknown "
        "questions and invalid values are refused first. The form lists every changed answer "
        "as old -> new and states that the answers are the developer's legal declaration that "
        "sets the App Store age rating. Asks the user to confirm.",
        properties={
            "app_info_id": string_prop("The app info's ID (see get_age_rating)."),
            "answers": {
                "type": "object",
                "description": _ANSWERS_HELP,
                "minProperties": 1,
            },
        },
        required=("app_info_id", "answers"),
        handler=update_age_rating,
        write=True,
    ),
]
