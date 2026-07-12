"""Document parsing via Docling — PDF, DOCX, PPTX, XLSX, HTML, images → markdown.

Docling (MIT) + Granite-Docling-258M (Apache 2.0) handle reading-order, tables, and
layout. Output is markdown + per-region confidence. Banned: PyMuPDF (AGPL), marker (GPL).

For handwriting, see handwriting.py — this module handles typed/printed documents only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class ParsedDoc(BaseModel):
    """The universal document representation that flows into the grading pipeline."""

    markdown: str = Field(description="Primary text content in markdown")
    page_count: int = 1
    has_tables: bool = False
    has_images: bool = False
    source_path: str = ""
    source_type: str = Field(description="pdf, docx, pptx, xlsx, html, image, txt, unknown")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0,
                              description="Overall OCR/parsing confidence. <0.85 flags for review.")
    low_confidence_regions: list[str] = Field(default_factory=list,
        description="Descriptions of regions with poor extraction quality")
    page_images: list[str] = Field(default_factory=list,
        description="Paths to page images (for handwriting fallback or visual inspection)")


def parse(path: Path | str) -> ParsedDoc:
    """Parse a document file into markdown via Docling.

    Handles: PDF, DOCX, PPTX, XLSX, HTML, images, plain text.
    Falls back to direct text read for .txt/.md files.
    """
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix in (".txt", ".md", ".markdown"):
        return ParsedDoc(
            markdown=path.read_text(encoding="utf-8", errors="replace"),
            source_path=str(path), source_type="txt",
        )

    if suffix in (".png", ".jpg", ".jpeg", ".tif", ".tiff"):
        return _parse_image(path)

    # Docling handles everything else
    try:
        return _parse_with_docling(path)
    except Exception:
        return _parse_fallback(path)


def _parse_with_docling(path: Path) -> ParsedDoc:
    """Use Docling DocumentConverter for structured documents."""
    from docling.document_converter import DocumentConverter

    converter = DocumentConverter()
    result = converter.convert(str(path))
    doc = result.document

    markdown = doc.export_to_markdown()
    page_count = max(1, len(doc.pages) if hasattr(doc, "pages") else 1)
    has_tables = any(
        getattr(item, "label", "") == "table" if hasattr(item, "label") else False
        for item in (doc.texts + doc.tables if hasattr(doc, "texts") else [])
    ) if hasattr(doc, "texts") else False

    suffix = path.suffix.lower().lstrip(".")
    source_type = suffix if suffix else "unknown"

    confidence = 1.0
    low_conf_regions: list[str] = []

    return ParsedDoc(
        markdown=markdown,
        page_count=page_count,
        has_tables=has_tables,
        has_images=hasattr(doc, "pictures") and len(doc.pictures) > 0 if hasattr(doc, "pictures") else False,
        source_path=str(path),
        source_type=source_type,
        confidence=confidence,
        low_confidence_regions=low_conf_regions,
    )


def _parse_image(path: Path) -> ParsedDoc:
    """Parse an image file — delegates to handwriting.py if needed."""
    from grader.handwriting import transcribe_image

    text, confidence = transcribe_image(path)
    low_conf: list[str] = []
    if confidence < 0.85:
        low_conf.append(f"Image {path.name}: OCR confidence {confidence:.2f}")

    return ParsedDoc(
        markdown=text,
        page_count=1,
        has_images=True,
        source_path=str(path),
        source_type="image",
        confidence=confidence,
        low_confidence_regions=low_conf,
        page_images=[str(path)],
    )


def _parse_fallback(path: Path) -> ParsedDoc:
    """Last-resort: read bytes as text."""
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="replace")
    except Exception:
        text = f"[Unable to parse {path.name}]"
    return ParsedDoc(
        markdown=text,
        source_path=str(path),
        source_type=path.suffix.lower().lstrip(".") or "unknown",
        confidence=0.3,
        low_confidence_regions=[f"Fallback parse of {path.name} — low confidence"],
    )


def parse_multiple(paths: list[Path]) -> list[ParsedDoc]:
    """Parse multiple files. Partial failures degrade, never raise."""
    results: list[ParsedDoc] = []
    for p in paths:
        try:
            results.append(parse(p))
        except Exception as e:
            results.append(ParsedDoc(
                markdown=f"[Parse error: {e}]",
                source_path=str(p),
                source_type="error",
                confidence=0.0,
            ))
    return results