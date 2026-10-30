import json
import os
import stat

import docx
import pytest
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from eoncloak import Anonymizer, EntityType, load_document, load_mapping, restore_document
from eoncloak.config import ConfigError, load_settings
from eoncloak.documents import TextDocument

LETTER = (
    "Contratto tra ACME S.p.A. e Mario Rossi, residente in Via Roma 12, 20121 Milano.\n"
    "Contatti: mario.rossi@example.com, tel. 02 1234 5678.\n"
    "Il signor Rossi accetta. Firmato: M. Rossi. Testimone: Giulia Bianchi.\n"
)


def test_text_end_to_end(settings, fake_llm):
    result = Anonymizer(settings, llm=fake_llm).anonymize_text(LETTER)
    assert result.text == (
        "Contratto tra [ORG_1] e [PERSON_1], residente in [ADDRESS_1].\n"
        "Contatti: [EMAIL_1], tel. [PHONE_1].\n"
        "Il signor [PERSON_1] accetta. Firmato: [PERSON_1]. Testimone: [PERSON_2].\n"
    )
    assert result.language.language == "it"
    assert result.leaks == []


def test_consistent_across_chunks(settings, fake_llm):
    settings.anonymize.chunk_chars = 80
    text = LETTER + "testo di riempimento\n" * 30 + "Rossi e Giulia Bianchi tornano.\n"
    out = Anonymizer(settings, llm=fake_llm).anonymize_text(text).text
    assert out.endswith("[PERSON_1] e [PERSON_2] tornano.\n")


def test_consistent_across_documents(settings, fake_llm):
    anonymizer = Anonymizer(settings, llm=fake_llm)
    anonymizer.anonymize_text("Giulia Bianchi")
    assert (
        anonymizer.anonymize_text("Mario Rossi e Giulia Bianchi").text == "[PERSON_2] e [PERSON_1]"
    )


def test_rules_only_mode(settings):
    settings = load_settings(provider="ollama", use_llm=False, region="IT")
    result = Anonymizer(settings).anonymize_text(LETTER)
    assert "[EMAIL_1]" in result.text and "[PHONE_1]" in result.text
    assert "Mario Rossi" in result.text
    assert result.language.language == "en"


def test_local_only_enforced_at_construction(settings, fake_llm):
    remote = load_settings(provider="vllm", base_url="http://8.8.8.8/v1", local_only=True)
    with pytest.raises(ConfigError):
        Anonymizer(remote, llm=fake_llm)


def test_mapping_file_and_restore(settings, fake_llm, tmp_path):
    source = tmp_path / "letter.txt"
    source.write_text(LETTER, encoding="utf-8")
    anonymizer = Anonymizer(settings, llm=fake_llm)
    result = anonymizer.anonymize_file(source)
    assert result.output == tmp_path / "letter.anon.txt"

    mapping = tmp_path / "case.map.json"
    anonymizer.save_mapping(mapping)
    if os.name == "posix":
        assert stat.S_IMODE(mapping.stat().st_mode) == 0o600
    data = json.loads(mapping.read_text(encoding="utf-8"))
    person = next(e for e in data["entities"] if e["label"] == "[PERSON_1]")
    assert set(person["aliases"]) == {"Mario Rossi", "Rossi", "M. Rossi"}

    document = load_document(result.output)
    restore_document(document, load_mapping(mapping))
    assert "Firmato: Mario Rossi" in document.text()
    assert "Il signor Mario Rossi accetta" in document.text()


def test_refuses_to_overwrite_input(settings, fake_llm, tmp_path):
    source = tmp_path / "a.txt"
    source.write_text("x")
    with pytest.raises(ValueError):
        Anonymizer(settings, llm=fake_llm).anonymize_file(source, source)


# -- DOCX --------------------------------------------------------------------------------


def _add_hyperlink(paragraph, url, text):
    rel_id = paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), rel_id)
    run = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = text
    run.append(t)
    link.append(run)
    paragraph._p.append(link)


def make_docx(path):
    document = docx.Document()
    document.core_properties.author = "Mario Rossi"
    document.core_properties.title = "Contratto ACME S.p.A."
    document.sections[0].header.paragraphs[0].text = "Riservato - Giulia Bianchi"

    p = document.add_paragraph("Firmato da ")
    p.add_run("Ma").bold = True
    p.add_run("rio Ros")
    p.add_run("si").italic = True
    p.add_run(" per ACME S.p.A.")

    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Indirizzo"
    table.cell(0, 1).text = "Via Roma 12, 20121 Milano"

    link_paragraph = document.add_paragraph("Email: ")
    _add_hyperlink(link_paragraph, "mailto:mario.rossi@example.com", "mario.rossi@example.com")

    commented = document.add_paragraph("Nota per Rossi")
    document.add_comment(commented.runs, text="Chiedere a Giulia Bianchi", author="Mario Rossi")
    document.save(path)


def test_docx_in_place(settings, fake_llm, tmp_path):
    source = tmp_path / "contratto.docx"
    make_docx(source)
    result = Anonymizer(settings, llm=fake_llm).anonymize_file(source)
    assert result.output.suffix == ".docx"
    assert result.leaks == []

    out = docx.Document(str(result.output))
    signed = out.paragraphs[0]
    assert signed.text == "Firmato da [PERSON_1] per [ORG_1]"
    # Formatting of the run where the name started is kept.
    assert signed.runs[1].bold and signed.runs[1].text == "[PERSON_1]"

    assert out.sections[0].header.paragraphs[0].text == "Riservato - [PERSON_2]"
    assert out.tables[0].cell(0, 1).text == "[ADDRESS_1]"
    assert out.core_properties.author == ""
    assert out.core_properties.title == "Contratto [ORG_1]"

    targets = [r.target_ref for r in out.part.rels.values() if r.reltype == RT.HYPERLINK]
    assert targets == ["mailto:[EMAIL_1]"]

    comment = next(iter(out.comments))
    assert comment.author == "Author"
    assert comment.text == "Chiedere a [PERSON_2]"


def test_docx_to_text(settings, fake_llm, tmp_path):
    source = tmp_path / "contratto.docx"
    make_docx(source)
    output = tmp_path / "out.txt"
    Anonymizer(settings, llm=fake_llm).anonymize_file(source, output)
    text = output.read_text(encoding="utf-8")
    assert "Firmato da [PERSON_1] per [ORG_1]" in text
    assert "Rossi" not in text


# -- PDF ---------------------------------------------------------------------------------


def make_pdf(path, lines):
    """A minimal one-page PDF with a text layer."""
    content = (
        "BT /F1 12 Tf 50 750 Td 14 TL "
        + " ".join("(" + line.replace("(", r"\(").replace(")", r"\)") + ") Tj T*" for line in lines)
        + " ET"
    )
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        "/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
    ]
    out = "%PDF-1.4\n"
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{obj}\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    path.write_bytes(out.encode("latin-1"))


def test_pdf_to_text_and_docx(settings, fake_llm, tmp_path):
    source = tmp_path / "lettera.pdf"
    make_pdf(source, ["Contratto tra ACME S.p.A. e Mario Rossi.", "Firmato: M. Rossi"])

    anonymizer = Anonymizer(settings, llm=fake_llm)
    result = anonymizer.anonymize_file(source)
    assert result.output == tmp_path / "lettera.anon.txt"
    text = result.output.read_text(encoding="utf-8")
    assert "[ORG_1]" in text and "[PERSON_1]" in text and "Rossi" not in text

    as_docx = anonymizer.anonymize_file(source, tmp_path / "lettera.anon.docx")
    assert as_docx.leaks == []
    assert "Firmato: [PERSON_1]" in [p.text for p in docx.Document(str(as_docx.output)).paragraphs]


def test_text_document_preserves_line_endings(tmp_path):
    path = tmp_path / "crlf.txt"
    path.write_bytes(b"line one\r\nline two\r\n")
    assert TextDocument.load(path).text() == "line one\r\nline two\r\n"


def test_links_and_short_company_forms(settings, fake_llm):
    fake_llm.entities["NORTHWIND TRADING LTD."] = (EntityType.ORG, "NORTHWIND TRADING LTD.")
    text = (
        "A label reads NORTHWIND TRADING LTD.\nThe name NORTHWIND TRADING LTD, and the site\n"
        "https://www.example.co.uk/traders/northwind-\n100200?q=x lead to NORTHWIND TRADING.\n"
    )
    result = Anonymizer(settings, llm=fake_llm).anonymize_text(text)
    assert result.text == (
        "A label reads [ORG_1]\nThe name [ORG_1], and the site\n[URL_1] lead to [ORG_1].\n"
    )
