from __future__ import annotations

import hashlib
import io
import re
import time
import zipfile
from difflib import SequenceMatcher
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pandas as pd
import pytesseract
import streamlit as st
from pytesseract import Output
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter

APP_TITLE = "ĐMX SMART REPORT AI"
MAX_FILES = 10
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_ZIP_BYTES = 25 * 1024 * 1024
MAX_BATCH_EXPANDED_BYTES = 60 * 1024 * 1024
OCR_PRIMARY_PSM = 6
OCR_RETRY_PSM = 11
OCR_LOW_CONFIDENCE = 78.0
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}

st.set_page_config(page_title=APP_TITLE, page_icon="📊", layout="wide")

# ---------- Security / file intake ----------
def require_password() -> None:
    try:
        expected = st.secrets.get("APP_PASSWORD", "")
    except Exception:
        expected = ""
    if not expected:
        st.error("Ứng dụng chưa được cấu hình APP_PASSWORD trong Streamlit Secrets.")
        st.code('APP_PASSWORD = "mat-khau-rieng-cua-anh"')
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
    st.caption("Mật khẩu dùng chung chỉ là lớp bảo vệ cơ bản, chưa phải phân quyền nhân viên.")
    st.stop()

def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

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
                suffix = Path(info.filename).suffix.lower()
                if suffix not in ALLOWED_EXT:
                    continue
                if info.file_size > MAX_IMAGE_BYTES:
                    st.warning(f"Bỏ qua {info.filename}: ảnh vượt 8 MB.")
                    continue
                expanded_total += info.file_size
                if expanded_total > MAX_BATCH_EXPANDED_BYTES:
                    raise ValueError("Tổng ảnh sau giải nén vượt 60 MB.")
                items.append({"name": info.filename, "bytes": zf.read(info), "source": "ZIP"})
                if len(items) > MAX_FILES:
                    raise ValueError("V1.2 giới hạn tối đa 10 ảnh mỗi lượt.")
    if len(items) > MAX_FILES:
        raise ValueError("V1.2 giới hạn tối đa 10 ảnh mỗi lượt.")
    if not items:
        raise ValueError("Chưa có ảnh hợp lệ.")
    seen = {}
    for item in items:
        item["sha256"] = sha256(item["bytes"])
        item["duplicate_of"] = seen.get(item["sha256"], "")
        seen.setdefault(item["sha256"], item["name"])
    return items

def fetch_public_drive_file(url: str) -> dict:
    import gdown
    url = url.strip()
    if not url or "/folders/" in url:
        raise ValueError("V1.2 hiện hỗ trợ link file ảnh Google Drive công khai, không hỗ trợ thư mục/private Drive.")
    out = gdown.download(url=url, quiet=True, fuzzy=True)
    if not out:
        raise ValueError("Không tải được file Drive. Kiểm tra quyền chia sẻ.")
    p = Path(out)
    raw = p.read_bytes()
    if p.suffix.lower() not in ALLOWED_EXT or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("File Drive không đúng định dạng ảnh hoặc vượt 8 MB.")
    return {"name": p.name, "bytes": raw, "source": "Google Drive công khai",
            "sha256": sha256(raw), "duplicate_of": ""}

# ---------- OCR ----------
def decode_image(raw: bytes) -> np.ndarray:
    image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Không giải mã được ảnh.")
    return image

def prepare_primary_image(bgr: np.ndarray) -> np.ndarray:
    """Một lần tiền xử lý chính; giữ chữ đủ lớn nhưng tránh phóng ảnh quá mức."""
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]
    longest = max(h, w)
    if longest < 1800:
        scale = min(1.6, 1800 / max(longest, 1))
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    elif longest > 3200:
        scale = 3200 / longest
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    # CLAHE nhẹ giúp tăng tương phản; chỉ tạo một ảnh chính.
    return cv2.createCLAHE(clipLimit=1.8, tileGridSize=(8, 8)).apply(gray)


def run_ocr(image: np.ndarray, psm: int) -> tuple[str, float, list[dict]]:
    """Một lần gọi Tesseract: lấy token, vị trí và confidence; dựng text từ cùng kết quả."""
    config = f"--oem 1 --psm {psm}"
    data = pytesseract.image_to_data(
        image, lang="vie+eng", config=config, output_type=Output.DICT,
        timeout=45
    )
    words, confs = [], []
    lines: dict[tuple[int, int, int, int], list[str]] = {}
    for i, raw_token in enumerate(data["text"]):
        token = str(raw_token).strip()
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
                "line": int(data["line_num"][i]), "word": int(data["word_num"][i])
            })
            key = (int(data["page_num"][i]), int(data["block_num"][i]),
                   int(data["par_num"][i]), int(data["line_num"][i]))
            lines.setdefault(key, []).append(token)
    text = "\n".join(" ".join(tokens) for tokens in lines.values())
    return text, (float(np.mean(confs)) if confs else 0.0), words


def _ocr_risk(text: str, confidence: float, words: list[dict]) -> list[str]:
    risks = []
    if confidence < OCR_LOW_CONFIDENCE:
        risks.append(f"Confidence OCR thấp ({confidence:.1f}%).")
    if len(words) < 8 or len(text.strip()) < 40:
        risks.append("Nội dung nhận diện ít; có thể ảnh bị cắt, mờ hoặc OCR bỏ sót.")
    if re.search(r"[Il|][0-9]|[0-9][OQ]", text):
        risks.append("Có ký tự dễ nhầm I/l/1 hoặc O/0.")
    return risks


def _process_one_uncached(item: dict) -> dict:
    started = time.perf_counter()
    bgr = decode_image(item["bytes"])
    primary = prepare_primary_image(bgr)
    attempts = []

    t0 = time.perf_counter()
    text, conf, words = run_ocr(primary, OCR_PRIMARY_PSM)
    attempts.append({"variant": "Ảnh xám + CLAHE nhẹ", "psm": OCR_PRIMARY_PSM,
                     "text": text, "confidence": conf, "words": words,
                     "seconds": round(time.perf_counter() - t0, 2)})
    selected = attempts[0]
    initial_risks = _ocr_risk(text, conf, words)

    # Chỉ kiểm tra sâu khi kết quả chính có dấu hiệu rủi ro.
    if initial_risks:
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape[:2]
        longest = max(h, w)
        if longest < 1800:
            scale = min(1.6, 1800 / max(longest, 1))
            gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        elif longest > 3200:
            scale = 3200 / longest
            gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
        for variant_name, variant_img, psm in (
            ("Otsu kiểm tra", otsu, OCR_PRIMARY_PSM),
            ("Ảnh xám PSM 11 kiểm tra", gray, OCR_RETRY_PSM),
        ):
            t0 = time.perf_counter()
            retry_text, retry_conf, retry_words = run_ocr(variant_img, psm)
            attempts.append({"variant": variant_name, "psm": psm,
                             "text": retry_text, "confidence": retry_conf,
                             "words": retry_words,
                             "seconds": round(time.perf_counter() - t0, 2)})
        # Chọn kết quả có confidence tốt hơn; không dùng kết quả này như số liệu đã xác minh.
        selected = max(attempts, key=lambda a: (a["confidence"], len(a["text"].strip())))

    warnings = _ocr_risk(selected["text"], selected["confidence"], selected["words"])
    if len(attempts) > 1:
        normalized = [re.sub(r"\s+", " ", a["text"]).strip().casefold() for a in attempts]
        if any(SequenceMatcher(None, normalized[0], other).ratio() < 0.65 for other in normalized[1:]):
            warnings.append("Các phương án OCR có khác biệt đáng kể; cần so sánh với ảnh gốc.")
    if item.get("duplicate_of"):
        warnings.append("Ảnh trùng byte với: " + item["duplicate_of"])
    if initial_risks and not warnings:
        warnings.append("Đã phải OCR kiểm tra bổ sung; cần đối chiếu ảnh gốc.")
    if not selected["text"].strip():
        warnings.append("Không nhận diện được văn bản.")
    return {
        "name": item["name"], "source": item["source"], "sha256": item["sha256"],
        "text": selected["text"], "confidence": round(selected["confidence"], 1),
        "variant": selected["variant"], "psm": selected["psm"], "warnings": warnings,
        "words": selected["words"], "attempts": attempts, "reviewed": False,
        "review_note": "", "ocr_seconds": round(time.perf_counter() - started, 2),
        "ocr_attempt_count": len(attempts), "needs_review": bool(warnings)
    }


@st.cache_data(show_spinner=False, max_entries=20)
def process_one_cached(raw: bytes, name: str, source: str, file_hash: str,
                       duplicate_of: str) -> dict:
    # Cache theo nội dung ảnh; đổi ảnh sẽ tự tạo khóa cache mới.
    item = {"bytes": raw, "name": name, "source": source,
            "sha256": file_hash, "duplicate_of": duplicate_of}
    return _process_one_uncached(item)


def process_one(item: dict) -> dict:
    return process_one_cached(item["bytes"], item["name"], item["source"],
                               item["sha256"], item.get("duplicate_of", ""))

# ---------- Business rules / structured input ----------
GROUP_COLUMNS = ["NHÓM HÀNG", "THỰC HIỆN", "TARGET", "% HT DỰ KIẾN"]
STAFF_COLUMNS = [
    "MÃ NHÂN VIÊN", "NHÂN VIÊN", "HẠNG", "HOÀN THÀNH THÁNG",
    "DOANH THU QĐ", "% TARGET", "LƯỢT BILL", "% QUY ĐỔI",
    "% TRẢ CHẬM", "% BÁN KÈM", "ĐIỂM THƯỞNG ERP",
    "ĐIỂM THƯỞNG / GIỜ CÔNG", "DTQĐ / GIỜ CÔNG"
]
REVENUE_COLUMNS = ["NHÂN VIÊN", "DOANH THU QĐ", "LƯỢT BILL", "% TRẢ CHẬM", "ĐIỂM THƯỞNG ERP"]

def parse_tsv(text: str, label: str) -> pd.DataFrame:
    if not text.strip():
        return pd.DataFrame()
    try:
        df = pd.read_csv(io.StringIO(text), sep="\t", dtype=str, keep_default_na=False)
    except Exception as exc:
        raise ValueError(f"{label}: không đọc được bảng TSV: {exc}")
    df.columns = [str(c).strip().upper() for c in df.columns]
    return df

def parse_staff_ids(text: str) -> list[str]:
    ids = [x.strip() for x in re.split(r"[,;\s]+", text.strip()) if x.strip()]
    if len(ids) != 9:
        raise ValueError(f"Phải có đúng 9 mã nhân viên có ảnh KPI. Hiện có {len(ids)} mã.")
    if len(set(ids)) != 9:
        raise ValueError("Danh sách mã nhân viên bị trùng. Mỗi người chỉ được xuất hiện một lần.")
    return ids

def normalize_date(report_dump_date: date) -> date:
    # Business rule confirmed by user: data date is dump date minus one calendar day.
    return report_dump_date - timedelta(days=1)

def as_numeric(series: pd.Series) -> pd.Series:
    # Conservative parser: preserve blank as NaN; don't turn missing into zero.
    return pd.to_numeric(series.astype(str).str.replace(",", "", regex=False).str.strip(), errors="coerce")

def filter_matrix_to_nine(matrix: pd.DataFrame, nine_ids: list[str]) -> tuple[pd.DataFrame, list[str]]:
    if matrix.empty:
        return matrix, ["Chưa có dữ liệu ma trận THI_DUA."]
    first_col = matrix.columns[0]
    if first_col not in ("MÃ NHÂN VIÊN", "NHÂN VIÊN", "TÊN NHÂN VIÊN"):
        # Expected first column contains a name and employee code, e.g. "Nguyễn Ngọc Tân - 137502".
        first_col = matrix.columns[0]
    keep_rows, warnings = [], []
    for _, row in matrix.iterrows():
        label = str(row.iloc[0]).strip()
        if label.upper() in ("TỔNG", "TONG", "TOTAL", ""):
            continue
        found = re.findall(r"(?<!\d)\d{4,8}(?!\d)", label)
        emp_id = found[-1] if found else ""
        if emp_id in nine_ids:
            keep_rows.append(row)
        elif emp_id:
            warnings.append(f"Đã loại khỏi THI_DUA nhân viên không thuộc 9 ảnh KPI: {label}")
        else:
            warnings.append(f"Không nhận diện được mã nhân viên ở dòng ma trận: {label}")
    filtered = pd.DataFrame(keep_rows, columns=matrix.columns)
    found_ids = set()
    for value in filtered.iloc[:, 0].astype(str) if not filtered.empty else []:
        ids = re.findall(r"(?<!\d)\d{4,8}(?!\d)", value)
        if ids:
            found_ids.add(ids[-1])
    missing = sorted(set(nine_ids) - found_ids)
    if missing:
        warnings.append("Thiếu dòng ma trận cho 9 nhân viên: " + ", ".join(missing))
    duplicated = filtered.iloc[:, 0].astype(str).str.extract(r"(\d{4,8})", expand=False).dropna()
    if duplicated.duplicated().any():
        warnings.append("Có mã nhân viên xuất hiện nhiều lần trong ma trận đã lọc.")
    return filtered, warnings

def make_sheet(wb: Workbook, title: str, df: pd.DataFrame, plain: bool = False):
    ws = wb.create_sheet(title)
    if df.empty:
        ws.append(["KHÔNG CÓ DATA"])
        return ws
    ws.append(list(df.columns))
    for row in df.itertuples(index=False, name=None):
        ws.append([None if pd.isna(v) else v for v in row])
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = ws.dimensions
    if plain:
        thin = Side(style="thin", color="808080")
        for row in ws.iter_rows():
            for cell in row:
                cell.fill = __import__("openpyxl").styles.PatternFill(fill_type=None)
                cell.font = Font(name="Arial", size=9, bold=(cell.row == 1))
                cell.alignment = Alignment(vertical="center", wrap_text=True)
                cell.border = Border(left=thin, right=thin, top=thin, bottom=thin)
        ws.sheet_view.showGridLines = True
    else:
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = __import__("openpyxl").styles.PatternFill("solid", fgColor="075DA8")
            cell.alignment = Alignment(vertical="center", wrap_text=True)
    for col_idx, col in enumerate(df.columns, 1):
        vals = [len(str(col))] + [len(str(v)) for v in df[col].head(100) if pd.notna(v)]
        ws.column_dimensions[get_column_letter(col_idx)].width = min(max(max(vals, default=12) + 2, 12), 38)
    return ws

def build_workbook(group_df: pd.DataFrame, staff_df: pd.DataFrame, matrix_df: pd.DataFrame,
                   revenue_df: pd.DataFrame, warnings: list[str], raw_rows: list[dict],
                   data_date: date) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)

    # Exact six requested sheets, in the requested order.
    make_sheet(wb, "TONG_HOP_TAP_THE", group_df)
    make_sheet(wb, "NHAN_VIEN", staff_df)
    make_sheet(wb, "THI_DUA", matrix_df, plain=True)
    make_sheet(wb, "DOANH THU", revenue_df)
    warn_df = pd.DataFrame([{"MỨC": "CẦN KIỂM TRA", "CẢNH BÁO": w} for w in warnings])
    make_sheet(wb, "CANH_BAO", warn_df)
    raw_df = pd.DataFrame(raw_rows, columns=["TÊN ẢNH", "NGUỒN", "SHA256", "NGÀY DỮ LIỆU", "OCR CONFIDENCE", "VĂN BẢN OCR", "ĐÃ XÁC NHẬN"])
    make_sheet(wb, "DATA_GOC", raw_df)

    # Data date is date-only. Keep metadata at the top of the first sheet.
    ws = wb["TONG_HOP_TAP_THE"]
    ws.insert_rows(1, 2)
    ws["A1"] = "NGÀY DỮ LIỆU"
    ws["B1"] = data_date.strftime("%d/%m/%Y")
    ws["A1"].font = Font(bold=True)
    ws["B1"].font = Font(bold=True)
    return save_wb(wb)

def save_wb(wb: Workbook) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------- UI V1.3: automatic extraction ----------
def main():
    from auto_extract import build_auto_tables, EMPLOYEE_NAME_BY_ID, GROUPS
    require_password()
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Be+Vietnam+Pro:wght@400;500;600;700;800&display=swap');
    html, body, [class*="css"] {font-family:'Be Vietnam Pro',Arial,sans-serif;}
    .stApp {background:linear-gradient(180deg,#f4f7fb 0%,#eef3f8 100%);}
    [data-testid="stSidebar"] {background:#102d4d;}
    [data-testid="stSidebar"] * {color:#f4f8fc !important;}
    h1,h2,h3 {font-weight:800 !important;color:#102d4d;}
    [data-testid="stMetric"] {background:#fff;border:1px solid #dce5ef;padding:14px;border-radius:13px;}
    div.stButton > button, div.stDownloadButton > button {border-radius:10px;font-weight:800;}
    </style>
    """, unsafe_allow_html=True)
    st.title("ĐMX SMART REPORT AI")
    st.caption("V1.3 BETA · Tự động phân tích 1 ảnh Tập thể + 9 ảnh KPI · Tối đa 10 ảnh/lượt · Xuất 6 Sheet")
    st.warning("OCR và bộ phân tích tự động là bản nháp. Các trường không chắc chắn được đánh dấu KHÔNG CÓ DATA/CẦN KIỂM TRA; phải đối chiếu ảnh gốc trước khi dùng báo cáo.")

    with st.sidebar:
        st.subheader("Quy trình tự động")
        st.markdown("1. Nạp 10 ảnh\n2. OCR tự động\n3. Phân tích Tập thể + 9 KPI\n4. Đối chiếu cảnh báo\n5. Xuất Excel 6 Sheet")
        if st.button("Đăng xuất"):
            st.session_state["authenticated"] = False
            st.rerun()

    tab1, tab2 = st.tabs(["① NẠP ẢNH & TỰ ĐỘNG PHÂN TÍCH", "② KIỂM TRA & XUẤT EXCEL"])
    with tab1:
        dump_date=st.date_input("Ngày đổ số",value=st.session_state.get("dump_date",date.today()))
        st.session_state["dump_date"]=dump_date
        data_date=normalize_date(dump_date)
        st.info(f"Ngày dữ liệu: **{data_date.strftime('%d/%m/%Y')}** (ngày đổ số trừ 1 ngày, chỉ tính ngày)")
        drive_url=st.text_input("① Link Google Drive ảnh/ZIP công khai",placeholder="Dán link file được chia sẻ công khai")
        uploads=st.file_uploader("② Tải ảnh trực tiếp (có thể chọn nhiều)",type=["jpg","jpeg","png","webp","bmp","tif","tiff"],accept_multiple_files=True)
        zip_upload=st.file_uploader("③ Tải ZIP chứa đúng 10 ảnh",type=["zip"])
        st.caption("Đầu vào yêu cầu: 1 ảnh LuyKe.JPG + 9 ảnh suc-khoe-nv-[mã]-202611.png. Hiện chưa hỗ trợ link thư mục/private Google Drive hoặc file 7Z trực tiếp.")
        if st.button("NẠP ẢNH",type="primary"):
            try:
                items=[]
                if drive_url.strip():
                    # A public Drive URL may point to a single file; ZIP is accepted by this app only via upload.
                    drive_item=fetch_public_drive_file(drive_url.strip())
                    items.append(drive_item)
                if uploads or zip_upload is not None:
                    items.extend(load_image_items(uploads or [],zip_upload))
                if not items: raise ValueError("Chưa có ảnh đầu vào.")
                if len(items)!=10: raise ValueError(f"Cần đúng 10 ảnh (1 Tập thể + 9 KPI). Hiện có {len(items)} ảnh.")
                seen={}
                for it in items:
                    it["sha256"]=sha256(it["bytes"])
                    it["duplicate_of"]=seen.get(it["sha256"],"")
                    seen.setdefault(it["sha256"],it["name"])
                st.session_state["items"]=items
                st.session_state.pop("results",None); st.session_state.pop("auto_tables",None)
                st.success("Đã nạp đủ 10 ảnh. Chưa xử lý OCR.")
                st.dataframe(pd.DataFrame([{"TÊN ẢNH":x["name"],"NGUỒN":x["source"],"KB":round(len(x["bytes"])/1024,1)} for x in items]),use_container_width=True,hide_index=True)
            except Exception as exc: st.error(str(exc))

        items=st.session_state.get("items",[])
        if items:
            group_names=[x["name"] for x in items if "luyke" in x["name"].lower()]
            staff_names=[x["name"] for x in items if "suc-khoe-nv-" in x["name"].lower()]
            st.write(f"Ảnh Tập thể nhận diện theo tên file: **{len(group_names)}/1** · Ảnh KPI nhận diện theo tên file: **{len(staff_names)}/9**")
            if st.button("CHẠY OCR & PHÂN TÍCH TỰ ĐỘNG",type="primary"):
                out=[]; progress=st.progress(0); status=st.empty(); began=time.perf_counter()
                for i,item in enumerate(items):
                    status.info(f"OCR ảnh {i+1}/10: {item['name']}")
                    try:
                        # Group table is extracted by cell geometry below; avoid a redundant full-page OCR pass.
                        if "luyke" in item["name"].lower():
                            result={"name":item["name"],"source":item["source"],"sha256":item["sha256"],
                                    "text":"Ảnh Tập thể: dữ liệu được trích xuất theo từng ô bảng 38 dòng.",
                                    "confidence":0.0,"variant":"Trích xuất ô bảng","psm":"CELL",
                                    "warnings":[],"words":[],"attempts":[],"reviewed":False,"review_note":"",
                                    "ocr_seconds":0.0,"ocr_attempt_count":0,"needs_review":True}
                        else:
                            result=process_one(item)
                        out.append(result)
                    except Exception as exc:
                        out.append({"name":item["name"],"source":item["source"],"sha256":item["sha256"],
                            "text":"","confidence":0,"variant":"LỖI","psm":0,"warnings":[str(exc)],
                            "words":[],"attempts":[],"reviewed":False,"review_note":"",
                            "ocr_seconds":0,"ocr_attempt_count":0,"needs_review":True})
                    progress.progress((i+1)/10)
                status.info("Đang phân tích bảng Tập thể và ghép dữ liệu 9 nhân viên…")
                try:
                    tables=build_auto_tables(out,items)
                    for rr in out:
                        if "luyke" in rr["name"].lower():
                            rr["text"] = tables["group_df"].to_csv(sep="\t", index=False)
                            rr["variant"] = "OCR theo từng ô bảng (38 dòng)"
                    st.session_state["results"]=out
                    st.session_state["auto_tables"]=tables
                    st.session_state["auto_reviewed"]={}
                    status.empty()
                    elapsed=time.perf_counter()-began
                    st.success(f"Đã hoàn tất OCR và phân tích bước đầu trong {elapsed:.1f} giây. Hệ thống giữ cảnh báo cho các trường chưa đọc chắc chắn.")
                except Exception as exc:
                    status.empty(); st.exception(exc)

        results=st.session_state.get("results",[])
        if results:
            st.markdown("### Kết quả OCR theo ảnh")
            for i,result in enumerate(results):
                with st.expander(f"{i+1}. {result['name']} · confidence {result.get('confidence',0):.1f}%",expanded=False):
                    st.image(items[i]["bytes"],use_container_width=True)
                    st.text_area("Văn bản OCR gốc",result.get("text",""),height=130,key=f"v13_ocr_{i}")
                    checked=st.checkbox("Đã đối chiếu ảnh gốc",key=f"v13_review_{i}")
                    st.session_state.setdefault("auto_reviewed",{})[result["name"]]=checked
                    for w in result.get("warnings",[]): st.warning(w)

        tables=st.session_state.get("auto_tables")
        if tables:
            st.markdown("### Bảng tự động trích xuất")
            st.caption("Các ô KHÔNG CÓ DATA hoặc cảnh báo không được tự suy diễn. Đối chiếu từng ảnh gốc trước khi sử dụng.")
            c1,c2=st.columns(2)
            with c1: st.metric("Nhóm hàng Tập thể",len(tables["group_df"]))
            with c2: st.metric("Nhân viên KPI",len(tables["staff_df"]))
            with st.expander("Xem Tập thể – 38 dòng",expanded=True):
                st.dataframe(tables["group_df"],use_container_width=True,hide_index=True)
            with st.expander("Xem NHÂN VIÊN – 9 người"):
                st.dataframe(tables["staff_df"],use_container_width=True,hide_index=True)
            with st.expander("Xem ma trận THI ĐUA"):
                st.dataframe(tables["matrix_df"],use_container_width=True,hide_index=True)
            with st.expander("Xem DOANH THU"):
                st.dataframe(tables["revenue_df"],use_container_width=True,hide_index=True)
            st.markdown("### Cảnh báo phân tích")
            for w in tables["warnings"]: st.warning(w)

    with tab2:
        tables=st.session_state.get("auto_tables")
        results=st.session_state.get("results",[])
        if not tables or not results:
            st.info("Anh nạp đủ 10 ảnh và chạy OCR + phân tích tự động trước.")
        else:
            dump_date=st.session_state.get("dump_date",date.today())
            data_date=normalize_date(dump_date)
            st.metric("NGÀY DỮ LIỆU",data_date.strftime("%d/%m/%Y"))
            warnings=list(tables["warnings"])
            items=st.session_state.get("items",[])
            # Hard gate: 1 group + exactly 9 distinct known employee IDs.
            staff_ids=tables["staff_df"]["MÃ NHÂN VIÊN"].astype(str).tolist() if not tables["staff_df"].empty else []
            if len(staff_ids)!=9 or len(set(staff_ids))!=9:
                warnings.append(f"Không đủ 9 mã nhân viên duy nhất; hiện nhận diện {len(set(staff_ids))}.")
            missing_review=[r["name"] for r in results if not st.session_state.get("auto_reviewed",{}).get(r["name"],False)]
            if missing_review:
                st.warning("Chưa xác nhận đối chiếu ảnh gốc: "+", ".join(missing_review))
            for w in warnings: st.warning(w)
            raw_rows=[{"TÊN ẢNH":r["name"],"NGUỒN":r["source"],"SHA256":r["sha256"],
                       "NGÀY DỮ LIỆU":data_date.strftime("%d/%m/%Y"),
                       "OCR CONFIDENCE":r["confidence"],"VĂN BẢN OCR":r["text"],
                       "ĐÃ XÁC NHẬN":"CÓ" if st.session_state.get("auto_reviewed",{}).get(r["name"],False) else "CHƯA"} for r in results]
            ready=(len(items)==10 and len(staff_ids)==9 and len(set(staff_ids))==9 and not missing_review)
            if not ready:
                st.info("Chỉ xuất khi đủ 10 ảnh, nhận diện đủ 9 mã khác nhau và đã xác nhận đối chiếu cả 10 ảnh.")
            if st.button("TẠO FILE EXCEL 6 SHEET",type="primary",disabled=not ready):
                xlsx=build_workbook(tables["group_df"],tables["staff_df"],tables["matrix_df"],
                                    tables["revenue_df"],warnings,raw_rows,data_date)
                st.download_button("⬇ TẢI EXCEL",data=xlsx,
                    file_name=f"DMX_SMART_REPORT_{data_date.strftime('%Y%m%d')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                st.success("Đã tạo Excel 6 Sheet. Các trường còn thiếu đã được giữ cảnh báo trong CANH_BAO.")

    st.divider()
    st.caption("ĐMX SMART REPORT AI · V1.3 BETA · Tự động phân tích từ 10 ảnh. Kết quả OCR chưa phải dữ liệu đã xác minh.")
if __name__=="__main__":
    main()
