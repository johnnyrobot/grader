"""Handwriting transcription — transcribe, then grade.

P0 finding: the VLM must NOT grade directly. Transcribe to text, then run the normal
grade() path. One grading path, one calibration mechanism, handwritten or typed.

Stack:
  1. Apple Vision (VNRecognizeTextRequest via ocrmac) — free, on-device, strong on print.
  2. grader-vision:1.3b for messy/cursive — ~6s/page (NOT qwen3-vl — see P0 findings).
  3. Return text + confidence. Low confidence regions flagged for teacher review.

Transcription must be editable before grading. Scanned work defaults to REVIEW_FIRST.
arXiv 2606.11477: VLMs hit 98.4% on handwritten exam answers vs 88-91% for detector
pipelines, because they read in context. But we still transcribe first — the grading
model (Qwen3 8B text) is better at rubric reasoning than the VLM.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def transcribe_image(
    path: Path | str,
    *,
    model: str = "grader-vision:1.3b",
) -> tuple[str, float]:
    """Transcribe a single image to text with confidence score.

    Returns (text, confidence). Confidence < 0.85 flags the item for teacher review.
    """
    path = Path(path)
    if not path.exists():
        return f"[Image not found: {path}]", 0.0

    # Layer 1: Apple Vision (fast, free, on-device)
    text, confidence = _apple_vision(path)
    if confidence >= 0.85:
        return text, confidence

    # Layer 2: grader-vision:1.3b for messy/cursive regions
    vlm_text, vlm_conf = _vlm_transcribe(path, model=model)
    if vlm_conf > confidence:
        return vlm_text, vlm_conf

    return text, confidence


def _apple_vision(path: Path) -> tuple[str, float]:
    """Apple Vision OCR via ocrmac. Free, on-device, strong on printed text."""
    try:
        from ocrmac import ocrmac

        result = ocrmac.OCR(str(path), recognition_level="accurate")
        texts = [item[0] for item in result if item[0].strip()]
        text = "\n".join(texts)

        # Apple Vision doesn't return a confidence score directly.
        # Use text length as a heuristic: empty or very short = low confidence.
        if not text.strip():
            return "", 0.0
        if len(text.strip()) < 10:
            return text, 0.5

        # Check for garbled output (high ratio of non-alphanumeric = likely bad OCR)
        alpha_ratio = sum(c.isalnum() or c.isspace() for c in text) / len(text)
        confidence = min(1.0, alpha_ratio * 1.15)
        return text, confidence
    except Exception:
        return "", 0.0


def _vlm_transcribe(path: Path, *, model: str = "grader-vision:1.3b") -> tuple[str, float]:
    """Use grader-vision model (MiniCPM-V or similar) via Ollama for cursive/handwriting.

    ~6s/page. NOT qwen3-vl — that model emits 20k+ chars of un-disableable reasoning
    (140s/item vs MiniCPM-V 5.8s). See P0 findings and AGENTS.md anti-patterns.
    """
    try:
        import httpx

        # Use a configured grader-vision capped variant, never a raw model tag.
        prompt = (
            "Transcribe this handwritten text exactly as written. "
            "Output ONLY the transcribed text, nothing else. "
            "If the image is not handwriting or contains no text, output '[no text found]'."
        )

        import base64
        img_data = base64.b64encode(path.read_bytes()).decode()

        with httpx.Client(timeout=120) as client:
            r = client.post(
                "http://localhost:11434/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "images": [img_data],
                    "stream": False,
                    "options": {"temperature": 0, "num_predict": 2000},
                },
            )
            r.raise_for_status()
            text = r.json().get("response", "").strip()

        if not text or text == "[no text found]":
            return "", 0.0

        # VLM confidence heuristic: reasonable text length + no excessive repetition
        words = text.split()
        if len(words) < 3:
            return text, 0.4
        unique_ratio = len(set(words)) / max(len(words), 1)
        confidence = min(0.95, 0.6 + unique_ratio * 0.3)
        return text, confidence
    except Exception:
        return "", 0.0


def transcribe_pages(
    page_paths: list[Path],
    *,
    min_confidence: float = 0.85,
    model: str = "grader-vision:1.3b",
) -> dict[str, Any]:
    """Transcribe multiple pages. Returns combined text + per-page confidence + flags.

    Returns:
        text: concatenated transcription
        avg_confidence: mean confidence across pages
        low_confidence_pages: list of page indices with confidence < min_confidence
        needs_review: True if any page is below threshold
    """
    results = []
    for p in page_paths:
        text, conf = transcribe_image(p, model=model)
        results.append({"text": text, "confidence": conf, "path": str(p)})

    combined_text = "\n\n--- Page break ---\n\n".join(r["text"] for r in results)
    avg_conf = sum(r["confidence"] for r in results) / max(len(results), 1)
    low_conf_pages = [i for i, r in enumerate(results) if r["confidence"] < min_confidence]

    return {
        "text": combined_text,
        "avg_confidence": avg_conf,
        "low_confidence_pages": low_conf_pages,
        "needs_review": len(low_conf_pages) > 0,
        "page_results": results,
    }
