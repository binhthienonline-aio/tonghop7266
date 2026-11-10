from __future__ import annotations

import hashlib
import io
import re
import zipfile
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
MAX_FILES = 30
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_ZIP_BYTES = 25 * 1024 * 1024
MAX_BATCH_EXPANDED_BYTES = 60 * 1024 * 1024
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
                    raise ValueError("Tổng số ảnh vượt 30.")
    if len(items) > MAX_FILES:
        raise ValueError("Tổng số ảnh vượt 30.")
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
        raise ValueError("V1.1 chỉ hỗ trợ link file ảnh Google Drive công khai, không hỗ trợ thư mục/private Drive.")
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

def preprocess_variants(bgr: np.ndarray) -> list[tuple[str, np.ndarray]]:
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]
    scale = 2.0 if max(h, w) < 2600 else 1.35
    enlarged = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(enlarged)
    otsu = cv2.threshold(clahe, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    adaptive = cv2.adaptiveThreshold(clahe, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                     cv2.THRESH_BINARY, 31, 11)
    return [("Ảnh xám tăng kích thước", enlarged), ("CLAHE + Otsu", otsu),
            ("CLAHE + Adaptive", adaptive)]

def run_ocr(image: np.ndarray, psm: int) -> tuple[str, float, list[dict]]:
    config = f"--oem 1 --psm {psm}"
    data = pytesseract.image_to_data(image, lang="vie+eng", config=config, output_type=Output.DICT)
    words, confs = [], []
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
    text = pytesseract.image_to_string(image, lang="vie+eng", config=config)
    return text, (float(np.mean(confs)) if confs else 0.0), words

def process_one(item: dict) -> dict:
    bgr = decode_image(item["bytes"])
    attempts = []
    for variant_name, image in preprocess_variants(bgr):
        for psm in (6, 11):
            text, conf, words = run_ocr(image, psm)
            attempts.append({"variant": variant_name, "psm": psm, "text": text,
                             "confidence": conf, "words": words})
    best = max(attempts, key=lambda x: (x["confidence"], len(x["text"].strip())))
    warnings = []
    if best["confidence"] < 75:
        warnings.append("Confidence OCR thấp; cần đối chiếu ảnh gốc.")
    if not best["text"].strip():
        warnings.append("Không nhận diện được văn bản.")
    if item.get("duplicate_of"):
        warnings.append("Ảnh trùng byte với: " + item["duplicate_of"])
    if re.search(r"[Il|][0-9]|[0-9][OQ]", best["text"]):
        warnings.append("Có ký tự dễ nhầm I/l/1 hoặc O/0.")
    return {
        "name": item["name"], "source": item["source"], "sha256": item["sha256"],
        "text": best["text"], "confidence": round(best["confidence"], 1),
        "variant": best["variant"], "psm": best["psm"], "warnings": warnings,
        "words": best["words"], "attempts": attempts, "reviewed": False, "review_note": ""
    }

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

# ---------- UI ----------
def main():
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
    st.caption("V1.1 · Kiểm soát ngày dữ liệu · Lọc đúng 9 nhân viên có ảnh KPI · Xuất 6 Sheet")
    st.warning("Ưu tiên độ chính xác: OCR chỉ là bản nháp. Chỉ dùng dữ liệu đã được người dùng đối chiếu ảnh gốc.")

    with st.sidebar:
        st.subheader("Quy trình")
        st.markdown("1. Nạp ảnh gốc\n2. Chạy OCR\n3. Khai báo 9 mã NV\n4. Nhập bảng đã xác minh\n5. Xuất Excel")
        if st.button("Đăng xuất"):
            st.session_state["authenticated"] = False
            st.rerun()

    tab1, tab2, tab3 = st.tabs(["① NẠP ẢNH & OCR", "② NHẬP DỮ LIỆU ĐÃ XÁC MINH", "③ KIỂM TRA & XUẤT EXCEL"])

    with tab1:
        dump_date = st.date_input("Ngày đổ số", value=date.today(), help="Ngày dữ liệu tự động = ngày đổ số - 1 ngày; không dùng giờ.")
        st.session_state["dump_date"] = dump_date
        st.info(f"Ngày dữ liệu sẽ là: **{normalize_date(dump_date).strftime('%d/%m/%Y')}**")
        uploads = st.file_uploader("Tải ảnh báo cáo (có thể chọn nhiều)", type=["jpg","jpeg","png","webp","bmp","tif","tiff"], accept_multiple_files=True)
        zip_upload = st.file_uploader("Hoặc tải ZIP chứa ảnh", type=["zip"])
        if st.button("Nạp ảnh", type="primary"):
            try:
                items = load_image_items(uploads or [], zip_upload)
                st.session_state["items"] = items
                st.session_state.pop("results", None)
                st.success(f"Đã nạp {len(items)} ảnh.")
                st.dataframe(pd.DataFrame([{"TÊN ẢNH": x["name"], "NGUỒN": x["source"],
                    "DUNG LƯỢNG KB": round(len(x["bytes"])/1024, 1), "TRÙNG": x["duplicate_of"]} for x in items]),
                    use_container_width=True, hide_index=True)
            except Exception as exc:
                st.error(str(exc))
        items = st.session_state.get("items", [])
        if items and st.button("Chạy OCR 6 phương án", type="secondary"):
            out, progress = [], st.progress(0)
            for i, item in enumerate(items):
                try:
                    out.append(process_one(item))
                except Exception as exc:
                    out.append({"name": item["name"], "source": item["source"], "sha256": item["sha256"],
                        "text": "", "confidence": 0, "variant": "LỖI", "psm": 0,
                        "warnings": [str(exc)], "words": [], "attempts": [], "reviewed": False, "review_note": ""})
                progress.progress((i+1)/len(items))
            st.session_state["results"] = out
            st.success("OCR hoàn tất. Hãy đối chiếu ảnh gốc trước khi xác nhận.")
        results = st.session_state.get("results", [])
        for i, result in enumerate(results):
            with st.expander(f"{i+1}. {result['name']} · OCR {result['confidence']}%", expanded=(i == 0)):
                st.image(items[i]["bytes"], use_container_width=True)
                st.text_area("Văn bản OCR – có thể chỉnh sửa", result["text"], height=180, key=f"ocr_text_{i}")
                result["text"] = st.session_state.get(f"ocr_text_{i}", result["text"])
                result["reviewed"] = st.checkbox("Đã đối chiếu với ảnh gốc", key=f"review_{i}")
                result["review_note"] = st.text_input("Ghi chú", key=f"review_note_{i}")
                for warning in result["warnings"]:
                    st.warning(warning)

    with tab2:
        st.markdown("### Danh sách 9 nhân viên có ảnh KPI")
        st.caption("Nhập đúng 9 mã nhân viên lấy từ 9 ảnh cá nhân. Không đưa 2 nhân viên không có ảnh vào kết quả.")
        staff_ids_text = st.text_input("9 mã nhân viên, phân cách bằng dấu phẩy",
            value=st.session_state.get("staff_ids_text", "137502, 181846"),
            help="Hiện mới có 2 mã đọc được từ ảnh mẫu. Cần bổ sung 7 mã còn lại trước khi xuất.")
        st.session_state["staff_ids_text"] = staff_ids_text
        try:
            staff_ids = parse_staff_ids(staff_ids_text)
            st.success("Đã đủ 9 mã nhân viên, không trùng.")
        except ValueError as exc:
            staff_ids = []
            st.warning(str(exc))

        st.markdown("### Dán bảng dữ liệu đã đối chiếu")
        st.caption("Dán bảng dạng TSV (các cột cách nhau bằng phím Tab), có hàng tiêu đề. Dữ liệu chỉ được xuất sau khi người dùng đã đối chiếu với ảnh.")
        group_text = st.text_area("A. Tập thể – cột: NHÓM HÀNG, THỰC HIỆN, TARGET, % HT DỰ KIẾN",
            height=130, key="group_tsv", placeholder="NHÓM HÀNG\\tTHỰC HIỆN\\tTARGET\\t% HT DỰ KIẾN\\nĐiện tử Samsung\\t20.00\\t108.00\\t64.7%")
        staff_text = st.text_area("B. Nhân viên – mỗi dòng một nhân viên, có MÃ NHÂN VIÊN và NHÂN VIÊN",
            height=150, key="staff_tsv", placeholder="MÃ NHÂN VIÊN\\tNHÂN VIÊN\\tHẠNG\\tHOÀN THÀNH THÁNG\\tDOANH THU QĐ\\t% TARGET\\tLƯỢT BILL\\t% QUY ĐỔI\\t% TRẢ CHẬM\\t% BÁN KÈM\\tĐIỂM THƯỞNG ERP")
        matrix_text = st.text_area("C. Ma trận THI ĐUA – cột đầu là NHÂN VIÊN (tên - mã), các cột sau là Nhóm hàng",
            height=180, key="matrix_tsv", placeholder="NHÂN VIÊN\\tĐIỆN TỬ SAMSUNG\\tCE LG\\tTIVI TCL\\nNguyễn Ngọc Tân - 137502\\t13.00\\t21.30\\t5.73")
        revenue_text = st.text_area("D. DOANH THU – đúng 5 cột theo yêu cầu",
            height=130, key="revenue_tsv", placeholder="NHÂN VIÊN\\tDOANH THU QĐ\\tLƯỢT BILL\\t% TRẢ CHẬM\\tĐIỂM THƯỞNG ERP")
        st.info("V1.1 chưa tự chuyển ảnh thành bảng nghiệp vụ hoàn chỉnh. Đây là bước nhập dữ liệu đã xác minh để tránh tự suy đoán cấu trúc bảng. OCR thô vẫn được lưu trong DATA_GOC.")

    with tab3:
        st.markdown("### Kiểm tra điều kiện xuất")
        dump_date = st.session_state.get("dump_date", date.today())
        data_date = normalize_date(dump_date)
        st.metric("NGÀY DỮ LIỆU", data_date.strftime("%d/%m/%Y"))
        try:
            staff_ids = parse_staff_ids(st.session_state.get("staff_ids_text", ""))
        except ValueError as exc:
            staff_ids = []
            st.error(str(exc))

        warnings = []
        try:
            group_df = parse_tsv(st.session_state.get("group_tsv", ""), "Tập thể")
            staff_df = parse_tsv(st.session_state.get("staff_tsv", ""), "Nhân viên")
            matrix_df = parse_tsv(st.session_state.get("matrix_tsv", ""), "Ma trận thi đua")
            revenue_df = parse_tsv(st.session_state.get("revenue_tsv", ""), "Doanh thu")
            if not staff_ids:
                warnings.append("Chưa khai báo đủ đúng 9 mã nhân viên có ảnh KPI.")
            if not staff_df.empty and "MÃ NHÂN VIÊN" in staff_df.columns:
                staff_df["MÃ NHÂN VIÊN"] = staff_df["MÃ NHÂN VIÊN"].astype(str)
                staff_df = staff_df[staff_df["MÃ NHÂN VIÊN"].isin(staff_ids)].copy()
                missing_staff = sorted(set(staff_ids) - set(staff_df["MÃ NHÂN VIÊN"]))
                if missing_staff:
                    warnings.append("Thiếu dữ liệu NHAN_VIEN cho mã: " + ", ".join(missing_staff))
                if staff_df["MÃ NHÂN VIÊN"].duplicated().any():
                    warnings.append("Trùng mã nhân viên trong bảng NHAN_VIEN.")
            elif not staff_df.empty:
                warnings.append("Bảng NHAN_VIEN thiếu cột MÃ NHÂN VIÊN.")
            if not matrix_df.empty and staff_ids:
                matrix_df, matrix_warnings = filter_matrix_to_nine(matrix_df, staff_ids)
                warnings.extend(matrix_warnings)
            if not revenue_df.empty:
                if list(revenue_df.columns) != REVENUE_COLUMNS:
                    warnings.append("DOANH THU chưa đúng thứ tự 5 cột yêu cầu: " + " | ".join(REVENUE_COLUMNS))
                if "NHÂN VIÊN" in revenue_df.columns and not staff_df.empty:
                    allowed_names = set(staff_df["NHÂN VIÊN"].astype(str))
                    revenue_df = revenue_df[revenue_df["NHÂN VIÊN"].astype(str).isin(allowed_names)].copy()
            if group_df.empty:
                warnings.append("Chưa có dữ liệu TONG_HOP_TAP_THE.")
            if staff_df.empty:
                warnings.append("Chưa có dữ liệu NHAN_VIEN đã xác minh.")
            if matrix_df.empty:
                warnings.append("Chưa có ma trận THI_DUA.")
            if revenue_df.empty:
                warnings.append("Chưa có dữ liệu DOANH THU.")
        except Exception as exc:
            warnings.append("Lỗi kiểm tra bảng: " + str(exc))
            group_df = staff_df = matrix_df = revenue_df = pd.DataFrame()

        for warning in warnings:
            st.warning(warning)
        ready = bool(staff_ids) and not any("Lỗi kiểm tra" in w for w in warnings)
        st.caption("Có cảnh báo không đồng nghĩa file không thể tạo; cảnh báo sẽ được ghi vào CANH_BAO. Tuy nhiên, dữ liệu trống sẽ được ghi rõ KHÔNG CÓ DATA.")
        results = st.session_state.get("results", [])
        raw_rows = [{
            "TÊN ẢNH": r["name"], "NGUỒN": r["source"], "SHA256": r["sha256"],
            "NGÀY DỮ LIỆU": data_date.strftime("%d/%m/%Y"),
            "OCR CONFIDENCE": r["confidence"], "VĂN BẢN OCR": r["text"],
            "ĐÃ XÁC NHẬN": "CÓ" if r.get("reviewed") else "CHƯA"
        } for r in results]
        if st.button("TẠO FILE EXCEL 6 SHEET", type="primary", disabled=not ready):
            xlsx = build_workbook(group_df, staff_df, matrix_df, revenue_df, warnings, raw_rows, data_date)
            st.download_button("⬇ TẢI EXCEL", data=xlsx,
                file_name=f"DMX_SMART_REPORT_{data_date.strftime('%Y%m%d')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            st.success("Đã tạo workbook 6 Sheet. Kiểm tra CANH_BAO trước khi sử dụng báo cáo.")

    st.divider()
    st.caption("ĐMX SMART REPORT AI · V1.1 nền tảng nghiệp vụ · OCR không thay thế xác minh thủ công.")

if __name__ == "__main__":
    main()
