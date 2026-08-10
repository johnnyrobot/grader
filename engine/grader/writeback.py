"""Annotated feedback write-back — comments in .docx, .pdf, .xlsx.

NEVER mutate the original. Always write a copy. Teacher previews before save/post.
Banned: PyMuPDF (AGPL). Use pypdf (BSD) for PDF annotations.

python-docx v1.2.0+ has native add_comment() — real margin comments anchored to runs.
pypdf add_annotation() — sticky notes / FreeText.
openpyxl cell Comment.
docxtpl — Jinja2-templated feedback report as fallback.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from pydantic import BaseModel, Field


class Comment(BaseModel):
    """A single comment to anchor in a document."""

    text: str = Field(description="The comment text shown to the student")
    anchor_text: str | None = Field(default=None,
        description="Text in the document to anchor this comment to. "
                    "If None, comment is general (not anchored to a specific passage).")
    author: str = "Grader"
    page: int | None = Field(default=None, description="For PDF: 1-based page number")
    cell_ref: str | None = Field(default=None, description="For XLSX: cell reference like 'B3'")


class AnnotatedDoc(BaseModel):
    """Result of an annotation operation."""

    output_path: str
    comments_added: int
    original_untouched: bool = True


def annotate_docx(src: Path | str, dest: Path | str, comments: list[Comment]) -> AnnotatedDoc:
    """Add margin comments to a .docx copy using python-docx.

    python-docx >= 1.2.0 has add_comment() for real margin comments anchored to runs.
    If anchor_text is provided, the comment is anchored to the first occurrence.
    If None, the comment is placed at the end of the document.
    """
    import docx

    src = Path(src)
    dest = Path(dest)
    shutil.copy2(src, dest)

    doc = docx.Document(str(dest))
    count = 0

    for comment in comments:
        try:
            if comment.anchor_text:
                # Find the anchor text in the document paragraphs
                found = False
                for para in doc.paragraphs:
                    if comment.anchor_text in para.text:
                        if hasattr(doc, "add_comment"):
                            doc.add_comment(para, text=comment.text, author=comment.author)
                        else:
                            # Fallback: insert as inline text
                            run = para.add_run(f" [Comment: {comment.text}]")
                            run.font.color.rgb = None  # type: ignore[attr-defined]
                        found = True
                        count += 1
                        break
                if not found:
                    # Append to end of document
                    para = doc.add_paragraph()
                    if hasattr(doc, "add_comment"):
                        doc.add_comment(para, text=comment.text, author=comment.author)
                    else:
                        para.add_run(f"[Comment: {comment.text}]")
                    count += 1
            else:
                # General comment at end
                para = doc.add_paragraph()
                if hasattr(doc, "add_comment"):
                    doc.add_comment(para, text=comment.text, author=comment.author)
                else:
                    para.add_run(f"[Comment: {comment.text}]")
                count += 1
        except Exception:
            # If add_comment fails, append as paragraph text
            doc.add_paragraph(f"[Comment from {comment.author}: {comment.text}]")
            count += 1

    doc.save(str(dest))
    return AnnotatedDoc(output_path=str(dest), comments_added=count)


def annotate_pdf(src: Path | str, dest: Path | str, comments: list[Comment]) -> AnnotatedDoc:
    """Add sticky-note annotations to a .pdf copy using pypdf (BSD, NOT PyMuPDF).

    pypdf add_annotation() supports Text (sticky note) and Popup annotations.
    """
    from pypdf import PdfReader, PdfWriter
    from pypdf.annotations import FreeText, Text

    src = Path(src)
    dest = Path(dest)

    reader = PdfReader(str(src))
    writer = PdfWriter()
    writer.append(reader)

    count = 0
    for comment in comments:
        page_idx = (comment.page - 1) if comment.page else 0
        if page_idx >= len(writer.pages):
            page_idx = len(writer.pages) - 1

        try:
            annotation = Text(text=comment.text, rect=(50, 750, 200, 770))
            writer.add_annotation(page_number=page_idx, annotation=annotation)
            count += 1
        except Exception:
            # Fallback: FreeText draws the comment onto the page itself, which
            # every reader renders — a sticky note is the nicer of the two.
            try:
                annotation = FreeText(
                    text=f"{comment.author}: {comment.text}",
                    rect=(50, 700, 400, 730),
                )
                writer.add_annotation(page_number=page_idx, annotation=annotation)
                count += 1
            except Exception:
                pass

    with open(dest, "wb") as f:
        writer.write(f)

    return AnnotatedDoc(output_path=str(dest), comments_added=count)


def annotate_xlsx(src: Path | str, dest: Path | str, comments: list[Comment]) -> AnnotatedDoc:
    """Add cell comments to a .xlsx copy using openpyxl."""
    from openpyxl import load_workbook
    from openpyxl.comments import Comment as XlComment

    src = Path(src)
    dest = Path(dest)
    shutil.copy2(src, dest)

    wb = load_workbook(str(dest))
    ws = wb.active

    count = 0
    for comment in comments:
        try:
            cell_ref = comment.cell_ref or "A1"
            cell = ws[cell_ref]
            cell.comment = XlComment(comment.text, comment.author)
            count += 1
        except Exception:
            pass

    wb.save(str(dest))
    return AnnotatedDoc(output_path=str(dest), comments_added=count)


def feedback_report(
    dest: Path | str,
    *,
    draft_text: str,
    rubric_title: str,
    scores: dict[str, float],
    feedback: str,
    total: float,
    max_total: float,
) -> AnnotatedDoc:
    """Generate a standalone feedback report as .docx using python-docx.

    This is the fallback when the original file format doesn't support comments
    or when the teacher wants a separate feedback document.
    """
    import docx

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    doc = docx.Document()

    # Title
    doc.add_heading(f"Feedback: {rubric_title}", level=1)

    # Scores table
    doc.add_heading("Scores", level=2)
    table = doc.add_table(rows=1, cols=3)
    table.style = "Table Grid"
    hdr = table.rows[0].cells
    hdr[0].text = "Criterion"
    hdr[1].text = "Score"
    hdr[2].text = "Max"

    for cid, score in scores.items():
        row = table.add_row().cells
        row[0].text = cid
        row[1].text = f"{score:g}"
        row[2].text = "—"

    # Total
    total_row = table.add_row().cells
    total_row[0].text = "Total"
    total_row[1].text = f"{total:g}"
    total_row[2].text = f"{max_total:g}"

    # Feedback
    doc.add_heading("Feedback", level=2)
    doc.add_paragraph(feedback)

    doc.save(str(dest))
    return AnnotatedDoc(output_path=str(dest), comments_added=0)


def annotate(src: Path | str, dest: Path | str, comments: list[Comment]) -> AnnotatedDoc:
    """Auto-dispatch based on file extension. Always writes a copy."""
    src = Path(src)
    suffix = src.suffix.lower()

    if suffix == ".docx":
        return annotate_docx(src, dest, comments)
    elif suffix == ".pdf":
        return annotate_pdf(src, dest, comments)
    elif suffix == ".xlsx":
        return annotate_xlsx(src, dest, comments)
    else:
        # Unsupported format → generate a feedback report instead
        return feedback_report(
            dest,
            draft_text="",
            rubric_title="Graded Feedback",
            scores={},
            feedback="\n".join(c.text for c in comments),
            total=0,
            max_total=0,
        )