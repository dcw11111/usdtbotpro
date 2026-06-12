# USDT Telegram Bot chạy Railway

Bot mẫu có sẵn:
- /start, /help
- /balance: xem số dư giả lập trong database
- /energy: xem lượt chuyển còn lại
- /buy: xem gói mua lượt
- /useenergy: mô phỏng dùng 1 lượt chuyển
- /history: xem lịch sử gần nhất
- /tx <hash>: tra cứu giao dịch đã lưu
- /watch_add <ví TRC20>: admin thêm ví theo dõi
- /watch_remove <ví TRC20>: admin xóa ví theo dõi
- /watch_list: xem ví đang theo dõi
- /addtimes <telegram_id> <số_lần>: admin cộng lượt
- /minustimes <telegram_id> <số_lần>: admin trừ lượt
- /setbalance <telegram_id> <số_usdt>: admin đặt số dư giả lập
- /restart: admin restart bot

Lưu ý: Bot theo dõi tiền vào/ra bằng TronGrid API cho USDT TRC20. Bot không cần private key, chỉ cần địa chỉ ví công khai.
