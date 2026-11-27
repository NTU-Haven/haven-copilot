"""Template Registry: the note templates as data.

A template is an id, a version, a name and its fields. Each field has a key
(the section key in a note), a label shown in the UI, an ``ai_mode`` and the
instructions the writer sends to the model for it. Adding a template is adding
one definition here (or calling ``register``); the writer, the signing gate and
the server read everything they need about a template from this registry.

``ai_mode``:
  "generate":   the model (or, without one, the rule engine) drafts the field.
  "human_only": the field is never sent to the model and starts empty; the
                therapist writes it. (A placeholder for it is a later ticket.)

Template-level settings:
  measures_field: where the server puts the questionnaire score lines (None: no scores).
  risk_field:     where a high-level risk line that no sentence cites is added as a
                  quote (None: not added; the signing gate still blocks on it).

``Field.fallback`` names what the rule engine (no model, or a part the model did
not answer) fills the field with: "subjective" (what the client said about
feelings and measures), "assessment" and "plan" (therapist lines), "risk" (risk
and protective lines). None: the rule engine leaves the field empty.
"""

from __future__ import annotations

from dataclasses import dataclass, field

GENERATE = "generate"
HUMAN_ONLY = "human_only"
AI_MODES = (GENERATE, HUMAN_ONLY)
FALLBACKS = ("subjective", "assessment", "plan", "risk")
DEFAULT = "SOAP"


class UnknownTemplate(ValueError):
    """No template with this id; answered with 400."""

    def __init__(self, template_id):
        super().__init__(f"unknown template {template_id!r}")
        self.template_id = template_id


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    instructions: str
    ai_mode: str = GENERATE
    fallback: str | None = None
    # Shown under the section title in the UI: what the draft cannot contain.
    hint: str = ""


@dataclass(frozen=True)
class Template:
    id: str
    version: int
    name: str
    fields: tuple[Field, ...]
    measures_field: str | None = None
    risk_field: str | None = None

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(f.key for f in self.fields)

    @property
    def generated(self) -> tuple[Field, ...]:
        return tuple(f for f in self.fields if f.ai_mode == GENERATE)

    def public(self) -> dict:
        """What the browser needs: ids, labels and hints, not the model instructions."""
        return {"id": self.id, "version": self.version, "name": self.name,
                "fields": [{"key": f.key, "label": f.label, "ai_mode": f.ai_mode, "hint": f.hint} for f in self.fields]}


_REGISTRY: dict[str, Template] = {}


def register(template: Template, replace: bool = False) -> Template:
    keys = template.keys
    if not template.id or not keys or len(set(keys)) != len(keys) or not all(keys):
        raise ValueError(f"template {template.id!r}: needs an id and distinct, non-empty field keys")
    if template.id in _REGISTRY and not replace:
        raise ValueError(f"template {template.id!r} is already registered")
    for f in template.fields:
        if f.ai_mode not in AI_MODES:
            raise ValueError(f"template {template.id!r} field {f.key!r}: ai_mode must be one of {AI_MODES}")
        if f.fallback is not None and (f.fallback not in FALLBACKS or f.ai_mode != GENERATE):
            raise ValueError(f"template {template.id!r} field {f.key!r}: bad fallback {f.fallback!r}")
    for name in ("measures_field", "risk_field"):
        key = getattr(template, name)
        if key is not None and (key not in keys or next(f for f in template.fields if f.key == key).ai_mode != GENERATE):
            raise ValueError(f"template {template.id!r}: {name} {key!r} must be a generated field")
    _REGISTRY[template.id] = template
    return template


def unregister(template_id: str) -> None:
    _REGISTRY.pop(template_id, None)


def get(template_id) -> Template:
    if not isinstance(template_id, str) or template_id not in _REGISTRY:
        raise UnknownTemplate(template_id)
    return _REGISTRY[template_id]


def find(template_id) -> Template | None:
    return _REGISTRY.get(template_id) if isinstance(template_id, str) else None


def all_templates() -> list[Template]:
    return list(_REGISTRY.values())


# --- SOAP and DAP -------------------------------------------------------------
# The instruction texts are sent to the model as written; tests pin them.

_A = "評估（只寫心理師在會談中說出口的觀察、概念化與介入重點，並引用心理師說的段落；心理師沒說出口的評估不要寫，寧可少寫）"
_P = ("計畫（會談中談定的作業、下次會談安排、轉介、安全措施；個案自己提出的計畫寫「個案計畫…」，心理師提出的才寫「心理師建議…」；"
      "心理師只是提議、逐字稿中沒有完成的事寫「心理師提議…」）")
_R = ("風險與安全（逐字稿中凡是提到自殺、自傷、傷害他人、受暴或受虐、物質使用，都必須寫在這一欄，不受句數限制；"
      "保留否定語、時間和主詞：他人的事寫明是誰，過去的事寫明時間；也寫保護因子與談到的安全措施；沒有這類內容就留空陣列）")
_R_HINT = "逐字稿中的風險線索、保護因子與安全措施；不受句數限制。提議而未完成的安全計畫會寫成「心理師提議」。"

ASSESSMENT = Field("A", "A 評估", _A, fallback="assessment")
PLAN = Field("P", "P 計畫", _P, fallback="plan")
RISK = Field("R", "R 風險與安全", _R, fallback="risk", hint=_R_HINT)

SOAP = register(Template(
    id="SOAP", version=1, name="SOAP 會談紀錄",
    fields=(
        Field("S", "S 主觀資料", "主觀資料（個案自述的困擾、感受、症狀、睡眠、體重、作業完成情形）", fallback="subjective"),
        # SOAP's O is what the clinician observes. The transcript is all the model has, so
        # each observation still cites the lines it shows in; the rest the therapist adds.
        # Batch 9: with 「語速」 as an example, Qwen3-32B wrote up to 35 lines like
        # 「語氣中透露出成就感」「表現出思索的神情」 per 50-minute note; a transcript
        # shows neither, and the verifier rightly held them all.
        Field("O", "O 客觀資料",
              ("客觀資料（只寫逐字稿文字本身看得到的行為：逐字稿標註的哭泣、沉默、笑，或心理師說出口的觀察，"
               "例如心理師說「我注意到你講到這裡停了很久」；語氣、語速、表情、神情、肢體動作，"
               "以及從說話內容推測的情緒（例如「語氣中透露出…」）一律不要寫，這些由心理師自己補；"
               "每句都要引用那個段落；量表分數由系統自動填入，不要寫；沒有就留空陣列，寧可少寫）"),
              hint="量表分數由系統填入。精神狀態觀察（外觀、情感、思考、定向、風險行為）逐字稿看不出來，請心理師自行補寫。"),
        ASSESSMENT, PLAN, RISK,
    ),
    measures_field="O", risk_field="R",
))

DAP = register(Template(
    id="DAP", version=1, name="DAP 會談紀錄",
    fields=(
        Field("D", "D 資料",
              ("資料（個案自述，以及逐字稿文字本身看得到的行為：逐字稿標註的哭泣、沉默，或心理師說出口的觀察；"
               "語氣、語速、表情、神情與推測的情緒不要寫；量表分數由系統自動填入，不要寫）"),
              fallback="subjective"),
        ASSESSMENT, PLAN, RISK,
    ),
    measures_field="D", risk_field="R",
))
