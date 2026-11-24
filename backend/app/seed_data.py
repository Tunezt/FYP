"""The invented content of the demo café (`app.seed`): who it buys from, what it
bought, who comes in. Pure data, no database: `scripts/demo_receipt_images.py`
draws the receipt photos from NOTAS, so the photo and the saved receipt cannot
disagree.

Every phone number below is invented (the 555 block). Nothing here is a real
person; the only real number in the demo is the owner's, passed on the command
line.
"""

HISTORY_DAYS = 56   # two months of sales: October is whole, so "bulan ini vs bulan lalu" is a fair comparison

# (name, phone, address, notes)
SUPPLIERS = [
    ("Toko Supply", "0812-5550-2001", "Pasar Anyar Blok C-4", "Kopi, gula aren, matcha. Bayar tunai, kirim sore."),
    ("Grosir Barokah", "0813-5550-2002", "Jl. Melati No. 12", "Plastik, teh, gula. Minimal order Rp 300.000."),
    ("Pak Udin Sayur", "0857-5550-2003", "Pasar pagi", "Telur dan sayur segar, datang jam 6 pagi."),
    ("CV Susu Nusantara", "0821-5550-2004", "Jl. Industri Raya No. 5", "Susu UHT per dus, kirim 2 hari setelah pesan."),
    ("Roti Pagi Bakery", "0819-5550-2005", "Jl. Kenanga No. 7", "Croissant tiap Senin pagi."),
]

# New raw materials and sellable items on top of the original eleven in seed.py:
# (name, unit, stock, cost, sell, reorder_threshold, popularity weight)
EXTRA_ITEMS = [
    ("Cappuccino", "cup", 30, 8500, 24000, 12, 5),
    ("Pisang Goreng Keju", "porsi", 30, 7000, 20000, 8, 2),
    ("Bubuk Matcha", "kg", 0.8, 320000, 0, 1, 0),
    ("Teh Hitam", "kg", 3, 90000, 0, 1, 0),
    ("Telur Ayam", "kg", 4, 28000, 0, 2, 0),
    ("Cup Plastik 16oz", "pcs", 1200, 650, 0, 300, 0),
]

EXTRA_PREP_STATIONS = {"Cappuccino": "bar", "Pisang Goreng Keju": "kitchen"}
EXTRA_MENU_CATEGORIES = {"Cappuccino": "Kopi", "Pisang Goreng Keju": "Camilan"}

# (item, variant, sell, cost)
EXTRA_VARIANTS = [
    ("Cappuccino", "Large", 29000, 11000),
    ("Teh Tarik", "Large", 19000, 6000),
]

# (item, variant, [(component, quantity, uom code)]) — made to order from the shelf.
EXTRA_RECIPES = [
    ("Cappuccino", "Standar", [("Biji Arabica", 18, "g"), ("Susu UHT", 150, "ml")]),
    ("Cappuccino", "Large", [("Biji Arabica", 24, "g"), ("Susu UHT", 200, "ml")]),
    ("Matcha Latte", "Standar", [("Bubuk Matcha", 8, "g"), ("Susu UHT", 150, "ml"), ("Gula Aren", 10, "g")]),
    ("Matcha Latte", "Large", [("Bubuk Matcha", 11, "g"), ("Susu UHT", 200, "ml"), ("Gula Aren", 14, "g")]),
    ("Teh Tarik", "Standar", [("Teh Hitam", 6, "g"), ("Susu UHT", 100, "ml"), ("Gula Aren", 12, "g")]),
    ("Teh Tarik", "Large", [("Teh Hitam", 8, "g"), ("Susu UHT", 140, "ml"), ("Gula Aren", 16, "g")]),
    ("Nasi Goreng Spesial", "Standar", [("Telur Ayam", 60, "g")]),
]

EXTRA_MODIFIERS = [
    ("Cappuccino", "Tambahan", "multi", False, [("Extra shot", 5000, False), ("Susu oat", 6000, False)]),
    ("Teh Tarik", "Suhu", "single", True, [("Panas", 0, True), ("Dingin", 0, False)]),
]

# Items that sold at a higher cost in the last week: the beans and the milk went up
# and the menu prices did not, which is what the margin alert reports.
DEARER_THIS_WEEK = {"Es Kopi Susu", "Kopi Arabica (cup)", "Americano", "Cappuccino", "Matcha Latte"}
DEARER_FACTOR = "1.75"

# (name, phone, notes, first day offset, last day offset, weight, birthday offset in days from today)
# Day offsets count back from today: a customer with (24, 56) was last seen 24 days ago.
CUSTOMERS = [
    ("Andi Wijaya", "0812-5550-0101", "suka kopi susu, gula sedikit", 0, 56, 6, None),
    ("Rina Kartika", "0813-5550-0102", "selalu bawa laptop, duduk dekat colokan", 0, 56, 4, None),
    ("Bayu Pratama", "0857-5550-0103", "pesan matcha, takeaway", 0, 56, 3, None),
    ("Maya Sari", "0811-5550-0104", "ulang tahun minggu ini", 0, 56, 3, 3),
    ("Dimas Aryo", "0812-5550-0105", "ojol, sering pesan 2 cup sekaligus", 0, 40, 2, None),
    ("Putri Lestari", "0819-5550-0106", "alergi kacang", 0, 56, 2, None),
    ("Hendra Gunawan", "0821-5550-0107", "dulu langganan, sudah lama tidak datang", 26, 56, 2, None),
    ("Wulan Dari", "0813-5550-0108", "pelanggan baru minggu ini", 0, 5, 2, None),
]

# Each nota is a purchase paid at the shop and photographed: it makes a receipt
# (with the photo), an expense and stock in. The dates are fixed: the photo shows its own date, and a re-seed must not make them disagree.
# (file, supplier, date, hour, [(item, quantity, unit, unit_price)], style)
NOTAS = [
    ("nota-toko-supply-1", "Toko Supply", "2026-11-08", 9, [("Gula Aren", 2, "kg", 38000), ("Biji Arabica", 3, "kg", 140000)], "thermal"),
    ("nota-grosir-barokah", "Grosir Barokah", "2026-11-12", 10, [("Cup Plastik 16oz", 500, "pcs", 650), ("Teh Hitam", 1, "kg", 90000)], "thermal"),
    ("nota-pak-udin", "Pak Udin Sayur", "2026-11-17", 7, [("Telur Ayam", 4, "kg", 28000)], "hand"),
    ("nota-toko-supply-2", "Toko Supply", "2026-11-22", 11, [("Bubuk Matcha", 1, "kg", 320000)], "hand"),
]
NOTA_ADDRESS = {
    "Toko Supply": "Pasar Anyar Blok C-4",
    "Grosir Barokah": "Jl. Melati No. 12",
    "Pak Udin Sayur": "Pasar pagi",
}

# Goods that arrived on a purchase order or straight off the truck. These feed
# the price history (Toko Supply raised its beans from Rp 145.000 to Rp 165.000
# yesterday) and the "next delivery" the stock-out alert compares against.
# (supplier, days_ago, hour, po_key or None, [(item, quantity, unit_price)])
RECEIPTS = [
    ("Toko Supply", 25, 10, "po-toko-1", [("Biji Arabica", 5, 140000), ("Gula Aren", 2, 36000)]),
    ("Toko Supply", 11, 10, None, [("Biji Arabica", 5, 145000)]),
    ("Toko Supply", 1, 9, None, [("Biji Arabica", 3, 165000)]),
    ("Grosir Barokah", 20, 11, "po-barokah-1", [("Gula Aren", 3, 36000), ("Teh Hitam", 1, 90000)]),
    ("Grosir Barokah", 9, 11, None, [("Gula Aren", 3, 40000)]),
    ("Grosir Barokah", 3, 11, "po-barokah-2", [("Cup Plastik 16oz", 600, 650)]),
    ("CV Susu Nusantara", 18, 14, None, [("Susu UHT", 36, 16000)]),
    ("CV Susu Nusantara", 6, 14, None, [("Susu UHT", 36, 16500)]),
    ("Roti Pagi Bakery", 28, 7, None, [("Croissant", 12, 12000)]),
    ("Roti Pagi Bakery", 21, 7, None, [("Croissant", 12, 12000)]),
    ("Roti Pagi Bakery", 14, 7, None, [("Croissant", 12, 12000)]),
    ("Roti Pagi Bakery", 7, 7, None, [("Croissant", 12, 12000)]),
]

# Purchase orders: key -> (supplier, status, ordered days ago, expected in days from today, note, [(item, quantity, unit_price)])
# "received" and "partially_received" come from the goods receipts above that name the key;
# the others are written here as they stand.
PURCHASE_ORDERS = {
    "po-toko-1": ("Toko Supply", "received", 27, None, "Stok kopi dan gula awal bulan", [("Biji Arabica", 5, 140000), ("Gula Aren", 2, 36000)]),
    "po-barokah-1": ("Grosir Barokah", "received", 22, None, "Gula dan teh", [("Gula Aren", 3, 36000), ("Teh Hitam", 1, 90000)]),
    "po-barokah-2": ("Grosir Barokah", "partially_received", 5, 2, "Cup baru, dikirim dua tahap: 600 dulu", [("Cup Plastik 16oz", 1000, 650)]),
    "po-susu": ("CV Susu Nusantara", "ordered", 1, 1, "Susu 3 dus, datang besok", [("Susu UHT", 36, 17000)]),
    "po-udin": ("Pak Udin Sayur", "cancelled", 4, None, "Telur kosong di pasar, batal", [("Telur Ayam", 5, 28000)]),
    "po-toko-draft": ("Toko Supply", "draft", 1, 3, "Pesanan minggu depan, tunggu harga baru dari Toko Supply", [("Biji Arabica", 5, 165000), ("Gula Aren", 5, 38000)]),
}

# Extra expenses besides the five in seed.py: (category, description, amount, days_ago)
EXTRA_EXPENSES = [
    ("sewa", "Sewa ruko bulan ini", 3500000, 22),
    ("pemasaran", "Cetak flyer promo September: sisa stok voucher", 150000, 15),
]

# Earlier nights' alerts the owner already read: (type, severity, days_ago, metric, message, details builder key)
OLD_ALERTS = [
    ("supplier_price", "medium", 9, "supplier_price",
     "Harga Gula Aren dari Grosir Barokah naik 11.1%: Rp 36.000 → Rp 40.000 per kg.", "gula"),
    ("stockout_risk", "medium", 9, "days_remaining",
     "Croissant: stok habis dalam ±3.2 hari, pengiriman berikutnya biasanya 7 hari lagi (8 pcs @ 2.5/hari). Pesan sekarang.", "croissant"),
    ("low_stock", "medium", 12, None,
     "Susu UHT tinggal 4 liter (batas minimal 10 liter). Segera belanja.", "susu"),
]

# One sale per voucher use, planned by day offset: the first order of that day with
# enough spend takes the voucher. (day offset, code or ("batch", index))
VOUCHER_USES = [(23, "SELAMAT-DATANG"), (17, "SELAMAT-DATANG"), (10, "SELAMAT-DATANG"), (14, ("batch", 0)), (6, ("batch", 1)), (2, ("batch", 2))]

# Who rang each sale up, by weight. Budi works fewer shifts; the void-rate alert is about him.
STAFF_WEIGHTS = {"owner": 8, "Sari": 42, "Budi": 17, "Dewi": 33}
VOID_NOTES = ["pelanggan batal, salah pesan", "salah input menu", "pelanggan pergi sebelum bayar", "double ketuk", "pesanan salah dibuat"]
