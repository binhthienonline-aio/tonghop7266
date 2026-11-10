from __future__ import annotations

import hashlib
import io
import re
import zipfile
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np
import pandas as pd
import pytesseract
import streamlit as st
from PIL import Image, ImageOps
from pytesseract import Output
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

APP_TITLE = "ĐMX SMART REPORT AI"
MAX_FILES = 30
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_ZIP_BYTES = 25 * 1024 * 1024
MAX_BATCH_EXPANDED_BYTES = 60 * 1024 * 1024
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}

st.set_page_config(page_title=APP_TITLE, page_icon="📊", layout="wide")

def require_password() -> None:
    """Simple shared-password gate. Configure APP_PASSWORD in Streamlit Secrets."""
    try:
        expected = st.secrets.get("APP_PASSWORD", "")
    except Exception:
        expected = ""
    if not expected:
        st.error("Ứng dụng chưa được cấu hình mật khẩu. Chủ ứng dụng hãy thêm APP_PASSWORD trong Streamlit → App settings → Secrets.")
        st.code('APP_PASSWORD = "thay-bang-mat-khau-rieng-manh"')
        st.stop()
    if st.session_state.get("authenticated"):
        return
    st.title("🔐 ĐMX SMART REPORT AI")
    pwd = st.text_input("Mật khẩu truy cập", type="password")
    if st.button("Đăng nhập", type="primary"):
        if pwd and pwd == expected:
            st.session_state["authenticated"] = True
            st.rerun()
        st.error("Mật khẩu chưa đúng.")
    st.caption("Đây là lớp khóa dùng chung cơ bản, không thay thế hệ thống tài khoản/phân quyền doanh nghiệp.")
    st.stop()

def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def safe_name(name: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return (base or "image")[:100]

def load_image_items(uploaded_files: list[Any], zip_upload: Any | None) -> list[dict]:
    items: list[dict] = []
    for f in uploaded_files:
        raw = f.getvalue()
        if len(raw) > MAX_IMAGE_BYTES:
            st.warning(f"Bỏ qua {f.name}: ảnh vượt 8 MB.")
            continue
        items.append({"name": f.name, "bytes": raw, "source": "Tải trực tiếp"})
    if zip_upload is not None:
        zraw = zip_upload.getvalue()
        if len(zraw) > MAX_ZIP_BYTES:
            raise ValueError("File ZIP vượt 25 MB.")
        expanded_total = 0
        with zipfile.ZipFile(io.BytesIO(zraw)) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                suffix = "." + info.filename.lower().rsplit(".", 1)[-1] if "." in info.filename else ""
                if suffix not in ALLOWED_EXT:
                    continue
                if info.file_size > MAX_IMAGE_BYTES:
                    st.warning(f"Bỏ qua {info.filename}: ảnh vượt 8 MB.")
                    continue
                expanded_total += info.file_size
                if expanded_total > MAX_BATCH_EXPANDED_BYTES:
                    raise ValueError("Tổng dung lượng ảnh sau giải nén vượt 60 MB. Hãy chia ZIP thành nhiều lượt.")
                raw = zf.read(info)
                items.append({"name": info.filename, "bytes": raw, "source": "ZIP"})
                if len(items) > MAX_FILES:
                    raise ValueError("Tổng số ảnh vượt 30. Hãy chia thành nhiều lượt.")
    if len(items) > MAX_FILES:
        raise ValueError("Tổng số ảnh vượt 30. Hãy chia thành nhiều lượt.")
    if not items:
        raise ValueError("Chưa có ảnh hợp lệ. Hỗ trợ JPG, PNG, WEBP, BMP, TIF.")
    # Duplicate bytes are flagged, not silently removed.
    seen: dict[str, str] = {}
    for item in items:
        digest = sha256(item["bytes"])
        item["sha256"] = digest
        item["duplicate_of"] = seen.get(digest, "")
        seen.setdefault(digest, item["name"])
    return items

def fetch_public_drive_file(url: str) -> dict:
    """Download a public/shared Google Drive file only; private Drive OAuth is not implemented."""
    import gdown
    url = url.strip()
    if not url:
        raise ValueError("Chưa nhập link Google Drive.")
    # Folder downloads are intentionally not automatic to avoid fetching an unknown number of files.
    if "/folders/" in url:
        raise ValueError("Bản V1 chỉ nhận link file ảnh Google Drive được chia sẻ công khai. Link thư mục/private Drive cần tích hợp OAuth riêng.")
    out = gdown.download(url=url, quiet=True, fuzzy=True)
    if not out:
        raise ValueError("Không tải được file. Kiểm tra link chia sẻ công khai hoặc tải ảnh trực tiếp.")
    from pathlib import Path
    p = Path(out)
    raw = p.read_bytes()
    if p.suffix.lower() not in ALLOWED_EXT:
        raise ValueError("File Drive tải về không phải định dạng ảnh được hỗ trợ.")
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("Ảnh Drive vượt 8 MB.")
    return {"name": p.name, "bytes": raw, "source": "Google Drive công khai", "sha256": sha256(raw), "duplicate_of": ""}

def decode_image(raw: bytes) -> np.ndarray:
    arr = np.frombuffer(raw, dtype=np.uint8)
    image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Không giải mã được ảnh. Kiểm tra định dạng hoặc file hỏng.")
    return image

def deskew(gray: np.ndarray) -> np.ndarray:
    # Estimate skew using foreground pixels; conservative limit to avoid harmful rotations.
    inv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    coords = np.column_stack(np.where(inv > 0))
    if len(coords) < 100:
        return gray
    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle
    if abs(angle) > 7 or abs(angle) < 0.15:
        return gray
    h, w = gray.shape[:2]
    matrix = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
    return cv2.warpAffine(gray, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)

def preprocess_variants(bgr: np.ndarray) -> list[tuple[str, np.ndarray]]:
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    gray = deskew(gray)
    h, w = gray.shape[:2]
    scale = 2.0 if max(h, w) < 2600 else 1.35
    enlarged = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(enlarged)
    otsu = cv2.threshold(clahe, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    adaptive = cv2.adaptiveThreshold(clahe, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 11)
    return [("Ảnh xám tăng nét", enlarged), ("CLAHE + Otsu", otsu), ("CLAHE + Adaptive", adaptive)]

def run_ocr(image: np.ndarray, psm: int) -> tuple[str, float, list[dict]]:
    config = f"--oem 1 --psm {psm}"
    data = pytesseract.image_to_data(image, lang="vie+eng", config=config, output_type=Output.DICT)
    words: list[dict] = []
    confs: list[float] = []
    n = len(data["text"])
    for i in range(n):
        token = str(data["text"][i]).strip()
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1
        if token and conf >= 0:
            confs.append(conf)
            words.append({
                "text": token, "confidence": round(conf, 1),
                "left": int(data["left"][i]), "top": int(data["top"][i]),
                "width": int(data["width"][i]), "height": int(data["height"][i]),
                "block": int(data["block_num"][i]), "paragraph": int(data["par_num"][i]),
                "line": int(data["line_num"][i]), "word": int(data["word_num"][i]),
            })
    # Keep OCR's original line breaks from image_to_string as a second trace.
    text = pytesseract.image_to_string(image, lang="vie+eng", config=config)
    avg = float(np.mean(confs)) if confs else 0.0
    return text, avg, words

def process_one(item: dict) -> dict:
    bgr = decode_image(item["bytes"])
    variants = preprocess_variants(bgr)
    attempts = []
    # Test two common page segmentation modes on each preprocessing variant.
    for variant_name, img in variants:
        for psm in (6, 11):
            text, confidence, words = run_ocr(img, psm)
            attempts.append({
                "variant": variant_name, "psm": psm, "text": text,
                "confidence": confidence, "words": words
            })
    # Confidence is a ranking aid only, not a correctness score.
    best = max(attempts, key=lambda x: (x["confidence"], len(x["text"].strip())))
    warnings = []
    if best["confidence"] < 75:
        warnings.append("Độ tin cậy OCR trung bình thấp (<75)")
    if not best["text"].strip():
        warnings.append("Không nhận diện được văn bản")
    if item.get("duplicate_of"):
        warnings.append("Ảnh trùng nội dung byte với: " + item["duplicate_of"])
    if re.search(r"\b\d{1,3}(?:[.,]\d{3})+\b", best["text"]):
        warnings.append("Có số dạng hàng nghìn: kiểm tra dấu chấm/phẩy và từng chữ số")
    if re.search(r"[Il|][0-9]|[0-9][OQ]", best["text"]):
        warnings.append("Có cụm ký tự dễ nhầm giữa chữ và số (I/l/1, O/0)")
    if len([x for x in best["text"].splitlines() if x.strip()]) <= 1:
        warnings.append("Chỉ nhận diện được tối đa một dòng; kiểm tra vùng cắt và chất lượng ảnh")
    return {
        "name": item["name"], "source": item["source"], "sha256": item["sha256"],
        "text": best["text"], "confidence": round(best["confidence"], 1),
        "variant": best["variant"], "psm": best["psm"],
        "warnings": warnings, "words": best["words"], "attempts": attempts,
        "reviewed": False, "review_note": ""
    }

def style_sheet(ws, freeze="A2", filter_on=True):
    ws.freeze_panes = freeze
    if filter_on and ws.max_row >= 1:
        ws.auto_filter.ref = ws.dimensions
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="075DA8")
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 30

def build_suggestions(results: list[dict]) -> list[dict]:
    """Deterministic review suggestions; deliberately does not rewrite source figures."""
    suggestions = []
    for r in results:
        name = r.get("name", "")
        text = r.get("text", "")
        warnings = r.get("warnings", [])
        conf = float(r.get("confidence", 0))
        if conf < 75:
            suggestions.append({
                "MỨC ƯU TIÊN": "CAO", "ẢNH / NGUỒN": name,
                "PHÁT HIỆN": f"Confidence OCR thấp ({conf:.1f}%).",
                "ĐỀ XUẤT": "Mở ảnh gốc, phóng to vùng bảng và chọn lại phương án OCR khác. Không dùng số liệu từ ảnh này khi chưa kiểm tra.",
                "TỰ ĐỘNG ĐIỀU CHỈNH": "KHÔNG – cần người rà soát"
            })
        if r.get("duplicate_of"):
            suggestions.append({
                "MỨC ƯU TIÊN": "TRUNG BÌNH", "ẢNH / NGUỒN": name,
                "PHÁT HIỆN": "Nội dung file trùng byte với một ảnh khác.",
                "ĐỀ XUẤT": f"Kiểm tra ảnh này có phải bản chụp lặp không. So sánh với {r['duplicate_of']} trước khi cộng dữ liệu.",
                "TỰ ĐỘNG ĐIỀU CHỈNH": "KHÔNG – không tự xóa ảnh"
            })
        if re.search(r"\b\d{1,3}(?:[.,]\d{3})+\b", text):
            suggestions.append({
                "MỨC ƯU TIÊN": "CAO", "ẢNH / NGUỒN": name,
                "PHÁT HIỆN": "Có chuỗi số có dấu chấm/phẩy phân tách hàng nghìn.",
                "ĐỀ XUẤT": "Đối chiếu từng chữ số với ảnh gốc; xác định rõ dấu phân cách hàng nghìn/thập phân theo định dạng báo cáo.",
                "TỰ ĐỘNG ĐIỀU CHỈNH": "KHÔNG – không tự chuyển kiểu số"
            })
        if re.search(r"[Il|][0-9]|[0-9][OQ]", text):
            suggestions.append({
                "MỨC ƯU TIÊN": "CAO", "ẢNH / NGUỒN": name,
                "PHÁT HIỆN": "Có ký tự dễ nhầm giữa chữ và số (I/l/1 hoặc O/0).",
                "ĐỀ XUẤT": "Kiểm tra mã hàng, mã nhân viên và các ô số có ký tự tương tự. Giữ nguyên dạng văn bản nếu là mã định danh.",
                "TỰ ĐỘNG ĐIỀU CHỈNH": "KHÔNG – cần xác minh"
            })
        if len([x for x in text.splitlines() if x.strip()]) <= 1:
            suggestions.append({
                "MỨC ƯU TIÊN": "CAO", "ẢNH / NGUỒN": name,
                "PHÁT HIỆN": "OCR chỉ nhận diện tối đa một dòng.",
                "ĐỀ XUẤT": "Kiểm tra ảnh có bị cắt, xoay, mờ hoặc bảng quá nhỏ. Chụp lại thẳng góc, đủ sáng và bao trọn bảng.",
                "TỰ ĐỘNG ĐIỀU CHỈNH": "KHÔNG – cần kiểm tra ảnh"
            })
        if not warnings and conf >= 75:
            suggestions.append({
                "MỨC ƯU TIÊN": "THẤP", "ẢNH / NGUỒN": name,
                "PHÁT HIỆN": "Không phát hiện cảnh báo OCR cơ bản.",
                "ĐỀ XUẤT": "Vẫn đối chiếu tổng dòng/cột và các con số quan trọng với ảnh gốc. Không xem đây là xác nhận dữ liệu đúng.",
                "TỰ ĐỘNG ĐIỀU CHỈNH": "KHÔNG – chỉ gợi ý"
            })
    if not suggestions:
        suggestions.append({
            "MỨC ƯU TIÊN": "THÔNG TIN", "ẢNH / NGUỒN": "Toàn bộ lô",
            "PHÁT HIỆN": "Chưa có kết quả OCR để phân tích.",
            "ĐỀ XUẤT": "Nạp ảnh và chạy OCR trước.",
            "TỰ ĐỘNG ĐIỀU CHỈNH": "CHƯA THỰC HIỆN"
        })
    return suggestions

def make_workbook(results: list[dict], reviewed_df: pd.DataFrame, suggestions: list[dict], user_feedback: str) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "TONG_HOP"
    headers = ["STT", "TÊN ẢNH", "NGUỒN", "SHA256", "OCR CONFIDENCE (%)", "PHƯƠNG ÁN OCR", "PSM", "SỐ TỪ", "CẢNH BÁO", "ĐÃ RÀ SOÁT"]
    ws.append(headers)
    for i, r in enumerate(results, 1):
        ws.append([i, r["name"], r["source"], r["sha256"], r["confidence"], r["variant"], r["psm"],
                   len(r["words"]), " | ".join(r["warnings"]) or "Không có cảnh báo cơ bản", "CÓ" if r["reviewed"] else "CHƯA"])
    style_sheet(ws)
    widths = [8, 32, 22, 66, 18, 22, 8, 10, 65, 15]
    for i, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = width

    review = wb.create_sheet("RA_SOAT")
    review.append(["STT", "TÊN ẢNH", "VĂN BẢN OCR ĐÃ CHỈNH SỬA", "ĐÃ ĐỐI CHIẾU ẢNH GỐC", "GHI CHÚ NGƯỜI RÀ SOÁT"])
    for i, row in reviewed_df.iterrows():
        review.append([int(i) + 1, str(row.get("TÊN ẢNH", "")), str(row.get("VĂN BẢN OCR", "")),
                       "CÓ" if bool(row.get("ĐÃ XÁC NHẬN", False)) else "CHƯA", str(row.get("GHI CHÚ", ""))])
    style_sheet(review)
    review.column_dimensions["A"].width = 8
    review.column_dimensions["B"].width = 32
    review.column_dimensions["C"].width = 100
    review.column_dimensions["D"].width = 24
    review.column_dimensions["E"].width = 45
    for row in review.iter_rows(min_row=2, min_col=3, max_col=3):
        row[0].alignment = Alignment(wrap_text=True, vertical="top")

    words = wb.create_sheet("OCR_TUNG_TU")
    words.append(["ẢNH", "TỪ/KÝ TỰ OCR", "CONFIDENCE (%)", "LEFT", "TOP", "WIDTH", "HEIGHT", "BLOCK", "PARAGRAPH", "LINE", "WORD"])
    for r in results:
        for w in r["words"]:
            words.append([r["name"], w["text"], w["confidence"], w["left"], w["top"], w["width"], w["height"],
                          w["block"], w["paragraph"], w["line"], w["word"]])
    style_sheet(words)
    for col, width in zip("ABCDEFGHIJK", [32, 24, 16, 12, 12, 12, 12, 10, 12, 10, 10]):
        words.column_dimensions[col].width = width

    qa = wb.create_sheet("CANH_BAO")
    qa.append(["MỨC", "ẢNH", "NỘI DUNG CẢNH BÁO", "HÀNH ĐỘNG"])
    for r in results:
        if not r["warnings"]:
            qa.append(["THAM KHẢO", r["name"], "Không phát hiện cảnh báo cơ bản; không đồng nghĩa dữ liệu đúng", "Vẫn đối chiếu ảnh gốc"])
        else:
            for warning in r["warnings"]:
                qa.append(["CẦN KIỂM TRA", r["name"], warning, "Mở ảnh gốc và kiểm tra thủ công"])
    style_sheet(qa)
    for col, width in zip("ABCD", [18, 32, 70, 45]):
        qa.column_dimensions[col].width = width

    ai = wb.create_sheet("AI_GOP_Y")
    ai.append(["MỨC ƯU TIÊN", "ẢNH / NGUỒN", "PHÁT HIỆN", "ĐỀ XUẤT ĐIỀU CHỈNH", "TỰ ĐỘNG ĐIỀU CHỈNH"])
    for item in suggestions:
        ai.append([item.get("MỨC ƯU TIÊN", ""), item.get("ẢNH / NGUỒN", ""), item.get("PHÁT HIỆN", ""),
                   item.get("ĐỀ XUẤT", ""), item.get("TỰ ĐỘNG ĐIỀU CHỈNH", "")])
    ai.append([])
    ai.append(["GÓP Ý / YÊU CẦU ĐIỀU CHỈNH DO NGƯỜI DÙNG NHẬP", user_feedback or "KHÔNG CÓ DATA"])
    style_sheet(ai, filter_on=False)
    for col, width in zip("ABCDE", [18, 32, 55, 95, 32]):
        ai.column_dimensions[col].width = width
    for row in ai.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")

    guide = wb.create_sheet("DOC_TRUOC_KHI_DUNG")
    guide.append(["MỤC", "NỘI DUNG"])
    notes = [
        ("Phạm vi", "OCR miễn phí bằng Tesseract 5 với ngôn ngữ vie+eng; không phải AI hiểu nghiệp vụ."),
        ("Độ tin cậy", "Confidence chỉ dùng để xếp hạng phương án OCR; không chứng minh số liệu chính xác."),
        ("Nguồn gốc", "SHA256 giúp nhận diện file trùng byte; không xác thực nguồn/chủ sở hữu."),
        ("Số liệu", "Mọi số tiền, %, ngày, mã hàng và số 0 đầu phải đối chiếu ảnh gốc."),
        ("Cột bảng", "Không tự suy diễn cấu trúc cột. Sheet OCR_TUNG_TU lưu tọa độ để hỗ trợ truy vết."),
        ("Đầu ra", "RA_SOAT chứa văn bản đã chỉnh sửa và trạng thái xác nhận do người dùng nhập."),
        ("Bảo mật", "File được xử lý trên máy chủ Streamlit Cloud; không tải dữ liệu nội bộ nếu chưa được cho phép."),
    ]
    for x in notes:
        guide.append(list(x))
    style_sheet(guide, filter_on=False)
    guide.column_dimensions["A"].width = 22
    guide.column_dimensions["B"].width = 115
    for row in guide.iter_rows(min_row=2):
        row[1].alignment = Alignment(wrap_text=True, vertical="top")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

def main() -> None:
    require_password()
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Be+Vietnam+Pro:wght@400;500;600;700;800&display=swap');
    html, body, [class*="css"] {font-family:'Be Vietnam Pro',Arial,sans-serif;}
    .stApp {background:linear-gradient(180deg,#f4f7fb 0%,#eef3f8 100%);}
    [data-testid="stHeader"] {background:rgba(255,255,255,0.85);}
    [data-testid="stSidebar"] {background:#102d4d;}
    [data-testid="stSidebar"] * {color:#f4f8fc !important;}
    h1,h2,h3 {font-weight:800 !important; letter-spacing:-.35px; color:#102d4d;}
    p, label, .stMarkdown {color:#20364d;}
    [data-testid="stMetric"] {background:#fff;border:1px solid #dce5ef;padding:16px 18px;border-radius:14px;box-shadow:0 5px 18px rgba(20,50,80,.06);}
    [data-testid="stMetricLabel"] p {font-weight:700;color:#53677d;}
    [data-testid="stMetricValue"] {font-weight:800;color:#075da8;}
    div.stButton > button, div.stDownloadButton > button {border-radius:10px;font-weight:800;border:0;padding:.62rem 1rem;box-shadow:0 3px 10px rgba(0,75,135,.12);}
    div.stButton > button[kind="primary"], div.stDownloadButton > button[kind="primary"] {background:#0874c9;color:#fff;}
    div.stButton > button:hover, div.stDownloadButton > button:hover {border:0;filter:brightness(1.06);}
    [data-testid="stFileUploader"] {background:#fff;border:1.5px dashed #9cb4cb;border-radius:13px;padding:12px;}
    [data-testid="stExpander"] {background:#fff;border:1px solid #dce5ef;border-radius:12px;}
    [data-testid="stTabs"] button {font-weight:800;}
    [data-testid="stAlert"] {border-radius:12px;}
    .hero {background:linear-gradient(115deg,#082d50,#075da8 70%,#0b83c6);padding:24px 26px;border-radius:18px;color:white;margin:4px 0 18px;box-shadow:0 12px 28px rgba(5,57,100,.18);}
    .hero h1 {color:white !important;margin:0;font-size:29px;letter-spacing:-.6px;}
    .hero p {color:#e5f3ff;margin:7px 0 0;font-size:14px;font-weight:500;}
    .eyebrow {color:#ffdf00;font-weight:800;font-size:11px;letter-spacing:1.8px;text-transform:uppercase;margin-bottom:8px;}
    .section-kicker {font-size:12px;font-weight:800;color:#0874c9;letter-spacing:1px;text-transform:uppercase;}
    .small-note {color:#63758b;font-size:12px;}
    </style>
    <div class="hero">
      <div class="eyebrow">ĐIỆN MÁY XANH · SMART DATA WORKFLOW</div>
      <h1>ĐMX SMART REPORT AI</h1>
      <p>Đọc báo cáo có kiểm soát · Rà soát minh bạch · Xuất Excel có truy vết</p>
    </div>
    """, unsafe_allow_html=True)
    st.caption("FREE V1 · OCR tiếng Việt/Anh · Giao diện tối ưu dễ đọc · Rà soát thủ công · Xuất Excel nhiều Sheet")
    with st.sidebar:
        st.markdown("## ĐMX SMART REPORT")
        st.markdown("**Bảng điều khiển**")
        st.markdown("---")
        st.markdown("**Quy trình xử lý**")
        st.markdown("1. Nạp ảnh báo cáo")
        st.markdown("2. Chạy OCR nhiều phương án")
        st.markdown("3. Đối chiếu ảnh gốc")
        st.markdown("4. Nhận góp ý điều chỉnh")
        st.markdown("5. Xuất Excel")
        st.markdown("---")
        st.caption("FREE MODE · Không gọi API AI trả phí")
        if st.button("Đăng xuất", use_container_width=True):
            st.session_state["authenticated"] = False
            st.rerun()
    st.warning("Ưu tiên chính xác: hệ thống không tự kết luận số liệu đúng. OCR confidence không phải tỷ lệ chính xác. Mọi số liệu quan trọng phải đối chiếu ảnh gốc.")
    with st.expander("Nguồn dữ liệu và giới hạn", expanded=False):
        st.markdown("""
        - **Ảnh trực tiếp:** JPG/JPEG/PNG/WEBP/BMP/TIF, tối đa 30 ảnh/lượt, 8 MB/ảnh.
        - **ZIP:** tối đa 25 MB, ảnh nằm trong thư mục con cũng được tìm.
        - **Google Drive:** V1 chỉ hỗ trợ link **file ảnh công khai/chia sẻ được tải bằng link**. Không hỗ trợ thư mục Drive và file private/OAuth.
        - **Không dùng Gemini/API trả phí.** OCR chạy bằng Tesseract trên máy chủ Streamlit.
        - File xử lý nằm trong bộ nhớ/không gian tạm của ứng dụng; không có kho lưu trữ bền vững hoặc tài khoản nhân viên trong V1.
        """)
    tab_upload, tab_drive, tab_result = st.tabs(["① Tải ảnh / ZIP", "② Google Drive", "③ Kết quả & Excel"])
    with tab_upload:
        images = st.file_uploader("Chọn ảnh báo cáo (có thể chọn nhiều)", type=["jpg","jpeg","png","webp","bmp","tif","tiff"], accept_multiple_files=True)
        zip_file = st.file_uploader("Hoặc chọn một file ZIP chứa ảnh", type=["zip"], accept_multiple_files=False)
        if st.button("Nạp dữ liệu đã chọn", type="primary"):
            try:
                items = load_image_items(images or [], zip_file)
                st.session_state["items"] = items
                st.session_state.pop("results", None)
                st.success(f"Đã nạp {len(items)} ảnh.")
                st.dataframe(pd.DataFrame([{"Tên ảnh":x["name"],"Nguồn":x["source"],"Dung lượng (KB)":round(len(x["bytes"])/1024,1),"Trùng ảnh":x["duplicate_of"] or ""} for x in items]), use_container_width=True)
            except Exception as e:
                st.error(str(e))
    with tab_drive:
        st.info("Chỉ dùng link file ảnh Drive có quyền tải bằng link. Không dán link chứa dữ liệu nhạy cảm nếu chưa được phép đưa lên máy chủ.")
        drive_url = st.text_input("Link Google Drive công khai")
        if st.button("Tải ảnh từ Drive"):
            try:
                item = fetch_public_drive_file(drive_url)
                st.session_state["items"] = [item]
                st.session_state.pop("results", None)
                st.success("Đã tải ảnh: " + item["name"])
            except Exception as e:
                st.error(str(e))
    with tab_result:
        items = st.session_state.get("items", [])
        if not items:
            st.info("Hãy nạp ảnh ở tab đầu vào trước.")
        else:
            st.write(f"**Số ảnh đã nạp: {len(items)} / {MAX_FILES}**")
            if st.button("▶ Chạy OCR và kiểm tra", type="primary"):
                try:
                    with st.spinner("Đang xử lý ảnh. Tốc độ phụ thuộc tài nguyên máy chủ và kích thước ảnh..."):
                        results = []
                        progress = st.progress(0)
                        for idx, item in enumerate(items):
                            try:
                                results.append(process_one(item))
                            except Exception as e:
                                results.append({
                                    "name": item["name"], "source": item["source"], "sha256": item["sha256"],
                                    "text": "", "confidence": 0.0, "variant": "LỖI", "psm": 0,
                                    "warnings": ["Lỗi xử lý: " + str(e)], "words": [], "attempts": [],
                                    "reviewed": False, "review_note": ""
                                })
                            progress.progress((idx+1)/len(items))
                    st.session_state["results"] = results
                    st.success("OCR hoàn tất. Chuyển xuống dưới để rà soát.")
                except Exception as e:
                    st.error("Không thể hoàn tất OCR: " + str(e))
            results = st.session_state.get("results", [])
            if results:
                st.subheader("Kết quả nhận dạng")
                st.caption("Có thể chọn ảnh và so sánh các phương án OCR. Phương án có confidence cao nhất được chọn mặc định; hãy kiểm tra với ảnh gốc.")
                for idx, r in enumerate(results):
                    with st.expander(f"{idx+1}. {r['name']} · confidence {r['confidence']:.1f}% · {len(r['warnings'])} cảnh báo", expanded=(idx == 0)):
                        current_items = st.session_state.get("items", [])
                        if idx < len(current_items):
                            st.image(current_items[idx]["bytes"], caption="Ảnh gốc để đối chiếu", use_container_width=True)
                        if r["warnings"]:
                            for warning in r["warnings"]:
                                st.warning(warning)
                        else:
                            st.info("Không phát hiện cảnh báo cơ bản. Vẫn cần đối chiếu ảnh gốc.")
                        if r["attempts"]:
                            choices = [f"{a['variant']} | PSM {a['psm']} | {a['confidence']:.1f}%" for a in r["attempts"]]
                            selected = st.selectbox("Chọn phương án OCR", range(len(choices)), format_func=lambda x: choices[x], index=next((j for j,a in enumerate(r["attempts"]) if a["variant"]==r["variant"] and a["psm"]==r["psm"]),0), key=f"choice_{idx}")
                            selected_attempt = r["attempts"][selected]
                            default_text = selected_attempt["text"]
                        else:
                            default_text = r["text"]
                        edited = st.text_area("Văn bản OCR (hãy sửa theo ảnh gốc)", value=default_text, height=220, key=f"text_{idx}")
                        confirmed = st.checkbox("Tôi đã mở ảnh gốc, đối chiếu và xác nhận nội dung", value=bool(r.get("reviewed")), key=f"confirm_{idx}")
                        note = st.text_input("Ghi chú rà soát (nếu có)", value=r.get("review_note",""), key=f"note_{idx}")
                        r["text"] = edited
                        r["reviewed"] = confirmed
                        r["review_note"] = note
                        if r["attempts"]:
                            r["variant"] = selected_attempt["variant"]
                            r["psm"] = selected_attempt["psm"]
                            r["confidence"] = round(selected_attempt["confidence"],1)
                            r["words"] = selected_attempt["words"]
                # Recompute review status from widget state.
                all_reviewed = all(r.get("reviewed") and r.get("text","").strip() for r in results)
                st.divider()
                st.write(f"**Đã xác nhận:** {sum(1 for r in results if r.get('reviewed'))}/{len(results)} ảnh")
                if not all_reviewed:
                    st.error("Chưa đủ điều kiện xuất theo quy trình an toàn: còn ảnh chưa xác nhận hoặc nội dung rỗng.")
                # Reconstruct the exact editable review rows from the current state.
                review_df = pd.DataFrame([{
                    "TÊN ẢNH": r["name"], "VĂN BẢN OCR": r["text"],
                    "ĐÃ XÁC NHẬN": bool(r.get("reviewed")), "GHI CHÚ": r.get("review_note","")
                } for r in results])

                st.markdown("## 🤖 Trợ lý rà soát & góp ý điều chỉnh")
                st.caption("Chế độ FREE: phân tích theo quy tắc kiểm tra minh bạch, không phải mô hình AI tạo sinh. Trợ lý chỉ đề xuất; không tự sửa số liệu hoặc ghi đè nội dung gốc.")
                suggestions = build_suggestions(results)
                high_count = sum(1 for x in suggestions if x["MỨC ƯU TIÊN"] == "CAO")
                medium_count = sum(1 for x in suggestions if x["MỨC ƯU TIÊN"] == "TRUNG BÌNH")
                m1, m2, m3 = st.columns(3)
                m1.metric("Gợi ý cần xử lý", len(suggestions))
                m2.metric("Ưu tiên cao", high_count)
                m3.metric("Trùng ảnh cần xem", medium_count)
                st.dataframe(pd.DataFrame(suggestions), use_container_width=True, hide_index=True)
                user_feedback = st.text_area(
                    "Góp ý bổ sung / yêu cầu điều chỉnh file Excel",
                    placeholder="Ví dụ: Đề nghị gộp các ảnh cùng loại vào một Sheet; giữ mã nhân viên dạng văn bản; bổ sung đối chiếu tổng Target và Lũy kế...",
                    height=110,
                    key="user_ai_feedback"
                )
                st.info("Trợ lý không tự thay đổi dữ liệu. Các đề xuất và góp ý của người dùng sẽ được lưu vào Sheet AI_GOP_Y để người kiểm tra quyết định.")
                st.download_button(
                    "⬇ Tải Excel nhiều Sheet",
                    data=make_workbook(results, review_df, suggestions, user_feedback),
                    file_name="DMX_SMART_REPORT_OCR_REVIEW.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    disabled=not all_reviewed,
                    type="primary"
                )
                st.caption("Excel gồm TONG_HOP, RA_SOAT, OCR_TUNG_TU, CANH_BAO, AI_GOP_Y và DOC_TRUOC_KHI_DUNG.")
    st.divider()
    st.caption("ĐMX SMART REPORT AI · V1 miễn phí · Không có AI API trả phí · Chưa phải hệ thống báo cáo chính thức.")

if __name__ == "__main__":
    main()
