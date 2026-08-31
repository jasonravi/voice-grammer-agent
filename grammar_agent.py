from __future__ import annotations

from dataclasses import asdict, dataclass, field

from voice_grammar_agent.llm import LLMClient, parse_json_object

ANALYSIS_PROMPT = """You are an English grammar analyst for spoken learner English.
Return JSON only:
{
  "has_errors": true,
  "errors": [
    {
      "original": "exact phrase",
      "issue": "short label such as subject-verb agreement",
      "why": "one sentence explanation",
      "corrected": "fixed phrase",
      "natural": "more natural phrasing"
    }
  ],
  "corrected": "full corrected sentence",
  "natural": "more natural full sentence"
}
If the sentence is already natural, has_errors=false and copy the sentence into corrected and natural.
Do not invent extra mistakes."""


@dataclass
class GrammarError:
    original: str
    issue: str
    why: str
    corrected: str
    natural: str


@dataclass
class GrammarReport:
    has_errors: bool = False
    errors: list[GrammarError] = field(default_factory=list)
    corrected: str = ""
    natural: str = ""

    def to_dict(self) -> dict:
        return {
            "has_errors": self.has_errors,
            "errors": [asdict(item) for item in self.errors],
            "corrected": self.corrected,
            "natural": self.natural,
        }


class GrammarAgent:
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    def analyze(self, text: str) -> GrammarReport:
        spoken = (text or "").strip()
        if not spoken:
            return GrammarReport()
        try:
            raw = self._llm.chat(
                [
                    {"role": "system", "content": ANALYSIS_PROMPT},
                    {"role": "user", "content": spoken},
                ],
                temperature=0.1,
                num_predict=280,
            )
            return report_from_dict(parse_json_object(raw), spoken)
        except Exception:
            return self._language_tool_fallback(spoken)

    def _language_tool_fallback(self, spoken: str) -> GrammarReport:
        try:
            import language_tool_python

            tool = language_tool_python.LanguageTool("en-US")
            matches = tool.check(spoken)
            corrected = tool.correct(spoken).strip() or spoken
            errors = []
            for match in matches[:5]:
                errors.append(
                    GrammarError(
                        original=spoken[match.offset : match.offset + match.errorLength],
                        issue=match.ruleIssueType or match.ruleId,
                        why=match.message,
                        corrected=(match.replacements[0] if match.replacements else corrected),
                        natural=corrected,
                    )
                )
            return GrammarReport(
                has_errors=bool(errors) and corrected.lower() != spoken.lower(),
                errors=errors,
                corrected=corrected,
                natural=corrected,
            )
        except Exception:
            return GrammarReport(corrected=spoken, natural=spoken)


def _clean_sentence(text: str) -> str:
    return (text or "").strip().strip('"').strip("'").strip()


def report_from_dict(data: dict, spoken: str) -> GrammarReport:
    grammar = data.get("grammar") if isinstance(data.get("grammar"), dict) else data
    errors = []
    for item in grammar.get("errors") or []:
        if not isinstance(item, dict):
            continue
        errors.append(
            GrammarError(
                original=str(item.get("original") or spoken),
                issue=str(item.get("issue") or "grammar"),
                why=str(item.get("why") or ""),
                corrected=_clean_sentence(str(item.get("corrected") or spoken)),
                natural=_clean_sentence(str(item.get("natural") or item.get("corrected") or spoken)),
            )
        )
    corrected = _clean_sentence(str(grammar.get("corrected") or spoken))
    natural = _clean_sentence(str(grammar.get("natural") or corrected))
    has_errors = bool(grammar.get("has_errors")) or bool(errors) or corrected.lower() != spoken.lower()
    return GrammarReport(
        has_errors=has_errors and corrected.lower() != spoken.lower(),
        errors=errors,
        corrected=corrected,
        natural=natural,
    )
