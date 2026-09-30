"""
Text from state-filing PDFs, including scans with no text layer.

pypdf reads the text layer when there is one. Some agencies post scanned
letters instead (Alabama SHPDA CO2026-069, Maine's Eagle Arc/Links LOI), and
there's no system OCR on the machine that runs the pipeline, so scans are
sent to Claude as a PDF document block and transcribed. Only the first few
pages are sent: the cover letter and filing form carry the parties, the rest
is org charts and exhibits.
"""

import base64
import io
import logging

import anthropic
from pypdf import PdfReader, PdfWriter

from config import get_config
from pipeline.run_health import health

logger = logging.getLogger(__name__)
config = get_config()
client = anthropic.Anthropic(api_key=config.anthropic_api_key)

# Below this many characters of text layer, treat the PDF as a scan
MIN_TEXT_LAYER_CHARS = 1500

TRANSCRIBE_PROMPT = (
    "This is a regulatory filing, possibly scanned. Transcribe all of its text "
    "verbatim, page by page, as plain text. Keep names, addresses, dates, "
    "dollar amounts and ID numbers exactly as written. Output only the "
    "transcription."
)


def _first_pages(content: bytes, max_pages: int) -> tuple[PdfReader, bytes]:
    reader = PdfReader(io.BytesIO(content))
    if len(reader.pages) <= max_pages:
        return reader, content
    writer = PdfWriter()
    for page in reader.pages[:max_pages]:
        writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    return reader, out.getvalue()


def transcribe_pdf(content: bytes, label: str = "") -> str:
    """Claude transcription of a (scanned) PDF. Returns "" on failure."""
    health.attempted("PDF transcription")
    try:
        response = client.messages.create(
            model=config.claude_model,
            max_tokens=16000,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "document", "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": base64.standard_b64encode(content).decode("ascii"),
                    }},
                    {"type": "text", "text": TRANSCRIBE_PROMPT},
                ],
            }],
        )
    except anthropic.APIError as e:
        logger.warning(f"PDF transcription failed for {label}: {e}")
        health.failed("PDF transcription", f"{label}: {e}")
        return ""
    if response.stop_reason == "refusal":
        health.failed("PDF transcription", f"{label}: refused")
        return ""
    return "\n".join(b.text for b in response.content if b.type == "text").strip()


def pdf_text(content: bytes, max_pages: int = 6, label: str = "") -> tuple[str, bool]:
    """
    Text of the first max_pages pages: the text layer when it has enough in
    it, otherwise a Claude transcription. Returns (text, was_transcribed).
    """
    reader, trimmed = _first_pages(content, max_pages)
    text = "\n".join((p.extract_text() or "") for p in reader.pages[:max_pages])
    if len(text.strip()) >= MIN_TEXT_LAYER_CHARS:
        return text, False
    logger.info(f"{label}: {len(text.strip())} chars of text layer, transcribing as a scan")
    transcribed = transcribe_pdf(trimmed, label)
    return (transcribed or text), bool(transcribed)
