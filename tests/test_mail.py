from email.message import EmailMessage

import pytest

from eoncloak import Anonymizer, EntityType, load_document
from eoncloak.documents.mail import html_to_text
from tests.msgbuilder import build_msg

BODY = "Ciao Giulia,\nin allegato il contratto di ACME S.p.A.\nA presto,\nMario Rossi\n"


def write_eml(path, *, html=False):
    message = EmailMessage()
    message["From"] = "Mario Rossi <mario.rossi@example.com>"
    message["To"] = "Giulia Bianchi <giulia.bianchi@example.com>"
    message["Cc"] = "=?utf-8?q?Luca_Verd=C3=AC?= <luca@example.com>"
    message["Subject"] = "Contratto ACME S.p.A."
    message["Date"] = "Mon, 26 Oct 2026 10:00:00 +0100"
    message["Received"] = "from mail.example.com (10.1.2.3)"
    message["Message-ID"] = "<abc@mail.example.com>"
    if html:
        message.set_content(
            "<p>Ciao <b>Giulia</b>,</p><p>firma: Mario&nbsp;Rossi</p>", subtype="html"
        )
    else:
        message.set_content(BODY)
    message.add_attachment(b"%PDF-1.4", maintype="application", subtype="pdf", filename="Rossi.pdf")
    path.write_bytes(message.as_bytes())


def test_eml_flattened(tmp_path):
    path = tmp_path / "mail.eml"
    write_eml(path)
    text = load_document(path).text()
    assert text.startswith(
        "From: Mario Rossi <mario.rossi@example.com>\n"
        "To: Giulia Bianchi <giulia.bianchi@example.com>\n"
        "Cc: Luca Verdì <luca@example.com>\n"
        "Date: Mon, 26 Oct 2026 10:00:00 +0100\n"
        "Subject: Contratto ACME S.p.A.\n"
        "Attachments: Rossi.pdf\n\n"
        "Ciao Giulia,"
    )
    # Routing headers are dropped.
    assert "10.1.2.3" not in text and "Message-ID" not in text


def test_eml_html_body(tmp_path):
    path = tmp_path / "mail.eml"
    write_eml(path, html=True)
    assert load_document(path).text().endswith("Ciao Giulia,\n\nfirma: Mario Rossi\n")


def make_msg(path, **overrides):
    fields = dict(
        subject="Contratto ACME S.p.A.",
        sender_name="Mario Rossi",
        sender_email="mario.rossi@example.com",
        recipients=[
            ("Giulia Bianchi", "giulia.bianchi@example.com", 1),
            ("Luca Verdi", "luca@example.com", 2),
            ("Archivio", "archivio@example.com", 3),
        ],
        body=BODY,
        attachments=["contratto Rossi.pdf"],
        headers="Date: Mon, 26 Oct 2026 10:00:00 +0100\r\nReceived: from x (10.1.2.3)\r\n",
    )
    fields.update(overrides)
    path.write_bytes(build_msg(**fields))


def test_msg_flattened(tmp_path):
    path = tmp_path / "mail.msg"
    make_msg(path)
    assert load_document(path).text() == (
        "From: Mario Rossi <mario.rossi@example.com>\n"
        "To: Giulia Bianchi <giulia.bianchi@example.com>\n"
        "Cc: Luca Verdi <luca@example.com>\n"
        "Bcc: Archivio <archivio@example.com>\n"
        "Date: Mon, 26 Oct 2026 10:00:00 +0100\n"
        "Subject: Contratto ACME S.p.A.\n"
        "Attachments: contratto Rossi.pdf\n\n" + BODY
    )


def test_msg_html_only_body(tmp_path):
    path = tmp_path / "mail.msg"
    make_msg(
        path, body="", html="<html><head><style>p{}</style></head><body><p>Caro Luca</p></body>"
    )
    assert load_document(path).text().endswith("\n\nCaro Luca\n")


def test_msg_long_body(tmp_path):
    # Bodies over 4 KiB live in regular sectors instead of the mini stream.
    path = tmp_path / "mail.msg"
    make_msg(path, body="Mario Rossi. " * 1000)
    assert load_document(path).text().count("Mario Rossi") == 1001


def test_not_a_msg(tmp_path):
    path = tmp_path / "fake.msg"
    path.write_text("hello")
    with pytest.raises(ValueError):
        load_document(path)


def test_msg_anonymized_to_text(settings, fake_llm, tmp_path):
    fake_llm.entities["Luca Verdi"] = (EntityType.PERSON, "Luca Verdi")
    fake_llm.entities["Giulia"] = (EntityType.PERSON, "Giulia Bianchi")
    path = tmp_path / "mail.msg"
    make_msg(path)
    result = Anonymizer(settings, llm=fake_llm).anonymize_file(path)
    assert result.output == tmp_path / "mail.anon.txt"
    assert result.leaks == []
    text = result.output.read_text(encoding="utf-8")
    assert text.startswith(
        "From: [PERSON_1] <[EMAIL_1]>\nTo: [PERSON_2] <[EMAIL_2]>\nCc: [PERSON_3] <[EMAIL_3]>\n"
    )
    assert "Subject: Contratto [ORG_1]\nAttachments: contratto [PERSON_1].pdf" in text
    assert text.endswith(
        "Ciao [PERSON_2],\nin allegato il contratto di [ORG_1]\nA presto,\n[PERSON_1]\n"
    )


def test_html_to_text_entities_and_tables():
    html = "<table><tr><td>A&amp;B</td><td>C</td></tr></table><script>x()</script>Fine"
    assert html_to_text(html) == "A&B\tC\n\nFine"
