"""Free, offline OCR cascade for clear and difficult scans.

Born-digital PDFs use embedded text. Scans try PaddleOCR, RapidOCR and Tesseract
with raw and OpenCV-preprocessed images. Passport MRZs can additionally be read
from an enlarged lower-page crop when full-page OCR misses name fields.
"""
from __future__ import annotations
import io
from pathlib import Path


def pdf_digital_text(path: str | Path) -> str:
    try:
        try:
            import pymupdf as fitz
        except ImportError:
            import fitz
        doc = fitz.open(str(path))
        return "\n".join(p.get_text() for p in doc).strip()
    except Exception:
        return ""


def render_pages(path: str | Path, dpi: int = 300) -> list[bytes]:
    """PDF/image -> list of PNG bytes (one per page)."""
    p = Path(path)
    if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp"}:
        return [p.read_bytes()]
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    doc = fitz.open(str(p))
    return [page.get_pixmap(dpi=dpi).tobytes("png") for page in doc]


def preprocess(png: bytes) -> bytes:
    """OpenCV clean-up; no-op if OpenCV is unavailable."""
    try:
        import cv2, numpy as np
        img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape
        if w < 1800:
            gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        gray = cv2.fastNlMeansDenoising(gray, None, 10, 7, 21)
        thr = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
        coords = cv2.findNonZero(thr)
        if coords is not None:
            angle = cv2.minAreaRect(coords)[-1]
            angle = -(90 + angle) if angle < -45 else -angle
            if 0.4 < abs(angle) < 12:
                cx, cy = gray.shape[1] // 2, gray.shape[0] // 2
                matrix = cv2.getRotationMatrix2D((cx, cy), angle, 1.0)
                gray = cv2.warpAffine(gray, matrix, (gray.shape[1], gray.shape[0]),
                                      flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
        out = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                    cv2.THRESH_BINARY, 31, 10)
        ok, buf = cv2.imencode(".png", out)
        return buf.tobytes() if ok else png
    except Exception:
        return png


def _ocr_paddle(png: bytes):
    from paddleocr import PaddleOCR
    import numpy as np, cv2
    global _PADDLE
    try: _PADDLE
    except NameError: _PADDLE = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)
    img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    res = _PADDLE.ocr(img, cls=True)
    lines, confs = [], []
    for page in (res or []):
        for _box, (txt, conf) in (page or []):
            lines.append(txt); confs.append(float(conf))
    return "\n".join(lines), (sum(confs) / len(confs) if confs else 0.0)


def _ocr_rapid(png: bytes):
    """Run RapidOCR locally; supports modern RapidOCR 3.x and legacy API."""
    import numpy as np, cv2
    global _RAPID, _RAPID_API
    try:
        _RAPID
    except NameError:
        try:
            from rapidocr import RapidOCR
            _RAPID = RapidOCR(); _RAPID_API = "modern"
        except ImportError:
            from rapidocr_onnxruntime import RapidOCR
            _RAPID = RapidOCR(); _RAPID_API = "legacy"
    if _RAPID_API == "modern":
        result = _RAPID(png)
        texts = list(getattr(result, "txts", None) or [])
        scores = list(getattr(result, "scores", None) or [])
        return "\n".join(texts), (sum(float(x) for x in scores) / len(scores) if scores else 0.0)
    img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    res, _ = _RAPID(img)
    if not res: return "", 0.0
    res.sort(key=lambda r: (r[0][0][1], r[0][0][0]))
    return "\n".join(r[1] for r in res), sum(float(r[2]) for r in res) / len(res)


def _ocr_tesseract(png: bytes):
    import pytesseract
    from PIL import Image
    img = Image.open(io.BytesIO(png))
    data = pytesseract.image_to_data(img, lang="eng", output_type=pytesseract.Output.DICT)
    confs = [int(c) for c in data["conf"] if str(c).lstrip("-").isdigit() and int(c) >= 0]
    text = pytesseract.image_to_string(img, lang="eng")
    return text, (sum(confs) / len(confs) / 100 if confs else 0.0)


ENGINES = [("paddleocr", _ocr_paddle), ("rapidocr", _ocr_rapid), ("tesseract", _ocr_tesseract)]


def ocr_mrz_region(path: str | Path) -> dict:
    """OCR the enlarged MRZ strip at the bottom of each passport page.

    This targeted second pass is only called when ordinary extraction leaves a
    name blank or low-confidence. A parsed pair of MRZ lines is ranked by
    ICAO check digits first, then field completeness and OCR confidence.
    """
    try:
        import cv2, numpy as np
        from .mrz import parse_mrz_text
        best = {"text": "", "confidence": 0.0, "engine": "none"}
        best_score = (-1, -1, -1.0)
        for png in render_pages(path):
            image = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
            if image is None: continue
            height, width = image.shape[:2]
            crop = image[int(height * 0.60):, :]
            if crop.size == 0: continue
            target_w = max(width, 2200)
            crop = cv2.resize(crop, (target_w, max(1, int(crop.shape[0] * target_w / crop.shape[1]))),
                              interpolation=cv2.INTER_CUBIC)
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            variants = [crop]
            for method in (cv2.THRESH_BINARY + cv2.THRESH_OTSU,
                           cv2.THRESH_BINARY + cv2.THRESH_TRIANGLE):
                binary = cv2.threshold(gray, 0, 255, method)[1]
                ok, encoded = cv2.imencode(".png", binary)
                if ok: variants.append(encoded.tobytes())
            ok, encoded = cv2.imencode(".png", crop)
            if not ok: continue
            variants[0] = encoded.tobytes()
            for engine_name, engine in ENGINES:
                for variant_i, data in enumerate(variants):
                    try: text, conf = engine(data)
                    except Exception: continue
                    parsed = parse_mrz_text(text)
                    if not parsed: continue
                    checks = sum(bool(v) for v in parsed["checks"].values())
                    names = int(bool(parsed["surname"])) + int(bool(parsed["given_names"]))
                    score = (checks, names, float(conf))
                    if score > best_score:
                        best_score = score
                        best = {"text": text, "confidence": float(conf),
                                "engine": f"{engine_name}/mrz-{variant_i}"}
                    if checks == 3 and names == 2: return best
        return best
    except Exception:
        return {"text": "", "confidence": 0.0, "engine": "none"}


def ocr_image(png: bytes, good_enough: float = 0.82) -> dict:
    attempts = []
    for name, fn in ENGINES:
        for variant, data in (("raw", png), ("processed", preprocess(png))):
            try:
                text, conf = fn(data)
            except Exception as e:
                attempts.append({"engine": name, "variant": variant, "error": str(e)[:160]})
                break
            attempts.append({"engine": name, "variant": variant, "confidence": round(conf, 3),
                             "chars": len(text), "text": text})
            if conf >= good_enough and len(text) > 40:
                return {"text": text, "confidence": conf, "engine": f"{name}/{variant}",
                        "attempts": [{k: v for k, v in a.items() if k != "text"} for a in attempts]}
    ok = [a for a in attempts if "text" in a]
    if ok:
        best = max(ok, key=lambda a: (a["confidence"], a["chars"]))
        return {"text": best["text"], "confidence": best["confidence"],
                "engine": f"{best['engine']}/{best['variant']}",
                "attempts": [{k: v for k, v in a.items() if k != "text"} for a in attempts]}
    return {"text": "", "confidence": 0.0, "engine": "none", "attempts": attempts,
            "hint": "No OCR engine available. Install one: pip install -r requirements-ocr.txt (RapidOCR is easiest), or install Tesseract 5."}


def extract_text(path: str | Path, force_ocr: bool = False) -> dict:
    """Extract text page-by-page, OCRing mixed PDFs and pages with no text layer.

    Many CV/application PDFs combine one born-digital page with scanned image
    pages. A document-wide text-length shortcut silently discarded those image
    pages. We now retain digital text per page and OCR the missing/weak pages;
    identity/application workflows may set force_ocr to compare OCR against a
    poor or stale PDF text layer.
    """
    p = Path(path)
    is_image = p.suffix.lower() in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp"}
    if is_image:
        page_bytes = render_pages(p)
        pages, texts = [], []
        for png in page_bytes:
            result = ocr_image(png)
            pages.append(result); texts.append(result.get("text", ""))
        conf = sum(x.get("confidence", 0.0) for x in pages) / len(pages) if pages else 0.0
        return {"text": "\n".join(texts), "confidence": conf,
                "engine": pages[0].get("engine", "none") if pages else "none", "pages": pages}

    try:
        try:
            import pymupdf as fitz
        except ImportError:
            import fitz
        doc = fitz.open(str(p))
        texts, page_results, engines, confidences = [], [], [], []
        for page in doc:
            digital = (page.get_text() or "").strip()
            if digital and not force_ocr and len(digital) >= 120:
                texts.append(digital)
                page_results.append({"text": digital, "confidence": 1.0, "engine": "pdf-text"})
                engines.append("pdf-text"); confidences.append(1.0)
                continue
            # OCR short/missing text-layer pages; force_ocr additionally audits
            # digital pages that may have a misleading or low-quality hidden layer.
            png = page.get_pixmap(dpi=300).tobytes("png")
            result = ocr_image(png)
            ocr_text = result.get("text", "")
            combined = "\n".join(x for x in (digital, ocr_text) if x).strip()
            texts.append(combined)
            result["text"] = combined
            if digital and ocr_text:
                result["engine"] = "pdf-text+" + result.get("engine", "ocr")
            page_results.append(result)
            engines.append(result.get("engine", "none"))
            confidences.append(result.get("confidence", 0.0))
        conf = sum(confidences) / len(confidences) if confidences else 0.0
        engine = engines[0] if engines and len(set(engines)) == 1 else ("mixed" if engines else "none")
        return {"text": "\n".join(texts), "confidence": conf, "engine": engine, "pages": page_results}
    except Exception:
        # Non-PDF or malformed file: keep the prior renderer fallback.
        pages, texts = [], []
        for png in render_pages(p):
            result = ocr_image(png)
            pages.append(result); texts.append(result.get("text", ""))
        conf = sum(x.get("confidence", 0.0) for x in pages) / len(pages) if pages else 0.0
        return {"text": "\n".join(texts), "confidence": conf,
                "engine": pages[0].get("engine", "none") if pages else "none", "pages": pages}
