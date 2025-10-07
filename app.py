# app.py
from fastapi import FastAPI, Response, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel, Field
from sqlmodel import select  # keep if you use it elsewhere
import os, io, zipfile, logging, glob, base64
from typing import Dict, Tuple, Optional, List
import pandas as pd

# ========= CONFIG =========
USE_MOCK = os.getenv("USE_MOCK", "false").strip().lower() == "true"
DATASET_DIRS: List[str] = ["dataset", "archive", "archive(1)"]
SHOW_SAMPLE_LINES = True  # set to False if you want ZERO sample rows in PDF
# =========================

# Manual overrides (these win over CSVs)
# TIP: put real party names here if you know them
OVERRIDE_CASES: Dict[int, dict] = {
    8152: {
        "parties": "Alice Sharma vs State of Odisha",  # 👈 put the actual names here
        "filing_date": "2020",
        "next_hearing": "2025-11-15",
        "status": "Disposed",
        "raw_source": {"url": "https://ndap.niti.gov.in"},
        "sample_data": {
            "Rowid": "4",
            "Country": "India",
            "State lgd code": "21",
            "State": "Odisha",
            "Year": "2020",
            "Sector": "Agriculture",
            "District": "Khordha",
            "Gender": "Male",
            "Category": "Rural",
        },
    }
}

# Optional scraper (often blocked by captchas)
try:
    from scraper.ecourts import fetch_case_details  # noqa: F401
except Exception:
    fetch_case_details = None

# DB bits
from storage.db import init_db, get_session
from models import QueryLog

logger = logging.getLogger("court_app")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

app = FastAPI(title="Court Fetcher API")

# CORS (dev)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

# Ensure downloads dir exists and mount for previews
os.makedirs("downloads", exist_ok=True)
app.mount("/downloads", StaticFiles(directory="downloads"), name="downloads")

# ======== Pretty PDF creation (ReportLab if available) ========
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import simpleSplit
    REPORTLAB_OK = True
except Exception:
    REPORTLAB_OK = False


def _create_pdf_with_details(path: str, title: str, details: dict):
    """Create a readable PDF; fallback to minimal PDF if reportlab missing."""
    if not REPORTLAB_OK:
        content = f"""%PDF-1.4
1 0 obj<<>>endobj
2 0 obj<< /Length 44 >>stream
BT /F1 24 Tf 72 720 Td ({title}) Tj ET
endstream endobj
3 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj
4 0 obj<< /Type /Page /Parent 5 0 R /Resources << /Font << /F1 3 0 R >> >> /Contents 2 0 R /MediaBox [0 0 612 792] >>endobj
5 0 obj<< /Type /Pages /Kids [4 0 R] /Count 1 >>endobj
6 0 obj<< /Type /Catalog /Pages 5 0 R >>endobj
xref
0 7
0000000000 65535 f 
0000000010 00000 n 
0000000053 00000 n 
0000000161 00000 n 
0000000228 00000 n 
0000000412 00000 n 
0000000473 00000 n 
trailer<< /Size 7 /Root 6 0 R >>
startxref
533
%%EOF"""
        with open(path, "w", encoding="latin-1") as f:
            f.write(content)
        return

    c = canvas.Canvas(path, pagesize=A4)
    W, H = A4
    y = H - 50
    c.setFont("Helvetica-Bold", 18); c.drawString(40, y, title); y -= 30
    c.setFont("Helvetica", 11)

    def kv(lbl, val):
        nonlocal y
        if y < 80:
            c.showPage(); y = H - 50; c.setFont("Helvetica", 11)
        txt = f"{lbl}: {('N/A' if val in (None, '') else str(val))}"
        for line in simpleSplit(txt, "Helvetica", 11, W - 80):
            c.drawString(40, y, line); y -= 16

    details = details or {}

    # -- Only print standard fields; "Sample Data" title is REMOVED --
    kv("Parties", details.get("parties"))
    kv("Filing Date", details.get("filing_date"))
    kv("Next Hearing", details.get("next_hearing"))
    kv("Status", details.get("status"))
    src = (details.get("raw_source") or {}).get("url", "")
    if src: kv("Source", src)

    # If you still want some extra lines, print them quietly (no heading)
    sample = details.get("sample_data") or {}
    if SHOW_SAMPLE_LINES and sample:
        for k, v in list(sample.items())[:12]:
            kv(k, v)

    c.showPage(); c.save()
# =============================================================

# ---------- helpers ----------
def _as_error(msg: str, where: str = "scraper"):
    return {"parties":"N/A","filing_date":"N/A","next_hearing":"N/A",
            "status":"Error","error":{"where":where,"message":msg},
            "raw_source":{"url":""}}

def _ensure_min_fields(details: dict) -> dict:
    if not isinstance(details, dict) or not details:
        details = _as_error("Empty response", "server")
    details.setdefault("parties","N/A"); details.setdefault("filing_date","N/A")
    details.setdefault("next_hearing","N/A"); details.setdefault("status","N/A")
    details.setdefault("raw_source",{"url":""})
    return details

def _ensure_logo_png():
    """Create a tiny placeholder logo at /downloads/logo.png (1x1 transparent PNG)."""
    logo_path = os.path.join("downloads", "logo.png")
    if os.path.exists(logo_path):
        return
    tiny_png_b64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMA"
        "ASsJTYQAAAAASUVORK5CYII="
    )
    with open(logo_path, "wb") as f:
        f.write(base64.b64decode(tiny_png_b64))

@app.get("/chrome.devtools.json", include_in_schema=False)
def _devtools_noise(): return Response(status_code=204)

@app.get("/favicon.ico", include_in_schema=False)
def favicon(): return Response(status_code=204)

# ---------- dataset loader ----------
DATASET_ROWS: Dict[Tuple[int,int], dict] = {}
CASE_INDEX: Dict[int,Tuple[int,int]] = {}
DATASET_SUMMARY: Dict[int,int] = {}
SCANNED_DIRS: List[str] = []

def _scan_dirs() -> List[str]:
    return [d for d in DATASET_DIRS if os.path.isdir(d)]

def _best_parties_from_row(data: dict) -> Optional[str]:
    """
    Try to produce human 'party names' from a CSV row.
    Falls back to any of these columns if present:
      - case_title / title / parties
      - petitioner + respondent
      - plaintiff + defendant
    """
    if not data:
        return None
    # direct titles
    for key in ["case_title", "title", "parties", "case name", "case_name"]:
        v = data.get(key) or data.get(key.title()) or data.get(key.upper())
        if v and str(v).strip():
            return str(v).strip()
    # petitioner/respondent
    pet = data.get("petitioner") or data.get("Petitioner") or data.get("PETITIONER")
    res = data.get("respondent") or data.get("Respondent") or data.get("RESPONDENT")
    if pet or res:
        return f"{str(pet or 'Petitioner').strip()} vs {str(res or 'Respondent').strip()}"
    # plaintiff/defendant
    pl = data.get("plaintiff") or data.get("Plaintiff") or data.get("PLAINTIFF")
    df = data.get("defendant") or data.get("Defendant") or data.get("DEFENDANT")
    if pl or df:
        return f"{str(pl or 'Plaintiff').strip()} vs {str(df or 'Defendant').strip()}"
    return None

def _load_ndap_datasets() -> int:
    global DATASET_ROWS, CASE_INDEX, DATASET_SUMMARY, SCANNED_DIRS
    DATASET_ROWS.clear(); CASE_INDEX.clear(); DATASET_SUMMARY.clear()
    SCANNED_DIRS = _scan_dirs()
    total = 0
    for folder in SCANNED_DIRS:
        for report_path in glob.glob(os.path.join(folder, "NDAP_REPORT_*.csv")):
            fname = os.path.basename(report_path)
            try:
                code = int(fname.split("_")[-1].split(".")[0])
            except Exception:
                logger.warning("Skip: cannot parse code from %s", report_path); continue
            try:
                df = pd.read_csv(report_path)
            except Exception as e:
                logger.warning("Failed to read %s: %s", report_path, e); continue
            if df.empty: continue
            DATASET_SUMMARY[code] = len(df); total += len(df)
            cols = list(df.columns)
            year_col = next((c for c in cols if "year" in c.lower()), None)
            for idx, row in df.iterrows():
                row_dict = {c: ("" if pd.isna(row[c]) else str(row[c])) for c in cols}
                DATASET_ROWS[(code, idx)] = {"dataset_code":code,"row_index":idx,"data":row_dict,"year_col":year_col}
                if idx == 0: CASE_INDEX[code] = (code, 0)
                CASE_INDEX[code + idx] = (code, idx)
    logger.info("Loaded rows=%s | datasets=%s | scanned=%s", total, DATASET_SUMMARY, SCANNED_DIRS)
    return total

def _dataset_lookup_by_case(case_number: int) -> Optional[dict]:
    # 1) manual overrides first
    if case_number in OVERRIDE_CASES:
        return OVERRIDE_CASES[case_number]

    # 2) CSV-backed mapping
    key = CASE_INDEX.get(int(case_number))
    if not key: return None
    code, idx = key
    row = DATASET_ROWS.get((code, idx)) or {}
    data = row.get("data") or {}; year_col = row.get("year_col")

    # derive party names nicely
    parties_name = _best_parties_from_row(data)
    if not parties_name:
        # fallback to readable placeholder if nothing in CSV:
        parties_name = f"Case {code}-{idx}"

    result = {
        "parties": parties_name,
        "filing_date": (data.get(year_col) if year_col else "N/A") or "N/A",
        "next_hearing": data.get("next_hearing") or "N/A",
        "status": data.get("status") or "From NDAP Dataset",
        "raw_source": {"url": "https://ndap.niti.gov.in"},
    }
    # keep a few columns in API (optional)
    result["sample_data"] = {k: data[k] for k in list(data.keys())[:5]}
    return result

# ---------- startup ----------
@app.on_event("startup")
def _startup():
    init_db()
    _ensure_logo_png()
    count = _load_ndap_datasets()
    logger.info("DB initialized; USE_MOCK=%s; ndap_rows=%s", USE_MOCK, count)

# ---------- schema ----------
class CaseQuery(BaseModel):
    case_type: str = Field(..., min_length=1)
    case_number: int = Field(..., gt=0)
    year: int = Field(..., ge=1950, le=2100)
    court_level: str = Field(..., min_length=1)

# ---------- utility routes ----------
@app.get("/ping")
def ping():
    return {"status":"ok","mock":USE_MOCK,"ndap_rows":len(DATASET_ROWS),
            "datasets":DATASET_SUMMARY,"scanned_dirs":SCANNED_DIRS}

@app.get("/datasets/list")
def list_datasets():
    return {"datasets":DATASET_SUMMARY,
            "examples":[{"case_number":code,"maps_to":f"{code}/row0"}
                        for code in sorted(DATASET_SUMMARY.keys())][:10]}

@app.post("/admin/dataset/reload")
def reload_dataset():
    count = _load_ndap_datasets()
    return {"ok": True, "rows": count, "datasets": DATASET_SUMMARY}

# ---------- main lookup ----------
@app.post("/cases/lookup")
def lookup_case(q: CaseQuery):
    details = _dataset_lookup_by_case(q.case_number)
    source_kind = "dataset" if details else None

    if not details:
        if USE_MOCK:
            details = {"parties": f"{q.case_type} Demo: Alice vs Bob",
                       "filing_date": "2023-07-18",
                       "next_hearing": "2025-10-10",
                       "status": "Listed",
                       "raw_source": {"url": "https://services.ecourts.gov.in"}}
            source_kind = "mock"
        else:
            if fetch_case_details is None:
                details = _as_error("scraper not available", "import")
            else:
                try:
                    d = fetch_case_details(q.case_type, q.case_number, q.year, q.court_level)
                    details = d if isinstance(d, dict) and d else _as_error("Unexpected scraper response","scraper")
                except Exception as e:
                    logger.exception("Lookup failed")
                    details = _as_error(str(e), "exception")
            source_kind = "live"

    details = _ensure_min_fields(details)

    # Always rebuild the judgment PDF with current details (no stale files)
    j_path = f"downloads/judgment_{q.case_number}.pdf"
    _create_pdf_with_details(j_path, f"Judgment for {q.case_number}", details)

    # Cause list: create once if missing
    c_path = "downloads/cause_list_demo.pdf"
    if not os.path.exists(c_path):
        _create_pdf_with_details(c_path, "Cause List Demo",
                                 {"status":"Generated","raw_source":{"url":"https://example.invalid"}})

    # Log query
    status_flag = "ok" if details.get("status") != "Error" else "error"
    with get_session() as ses:
        ses.add(QueryLog(case_type=q.case_type, case_number=q.case_number, year=q.year,
                         court_level=q.court_level, status=status_flag,
                         source_url=details.get("raw_source",{}).get("url",""),
                         html_preview=str(details)[:500]))
        ses.commit()

    return {"input": q.model_dump(),
            "parsed": details,
            "source": source_kind,
            "documents": [
                {"kind":"judgment","file_name":os.path.basename(j_path),
                 "download_url": f"/dl/file/{os.path.basename(j_path)}"},
                {"kind":"cause_list","file_name":os.path.basename(c_path),
                 "download_url": f"/dl/file/{os.path.basename(c_path)}"},
            ]}

# ---------- dedicated download (no cache; always attachment) ----------
@app.get("/dl/file/{fname}")
def dl_file(fname: str):
    """
    Force download. If it's a judgment_<num>.pdf, regenerate it with the
    best available details so you never get an empty placeholder.
    """
    path = os.path.join("downloads", fname)

    # If it's a judgment PDF, parse number and regenerate with details
    if fname.startswith("judgment_") and fname.endswith(".pdf"):
        try:
            num = int(fname[len("judgment_"):-4])
        except Exception:
            raise HTTPException(status_code=400, detail="Bad judgment file name")

        details = _dataset_lookup_by_case(num)
        if not details:
            if USE_MOCK:
                details = {
                    "parties": "CIVIL Demo: Alice vs Bob",
                    "filing_date": "2023-07-18",
                    "next_hearing": "2025-10-10",
                    "status": "Listed",
                    "raw_source": {"url": "https://services.ecourts.gov.in"},
                }
            else:
                details = _as_error("No dataset/mocked details available", "server")

        _create_pdf_with_details(path, f"Judgment for {num}", details)

    else:
        # Known demo file; create if missing
        if fname == "cause_list_demo.pdf" and not os.path.exists(path):
            _create_pdf_with_details(path, "Cause List Demo", {"status": "Generated"})
        elif not os.path.exists(path):
            raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(
        path,
        media_type="application/pdf",
        filename=fname,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )

# ---------- UI ----------
@app.get("/", response_class=FileResponse)
def ui(): 
    return FileResponse("index.html")
