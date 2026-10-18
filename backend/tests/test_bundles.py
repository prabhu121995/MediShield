"""
Case bundle tests: the roll-up rules, upload checks, PDF page splitting and the create
endpoint. The database, the pipeline and the upload folder are stubbed, so these run offline.

    cd backend && ../.venv/Scripts/python.exe -m unittest tests.test_bundles -v
"""

from __future__ import annotations

import io
import pathlib
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient
from PIL import Image

from app import auth, config, uploads
from app.bundles import summarize_bundle
from app.main import app

AGENT = {"username": "agent1", "role": "agent", "full_name": "Agent One"}


def png_bytes(size=(40, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, format="PNG")
    return buf.getvalue()


def pdf_bytes(pages: int) -> bytes:
    from fpdf import FPDF

    pdf = FPDF()
    for i in range(pages):
        pdf.add_page()
        pdf.set_font("Helvetica", size=24)
        pdf.cell(0, 20, f"Page {i + 1}")
    return bytes(pdf.output())


def doc(decision=None, status="APPROVED", needs_review=False):
    return {"decision": decision, "status": status, "needs_review": needs_review}


class SummaryTests(unittest.TestCase):
    def test_empty_bundle(self):
        self.assertEqual(summarize_bundle([])["status"], "EMPTY")

    def test_all_approved(self):
        s = summarize_bundle([doc("APPROVE"), doc("APPROVE")])
        self.assertEqual((s["status"], s["decision"]), ("APPROVED", "APPROVE"))

    def test_one_reject_rejects_the_case(self):
        s = summarize_bundle([doc("APPROVE"), doc("REJECT", "REJECTED")])
        self.assertEqual((s["status"], s["decision"]), ("REJECTED", "REJECT"))

    def test_escalate_beats_reject(self):
        s = summarize_bundle([doc("REJECT", "REJECTED"), doc("ESCALATE", "NEEDS_REVIEW")])
        self.assertEqual((s["status"], s["decision"]), ("NEEDS_REVIEW", "ESCALATE"))

    def test_still_processing_has_no_decision_yet(self):
        s = summarize_bundle([doc("APPROVE"), doc(None, "PROCESSING")])
        self.assertEqual((s["status"], s["decision"], s["processing"]), ("PROCESSING", None, 1))

    def test_failed_document_keeps_the_case_open(self):
        s = summarize_bundle([doc("APPROVE"), doc(None, "FAILED")])
        self.assertEqual((s["status"], s["decision"], s["failed"]), ("FAILED", "ESCALATE", 1))


class UploadCheckTests(unittest.TestCase):
    def test_accepts_each_allowed_type(self):
        self.assertEqual(uploads.check_upload("a.png", png_bytes()), "png")
        self.assertEqual(uploads.check_upload("a.pdf", pdf_bytes(1)), "pdf")
        self.assertEqual(uploads.check_upload("a.jpg", b"\xff\xd8\xff\xe0" + b"0" * 20), "jpeg")
        self.assertEqual(uploads.check_upload("a.tiff", b"II*\x00" + b"0" * 20), "tiff")

    def test_renamed_file_is_refused(self):
        with self.assertRaises(uploads.UploadError) as cm:
            uploads.check_upload("claim.png", pdf_bytes(1))
        self.assertEqual(cm.exception.status, 415)

    def test_unknown_extension_is_refused(self):
        with self.assertRaises(uploads.UploadError):
            uploads.check_upload("notes.txt", b"hello")

    def test_oversized_file_is_refused(self):
        with mock.patch.object(uploads, "MAX_FILE_BYTES", 10):
            with self.assertRaises(uploads.UploadError) as cm:
                uploads.check_upload("a.png", png_bytes())
        self.assertEqual(cm.exception.status, 413)

    def test_pdf_is_rendered_one_png_per_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            pages = uploads.render_pdf_pages(pdf_bytes(3), pathlib.Path(tmp))
            self.assertEqual(len(pages), 3)
            for p in pages:
                self.assertEqual(Image.open(p).format, "PNG")

    def test_pdf_page_limit(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(uploads, "MAX_PDF_PAGES", 2):
            with self.assertRaises(uploads.UploadError):
                uploads.render_pdf_pages(pdf_bytes(3), pathlib.Path(tmp))
            self.assertEqual(list(pathlib.Path(tmp).iterdir()), [])   # nothing left behind

    def test_corrupt_pdf_is_422(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(uploads.UploadError) as cm:
                uploads.render_pdf_pages(b"%PDF-1.4 truncated", pathlib.Path(tmp))
        self.assertEqual(cm.exception.status, 422)


class CreateBundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bundles: dict[str, dict] = {}
        self.cases: list[dict] = []
        self.patients = {"p1": {"patient_id": "p1"}}
        self.enqueued: list[dict] = []

        def insert_bundle(bundle_id, title, patient_id, created_by):
            self.bundles[bundle_id] = {"bundle_id": bundle_id, "title": title, "patient_id": patient_id,
                                       "created_by": created_by}

        def submit(title, pages, created_by, patient_id=None):
            """Stands in for the claim service: one claim, every page queued under it."""
            bundle_id = f"b{len(self.bundles)}"
            insert_bundle(bundle_id, title, patient_id, created_by)
            self.bundles[bundle_id]["status"] = "QUEUED"
            for path, name, doc_type in pages:
                self.enqueued.append({"name": name, "doc_type": doc_type, "bundle_id": bundle_id,
                                      "patient_id": patient_id, "path": path})
                self.cases.append({"case_id": f"c{len(self.cases)}", "file_name": name,
                                   "bundle_id": bundle_id, "status": "QUEUED", "decision": None,
                                   "needs_review": False})
            return bundle_id

        fake_db = mock.Mock()
        fake_db.insert_bundle.side_effect = insert_bundle
        fake_db.get_bundle.side_effect = lambda bid, **kw: self.bundles.get(bid)
        fake_db.list_cases.side_effect = lambda bundle_id=None, **kw: [c for c in self.cases if c["bundle_id"] == bundle_id]
        fake_db.cases_for_bundles.side_effect = lambda ids: {i: [c for c in self.cases if c["bundle_id"] == i] for i in ids}
        fake_db.get_patient.side_effect = lambda pid: self.patients.get(pid)

        self.patches = [
            mock.patch("app.main.db", fake_db),
            mock.patch("app.main.claims_service.submit_claim", side_effect=submit),
            mock.patch.object(config, "UPLOAD_DIR", pathlib.Path(self.tmp.name)),
        ]
        for p in self.patches:
            p.start()
        app.dependency_overrides[auth.current_user] = lambda: AGENT
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def post(self, parts, categories=None, **form):
        """parts: list of (filename, bytes)."""
        data = {"title": "Jane Doe – March claim", **form}
        if categories is not None:
            data["categories"] = categories
        files = [("files", (name, io.BytesIO(raw), "application/octet-stream")) for name, raw in parts]
        return self.client.post("/api/claim-bundles", data=data, files=files)

    def test_one_request_creates_one_case_with_every_document(self):
        res = self.post([("claim.png", png_bytes()), ("discharge.png", png_bytes()), ("id.jpg", b"\xff\xd8\xff\xe0" + b"0" * 30)],
                        patient_id="p1")
        self.assertEqual(res.status_code, 202, res.text)
        body = res.json()
        self.assertEqual(len(body["documents"]), 3)
        self.assertEqual({e["bundle_id"] for e in self.enqueued}, {body["bundle_id"]})
        self.assertEqual(body["summary"]["status"], "QUEUED")
        self.assertEqual(body["created_by"], "agent1")

    def test_each_file_takes_its_card_category(self):
        res = self.post([("claim.png", png_bytes()), ("rx.png", png_bytes()), ("unknown.png", png_bytes())],
                        categories=["CLAIM_FORM", "PRESCRIPTION", ""])
        self.assertEqual(res.status_code, 202, res.text)
        self.assertEqual([e["doc_type"] for e in self.enqueued], ["CLAIM_FORM", "PRESCRIPTION", None])

    def test_category_count_must_match_files(self):
        res = self.post([("a.png", png_bytes()), ("b.png", png_bytes())], categories=["CLAIM_FORM"])
        self.assertEqual(res.status_code, 422)

    def test_unknown_category_is_refused(self):
        self.assertEqual(self.post([("a.png", png_bytes())], categories=["LAB_REPORT"]).status_code, 422)

    def test_pdf_is_split_into_one_document_per_page(self):
        res = self.post([("discharge.pdf", pdf_bytes(3))], categories=["DISCHARGE_SUMMARY"])
        self.assertEqual(res.status_code, 202, res.text)
        self.assertEqual(len(self.enqueued), 3)
        self.assertEqual({e["doc_type"] for e in self.enqueued}, {"DISCHARGE_SUMMARY"})
        self.assertIn("(page 2 of 3)", self.enqueued[1]["name"])
        for e in self.enqueued:
            self.assertEqual(Image.open(e["path"]).format, "PNG")

    def test_mixed_images_and_pdf_in_one_case(self):
        res = self.post([("claim.png", png_bytes()), ("rx.pdf", pdf_bytes(2))])
        self.assertEqual(res.status_code, 202, res.text)
        self.assertEqual(len(res.json()["documents"]), 3)

    def test_bad_content_rejects_the_whole_case_and_stores_nothing(self):
        res = self.post([("good.png", png_bytes()), ("fake.png", pdf_bytes(1))])
        self.assertEqual(res.status_code, 415, res.text)
        self.assertEqual(self.bundles, {})
        self.assertEqual(self.enqueued, [])
        self.assertEqual(list(pathlib.Path(self.tmp.name).iterdir()), [])

    def test_too_many_pages_is_refused_before_creating_the_case(self):
        with mock.patch("app.main.MAX_BUNDLE_DOCUMENTS", 2):
            res = self.post([("a.png", png_bytes()), ("b.pdf", pdf_bytes(2))])
        self.assertEqual(res.status_code, 422)
        self.assertEqual(self.bundles, {})
        self.assertEqual(list(pathlib.Path(self.tmp.name).iterdir()), [])

    def test_blank_title_gets_a_default(self):
        res = self.post([("a.png", png_bytes()), ("b.png", png_bytes())], title=" ")
        self.assertEqual(res.status_code, 202, res.text)
        self.assertRegex(res.json()["title"], r"^Claim \d{4}-\d{2}-\d{2} \(2 documents\)$")

    def test_overlong_title_is_refused(self):
        self.assertEqual(self.post([("a.png", png_bytes())], title="x" * 161).status_code, 422)

    def test_unknown_patient_is_404(self):
        self.assertEqual(self.post([("a.png", png_bytes())], patient_id="nope").status_code, 404)

    def test_no_patient_is_allowed(self):
        res = self.post([("a.png", png_bytes())])
        self.assertEqual(res.status_code, 202)
        self.assertIsNone(res.json()["patient_id"])

    def test_list_honours_limit_and_loads_pages_in_one_call(self):
        from app import main
        main.db.list_bundles.side_effect = lambda **kw: [{"bundle_id": "b1", "status": "APPROVED", "decision": "APPROVE",
                                                          "created_at": "2025-01-01"}]
        main.db.cases_for_bundles.side_effect = lambda ids: {i: [] for i in ids}
        res = self.client.get("/api/claim-bundles?limit=5")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(main.db.list_bundles.call_args.kwargs["limit"], 5)
        main.db.cases_for_bundles.assert_called_once_with(["b1"])

    def test_queues_filter_by_final_decision(self):
        from app import main
        main.db.list_bundles.side_effect = lambda **kw: []
        main.db.cases_for_bundles.side_effect = lambda ids: {}
        for name, expected in (("approved", {"decision": "APPROVE"}), ("rejected", {"decision": "REJECT"}),
                               ("review", {"status": "NEEDS_REVIEW", "oldest_first": True})):
            self.assertEqual(self.client.get(f"/api/queues/{name}").status_code, 200)
            kwargs = main.db.list_bundles.call_args.kwargs
            self.assertEqual({k: kwargs[k] for k in expected}, expected)
        self.assertEqual(self.client.get("/api/queues/other").status_code, 422)

    def test_unknown_bundle_is_404(self):
        self.assertEqual(self.client.get("/api/claim-bundles/missing").status_code, 404)


if __name__ == "__main__":
    unittest.main()
