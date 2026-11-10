# ĐMX SMART REPORT AI V1.4 BETA

## Điểm sửa trong V1.4
- Nhận diện ảnh Tập thể là ảnh duy nhất không khớp mẫu tên `suc-khoe-nv-[mã]`; không bắt buộc file phải tên `LuyKe`. Đã kiểm thử với `IMG_5606.JPG` trong bộ ZIP Ngày 9.11.
- Nhận diện 9 ảnh KPI theo mã nhân viên trong tên file.
- Trích xuất KPI cá nhân theo vùng tọa độ từng thẻ chỉ tiêu; OCR toàn trang chỉ hỗ trợ tên/hạng và kiểm tra bổ sung.
- Sửa lỗi phân cách TSV: dùng đúng ký tự Tab.
- Ảnh Tập thể vẫn đọc theo vùng ô bảng 38 dòng; ô không chắc chắn để trống và cảnh báo.

## Cài đặt / cập nhật Streamlit Cloud
1. Giải nén ZIP.
2. Upload toàn bộ nội dung thư mục vào repository GitHub (cùng cấp `app.py`, `auto_extract.py`, `requirements.txt`, `packages.txt`, `.streamlit/`).
3. Commit changes.
4. Giữ nguyên `APP_PASSWORD` trong Streamlit Secrets.
5. Chờ Streamlit deploy, sau đó tải lại trang.

## Đầu vào
- 1 ảnh Tập thể + đúng 9 ảnh KPI cá nhân.
- Ảnh KPI cần tên theo mẫu `suc-khoe-nv-[mã]-202611.png`.
- Ảnh Tập thể có thể mang tên bất kỳ; trong bộ thử nghiệm là `IMG_5606.JPG`.
- Ngày dữ liệu = ngày đổ số trừ 1 ngày, không tính giờ.

## Giới hạn cần biết
- Đây là bản beta; OCR không phải dữ liệu đã xác minh.
- Đã thử trích xuất ảnh Tập thể `IMG_5606.JPG`: 38 tên nhóm hàng, 34/38 ô Thực hiện, 37/38 ô Target và 38/38 ô % HT dự kiến đọc được ở lần chạy thử. Các ô còn lại phải đối chiếu ảnh gốc.
- Parser KPI được cải thiện nhưng cần kiểm tra toàn bộ 9 ảnh trên Streamlit Cloud; không cam kết chính xác tuyệt đối.
- Không hỗ trợ trực tiếp 7Z, thư mục Google Drive riêng tư hoặc OAuth.
