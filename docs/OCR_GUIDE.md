# OCR guide — powerful + completely free

## Cascade (app/ocr/engine.py) — local OCR with page-by-page PDF handling
1. **Born-digital PDF pages** use embedded text when it is present and substantial.
2. **Scanned or weak-text pages** are OCR'd with RapidOCR 3.x / PP-OCRv6; passport, application, police, and policy uploads also run OCR even if a hidden PDF text layer exists.
3. **Mixed PDFs** keep digital text on readable pages and OCR image-only pages. This prevents a text-heavy first page from causing later scanned pages (including phone/contact pages) to be skipped.
4. **Passport MRZ pass** repairs common OCR confusion at the initial `P<` marker and checks the two-line MRZ; a lower-page crop is used when name fields are missing/uncertain.
5. **Ollama + local vision model** remains an optional fallback for low-confidence fields. Its model can be selected in `config.yaml`.

RapidOCR is free and local. Docling supports multiple OCR engines/layout processing, but the issue reproduced in the stored documents was the old whole-PDF shortcut, so page-aware OCR plus passport-specific parsing was implemented and verified first. DeepSeek-OCR is not forced into the core requirements because its official setup depends on a separate CUDA/PyTorch/vLLM stack; it can be evaluated as an optional backend on a GPU-equipped machine.

## Accuracy design (why "even if details are not clear" is handled safely)
- **Passport MRZ check digits**: check digits validate passport number, birth date, and expiry; they do **not** validate surname/given names. Names are read from the MRZ and cross-checked against visible surname/given-name labels; mismatches are flagged for human review.
- **Police reports**: address fragments are reassembled (your red-circled city is OCR'd as a
  separate line — the system stitches it back and matches the dropdown city). Tested on your
  two samples: both NO/STREET/CITY correct with RapidOCR.
- **Extension policy documents** → EXTEND single and batch uploads accept the existing insurance certificate; OCR extracts the labeled policy number, associates batch files by passport number (upload order only when counts match), and Review allows correction before approval. Allianz writes this value to `E-TRAVEL POLICY NO`.
- **Application phones**: phone-labelled candidates outrank arbitrary digit strings. If multiple unlabelled phone-shaped values appear, the field is left blank instead of guessing; Review allows manual correction.
- **Mixed batch uploads**: the batch picker accepts one or many separate document files. A local OCR classifier groups files into passport, police, applicant/phone, and EXTEND policy categories before the existing batch extractors run. Classification is best-effort; low-confidence filename/type labels are shown, so verify the grouped files and Review data before approval. After extraction, Clear uploaded source documents removes that batch's source scans while retaining any linked passport copy used by the Email attachment checkbox.
- **Final review**: the insurer’s actual row is the editable source of truth. Every cell in the generated insurer row is editable there and the same saved overrides flow into Excel.
- A stored CV sample has no detectable mobile number; the importer leaves it blank. Another scanned application sample has a labelled phone, which the OCR path now extracts.
- **Confidence per field** → Final check highlights fields below the configured threshold. Nothing is approved until the user checks the complete insurer row.

## Tips for better scans
300 dpi, flat (not photographed at an angle), include the full passport MRZ (bottom 2 lines),
avoid writing/circling on the copy you scan (circle on a *separate* copy if you must).

## Police reports: guaranteed on every upload
`pipeline._best_police_read()` runs for single add, Bulk add, Review-page re-upload and the MCP
tool `extract_police_report`. First OCR pass → if no dropdown CITY matched, every engine ×
{raw, processed} variant is retried and the best CITY-scoring read wins. Address extraction
tolerates: Passport-no *or* NIC variants, misread "Address" labels (structural block fallback),
city circled/OCR'd as orphan lines (before or after the address line), no house number,
multi-word cities, province/postal-code tails, and letter/digit confusions (DANK0TUWA, 2O3/C).
Verified end-to-end by `scripts/e2e_police_upload.py` + `tests/test_police_any.py`. Passport MRZ name fields are cross-checked against printed visual-name OCR; mismatches are lowered below the Review threshold because ICAO MRZ check digits do not validate names.
