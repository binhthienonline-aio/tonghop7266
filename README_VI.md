# ĐMX SMART REPORT AI – V1.3 BETA

## Đầu vào
- Đúng 10 ảnh/lượt: 1 ảnh `LuyKe.JPG` và 9 ảnh KPI có tên dạng `suc-khoe-nv-<mã>-202611.png`.
- Tải ZIP thường (không hỗ trợ 7Z trực tiếp), hoặc tải từng ảnh. Link Google Drive hiện chỉ hỗ trợ file công khai đơn lẻ; không hỗ trợ thư mục/private Drive.

## Quy trình
Nạp ảnh → OCR → phân tích tự động → đối chiếu cảnh báo → xuất Excel 6 Sheet.

Đã bỏ nhập mã nhân viên và dán TSV thủ công.

## 6 Sheet
1. `TONG_HOP_TAP_THE`
2. `NHAN_VIEN`
3. `THI_DUA`
4. `DOANH THU`
5. `CANH_BAO`
6. `DATA_GOC`

## Giới hạn cần biết
- Đây là V1.3 BETA. Bộ phân tích dùng bố cục ảnh LuyKe và OCR Tesseract với quy tắc bảo thủ.
- Các ô không nhận diện chắc chắn được ghi `KHÔNG CÓ DATA` và cảnh báo; không tự điền số.
- Nhận diện thi đua từ các ảnh cá nhân chưa hoàn chỉnh trong mọi trường hợp; nhiều ô có thể để `KHÔNG CÓ DATA`.
- Mọi ảnh phải được người dùng đối chiếu trước khi xuất.
- Chưa có kiểm thử triển khai end-to-end trên Streamlit Cloud; phải chạy thử bộ 10 ảnh chuẩn trước khi dùng chính thức.

## Triển khai
Thay `app.py` và thêm `auto_extract.py` vào cùng thư mục gốc GitHub. Giữ nguyên `requirements.txt`, `packages.txt`, `.streamlit/config.toml` và Secrets. Commit hai file, đợi Streamlit deploy.

## Kiểm thử bộ ảnh chuẩn được cung cấp
- Đã giải nén bộ 7Z lồng 7Z trong môi trường phát triển; tìm thấy đúng 10 ảnh.
- Chạy thử OCR 9 ảnh cá nhân bằng Tesseract tiếng Việt + Anh.
- Bộ phân tích tạo được 38 dòng Tập thể, 9 dòng NHÂN_VIÊN và ma trận 9 x 38.
- Trong lần chạy kiểm thử này, một số trường (ví dụ lượt bill của một số ảnh, các ô Tập thể khó đọc và một phần tỷ lệ THI ĐUA) chưa trích xuất chắc chắn. Chúng được đánh dấu KHÔNG CÓ DATA/cảnh báo.
- Kiểm thử cục bộ không đồng nghĩa kiểm thử trên Streamlit Cloud. Hiệu năng có thể chậm hơn do giới hạn CPU của Community Cloud.
