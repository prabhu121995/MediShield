"""
Classify, then preprocess + extract, every dataset page once, so later runs hit the cache.
Resumable: pages already cached for this model and rule set are skipped.

    cd backend && ../.venv/Scripts/python.exe -m scripts.warm_vision                # every page
    ../.venv/Scripts/python.exe -m scripts.warm_vision --folders claim_forms id_documents
"""

from __future__ import annotations

import logging

import argparse
import time

from app import config
from app import logdb
from app.claim.vision import classify, extract, transcribe


log = logging.getLogger("medishield.warm_vision")


def main() -> None:
    logdb.install("warm_vision")      # logs go to MySQL (app_log)
    ap = argparse.ArgumentParser()
    ap.add_argument("--folders", nargs="*", help="dataset sub-folders to read (default: all)")
    args = ap.parse_args()
    images = sorted(p for p in config.DATASET_DIR.rglob("*")
                    if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".tif", ".tiff"} and p.stat().st_size
                    and (not args.folders or p.parent.name in args.folders))
    t0 = time.perf_counter()
    for i, path in enumerate(images, start=1):
        t = time.perf_counter()
        try:
            kind = classify(str(path))                    # classify
            text = transcribe(str(path))                  # preprocess + extract
            extract(text["text"], kind["doc_type"])
            note = f"{kind['doc_type']:18} conf={kind['confidence']:.2f} quality={text['image_quality']} chars={len(text['text'])}"
        except Exception as exc:                  # a bad page or a model error: report and move on
            note = f"FAILED {type(exc).__name__}: {exc}"
        log.info(f"[{i:3}/{len(images)}] {path.name:36} {time.perf_counter() - t:6.1f}s {note}")
    log.info(f"done: {len(images)} pages in {time.perf_counter() - t0:.0f}s ({config.VISION_MODEL_NAME})")


if __name__ == "__main__":
    main()
