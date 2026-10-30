from eoncloak.detect import LanguageInfo, LLMDetector, chunk_text, detect_language
from eoncloak.entities import ALL_TYPES, EntityType
from tests.conftest import FakeLLM


def test_chunks_respect_size_and_keep_text():
    text = "".join(f"Paragraph {i} " + "word " * 30 + "\n" for i in range(50))
    chunks = list(chunk_text(text, 500))
    assert all(len(c) <= 500 for c in chunks)
    assert "".join(chunks) == text


def test_long_line_split_on_spaces():
    text = "word " * 1000
    chunks = list(chunk_text(text, 300))
    assert "".join(chunks) == text
    assert all(len(c) <= 300 for c in chunks)


def test_language_detection(fake_llm):
    assert detect_language(fake_llm, "Il signor Rossi") == LanguageInfo("it", "IT")


def test_language_falls_back_to_english():
    class Broken:
        def complete_json(self, *args):
            raise RuntimeError("down")

    assert detect_language(Broken(), "text") == LanguageInfo("en", None)
    assert detect_language(FakeLLM({}, language="Italian??", country="Italy"), "x") == LanguageInfo(
        "it", None
    )


def test_hallucinated_entities_dropped():
    class Hallucinating(FakeLLM):
        def complete_json(self, system, user, schema):
            return schema.model_validate(
                {
                    "entities": [
                        {"text": "Mario Rossi", "type": "PERSON", "canonical": "Mario Rossi"},
                        {"text": "Not There", "type": "PERSON", "canonical": "Not There"},
                    ]
                }
            )

    seen = []
    LLMDetector(Hallucinating({}), ALL_TYPES).detect("Mario Rossi signed.", "en", list, seen.extend)
    assert [m.text for m in seen] == ["Mario Rossi"]


def test_known_entities_passed_to_later_chunks(fake_llm):
    text = "Mario Rossi signed.\n" + "filler text\n" * 20 + "Rossi left.\n"
    known = [(EntityType.PERSON, "Mario Rossi")]
    LLMDetector(fake_llm, ALL_TYPES, chunk_chars=60).detect(text, "it", lambda: known, list)
    assert len(fake_llm.calls) > 1
    assert '"canonical": "Mario Rossi"' in fake_llm.calls[-1][1]
