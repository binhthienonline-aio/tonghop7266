# ĐMX SMART REPORT AI – Streamlit FREE V1

## 1. Kiến trúc
- GitHub: lưu mã nguồn và lịch sử thay đổi.
- Streamlit Community Cloud: chạy website Python miễn phí theo điều kiện/hạn mức của dịch vụ.
- Tesseract 5 + traineddata `vie` và `eng`: OCR chữ in tiếng Việt/Anh.
- OpenCV: xoay thẳng, phóng lớn, tăng tương phản, tạo các biến thể tiền xử lý.
- pandas + openpyxl: tạo workbook XLSX nhiều sheet.
- Không gọi Gemini/OpenAI hoặc API AI trả phí.

## 2. Những gì V1 làm
- Upload nhiều ảnh, tối đa 30 ảnh/lượt.
- Upload ZIP (25 MB), tìm ảnh trong các thư mục con.
- Link Google Drive công khai cho một file ảnh.
- Mỗi ảnh chạy 6 lượt OCR (3 biến thể tiền xử lý × PSM 6/11).
- Chọn phương án có confidence cao nhất làm mặc định; người dùng có thể chọn phương án khác.
- Hiển thị nội dung để sửa, bắt buộc tick đã đối chiếu ảnh gốc.
- Xuất Excel gồm:
  - `TONG_HOP`: danh sách ảnh, confidence, nguồn, cảnh báo, SHA256.
  - `RA_SOAT`: văn bản OCR đã chỉnh sửa và trạng thái xác nhận.
  - `OCR_TUNG_TU`: từng từ, confidence, tọa độ bounding box.
  - `CANH_BAO`: cảnh báo theo từng ảnh.
  - `DOC_TRUOC_KHI_DUNG`: phạm vi và giới hạn.

## 3. Điều KHÔNG được hiểu nhầm
- OCR confidence **không phải** xác suất số liệu đúng.
- Tesseract phù hợp nhất với chữ in rõ. Ảnh mờ, nghiêng mạnh, bảng dày, font nhỏ, ảnh chụp màn hình bị nén có thể sai đáng kể.
- 6 lần OCR là các cách tiền xử lý/segment khác nhau của cùng engine, không phải 6 AI độc lập và không tạo ra kiểm chứng độc lập.
- V1 không tự chia cột thành bảng chuẩn, không tự cộng doanh thu, không đối chiếu Target/Lũy kế/Còn thiếu, không tự hợp nhất bảng giữa nhiều ảnh. Không tự sửa số.
- Người dùng phải so từng con số quan trọng với ảnh gốc trước khi tick xác nhận.
- Link Drive private/folder không được hỗ trợ ở V1. Chỉ link file ảnh tải được bằng quyền công khai/chia sẻ link. Không có Google OAuth.
- Chưa có đăng nhập Google, phân quyền nhân viên, cơ sở dữ liệu, lịch sử lưu trữ hoặc backup.
- Dữ liệu upload được xử lý trên máy chủ Streamlit Community Cloud. Không đưa dữ liệu nội bộ/mật lên dịch vụ này nếu chưa được công ty cho phép và chưa đánh giá chính sách bảo mật.
- Mật khẩu ứng dụng là lớp khóa dùng chung cơ bản, không thay thế xác thực cá nhân và phân quyền.
- Community Cloud có thể ngủ khi không có lượt truy cập và tài nguyên có giới hạn. Không cam kết SLA hoặc chạy 24/7.

## 4. Tạo GitHub repository
1. Đăng nhập https://github.com
2. Chọn New repository.
3. Tên: `dmx-smart-report-ai`.
4. Chọn Private nếu mã nguồn là tài sản nội bộ (Streamlit Community Cloud có thể kết nối repo private theo quyền GitHub).
5. Tải các tệp trong thư mục này lên repository, giữ nguyên cấu trúc:
   - `app.py`
   - `requirements.txt`
   - `packages.txt`
   - `.streamlit/config.toml`
   - `.gitignore`
6. Commit changes.

## 5. Triển khai Streamlit Community Cloud
1. Mở https://share.streamlit.io
2. Đăng nhập bằng GitHub và cấp quyền cần thiết.
3. Chọn Create app.
4. Chọn repository `dmx-smart-report-ai`, branch `main`, main file path `app.py`.
5. Deploy.
6. Vào App settings → Secrets, thêm:
   APP_PASSWORD = "mật-khẩu-riêng-dài-và-khó-đoán"
7. Reboot app và kiểm tra trang đăng nhập.

## 6. Kiểm thử trước khi dùng
Dùng 5–10 ảnh đã có số liệu gốc được xác minh:
- Ảnh rõ, ảnh nhỏ, ảnh nghiêng, ảnh mờ, bảng nhiều cột.
- So sánh từng ký tự và từng số: 0/O, 1/I/l, dấu chấm/phẩy, %, ngày tháng, mã hàng.
- Đối chiếu tổng từng dòng/cột bằng phép tính độc lập.
- Kiểm tra ảnh trùng, ZIP nhiều thư mục, ảnh vượt giới hạn.
- Kiểm tra workbook trên Excel và WPS.
- Ghi nhận lỗi theo loại ảnh; không dùng báo cáo chính thức nếu chưa đạt ngưỡng sai số do chủ quy định.

## 7. Giới hạn triển khai miễn phí
Streamlit Community Cloud là dịch vụ miễn phí nhưng có quota/tài nguyên, có thể hibernation sau thời gian không hoạt động. Với dữ liệu cửa hàng, cần đánh giá bảo mật và điều khoản nội bộ trước khi đưa vào.


## 8. Giao diện và trợ lý góp ý
- Giao diện dùng tông xanh navy/xanh dương, điểm nhấn vàng, chữ đậm, tương phản cao.
- Sidebar thể hiện quy trình 5 bước và nút đăng xuất.
- Cuối quy trình có phần **Trợ lý rà soát & góp ý điều chỉnh**.
- FREE V1 hiện là trợ lý dựa trên quy tắc (rule-based): phân tích confidence OCR, ảnh trùng, số có dấu phân cách hàng nghìn, ký tự dễ nhầm và ảnh nhận diện quá ít dòng.
- Trợ lý không tự sửa số liệu, không tự xóa ảnh, không tự quyết định gộp bảng. Góp ý do người dùng nhập được lưu trong sheet `AI_GOP_Y`.
- Đây chưa phải mô hình AI tạo sinh. Muốn có AI hiểu ngữ nghĩa và tự đề xuất cấu trúc bảng nâng cao sẽ cần mô hình chạy local có tài nguyên phù hợp hoặc API AI; không cam kết miễn phí hoàn toàn ở cloud.
