# ĐMX SMART REPORT AI – STREAMLIT V1.1

## Mục tiêu V1.1
Nâng cấp nền tảng xuất Excel theo quy tắc nghiệp vụ đã xác nhận:
- Chỉ lấy đúng 9 nhân viên có ảnh KPI cá nhân.
- Dùng mã nhân viên làm khóa lọc ma trận THI_DUA.
- Ngày dữ liệu = ngày đổ số - 1 ngày lịch; bỏ qua giờ.
- Xuất đúng 6 Sheet theo thứ tự:
  1. `TONG_HOP_TAP_THE`
  2. `NHAN_VIEN`
  3. `THI_DUA`
  4. `DOANH THU`
  5. `CANH_BAO`
  6. `DATA_GOC`
- `THI_DUA`: nhân viên ở cột đầu, Nhóm hàng là các cột tiếp theo; không tô màu.
- `DOANH THU`: đúng thứ tự `NHÂN VIÊN`, `DOANH THU QĐ`, `LƯỢT BILL`, `% TRẢ CHẬM`, `ĐIỂM THƯỞNG ERP`.
- Không tính số Thực hiện dự kiến tuyệt đối.
- Không đổi dữ liệu thiếu thành 0.

## Lưu ý về mức độ tự động
V1.1 đã triển khai bộ khung nghiệp vụ và quy tắc lọc 9 nhân viên, ngày dữ liệu, cấu trúc 6 Sheet. OCR vẫn tạo văn bản thô; người dùng phải dán bảng TSV đã đối chiếu vào bước nhập dữ liệu. V1.1 CHƯA tự trích xuất hoàn chỉnh bảng nhiều cột từ ảnh cá nhân/ma trận. Không được coi đây là tính năng đã hoàn thành.

Hiện chỉ có 2 ảnh KPI mẫu được cung cấp (137502 và 181846). Trước khi xuất báo cáo thực tế cần nhập đủ 9 mã nhân viên có ảnh. Không tự tạo mã còn thiếu.

## Cách triển khai
1. Sao lưu repository hiện tại hoặc tạo branch `v1-1-business-rules`.
2. Thay `app.py`, `requirements.txt`, `packages.txt` bằng các file trong gói này.
3. Giữ `.streamlit/config.toml` và `.gitignore` hiện có nếu đã cấu hình đúng.
4. Commit lên GitHub; Streamlit Cloud sẽ tự deploy.
5. Kiểm tra Logs và chạy bộ thử trước khi dùng số liệu thật.
6. Thêm `APP_PASSWORD` trong Streamlit Secrets.

## Kiểm thử tối thiểu
- Ngày đổ số 09/11/2026 phải ra ngày dữ liệu 08/11/2026.
- Chỉ có 8 hoặc 10 mã nhân viên: không cho xác nhận danh sách 9.
- Mã nhân viên trùng: báo lỗi.
- Ma trận có 11 người: chỉ giữ dòng của 9 mã trong danh sách KPI; không lấy dòng tổng nguồn làm tổng 9 người.
- Target trống phải giữ trống, không biến thành 0.
- `DOANH THU` phải có đúng 5 cột đúng thứ tự.
- Workbook có đúng 6 Sheet và `THI_DUA` không tô màu.

## Bảo mật
Dữ liệu được xử lý trên máy chủ Streamlit Community Cloud. Không tải dữ liệu nội bộ nếu chưa được tổ chức cho phép. Mật khẩu dùng chung chưa thay thế đăng nhập cá nhân/phân quyền.
