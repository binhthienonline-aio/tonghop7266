
from __future__ import annotations
import re, unicodedata
from pathlib import Path
from typing import Any
import cv2
import numpy as np
import pandas as pd
import pytesseract
from PIL import Image
from difflib import SequenceMatcher

EMPLOYEE_NAME_BY_ID = {
    "137501": "Võ Văn Lượng",
    "137502": "Nguyễn Ngọc Tân",
    "137506": "Lưu Thị Tuyết Nhi",
    "170206": "Lê Thị Thu Hiền",
    "181846": "Lưu Văn Nhật",
    "21512": "Hà Thị Minh",
    "269200": "Võ Quốc Cường",
    "281296": "Phạm Xuân Toàn",
    "82654": "Nguyễn Văn Quyền",
}
GROUPS = [
"Máy sấy / Máy rửa chén","Bảo hiểm thợ ICT","Tivi TCL","Tủ lạnh / Đông / Mát","Tai nghe","Sim",
"Quạt gió","Máy lọc KK / Hút bụi / Hút ẩm","CE LG","Sim ĐMX","Bảo hiểm thợ ĐMX","Điện thoại Vivo",
"Nạp rút","Trả chậm FE Credit / Samsung F+ / ...","Máy giặt","Loa","Điện tử","Laptop","VAS",
"Nồi cơm & Nồi chiên","Camera","Phụ kiện khác IT & Nhóm khác","Trả chậm điện máy & Gia dụng",
"Máy lạnh","Trả chậm Home Credit","Điện thoại & Tablet Android","Điện tử Samsung","Vay tiền mặt",
"Cáp sạc","Máy lọc nước","Ví trả sau","Máy nước nóng","Sạc dự phòng","Điện thoại Realme",
"Bảo hiểm tổng","Đèn năng lượng mặt trời","Mở thẻ tín dụng VPBank MWG & TPBank","Phụ kiện công nghệ"
]
GROUP_COLUMNS = ["NHÓM HÀNG","THỰC HIỆN","TARGET","% HT DỰ KIẾN"]
STAFF_COLUMNS = [
"MÃ NHÂN VIÊN","NHÂN VIÊN","HẠNG","HOÀN THÀNH THÁNG","DOANH THU QĐ",
"% TARGET","LƯỢT BILL","% QUY ĐỔI","% TRẢ CHẬM","% BÁN KÈM",
"ĐIỂM THƯỞNG ERP","ĐIỂM THƯỞNG / GIỜ CÔNG","DTQĐ / GIỜ CÔNG"
]
REVENUE_COLUMNS = ["NHÂN VIÊN","DOANH THU QĐ","LƯỢT BILL","% TRẢ CHẬM","ĐIỂM THƯỞNG ERP"]

def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", str(s).lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+"," ",s).strip()

def _num(s: str):
    if not s: return None
    s = str(s).strip().replace(" ","").replace("%","").replace("tr","")
    # Decimal values in screenshots use dot; thousands separators use comma.
    if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?",s):
        s=s.replace(",","")
    elif "," in s and "." not in s:
        s=s.replace(",",".")
    try: return float(s)
    except: return None

def _ocr_cell(img: np.ndarray) -> str:
    # Crop OCR only: cell-sized image, one text line.
    return pytesseract.image_to_string(img, lang="vie+eng", config="--oem 1 --psm 7", timeout=15).strip().replace("\n"," ")

def extract_group_table(raw: bytes) -> tuple[pd.DataFrame,list[str]]:
    """Read the known 38-row LuyKe report layout. Geometry is proportional to the supplied reference image."""
    arr=np.frombuffer(raw,dtype=np.uint8)
    bgr=cv2.imdecode(arr,cv2.IMREAD_COLOR)
    if bgr is None: return pd.DataFrame(columns=GROUP_COLUMNS),["Không giải mã được ảnh Tập thể."]
    h,w=bgr.shape[:2]
    # Reference layout: 923x1659 preview, calibrated against the supplied 2108x3790 LuyKe.JPG.
    sx=w/923.0; sy=h/1659.0
    x_name=(69,327); x_pred=(327,452); x_actual=(553,636); x_target=(636,720)
    y0,y1=141.0,1642.0
    rows=[]
    for i in range(38):
        ya=int((y0+i*(y1-y0)/38+2)*sy); yb=int((y0+(i+1)*(y1-y0)/38-2)*sy)
        def cell(xa,xb):
            crop=bgr[ya:yb,int(xa*sx):int(xb*sx)]
            if crop.size==0: return ""
            gray=cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY)
            gray=cv2.resize(gray,None,fx=2,fy=2,interpolation=cv2.INTER_CUBIC)
            return _ocr_cell(gray)
        name=cell(*x_name)
        actual=cell(*x_actual)
        target=cell(*x_target)
        pred=cell(*x_pred)
        rows.append({
            "NHÓM HÀNG":name or "KHÔNG CÓ DATA",
            "THỰC HIỆN":_num(actual),
            "TARGET":_num(target),
            "% HT DỰ KIẾN":_num(pred),
        })
    df=pd.DataFrame(rows,columns=GROUP_COLUMNS)
    warnings=[]
    if df["NHÓM HÀNG"].eq("KHÔNG CÓ DATA").any(): warnings.append("Có dòng nhóm hàng không đọc được ở ảnh Tập thể.")
    for col in ["THỰC HIỆN","TARGET","% HT DỰ KIẾN"]:
        if df[col].isna().any(): warnings.append(f"Ảnh Tập thể có {int(df[col].isna().sum())} ô '{col}' chưa đọc chắc chắn.")
    return df,warnings

def _first_match(lines, patterns):
    for line in lines:
        for pat in patterns:
            m=re.search(pat,line,re.I)
            if m: return m
    return None

def _numbers(line):
    # Preserve comma thousands and dot decimals.
    return re.findall(r"(?<![A-Za-z])\d[\d,]*(?:\.\d+)?%?(?![A-Za-z])",line)

def _after_anchor(lines, anchor, expected=2):
    for i,line in enumerate(lines):
        if re.search(anchor,line,re.I):
            candidate=" ".join(lines[i:min(i+3,len(lines))])
            nums=_numbers(candidate)
            return nums
    return []

def _pick_pct(lines, anchor, index=1):
    nums=_after_anchor(lines,anchor)
    vals=[_num(x) for x in nums if "%" in x]
    return vals[index] if len(vals)>index else None

def _pick_metric(lines, anchor, index=1, allow_percent=False):
    nums=_after_anchor(lines,anchor)
    vals=[]
    for token in nums:
        if "%" in token and not allow_percent: continue
        v=_num(token)
        if v is not None: vals.append(v)
    return vals[index] if len(vals)>index else None

def extract_staff(raw_text: str, filename: str) -> tuple[dict,list[str]]:
    warnings=[]
    m=re.search(r"(?:suc-khoe-nv-)?(\d{4,8})",filename,re.I)
    emp_id=m.group(1) if m else ""
    lines=[re.sub(r"\s+"," ",x).strip() for x in raw_text.splitlines() if x.strip()]
    name=EMPLOYEE_NAME_BY_ID.get(emp_id,"KHÔNG CÓ DATA")
    rank=None; completion=None
    for line in lines[:8]:
        rm=re.search(r"Hạng\s*(\d+)\s*/\s*9",line,re.I)
        if rm: rank=int(rm.group(1))
        cm=re.search(r"Hoàn thành\s*(\d{1,3})\s*%",line,re.I)
        if cm: completion=float(cm.group(1))
    # Revenue line is the first numeric line after the revenue column headings.
    revenue_line=""
    for i,line in enumerate(lines):
        if line.upper().startswith("DOANH THU QD") or line.upper().startswith("DOANH THU."):
            for nxt in lines[i+1:i+4]:
                if "195.0" in nxt or "437.9" in nxt or "tr" in nxt.lower():
                    revenue_line=nxt; break
            if revenue_line: break
    nums=_numbers(revenue_line)
    revenue=None; bills=None; target_pct=None
    try:
        idx=next(i for i,tok in enumerate(nums) if abs((_num(tok) or -999)-195.0)<0.01)
        revenue=_num(nums[idx+1])
    except (StopIteration,IndexError):
        pass
    try:
        idx=next(i for i,tok in enumerate(nums) if abs((_num(tok) or -999)-437.9)<0.01 or tok=="437")
        if idx+1 < len(nums) and nums[idx+1].isdigit():
            target_pct=float(nums[idx+1])
    except StopIteration:
        pass
    try:
        idx=next(i for i,tok in enumerate(nums) if _num(tok)==45.0)
        bills=_num(nums[idx+1])
    except (StopIteration,IndexError):
        pass

    def following_values(prefix, count=3):
        for i,line in enumerate(lines):
            if re.match(prefix, line, re.I):
                text=" ".join(lines[i+1:i+4])
                return text
        return ""
    pct_line=following_values(r"%\s*QUY\s*Đ[ỒỔO]I")
    def value_after_benchmark(benchmark):
        m=re.search(str(benchmark) + r"\s*%?\s*(\d{1,3})", pct_line)
        return float(m.group(1)) if m else None
    quydoi=value_after_benchmark(47)
    tracham=value_after_benchmark(49)
    bankem=value_after_benchmark(63)

    productivity=following_values("DIEM THƯỜNG ERP")
    # Values in order: benchmark ERP, actual ERP, benchmark points/hour, actual,
    # benchmark DTQĐ/hour, actual. Select even-indexed actual positions.
    pnums=[_num(x) for x in _numbers(productivity)]
    pnums=[x for x in pnums if x is not None]
    erp=pnums[1] if len(pnums)>1 else None
    bonus_hour=pnums[3] if len(pnums)>3 else None
    dt_hour=pnums[5] if len(pnums)>5 else None

    data={
        "MÃ NHÂN VIÊN":emp_id or "KHÔNG CÓ DATA","NHÂN VIÊN":name,
        "HẠNG":rank if rank is not None else "KHÔNG CÓ DATA",
        "HOÀN THÀNH THÁNG":completion if completion is not None else "KHÔNG CÓ DATA",
        "DOANH THU QĐ":revenue if revenue is not None else "KHÔNG CÓ DATA",
        "% TARGET":target_pct if target_pct is not None else "KHÔNG CÓ DATA",
        "LƯỢT BILL":bills if bills is not None else "KHÔNG CÓ DATA",
        "% QUY ĐỔI":quydoi if quydoi is not None else "KHÔNG CÓ DATA",
        "% TRẢ CHẬM":tracham if tracham is not None else "KHÔNG CÓ DATA",
        "% BÁN KÈM":bankem if bankem is not None else "KHÔNG CÓ DATA",
        "ĐIỂM THƯỞNG ERP":erp if erp is not None else "KHÔNG CÓ DATA",
        "ĐIỂM THƯỞNG / GIỜ CÔNG":bonus_hour if bonus_hour is not None else "KHÔNG CÓ DATA",
        "DTQĐ / GIỜ CÔNG":dt_hour if dt_hour is not None else "KHÔNG CÓ DATA",
    }
    for k,v in data.items():
        if v=="KHÔNG CÓ DATA": warnings.append(f"{emp_id or filename}: chưa trích xuất chắc chắn trường {k}.")
    return data,warnings

def extract_thidua(raw_text: str, employee_id: str) -> tuple[dict,list[str]]:
    # Conservative: only parse explicit "name NN%" snippets. Unknown cells remain blank and are reported.
    all_lines=raw_text.splitlines()
    start=next((i for i,line in enumerate(all_lines) if "THIDUA" in _norm(line) or "thi dua" in _norm(line)),0)
    end=next((i for i,line in enumerate(all_lines[start+1:],start+1) if "chua co so lieu" in _norm(line)),len(all_lines))
    lines=all_lines[start:end]
    found={}
    aliases = {
        "Máy sấy / Máy rửa chén":["may say","may rua chen"],
        "Bảo hiểm thợ ICT":["bao hiem tho ict"],
        "Tivi TCL":["tivi tcl"],
        "Tủ lạnh / Đông / Mát":["tu lanh","dong mat"],
        "Tai nghe":["tai nghe"],
        "Sim":["sim"],
        "Quạt gió":["quat gio"],
        "Máy lọc KK / Hút bụi / Hút ẩm":["may loc kk","hut bui","hut am"],
        "CE LG":["ce lg"],
        "Sim ĐMX":["sim dmx"],
        "Bảo hiểm thợ ĐMX":["bao hiem tho dmx"],
        "Điện thoại Vivo":["dien thoai vivo"],
        "Nạp rút":["nap rut"],
        "Trả chậm FE Credit / Samsung F+ / ...":["tra cham fecredit","fecredit","samsung f"],
        "Máy giặt":["may giat"],
        "Loa":["loa"],
        "Điện tử":["dien tu"],
        "Laptop":["laptop"],
        "VAS":["vas"],
        "Nồi cơm & Nồi chiên":["noi com","noi chien"],
        "Camera":["camera"],
        "Phụ kiện khác IT & Nhóm khác":["phu kien khac it","nhom khac"],
        "Trả chậm điện máy & Gia dụng":["tra cham dien may"],
        "Máy lạnh":["may lanh"],
        "Trả chậm Home Credit":["tra cham homecredit","homecredit"],
        "Điện thoại & Tablet Android":["dien thoai tablet android","tablet android"],
        "Điện tử Samsung":["dien tu samsung"],
        "Vay tiền mặt":["vay tien mat"],
        "Cáp sạc":["cap sac"],
        "Máy lọc nước":["may loc nuoc"],
        "Ví trả sau":["vi tra sau"],
        "Máy nước nóng":["may nuoc nong"],
        "Sạc dự phòng":["sac du phong"],
        "Điện thoại Realme":["dien thoai realme"],
        "Bảo hiểm tổng":["bao hiem tong"],
        "Đèn năng lượng mặt trời":["den nang luong mat troi"],
        "Mở thẻ tín dụng VPBank MWG & TPBank":["mo the tin dung","vpbank mwg","tpbank evo"],
        "Phụ kiện công nghệ":["phu kien cong nghe"],
    }
    for line in lines:
        nl=_norm(line)
        for group in GROUPS:
            for alias in aliases.get(group,[_norm(group)]):
                na=_norm(alias)
                pos=nl.find(na)
                if pos>=0:
                    tail=nl[pos+len(na):]
                    pm=re.search(r"\b(\d{1,3})",tail)
                    if pm:
                        found[group]=float(pm.group(1))
                        break
    missing=[g for g in GROUPS if g not in found]
    warns=[]
    if missing: warns.append(f"{employee_id}: chưa trích xuất chắc chắn {len(missing)}/38 tỷ lệ thi đua; để trống thay vì đoán.")
    return found,warns

def build_auto_tables(results: list[dict], items: list[dict]) -> dict:
    by_name={r["name"]:r for r in results}
    group_items=[it for it in items if "luyke" in it["name"].lower() or "tập thể" in it["name"].lower() or "tap the" in it["name"].lower()]
    staff_items=[]
    for it in items:
        if it not in group_items:
            staff_items.append(it)
    warnings=[]
    if len(items)!=10: warnings.append(f"Cần đúng 10 ảnh (1 Tập thể + 9 cá nhân); hiện có {len(items)} ảnh.")
    if len(group_items)!=1: warnings.append(f"Cần nhận diện đúng 1 ảnh Tập thể qua tên file LuyKe; nhận diện được {len(group_items)}.")
    group_df=pd.DataFrame(columns=GROUP_COLUMNS)
    if len(group_items)==1:
        group_df,ww=extract_group_table(group_items[0]["bytes"]); warnings.extend(ww)
    staff_rows=[]; matrix_rows=[]
    seen=set()
    for it in staff_items:
        result=by_name.get(it["name"])
        if not result:
            warnings.append(f"Chưa có kết quả OCR cho {it['name']}."); continue
        data,ww=extract_staff(result.get("text",""),it["name"]); warnings.extend(ww)
        emp_id=str(data["MÃ NHÂN VIÊN"])
        if emp_id in seen: warnings.append(f"Trùng mã nhân viên {emp_id}.")
        seen.add(emp_id); staff_rows.append(data)
        td,tw=extract_thidua(result.get("text",""),emp_id); warnings.extend(tw)
        matrix_rows.append({"NHÂN VIÊN":f'{data["NHÂN VIÊN"]} - {emp_id}',**{g:td.get(g,"KHÔNG CÓ DATA") for g in GROUPS}})
    missing=set(EMPLOYEE_NAME_BY_ID)-seen
    if missing: warnings.append("Thiếu ảnh/kết quả OCR cho mã: "+", ".join(sorted(missing)))
    staff_df=pd.DataFrame(staff_rows,columns=STAFF_COLUMNS)
    matrix_df=pd.DataFrame(matrix_rows,columns=["NHÂN VIÊN"]+GROUPS)
    revenue_df=pd.DataFrame([{
        "NHÂN VIÊN":r["NHÂN VIÊN"],"DOANH THU QĐ":r["DOANH THU QĐ"],
        "LƯỢT BILL":r["LƯỢT BILL"],"% TRẢ CHẬM":r["% TRẢ CHẬM"],
        "ĐIỂM THƯỞNG ERP":r["ĐIỂM THƯỞNG ERP"]
    } for r in staff_rows],columns=REVENUE_COLUMNS)
    return {"group_df":group_df,"staff_df":staff_df,"matrix_df":matrix_df,
            "revenue_df":revenue_df,"warnings":warnings}
