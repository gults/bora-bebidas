# =====================================================================
# BORA BEBIDAS - Sistema de loja, estoque e entregas
# Python + Streamlit + SQLite + Pandas
# =====================================================================
import base64
import hashlib
import hmac
import html
import io
import os
import secrets
import sqlite3
import time
import uuid
import zipfile
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import streamlit as st
from PIL import Image, ImageOps

# ---------------------------------------------------------------------
# CONFIGURAÇÕES (você pode editar esta parte quando quiser)
# ---------------------------------------------------------------------
ROOT = Path(__file__).parent
DATA_DIR = Path(os.environ.get("BORA_DATA_DIR") or ROOT / "data")   # na hospedagem (Render) aponta para o disco permanente
DB = DATA_DIR / "giro.db"          # mesmo banco de antes: seus dados continuam aqui
IMG_DIR = DATA_DIR / "images"      # fotos dos produtos ficam nesta pasta
DATA_DIR.mkdir(parents=True, exist_ok=True)
IMG_DIR.mkdir(exist_ok=True)

LOJA_WHATSAPP = ""        # Ex.: "11999998888" (só números, com DDD). Deixe "" para não mostrar.
TAXA_ENTREGA_PADRAO = 0.0  # Taxa aplicada automaticamente nas entregas. 0 = "a combinar".
# Senha inicial do painel: guardada só como HASH (PBKDF2). O sistema exige trocá-la no primeiro acesso.
INITIAL_PASSWORD_HASH = "pbkdf2_sha256$260000$346cc383c7505d6996c4d2026af325b1$08be813b860006f8ac08f0cc900623a62d506ae203e7cb0a86a267a9867a65ab"
PBKDF2_ITER = 260_000
SESSION_IDLE_MIN = 30      # sessão do painel expira após 30 min sem uso
SESSION_MAX_HOURS = 8      # e, no máximo, 8 horas depois do login
LOCK_WINDOW_S = 15 * 60    # janela de bloqueio após tentativas erradas
MAX_FAILS_IP = 5           # falhas por origem dentro da janela
MAX_FAILS_GLOBAL = 20      # falhas totais dentro da janela (protege contra troca de IP)

STATUSES = ["Recebido", "Em preparação", "Saiu para entrega", "Entregue", "Cancelado"]
ACTIVE_STATUSES = ["Recebido", "Em preparação", "Saiu para entrega"]
CATEGORIES = ["Águas", "Refrigerantes", "Sucos", "Energéticos", "Cervejas", "Destilados",
              "Vinhos", "Complementos", "Outros"]
PAYMENTS = ["Pix na entrega/retirada", "Dinheiro na entrega/retirada", "Cartão na entrega/retirada"]
PDV_PAYMENTS = ["Dinheiro", "Pix", "Cartão de débito", "Cartão de crédito"]
ACCESS_CODE = (os.environ.get("BORA_ACCESS_CODE") or "gestao").strip()   # Painel do dono: endereço terminando com ?area=<este código>
SEED_SAMPLES = os.environ.get("BORA_SEED_SAMPLES", "1") != "0"   # "0" = não criar produtos de exemplo (use em produção)
FIN_PURCHASE = "Compra de mercadoria"
FIN_CATEGORIES = [FIN_PURCHASE, "Aluguel", "Energia e água", "Funcionários", "Combustível e entregas",
                  "Manutenção", "Impostos e taxas", "Marketing", "Retirada do dono", "Outras despesas",
                  "Aporte do dono", "Outras receitas"]

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo(os.environ.get("BORA_TZ", "America/Belem"))   # horário de Brasília/Pará, independente do servidor
except Exception:
    TZ = None


def now_local():
    return datetime.now(TZ).replace(tzinfo=None) if TZ else datetime.now()


def make_icon():
    """Gera um ícone amarelo com a letra B (sem usar emoji)."""
    try:
        from PIL import ImageDraw, ImageFont
        im = Image.new("RGB", (128, 128), "#FFD600")
        d = ImageDraw.Draw(im)
        try:
            f = ImageFont.load_default(size=90)
        except TypeError:
            f = ImageFont.load_default()
        d.text((64, 64), "B", fill="#171717", font=f, anchor="mm")
        return im
    except Exception:
        return None


st.set_page_config(page_title="BORA BEBIDAS", page_icon=make_icon(), layout="wide", initial_sidebar_state="collapsed")

# ---------------------------------------------------------------------
# IDENTIDADE VISUAL: Amarelo #FFD600 | Preto #171717 | Branco | #FFEB70
# ---------------------------------------------------------------------
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700;800&display=swap');
html, body, [class*="css"] {font-family:'DM Sans',sans-serif;}
.stApp {background:#F7F7F3;}
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"], [data-testid="collapsedControl"] {display:none !important;}
#MainMenu, footer {visibility:hidden;}
header[data-testid="stHeader"] {background:transparent;}
.block-container {padding-top:1.4rem !important;max-width:1240px;}
h1,h2,h3,h4 {color:#171717;font-weight:800;letter-spacing:-0.2px;}
.stApp {color:#171717;}
/* Texto sobre fundo CLARO: sempre escuro, mesmo se o navegador/Windows estiver em modo escuro.
   Fica de fora o que tem fundo escuro próprio (hero, topbar, caixas do carrinho, barras de aviso). */
.stApp [data-testid="stMarkdownContainer"] :is(p,li,h1,h2,h3,h4,strong,em):not(.hero *):not([class*="st-key-cart_box"] *):not(.topbar *):not(.alertbar *):not(.cartbar *):not(.metric-dark *) {color:#171717 !important;}
.stApp [data-testid="stCaptionContainer"]:not([class*="st-key-cart_box"] *) {color:#555555 !important;}
.stApp [data-testid="stCaptionContainer"]:not([class*="st-key-cart_box"] *) :is(p,span) {color:#555555 !important;}
[data-testid="stMarkdownContainer"] h3 {border-left:6px solid #FFD600;padding-left:12px !important;}
[data-testid="stForm"] {background:#FFFFFF;border:1px solid #E7E7E0;border-radius:16px;}
[data-baseweb="input"], [data-baseweb="select"] > div, textarea {border-radius:10px !important;}

.topbar {background:#171717;border-radius:18px;padding:18px 26px;border-bottom:5px solid #FFD600;margin-bottom:14px;}
.brand {color:#FFD600;font-weight:800;font-size:28px;letter-spacing:3px;line-height:1;}
.brand small {display:block;color:#FFFFFF;font-weight:500;letter-spacing:2px;font-size:11px;margin-top:6px;}

.st-key-page div[role="radiogroup"], .st-key-cat_chip div[role="radiogroup"], .st-key-adm_page div[role="radiogroup"] {gap:8px;flex-wrap:wrap;}
.st-key-page label, .st-key-cat_chip label, .st-key-adm_page label {background:#FFFFFF;border:1px solid #DEDED6;padding:8px 18px;border-radius:999px;cursor:pointer;margin:0;}
.st-key-page label > div:first-child, .st-key-cat_chip label > div:first-child, .st-key-adm_page label > div:first-child {display:none;}
.st-key-page label p, .st-key-cat_chip label p, .st-key-adm_page label p {font-weight:700;font-size:14px;color:#171717;}
.st-key-page label:has(input:checked), .st-key-cat_chip label:has(input:checked), .st-key-adm_page label:has(input:checked) {background:#171717;border-color:#171717;}
.st-key-page label:has(input:checked) p, .st-key-cat_chip label:has(input:checked) p, .st-key-adm_page label:has(input:checked) p {color:#FFD600 !important;}

.hero {background:#171717;border-radius:22px;padding:42px 40px;margin:10px 0 22px 0;position:relative;overflow:hidden;}
.hero:before {content:"";position:absolute;right:0;top:0;bottom:0;width:30%;background:#FFD600;clip-path:polygon(32% 0,100% 0,100% 100%,0 100%);}
.hero > * {position:relative;z-index:1;}
.hero-tag {display:inline-block;background:#FFD600;color:#171717;font-weight:800;font-size:12px;letter-spacing:2px;padding:6px 12px;border-radius:6px;}
.hero h1 {color:#FFFFFF !important;font-size:46px;line-height:1.05;margin:16px 0 10px 0;max-width:62%;}
.hero h1 span {color:#FFD600 !important;}
.hero p {color:#D8D8D8 !important;font-size:17px;max-width:62%;margin:0 0 18px 0;}
.hero-points span {display:inline-block;color:#FFFFFF !important;border:1px solid #4A4A4A;border-radius:999px;padding:6px 14px;font-size:13px;font-weight:600;margin:0 8px 8px 0;}
@media (max-width:900px) {.hero:before {display:none;} .hero h1, .hero p {max-width:100%;} .hero h1 {font-size:32px;} .hero {padding:28px 22px;}}

.pill-dark {display:inline-block;background:#171717;color:#FFD600;padding:4px 10px;border-radius:99px;font-size:11px;font-weight:800;letter-spacing:.5px;}
.pill-soft {display:inline-block;background:#FFEB70;color:#171717;padding:4px 10px;border-radius:99px;font-size:11px;font-weight:800;letter-spacing:.5px;}

.product {background:#FFFFFF;border:1px solid #E7E7E0;border-radius:18px;overflow:hidden;transition:all .15s ease;}
.product:hover {transform:translateY(-3px);box-shadow:0 10px 26px rgba(0,0,0,.10);}
.product .media {height:210px;background:#F3F3EE;display:flex;align-items:center;justify-content:center;position:relative;}
.product .media img {width:100%;height:100%;object-fit:contain;padding:14px;box-sizing:border-box;mix-blend-mode:multiply;}
.product .ph {text-align:center;}
.product .ph span {display:block;font-size:46px;font-weight:800;color:#CDAE00;letter-spacing:2px;}
.product .ph small {font-size:10px;letter-spacing:2px;color:#8A8A8A;font-weight:700;}
.product .badge {position:absolute;top:10px;left:10px;}
.product .body {padding:14px 16px 12px 16px;border-top:3px solid #FFD600;}
.product .name {font-weight:800;color:#171717;font-size:17px;line-height:1.25;margin:2px 0 4px 0;}
.product .muted {color:#6B6B6B;font-size:12px;letter-spacing:.3px;font-weight:600;text-transform:uppercase;}
.product .stock {color:#6B6B6B;font-size:13px;}
.price {display:inline-block;background:#FFD600;color:#171717;font-size:21px;font-weight:800;padding:2px 10px;border-radius:8px;margin:6px 0;}

div.stButton > button[kind="primary"], div.stButton > button[data-testid="stBaseButton-primary"],
button[kind="primaryFormSubmit"], button[data-testid="stBaseButton-primaryFormSubmit"] {
    background:#FFD600;color:#171717;border:0;border-radius:10px;font-weight:800;}
div.stButton > button[kind="primary"]:hover, div.stButton > button[data-testid="stBaseButton-primary"]:hover,
button[kind="primaryFormSubmit"]:hover, button[data-testid="stBaseButton-primaryFormSubmit"]:hover {
    background:#171717;color:#FFD600;border:0;}
div.stButton > button {border-radius:10px;font-weight:600;}

.metric-card {background:#FFFFFF;border:1px solid #E7E7E0;border-left:6px solid #FFD600;border-radius:14px;padding:14px 16px;margin-bottom:10px;}
.metric-label {font-size:12px;color:#6B6B6B;font-weight:700;letter-spacing:.4px;text-transform:uppercase;}
.metric-value {font-size:26px;font-weight:800;color:#171717;}
.metric-dark {background:#171717;border-color:#171717;border-left:6px solid #FFD600;}
.metric-dark .metric-label {color:#CFCFCF;} .metric-dark .metric-value {color:#FFD600;}

.dcard {background:#FFFFFF;border:1px solid #E7E7E0;border-left:6px solid #FFD600;border-radius:12px;padding:12px 14px;margin-bottom:6px;}
.dcard b {color:#171717;} .dcard .small {color:#555;font-size:13px;}
a.linkbtn {display:inline-block;background:#171717;color:#FFD600 !important;padding:6px 12px;border-radius:8px;font-weight:700;font-size:13px;text-decoration:none;margin:4px 6px 4px 0;}
.stTabs [data-baseweb="tab"] {font-weight:700;}
.stTabs [data-baseweb="tab-highlight"] {background:#FFD600;height:4px;}
.cartbar {background:#FFD600;color:#171717;padding:12px 18px;border-radius:12px;font-weight:700;margin-bottom:14px;}

.cart-ph {width:75px;height:75px;border-radius:12px;background:#FFD600;color:#171717;font-weight:800;font-size:22px;display:flex;align-items:center;justify-content:center;}
.footer {text-align:center;color:#8A8A8A;font-size:12px;letter-spacing:1px;margin:46px 0 10px 0;}
.alertbar {background:#171717;color:#FFD600;padding:12px 18px;border-radius:12px;font-weight:700;margin-bottom:12px;border-left:6px solid #FFD600;}
/* ---- Etapa 1: acabamento, animações e responsividade ---- */
:root {--gold:#E8A400;--orange:#F28C00;}
html, body {overflow-x:hidden;}
@keyframes fadeUp {from {opacity:0;transform:translateY(10px);} to {opacity:1;transform:none;}}
.block-container {animation:fadeUp .4s ease both;}
.product {animation:fadeUp .45s ease both;}
.product .media img {transition:transform .25s ease;}
.product:hover .media img {transform:scale(1.04);}
div.stButton > button, button[kind="primaryFormSubmit"] {transition:transform .12s ease, box-shadow .15s ease, background .15s ease;}
div.stButton > button:hover {transform:translateY(-1px);box-shadow:0 6px 14px rgba(0,0,0,.14);}
div.stButton > button:active {transform:scale(.97);}
.topbar {display:flex;align-items:center;gap:16px;box-shadow:0 8px 24px rgba(0,0,0,.18);}
.topbar img.logo {height:54px;max-width:200px;width:auto;object-fit:contain;}
.hero-tag {background:linear-gradient(90deg,#FFD600,var(--orange));}
.hero-img {background-size:cover;background-position:center;}
.hero-img:before {display:none;}
.hero-img:after {content:"";position:absolute;inset:0;background:linear-gradient(90deg,rgba(23,23,23,.92) 0%,rgba(23,23,23,.65) 55%,rgba(23,23,23,.2) 100%);}
.price {box-shadow:inset 0 -3px 0 rgba(0,0,0,.08);}
.cartbar {border-left:6px solid var(--gold);box-shadow:0 4px 14px rgba(232,164,0,.25);}
@media (max-width:640px) {
  .block-container {padding:.8rem .8rem 3rem !important;}
  .topbar {padding:12px 14px;border-radius:14px;gap:12px;}
  .brand {font-size:21px;letter-spacing:2px;}
  .topbar img.logo {height:40px;max-width:120px;}
  .hero {padding:24px 18px;border-radius:16px;}
  .hero h1 {font-size:28px;} .hero p {font-size:15px;}
  .product .media {height:170px;}
  .st-key-page label, .st-key-cat_chip label, .st-key-adm_page label {padding:10px 16px;min-height:44px;display:flex;align-items:center;}
  div.stButton > button, button[kind="primaryFormSubmit"], button[data-testid="stBaseButton-primaryFormSubmit"] {min-height:46px;}
  [data-testid="stDataFrame"] {max-width:100%;}
  .metric-value {font-size:22px;}
}
@media (prefers-reduced-motion:reduce) {
  *, *:before, *:after {animation:none !important;transition:none !important;scroll-behavior:auto !important;}
}
/* CARRINHO: só os blocos criados com key="cart_box_*" ficam escuros (não afeta outras páginas) */
[class*="st-key-cart_box"] {background:#171717;border:1px solid #383838;border-left:5px solid #FFD600;border-radius:16px;padding:16px;margin-bottom:12px;}
[class*="st-key-cart_box"] p, [class*="st-key-cart_box"] label, [class*="st-key-cart_box"] strong,
[class*="st-key-cart_box"] span, [class*="st-key-cart_box"] h3 {color:#FFFFFF !important;}
[class*="st-key-cart_box"] [data-testid="stCaptionContainer"] p {color:#CCCCCC !important;}
[class*="st-key-cart_box"] [data-testid="stForm"] {background:transparent;border:0;padding:0;}
[class*="st-key-cart_box"] div[data-baseweb="input"], [class*="st-key-cart_box"] div[data-baseweb="base-input"],
[class*="st-key-cart_box"] div[data-baseweb="select"] > div, [class*="st-key-cart_box"] textarea {background:#2B2B2B !important;border-color:#555555 !important;}
[class*="st-key-cart_box"] input, [class*="st-key-cart_box"] textarea {color:#FFFFFF !important;-webkit-text-fill-color:#FFFFFF;}
[class*="st-key-cart_box"] [data-testid="stNumberInput"] button {background:#FFD600 !important;color:#171717 !important;border-color:#FFD600 !important;}
[class*="st-key-cart_box"] button[data-testid="stBaseButton-secondary"] {background:#FFD600;color:#171717;border:0;font-weight:800;}
[class*="st-key-cart_box"] button[data-testid="stBaseButton-secondary"] p {color:#171717;}
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------
# BANCO DE DADOS
# ---------------------------------------------------------------------
@contextmanager
def conn():
    """Abre o banco, salva (commit) se deu certo, desfaz (rollback) se deu erro, e sempre fecha."""
    c = sqlite3.connect(DB, timeout=15)
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def query(sql, params=()):
    c = sqlite3.connect(DB, timeout=15)
    try:
        return pd.read_sql_query(sql, c, params=params)
    finally:
        c.close()


def add_column(c, table, col, ddl):
    """Adiciona uma coluna nova SOMENTE se ela ainda não existe (não apaga nada)."""
    cols = [r[1] for r in c.execute(f"PRAGMA table_info({table})")]
    if col not in cols:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")


def init():
    with conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS products(
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            category TEXT, price REAL NOT NULL, stock INTEGER NOT NULL,
            emoji TEXT DEFAULT '', active INTEGER DEFAULT 1)""")
        c.execute("""CREATE TABLE IF NOT EXISTS orders(
            id INTEGER PRIMARY KEY AUTOINCREMENT, customer TEXT NOT NULL,
            phone TEXT, address TEXT, delivery TEXT, payment TEXT,
            total REAL, status TEXT, created_at TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS order_items(
            id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER,
            product_id INTEGER, product_name TEXT, quantity INTEGER,
            unit_price REAL, subtotal REAL)""")
        # Colunas novas (migração segura)
        add_column(c, "products", "brand", "TEXT")
        add_column(c, "products", "image", "TEXT")
        add_column(c, "orders", "delivery_fee", "REAL DEFAULT 0")
        add_column(c, "orders", "driver", "TEXT")
        add_column(c, "orders", "notes", "TEXT")
        add_column(c, "orders", "stock_restored", "INTEGER DEFAULT 0")
        add_column(c, "orders", "delivered_at", "TEXT")
        add_column(c, "products", "cost", "REAL DEFAULT 0")
        add_column(c, "orders", "channel", "TEXT")
        add_column(c, "orders", "discount", "REAL DEFAULT 0")
        add_column(c, "orders", "scheduled_for", "TEXT")
        add_column(c, "order_items", "unit_cost", "REAL")
        c.execute("""CREATE TABLE IF NOT EXISTS finance_moves(
            id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, category TEXT, description TEXT,
            amount REAL, method TEXT, created_at TEXT)""")
        # Tabela nova: histórico de movimentação de estoque
        c.execute("""CREATE TABLE IF NOT EXISTS stock_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER, product_name TEXT,
            change INTEGER, reason TEXT, created_at TEXT)""")
        c.execute("CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS admin_auth(id INTEGER PRIMARY KEY CHECK(id=1), pw_hash TEXT NOT NULL, must_change INTEGER DEFAULT 1, updated_at TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS admin_recovery(id INTEGER PRIMARY KEY AUTOINCREMENT, code_hash TEXT, created_at TEXT, used_at TEXT)")
        c.execute("""CREATE TABLE IF NOT EXISTS admin_sessions(id TEXT PRIMARY KEY, token_hash TEXT, created_at TEXT, last_seen TEXT,
                     ip TEXT, agent TEXT, revoked_at TEXT, revoked_reason TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS login_attempts(id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, ip TEXT, agent TEXT,
                     kind TEXT, success INTEGER, reason TEXT)""")
        c.execute("CREATE TABLE IF NOT EXISTS admin_audit(id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, event TEXT, detail TEXT, ip TEXT, session_id TEXT)")
        ensure_admin_auth(c)
        if SEED_SAMPLES and c.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0:
            samples = [
                ("Água mineral 1,5L", "Águas", 3.50, 40, ""),
                ("Refrigerante cola 2L", "Refrigerantes", 9.99, 25, ""),
                ("Guaraná 2L", "Refrigerantes", 8.99, 20, ""),
                ("Suco de uva 1L", "Sucos", 7.50, 18, ""),
                ("Água com gás 500ml", "Águas", 3.00, 30, ""),
                ("Energético lata", "Energéticos", 8.50, 15, ""),
                ("Refrigerante laranja 2L", "Refrigerantes", 8.99, 12, ""),
                ("Gelo pacote 3kg", "Complementos", 6.00, 20, ""),
            ]
            c.executemany("INSERT INTO products(name,category,price,stock,emoji) VALUES(?,?,?,?,?)", samples)


def get_setting(key, default=""):
    try:
        with conn() as c:
            r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r[0] if r and r[0] is not None else default
    except sqlite3.Error:
        return default


def set_setting(key, value):
    with conn() as c:
        c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (key, value))


def now_iso():
    return now_local().isoformat(timespec="minutes")


def log_stock(c, pid, name, change, reason):
    c.execute("INSERT INTO stock_log(product_id,product_name,change,reason,created_at) VALUES(?,?,?,?,?)",
              (int(pid), name, int(change), reason, now_iso()))

# ---------------------------------------------------------------------
# FUNÇÕES AUXILIARES
# ---------------------------------------------------------------------
def money(v):
    return ("R$ {:,.2f}".format(float(v))).replace(",", "X").replace(".", ",").replace("X", ".")


def esc(v):
    """Protege o HTML contra textos maliciosos digitados por clientes."""
    return html.escape("" if v is None or (not isinstance(v, str) and pd.isna(v)) else str(v))


def clean(v):
    return "" if v is None or (not isinstance(v, str) and pd.isna(v)) else str(v)


def digits(v):
    return "".join(ch for ch in clean(v) if ch.isdigit())


@st.cache_data(show_spinner=False)
def _b64(path_str, mtime):
    mime = "image/png" if Path(path_str).suffix.lower() == ".png" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(Path(path_str).read_bytes()).decode()


def img_uri(filename):
    filename = clean(filename)
    if not filename:
        return ""
    p = IMG_DIR / Path(filename).name
    return _b64(str(p), p.stat().st_mtime) if p.exists() else ""


def save_image(uploaded):
    """Valida, redimensiona e salva a foto. Devolve o nome do arquivo salvo."""
    data = uploaded.getvalue()
    if len(data) > 5 * 1024 * 1024:
        raise ValueError("A imagem é maior que 5 MB.")
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        raise ValueError("O arquivo enviado não é uma imagem válida.")
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    else:
        img = img.convert("RGB")
    img.thumbnail((900, 900))
    name = uuid.uuid4().hex + ".jpg"
    img.save(IMG_DIR / name, "JPEG", quality=85, optimize=True)
    return name


def save_brand_image(uploaded, kind):
    """Valida e salva a logo (PNG, mantém transparência) ou o banner (JPEG). Devolve o nome do arquivo."""
    data = uploaded.getvalue()
    if len(data) > 5 * 1024 * 1024:
        raise ValueError("A imagem é maior que 5 MB.")
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        raise ValueError("O arquivo enviado não é uma imagem válida.")
    img = ImageOps.exif_transpose(img)
    if kind == "logo":
        img = img.convert("RGBA")
        img.thumbnail((600, 300))
        name = f"logo_{uuid.uuid4().hex}.png"
        img.save(IMG_DIR / name, "PNG", optimize=True)
    else:
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            bg = Image.new("RGB", img.size, (23, 23, 23))
            bg.paste(img, mask=img.split()[-1])
            img = bg
        else:
            img = img.convert("RGB")
        img.thumbnail((1600, 900))
        name = f"banner_{uuid.uuid4().hex}.jpg"
        img.save(IMG_DIR / name, "JPEG", quality=82, optimize=True)
    return name


def topbar(subtitle):
    logo = img_uri(get_setting("logo"))
    mark = f'<img class="logo" alt="BORA BEBIDAS" src="{logo}">' if logo else ""
    return f'<div class="topbar">{mark}<div class="brand">BORA BEBIDAS<small>{esc(subtitle)}</small></div></div>'


def delete_image(filename):
    filename = clean(filename)
    if filename:
        try:
            p = IMG_DIR / Path(filename).name
            if p.exists():
                p.unlink()
        except Exception:
            pass


def kpi(label, value, dark=False):
    cls = "metric-card metric-dark" if dark else "metric-card"
    return f'<div class="{cls}"><div class="metric-label">{esc(label)}</div><div class="metric-value">{esc(value)}</div></div>'


def whatsapp_link(phone, text=""):
    d = digits(phone)
    if len(d) <= 11:
        d = "55" + d
    return f"https://wa.me/{d}?text={quote(text)}"


def maps_link(address):
    return "https://www.google.com/maps/search/?api=1&query=" + quote(clean(address))

# ---------------------------------------------------------------------
# REGRAS DE NEGÓCIO (pedidos, estoque, status)
# ---------------------------------------------------------------------
def _create_order(items, customer, phone, address, delivery, payment, notes, fee,
                 channel="Online", discount=0.0, scheduled_for=""):
    """Cria o pedido/venda e baixa o estoque, tudo de uma vez. Se algo falhar, nada é salvo."""
    with conn() as c:
        rows = []
        for pid, qty in items:
            r = c.execute("SELECT name, price, stock, active, COALESCE(cost,0) FROM products WHERE id=?", (int(pid),)).fetchone()
            if r is None or not r[3]:
                raise ValueError("Um dos produtos não está mais disponível.")
            if qty > r[2]:
                raise ValueError(f"Estoque insuficiente para {r[0]} (restam {r[2]}).")
            rows.append((int(pid), r[0], float(r[1]), int(qty), float(r[4])))
        subtotal = sum(p * q for _, _, p, q, _ in rows)
        discount = min(max(float(discount), 0.0), subtotal)
        total = round(subtotal + float(fee) - discount, 2)
        status = "Entregue" if channel == "Balcão" else "Recebido"
        when = now_iso()
        cur = c.execute(
            "INSERT INTO orders(customer,phone,address,delivery,payment,total,status,created_at,delivery_fee,notes,"
            "stock_restored,channel,discount,scheduled_for,delivered_at) VALUES(?,?,?,?,?,?,?,?,?,?,0,?,?,?,?)",
            (customer, phone, address, delivery, payment, total, status, when, fee, notes,
             channel, discount, scheduled_for, when if status == "Entregue" else None))
        oid = cur.lastrowid
        origem = "Venda balcão" if channel == "Balcão" else "Venda online"
        for pid, name, price, qty, cost in rows:
            c.execute("INSERT INTO order_items(order_id,product_id,product_name,quantity,unit_price,subtotal,unit_cost) VALUES(?,?,?,?,?,?,?)",
                      (oid, pid, name, qty, price, price * qty, cost))
            c.execute("UPDATE products SET stock=stock-? WHERE id=?", (qty, pid))
            log_stock(c, pid, name, -qty, f"{origem} - pedido #{oid}")
        return oid


def _change_status(order_id, new_status):
    """Muda o status. Cancelar DEVOLVE o estoque; reabrir pedido cancelado baixa de novo."""
    with conn() as c:
        o = c.execute("SELECT status, stock_restored FROM orders WHERE id=?", (int(order_id),)).fetchone()
        if o is None:
            raise ValueError("Pedido não encontrado.")
        old, restored = o[0], int(o[1] or 0)
        if new_status == old:
            return "Nada mudou."
        items = c.execute("SELECT product_id, product_name, quantity FROM order_items WHERE order_id=?", (int(order_id),)).fetchall()
        msg = "Andamento atualizado."
        if new_status == "Cancelado" and old != "Cancelado":
            for pid, name, q in items:
                c.execute("UPDATE products SET stock=stock+? WHERE id=?", (q, pid))
                log_stock(c, pid, name, q, f"Cancelamento - pedido #{order_id}")
            c.execute("UPDATE orders SET stock_restored=1 WHERE id=?", (int(order_id),))
            msg = "Pedido cancelado e estoque devolvido."
        elif old == "Cancelado" and new_status != "Cancelado":
            if restored:
                for pid, name, q in items:
                    s = c.execute("SELECT stock FROM products WHERE id=?", (pid,)).fetchone()
                    if s is None or s[0] < q:
                        raise ValueError(f"Sem estoque suficiente de {name} para reabrir este pedido.")
                for pid, name, q in items:
                    c.execute("UPDATE products SET stock=stock-? WHERE id=?", (q, pid))
                    log_stock(c, pid, name, -q, f"Pedido #{order_id} reaberto")
                c.execute("UPDATE orders SET stock_restored=0 WHERE id=?", (int(order_id),))
                msg = "Pedido reaberto e estoque baixado novamente."
        c.execute("UPDATE orders SET status=? WHERE id=?", (new_status, int(order_id)))
        if new_status == "Entregue":
            c.execute("UPDATE orders SET delivered_at=? WHERE id=?", (now_iso(), int(order_id)))
        return msg


def _update_delivery(order_id, driver, fee):
    with conn() as c:
        sub = c.execute("SELECT COALESCE(SUM(subtotal),0) FROM order_items WHERE order_id=?", (int(order_id),)).fetchone()[0]
        c.execute("UPDATE orders SET driver=?, delivery_fee=?, total=? WHERE id=?",
                  (driver.strip(), float(fee), float(sub) + float(fee), int(order_id)))


def _set_stock(pid, mode, qty, reason):
    with conn() as c:
        r = c.execute("SELECT name, stock FROM products WHERE id=?", (int(pid),)).fetchone()
        if r is None:
            raise ValueError("Produto não encontrado.")
        name, old = r
        new = {"Definir novo saldo": qty, "Entrada (+)": old + qty, "Saída (−)": old - qty}[mode]
        if new < 0:
            raise ValueError("O estoque não pode ficar negativo.")
        c.execute("UPDATE products SET stock=? WHERE id=?", (int(new), int(pid)))
        log_stock(c, pid, name, new - old, reason or mode)
        return old, new


def restore_old_cancelled():
    """Devolve ao estoque pedidos que já estavam cancelados antes desta atualização."""
    count = 0
    with conn() as c:
        ids = [r[0] for r in c.execute("SELECT id FROM orders WHERE status='Cancelado' AND COALESCE(stock_restored,0)=0")]
        for oid in ids:
            for pid, name, q in c.execute("SELECT product_id, product_name, quantity FROM order_items WHERE order_id=?", (oid,)).fetchall():
                c.execute("UPDATE products SET stock=stock+? WHERE id=?", (q, pid))
                log_stock(c, pid, name, q, f"Cancelamento antigo - pedido #{oid}")
            c.execute("UPDATE orders SET stock_restored=1 WHERE id=?", (oid,))
            count += 1
    return count

def create_order(items, customer, phone, address, delivery, payment, notes, fee,
                 channel="Online", discount=0.0, scheduled_for=""):
    if channel == "Balcão":
        require_admin()          # venda de balcão só pelo painel autenticado
    oid = _create_order(items, customer, phone, address, delivery, payment, notes, fee, channel, discount, scheduled_for)
    if channel == "Balcão":
        audit("venda_balcao", f"venda #{oid}")
    return oid


def change_status(order_id, new_status):
    require_admin()
    msg = _change_status(order_id, new_status)
    audit("pedido_status", f"pedido #{order_id} -> {new_status}")
    return msg


def update_delivery(order_id, driver, fee):
    require_admin()
    _update_delivery(order_id, driver, fee)
    audit("entrega_atualizada", f"pedido #{order_id} · entregador {driver.strip() or '-'} · taxa {money(fee)}")


def set_stock(pid, mode, qty, reason):
    require_admin()
    old, new = _set_stock(pid, mode, qty, reason)
    audit("estoque_ajustado", f"produto #{pid} · {old} -> {new} · {mode}")
    return old, new


# ---------------------------------------------------------------------
# SEGURANÇA (login do painel)
# ---------------------------------------------------------------------
def now_sec():
    return now_local().isoformat(timespec="seconds")


def hash_password(pw, salt=None):
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, PBKDF2_ITER)
    return f"pbkdf2_sha256${PBKDF2_ITER}${salt.hex()}${dk.hex()}"


def verify_password(pw, stored):
    try:
        algo, it, salt, h = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), int(it))
        return algo == "pbkdf2_sha256" and hmac.compare_digest(dk.hex(), h)
    except Exception:
        return False


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def ensure_admin_auth(c):
    """Cria a credencial do admin na primeira execução. Só o hash é guardado."""
    if c.execute("SELECT 1 FROM admin_auth WHERE id=1").fetchone():
        return
    legacy = None
    try:
        legacy = st.secrets.get("ADMIN_PASSWORD")
    except Exception:
        pass
    legacy = legacy or os.environ.get("BORA_ADMIN_PASSWORD")
    if legacy:      # compatibilidade: quem já tinha definido senha própria mantém essa senha
        h, must = hash_password(str(legacy)), 0
    else:
        h, must = INITIAL_PASSWORD_HASH, 1
    c.execute("INSERT INTO admin_auth(id,pw_hash,must_change,updated_at) VALUES(1,?,?,?)", (h, must, now_sec()))


def get_pw_hash():
    with conn() as c:
        return c.execute("SELECT pw_hash FROM admin_auth WHERE id=1").fetchone()[0]


def must_change_password():
    with conn() as c:
        return bool(c.execute("SELECT must_change FROM admin_auth WHERE id=1").fetchone()[0])


def client_info():
    ip, agent = "desconhecido", ""
    try:
        h = st.context.headers
        ip = (h.get("X-Forwarded-For") or h.get("X-Real-Ip") or "").split(",")[0].strip() or "desconhecido"
        agent = (h.get("User-Agent") or "")[:200]
    except Exception:
        pass
    return ip, agent


def audit(event, detail=""):
    """Registra uma atividade do painel (quem/quando/de onde). Nunca derruba a ação principal."""
    ip, _ = client_info()
    try:
        with conn() as c:
            c.execute("INSERT INTO admin_audit(created_at,event,detail,ip,session_id) VALUES(?,?,?,?,?)",
                      (now_sec(), event, str(detail)[:300], ip, st.session_state.get("admin_sid")))
    except sqlite3.Error:
        pass


def record_attempt(ip, agent, kind, success, reason=""):
    with conn() as c:
        c.execute("INSERT INTO login_attempts(created_at,ip,agent,kind,success,reason) VALUES(?,?,?,?,?,?)",
                  (now_sec(), ip, agent, kind, 1 if success else 0, reason))


def lock_remaining(ip):
    """Segundos de bloqueio restantes (0 = liberado). Contado no BANCO, então recarregar a página não zera."""
    now = now_local()
    cutoff = (now - timedelta(seconds=LOCK_WINDOW_S)).isoformat(timespec="seconds")
    with conn() as c:
        ok_ip = c.execute("SELECT COALESCE(MAX(id),0) FROM login_attempts WHERE success=1 AND ip=?", (ip,)).fetchone()[0]
        ok_any = c.execute("SELECT COALESCE(MAX(id),0) FROM login_attempts WHERE success=1").fetchone()[0]
        f_ip = [r[0] for r in c.execute("SELECT created_at FROM login_attempts WHERE success=0 AND ip=? AND id>? AND created_at>=? ORDER BY id", (ip, ok_ip, cutoff))]
        f_all = [r[0] for r in c.execute("SELECT created_at FROM login_attempts WHERE success=0 AND id>? AND created_at>=? ORDER BY id", (ok_any, cutoff))]
    wait = 0.0
    for lst, limit in ((f_ip, MAX_FAILS_IP), (f_all, MAX_FAILS_GLOBAL)):
        if len(lst) >= limit:
            until = datetime.fromisoformat(lst[-1]) + timedelta(seconds=LOCK_WINDOW_S)
            wait = max(wait, (until - now).total_seconds())
    return max(0, int(wait))


def fmt_wait(sec):
    return f"{sec // 60} min {sec % 60:02d} s" if sec >= 60 else f"{sec} s"


def start_session(ip, agent):
    sid, tok = secrets.token_hex(8), secrets.token_urlsafe(32)
    with conn() as c:
        c.execute("INSERT INTO admin_sessions(id,token_hash,created_at,last_seen,ip,agent) VALUES(?,?,?,?,?,?)",
                  (sid, _sha(tok), now_sec(), now_sec(), ip, agent))
    st.session_state["admin_sid"], st.session_state["admin_token"], st.session_state["admin_ok"] = sid, tok, True
    return sid


def _drop_local_session():
    for k in ("admin_sid", "admin_token", "admin_ok"):
        st.session_state.pop(k, None)


def session_is_valid():
    """Confere NO SERVIDOR (banco) se a sessão existe, não foi revogada e não expirou."""
    sid, tok = st.session_state.get("admin_sid"), st.session_state.get("admin_token")
    if not sid or not tok:
        return False
    now = now_local()
    with conn() as c:
        r = c.execute("SELECT token_hash, created_at, last_seen, revoked_at FROM admin_sessions WHERE id=?", (sid,)).fetchone()
        if r is None or r[3] is not None or not hmac.compare_digest(r[0] or "", _sha(tok)):
            st.session_state["session_msg"] = "Sua sessão foi encerrada. Entre novamente."
            _drop_local_session()
            return False
        idle = now - datetime.fromisoformat(r[2])
        age = now - datetime.fromisoformat(r[1])
        if idle > timedelta(minutes=SESSION_IDLE_MIN) or age > timedelta(hours=SESSION_MAX_HOURS):
            c.execute("UPDATE admin_sessions SET revoked_at=?, revoked_reason=? WHERE id=?", (now_sec(), "expirada", sid))
            st.session_state["session_msg"] = "Sessão expirada por inatividade. Entre novamente."
            _drop_local_session()
            return False
        c.execute("UPDATE admin_sessions SET last_seen=? WHERE id=?", (now_sec(), sid))
    return True


def require_admin():
    """Barreira de permissão: qualquer função administrativa recusa quem não tem sessão válida."""
    if not session_is_valid():
        raise PermissionError("Acesso negado: ação restrita ao administrador.")


def revoke_session(sid, reason="encerrada pelo admin"):
    require_admin()
    with conn() as c:
        c.execute("UPDATE admin_sessions SET revoked_at=?, revoked_reason=? WHERE id=? AND revoked_at IS NULL", (now_sec(), reason, sid))
    audit("sessao_encerrada", f"sessão {sid}")


def revoke_other_sessions(reason):
    keep = st.session_state.get("admin_sid")
    with conn() as c:
        c.execute("UPDATE admin_sessions SET revoked_at=?, revoked_reason=? WHERE revoked_at IS NULL AND id!=?", (now_sec(), reason, keep or ""))


def end_session(reason="logout"):
    sid = st.session_state.get("admin_sid")
    if sid:
        audit("logout", "")
        with conn() as c:
            c.execute("UPDATE admin_sessions SET revoked_at=?, revoked_reason=? WHERE id=? AND revoked_at IS NULL", (now_sec(), reason, sid))
    _drop_local_session()


def active_sessions():
    now = now_local()
    ci = (now - timedelta(minutes=SESSION_IDLE_MIN)).isoformat(timespec="seconds")
    cm = (now - timedelta(hours=SESSION_MAX_HOURS)).isoformat(timespec="seconds")
    return query("SELECT id, created_at, last_seen, ip, agent FROM admin_sessions WHERE revoked_at IS NULL AND last_seen>=? AND created_at>=? ORDER BY last_seen DESC", (ci, cm))


def password_problem(new, current_hash=None):
    low = new.lower()
    if len(new) < 10:
        return "A senha precisa ter pelo menos 10 caracteres."
    if not any(ch.isalpha() for ch in new) or not any(ch.isdigit() for ch in new):
        return "Use letras e números na senha."
    if any(w in low for w in ("12345678", "senha123", "qwerty", "password")):
        return "Essa senha é muito comum. Escolha outra."
    if verify_password(new, INITIAL_PASSWORD_HASH):
        return "Não reutilize a senha inicial."
    if current_hash and verify_password(new, current_hash):
        return "A nova senha precisa ser diferente da atual."
    return ""


def set_password(new):
    with conn() as c:
        c.execute("UPDATE admin_auth SET pw_hash=?, must_change=0, updated_at=? WHERE id=1", (hash_password(new), now_sec()))


def change_password(new):
    require_admin()
    set_password(new)
    revoke_other_sessions("senha alterada")
    audit("senha_alterada", "outras sessões encerradas")


CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _norm_code(v):
    return "".join(ch for ch in v.upper() if ch.isalnum())


def new_recovery_codes(n=8):
    """Gera n códigos de uso único (mostrados só agora); guarda apenas o hash e invalida os antigos."""
    codes = ["".join(secrets.choice(CODE_ALPHABET) for _ in range(10)) for _ in range(n)]
    with conn() as c:
        c.execute("DELETE FROM admin_recovery WHERE used_at IS NULL")
        for code in codes:
            c.execute("INSERT INTO admin_recovery(code_hash,created_at) VALUES(?,?)", (hash_password(code), now_sec()))
    return [f"{x[:5]}-{x[5:]}" for x in codes]


def recovery_codes_left():
    with conn() as c:
        return c.execute("SELECT COUNT(*) FROM admin_recovery WHERE used_at IS NULL").fetchone()[0]


def reset_password_with_code(code, new):
    """Recuperação de acesso: código de uso único + nova senha. Tem o mesmo bloqueio do login."""
    ip, agent = client_info()
    wait = lock_remaining(ip)
    if wait:
        return False, f"Muitas tentativas. Tente novamente em {fmt_wait(wait)}."
    norm = _norm_code(code)
    match = None
    with conn() as c:
        for rid, h in c.execute("SELECT id, code_hash FROM admin_recovery WHERE used_at IS NULL"):
            if len(norm) == 10 and verify_password(norm, h):
                match = rid
                break
    if match is None:
        time.sleep(0.6)
        record_attempt(ip, agent, "recuperacao", False, "código inválido")
        audit("recuperacao_falhou", "código inválido")
        return False, "Código inválido ou já usado."
    problem = password_problem(new, get_pw_hash())
    if problem:
        return False, problem
    with conn() as c:
        c.execute("UPDATE admin_recovery SET used_at=? WHERE id=?", (now_sec(), match))
        c.execute("UPDATE admin_auth SET pw_hash=?, must_change=0, updated_at=? WHERE id=1", (hash_password(new), now_sec()))
        c.execute("UPDATE admin_sessions SET revoked_at=?, revoked_reason=? WHERE revoked_at IS NULL", (now_sec(), "recuperação de acesso"))
    record_attempt(ip, agent, "recuperacao", True, "senha redefinida")
    audit("recuperacao_usada", "senha redefinida; todas as sessões encerradas")
    return True, "Senha redefinida. Entre com a nova senha."


def security_alerts():
    """Alertas de atividade suspeita calculados a partir dos registros reais."""
    out = []
    if must_change_password():
        out.append(("error", "A senha inicial ainda está em uso. Troque-a agora."))
    since = (now_local() - timedelta(hours=24)).isoformat(timespec="seconds")
    hour = (now_local() - timedelta(hours=1)).isoformat(timespec="seconds")
    with conn() as c:
        f1 = c.execute("SELECT COUNT(*) FROM login_attempts WHERE success=0 AND created_at>=?", (hour,)).fetchone()[0]
        f24 = c.execute("SELECT COUNT(*) FROM login_attempts WHERE success=0 AND created_at>=?", (since,)).fetchone()[0]
        oks = c.execute("SELECT id, created_at, ip FROM login_attempts WHERE success=1 AND kind='login' ORDER BY id DESC").fetchall()
        new_ip = None
        if len(oks) >= 2 and oks[0][1] >= since and oks[0][2] not in {r[2] for r in oks[1:]}:
            new_ip = oks[0][2]
        after_fails = None
        if oks and oks[0][1] >= since:
            n = c.execute("SELECT COUNT(*) FROM login_attempts WHERE success=0 AND id<? AND created_at>=?",
                          (oks[0][0], (datetime.fromisoformat(oks[0][1]) - timedelta(minutes=15)).isoformat(timespec="seconds"))).fetchone()[0]
            after_fails = n if n >= 3 else None
    if f1 >= 3:
        out.append(("warning", f"{f1} tentativas de login erradas na última hora."))
    elif f24 >= 1:
        out.append(("info", f"{f24} tentativa(s) de acesso falha(s) nas últimas 24 horas."))
    if new_ip:
        out.append(("warning", f"Login recente de uma origem nunca usada antes ({new_ip}). Se não foi você, troque a senha."))
    if after_fails:
        out.append(("warning", f"Um login bem-sucedido veio logo depois de {after_fails} falhas seguidas."))
    n_sess = len(active_sessions())
    if n_sess > 1:
        out.append(("info", f"{n_sess} sessões ativas ao mesmo tempo. Confira em Sessões."))
    if recovery_codes_left() == 0:
        out.append(("warning", "Você não tem códigos de recuperação. Gere novos na aba Senha."))
    return out


def admin_login():
    st.markdown("## Acesso restrito")
    st.caption("Acesso restrito à equipe da BORA BEBIDAS.")
    if st.session_state.get("session_msg"):
        st.warning(st.session_state.pop("session_msg"))
    if st.session_state.pop("reset_ok", None):
        st.success("Senha redefinida. Entre com a nova senha.")
    ip, agent = client_info()
    wait = lock_remaining(ip)
    if wait:
        st.error(f"Muitas tentativas erradas. Tente novamente em {fmt_wait(wait)}.")
        return
    with st.form("login"):
        pw = st.text_input("Senha", type="password")
        go = st.form_submit_button("Entrar", type="primary")
    if go:
        if verify_password(pw, get_pw_hash()):
            record_attempt(ip, agent, "login", True)
            start_session(ip, agent)
            audit("login_ok", "")
            st.rerun()
        else:
            time.sleep(0.6)
            record_attempt(ip, agent, "login", False, "senha incorreta")
            audit("login_falhou", "senha incorreta")
            left = lock_remaining(ip)
            st.error("Senha incorreta." + (f" Acesso bloqueado por {fmt_wait(left)}." if left else ""))
    with st.expander("Esqueci minha senha"):
        st.caption("Use um dos códigos de recuperação gerados quando você trocou a senha. Cada código vale uma vez.")
        with st.form("recover"):
            code = st.text_input("Código de recuperação", placeholder="XXXXX-XXXXX")
            n1 = st.text_input("Nova senha", type="password")
            n2 = st.text_input("Repita a nova senha", type="password")
            rec = st.form_submit_button("Redefinir senha")
        if rec:
            if n1 != n2:
                st.error("As senhas não conferem.")
            else:
                ok, msg = reset_password_with_code(code, n1)
                if ok:
                    st.session_state["reset_ok"] = True
                    st.rerun()
                else:
                    st.error(msg)
        st.caption("Sem os códigos? Quem tem acesso ao computador onde o sistema roda pode usar o arquivo resetar_senha_admin.py.")


# ---------------------------------------------------------------------
# PÁGINA: LOJA
# ---------------------------------------------------------------------
def initials(name):
    return "".join(w[0] for w in clean(name).split()[:2]).upper() or "B"


def product_card(p):
    stock = int(p["stock"])
    uri = img_uri(p.get("image"))
    if uri:
        media = f'<img src="{uri}">'
    else:
        media = f'<div class="ph"><span>{esc(initials(p["name"]))}</span><small>SEM FOTO</small></div>'
    if stock <= 0:
        badge = '<span class="pill-dark">ESGOTADO</span>'
    elif stock <= 5:
        badge = '<span class="pill-soft">ÚLTIMAS UNIDADES</span>'
    else:
        badge = ""
    brand = clean(p.get("brand"))
    top = f"{esc(brand)} · " if brand else ""
    return (f'<div class="product"><div class="media">{media}<div class="badge">{badge}</div></div>'
            f'<div class="body"><div class="muted">{top}{esc(p["category"])}</div>'
            f'<div class="name">{esc(p["name"])}</div><div class="price">{money(p["price"])}</div>'
            f'<div class="stock">{"Disponível" if stock > 0 else "Indisponível"}</div></div></div>')


def page_store():
    products = query("SELECT * FROM products WHERE active=1 ORDER BY category,name")
    banner = img_uri(get_setting("banner"))
    hero_cls = "hero hero-img" if banner else "hero"
    hero_style = f' style="background-image:url({banner})"' if banner else ""
    st.markdown(f"""<div class="{hero_cls}"{hero_style}><div class="hero-tag">ENTREGA LOCAL</div>
        <h1>Bebida gelada,<br><span>sem complicação.</span></h1>
        <p>Escolha seus produtos, adicione ao carrinho e receba em casa ou retire no depósito.</p>
        <div class="hero-points"><span>Pix, dinheiro ou cartão</span><span>Acompanhe seu pedido</span><span>Estoque sempre atualizado</span></div></div>""",
                unsafe_allow_html=True)
    cart = st.session_state.cart
    if cart:
        n = sum(cart.values())
        tot = 0.0
        for pid, q in cart.items():
            r = products[products["id"] == pid]
            if not r.empty:
                tot += float(r.iloc[0]["price"]) * q
        st.markdown(f'<div class="cartbar">{n} item(ns) no carrinho  |  Subtotal {money(tot)}  |  Abra "Meu carrinho" no menu para finalizar</div>',
                    unsafe_allow_html=True)
    c1, c2 = st.columns([3, 1.4])
    search = c1.text_input("Buscar", placeholder="Buscar por nome ou marca", label_visibility="collapsed")
    sort = c2.selectbox("Ordenar", ["Nome (A-Z)", "Menor preço", "Maior preço"], label_visibility="collapsed")
    cats = ["Todas"] + (sorted(products["category"].dropna().unique().tolist()) if not products.empty else [])
    if st.session_state.get("cat_chip") not in cats:
        st.session_state["cat_chip"] = cats[0]
    sel = st.radio("Categoria", cats, horizontal=True, key="cat_chip", label_visibility="collapsed")
    shown = products.copy()
    if search:
        mask = shown["name"].str.contains(search, case=False, na=False) | shown["brand"].fillna("").str.contains(search, case=False, na=False)
        shown = shown[mask]
    if sel != "Todas":
        shown = shown[shown["category"] == sel]
    if sort == "Menor preço":
        shown = shown.sort_values("price")
    elif sort == "Maior preço":
        shown = shown.sort_values("price", ascending=False)
    else:
        shown = shown.sort_values("name")
    st.markdown(f"### Produtos ({len(shown)})")
    if shown.empty:
        st.info("Nenhum produto encontrado.")
        return
    cols = st.columns(3)
    for i, (_, p) in enumerate(shown.iterrows()):
        pid, stock = int(p["id"]), int(p["stock"])
        with cols[i % 3]:
            st.markdown(product_card(p), unsafe_allow_html=True)
            qc, bc = st.columns([1, 2])
            qty = qc.number_input("Qtd.", min_value=1, max_value=max(1, stock), value=1, key=f"qty_{pid}", label_visibility="collapsed")
            if bc.button("Adicionar ao carrinho", key=f"add_{pid}", type="primary", width="stretch", disabled=stock <= 0):
                st.session_state.cart[pid] = min(st.session_state.cart.get(pid, 0) + int(qty), stock)
                st.toast(f"{p['name']} adicionado ao carrinho")
                st.rerun()
            st.write("")



# ---------------------------------------------------------------------
# PÁGINA: CARRINHO
# ---------------------------------------------------------------------
def page_cart():
    st.markdown("""
        <div style="margin-bottom:22px;">
            <div style="font-size:13px;color:#E8A400;font-weight:800;letter-spacing:2px;">BORA BEBIDAS</div>
            <h2 style="margin:4px 0 0 0;">Seu carrinho</h2>
            <p style="color:#6B6B6B;margin-top:4px;">Confira seus produtos antes de finalizar o pedido.</p>
        </div>
    """, unsafe_allow_html=True)

    # Mensagem do pedido recém-enviado (mostrada depois do rerun, com o carrinho já limpo)
    done = st.session_state.pop("order_done", None)
    if done:
        st.success(f"Pedido #{done} enviado com sucesso! Guarde o número e use 'Acompanhar pedido' no menu, com o seu telefone.")
        st.balloons()
        if digits(LOJA_WHATSAPP):
            st.markdown(f'<a class="linkbtn" href="{whatsapp_link(LOJA_WHATSAPP, f"Olá! Fiz o pedido #{done} na BORA BEBIDAS.")}" target="_blank">Falar com a loja no WhatsApp</a>',
                        unsafe_allow_html=True)

    if not st.session_state.cart:
        if not done:
            with st.container(key="cart_box_empty"):
                st.markdown("### Seu carrinho está vazio")
                st.caption("Volte à loja e escolha seus produtos favoritos.")
        return

    items = []
    for pid, qty in list(st.session_state.cart.items()):
        p = query("SELECT * FROM products WHERE id=? AND active=1", (int(pid),))
        if p.empty:
            del st.session_state.cart[pid]
            continue
        r = p.iloc[0]
        items.append({"id": int(pid), "name": r["name"], "price": float(r["price"]), "stock": int(r["stock"]),
                      "qty": int(qty), "image": r["image"]})

    if not items:
        st.rerun()
        return

    subtotal = 0.0
    st.markdown("### Produtos selecionados")
    for it in items:
        with st.container(key=f"cart_box_item_{it['id']}"):
            col_img, col_info, col_qty, col_remove = st.columns([1.1, 3.8, 1.5, 1.2], vertical_alignment="center")
            uri = img_uri(it["image"])
            if uri:
                col_img.markdown(f'<img src="{uri}" style="width:75px;height:75px;object-fit:cover;border-radius:12px;">',
                                 unsafe_allow_html=True)
            else:
                col_img.markdown(f'<div class="cart-ph">{esc(initials(it["name"]))}</div>', unsafe_allow_html=True)
            col_info.markdown(f"**{esc(it['name'])}**")
            col_info.caption(f"{money(it['price'])} por unidade")
            col_info.markdown(f"**Subtotal: {money(it['price'] * it['qty'])}**")
            new_q = col_qty.number_input("Quantidade", min_value=1, max_value=max(1, it["stock"]),
                                         value=min(it["qty"], max(1, it["stock"])),
                                         key=f"cartqty_{it['id']}", label_visibility="collapsed")
            if new_q != it["qty"]:
                st.session_state.cart[it["id"]] = int(new_q)
                st.rerun()
            if col_remove.button("Remover", key=f"remove_{it['id']}", width="stretch"):
                del st.session_state.cart[it["id"]]
                st.rerun()
        subtotal += it["price"] * it["qty"]

    # --- Recebimento
    st.markdown("### Como deseja receber?")
    with st.container(key="cart_box_ship"):
        delivery = st.radio("Escolha uma opção", ["Entrega", "Retirada no depósito"], horizontal=True, label_visibility="collapsed")
        fee = TAXA_ENTREGA_PADRAO if delivery == "Entrega" else 0.0
        st.caption("A entrega será feita no endereço informado abaixo." if delivery == "Entrega"
                   else "Você poderá retirar seu pedido no depósito.")
        when = st.radio("Quando deseja receber?", ["O quanto antes", "Agendar para outro dia ou horário"], horizontal=True)
        scheduled = ""
        if when != "O quanto antes":
            d1, d2 = st.columns(2)
            sd = d1.date_input("Data", value=now_local().date(), min_value=now_local().date(), key="sched_d")
            stime = d2.time_input("Horário", value=(now_local() + timedelta(hours=1)).time().replace(second=0, microsecond=0), key="sched_t")
            scheduled = f"{sd} {stime.strftime('%H:%M')}"

    # --- Resumo
    st.markdown("### Resumo do pedido")
    with st.container(key="cart_box_sum"):
        c1, c2 = st.columns(2)
        c1.markdown("Produtos")
        c2.markdown(f"<div style='text-align:right;font-weight:700;'>{money(subtotal)}</div>", unsafe_allow_html=True)
        if delivery == "Entrega":
            c1.markdown("Taxa de entrega")
            c2.markdown(f"<div style='text-align:right;font-weight:700;'>{money(fee) if fee > 0 else 'A combinar com o depósito'}</div>",
                        unsafe_allow_html=True)
        st.markdown(
            f"""<div style="border-top:1px solid #383838;margin-top:10px;padding-top:12px;display:flex;justify-content:space-between;align-items:center;">
                <span style="font-size:17px;font-weight:700;color:#FFFFFF;">TOTAL DO PEDIDO</span>
                <span style="font-size:25px;font-weight:900;color:#FFD600;">{money(subtotal + fee)}</span></div>""",
            unsafe_allow_html=True)

    # --- Dados do cliente
    st.markdown("### Dados para finalizar")
    with st.container(key="cart_box_form"):
        with st.form("checkout"):
            customer = st.text_input("Nome", max_chars=80, placeholder="Digite seu nome")
            phone = st.text_input("Telefone / WhatsApp com DDD", max_chars=20, placeholder="Ex.: 91999999999")
            address = st.text_area("Endereço de entrega", max_chars=250,
                                   placeholder="Rua, número, bairro e ponto de referência") if delivery == "Entrega" else ""
            payment = st.selectbox("Forma de pagamento", PAYMENTS)
            notes = st.text_area("Observações", max_chars=250, placeholder="Troco, portaria ou outras informações (opcional)")
            ok = st.form_submit_button("FINALIZAR PEDIDO", type="primary", width="stretch")

    if ok:
        if len(customer.strip()) < 2:
            st.error("Informe seu nome.")
        elif len(digits(phone)) < 10:
            st.error("Informe um telefone válido com DDD.")
        elif delivery == "Entrega" and len(address.strip()) < 8:
            st.error("Informe o endereço completo para entrega.")
        elif scheduled and datetime.strptime(scheduled, "%Y-%m-%d %H:%M") < now_local():
            st.error("Escolha uma data e um horário futuros.")
        else:
            try:
                oid = create_order([(x["id"], x["qty"]) for x in items], customer.strip(), phone.strip(),
                                   address.strip(), delivery, payment, notes.strip(), fee, scheduled_for=scheduled)
                st.session_state.cart = {}
                st.session_state["order_done"] = oid
                st.rerun()
            except ValueError as e:
                st.error(str(e))


# ---------------------------------------------------------------------
# PÁGINA: ACOMPANHAR PEDIDO (cliente)
# ---------------------------------------------------------------------
def page_track():
    st.markdown("## Acompanhar pedido")
    st.caption("Informe o número do pedido e o telefone usado na compra.")
    with st.form("track"):
        oid = st.number_input("Número do pedido", min_value=1, step=1)
        phone = st.text_input("Telefone usado no pedido")
        go = st.form_submit_button("Consultar", type="primary")
    if not go:
        return
    o = query("SELECT * FROM orders WHERE id=?", (int(oid),))
    if o.empty or digits(phone)[-8:] != digits(o.iloc[0]["phone"])[-8:] or len(digits(phone)) < 8:
        st.error("Pedido não encontrado. Confira o número e o telefone.")
        return
    o = o.iloc[0]
    status = o["status"]
    st.markdown(f"### Pedido #{int(o['id'])} — {esc(status)}")
    if status == "Cancelado":
        st.error("Este pedido foi cancelado.")
    else:
        flow = ["Recebido", "Em preparação", "Saiu para entrega", "Entregue"]
        st.progress(flow.index(status) / 3 if status in flow else 0)
        st.caption(" → ".join(flow))
    st.write(f"**Total:** {money(o['total'])}  |  **Pagamento:** {o['payment']}")
    st.write(f"**Recebimento:** {o['delivery']}")
    st.dataframe(query("SELECT product_name AS Produto, quantity AS Qtd, subtotal AS Subtotal FROM order_items WHERE order_id=?", (int(oid),)),
                 width="stretch", hide_index=True)

# ---------------------------------------------------------------------
# FINANCEIRO E DASHBOARD
# ---------------------------------------------------------------------
def _add_move(kind, category, description, amount, day=None, method=""):
    when = (day or now_local().date()).isoformat() + "T" + now_local().strftime("%H:%M")
    with conn() as c:
        c.execute("INSERT INTO finance_moves(kind,category,description,amount,method,created_at) VALUES(?,?,?,?,?,?)",
                  (kind, category, description, float(amount), method, when))


def add_move(kind, category, description, amount, day=None, method=""):
    require_admin()
    _add_move(kind, category, description, amount, day, method)
    audit("lancamento_financeiro", f"{kind} · {category} · {money(amount)}")


def finance_summary(ini, fim):
    """Calcula vendas, custo, despesas, lucro e saldo de caixa entre duas datas (YYYY-MM-DD)."""
    sales = query("SELECT COALESCE(SUM(total),0) v, COUNT(*) n FROM orders WHERE status!='Cancelado' "
                  "AND substr(COALESCE(created_at,''),1,10) BETWEEN ? AND ?", (ini, fim)).iloc[0]
    cogs = query("""SELECT COALESCE(SUM(oi.quantity*COALESCE(oi.unit_cost,p.cost,0)),0) v
                    FROM order_items oi JOIN orders o ON o.id=oi.order_id LEFT JOIN products p ON p.id=oi.product_id
                    WHERE o.status!='Cancelado' AND substr(COALESCE(o.created_at,''),1,10) BETWEEN ? AND ?""", (ini, fim)).iloc[0]["v"]
    mv = query("SELECT kind, category, COALESCE(SUM(amount),0) v FROM finance_moves "
               "WHERE substr(created_at,1,10) BETWEEN ? AND ? GROUP BY kind, category", (ini, fim))
    other_in = float(mv[mv["kind"] == "Entrada"]["v"].sum())
    out_all = float(mv[mv["kind"] == "Saída"]["v"].sum())
    out_ops = float(mv[(mv["kind"] == "Saída") & (mv["category"] != FIN_PURCHASE)]["v"].sum())
    sv, cg = float(sales["v"]), float(cogs)
    return {"sales": sv, "n": int(sales["n"]), "cogs": cg, "gross": sv - cg, "other_in": other_in,
            "out_all": out_all, "out_ops": out_ops, "net": sv - cg - out_ops,
            "cash_in": sv + other_in, "balance": sv + other_in - out_all}


def date_range(period):
    today = now_local().date()
    if period == "Hoje":
        return str(today), str(today)
    if period == "7 dias":
        return str(today - timedelta(days=7)), str(today)
    if period == "30 dias":
        return str(today - timedelta(days=30)), str(today)
    return "0000-01-01", "9999-12-31"


def tab_dashboard():
    period = st.radio("Período", ["Hoje", "7 dias", "30 dias", "Tudo"], horizontal=True, key="dash_period")
    ini, fim = date_range(period)
    f = finance_summary(ini, fim)
    per = query("SELECT * FROM orders WHERE substr(COALESCE(created_at,''),1,10) BETWEEN ? AND ? ORDER BY id DESC", (ini, fim))
    per["channel"] = per["channel"].fillna("Online")
    valid = per[per["status"] != "Cancelado"].copy()
    prods = query("SELECT * FROM products WHERE active=1")
    pend = int(query("SELECT COUNT(*) n FROM orders WHERE status IN ('Recebido','Em preparação','Saiu para entrega')")["n"][0])
    low = int((prods["stock"] <= 5).sum()) if not prods.empty else 0
    stock_cost = float((prods["cost"].fillna(0) * prods["stock"]).sum()) if not prods.empty else 0.0
    stock_sale = float((prods["price"] * prods["stock"]).sum()) if not prods.empty else 0.0
    ticket = f["sales"] / f["n"] if f["n"] else 0.0
    k = st.columns(4)
    k[0].markdown(kpi("Vendas (faturamento)", money(f["sales"]), dark=True), unsafe_allow_html=True)
    k[1].markdown(kpi("Número de vendas", f["n"]), unsafe_allow_html=True)
    k[2].markdown(kpi("Ticket médio", money(ticket)), unsafe_allow_html=True)
    k[3].markdown(kpi("Lucro líquido", money(f["net"]), dark=True), unsafe_allow_html=True)
    k = st.columns(4)
    k[0].markdown(kpi("Entradas no caixa", money(f["cash_in"])), unsafe_allow_html=True)
    k[1].markdown(kpi("Saídas do caixa", money(f["out_all"])), unsafe_allow_html=True)
    k[2].markdown(kpi("Saldo do período", money(f["balance"])), unsafe_allow_html=True)
    k[3].markdown(kpi("Pedidos em andamento", pend), unsafe_allow_html=True)
    k = st.columns(4)
    k[0].markdown(kpi("Estoque a preço de custo", money(stock_cost)), unsafe_allow_html=True)
    k[1].markdown(kpi("Estoque a preço de venda", money(stock_sale)), unsafe_allow_html=True)
    k[2].markdown(kpi("Produtos com estoque baixo (até 5)", low), unsafe_allow_html=True)
    k[3].markdown(kpi("Lucro bruto (vendas - custo)", money(f["gross"])), unsafe_allow_html=True)
    st.caption("Saldo = entradas (vendas + outras entradas) menos saídas registradas no Financeiro. "
               "Lucro líquido = vendas menos custo dos produtos vendidos menos despesas (compras de mercadoria não entram como despesa aqui, porque o custo já é contado nas vendas).")
    a, b = st.columns(2)
    with a:
        st.markdown("#### Vendas por dia")
        if valid.empty:
            st.caption("Sem vendas no período.")
        else:
            valid["dia"] = valid["created_at"].astype(str).str[:10]
            st.bar_chart(valid.groupby("dia")["total"].sum(), color="#FFD600")
    with b:
        st.markdown("#### Vendas por canal (online x balcão)")
        if valid.empty:
            st.caption("Sem vendas no período.")
        else:
            st.bar_chart(valid.groupby("channel")["total"].sum(), color="#171717")
    a, b = st.columns(2)
    with a:
        st.markdown("#### Produtos mais vendidos")
        top = query("""SELECT oi.product_name AS Produto, SUM(oi.quantity) AS Quantidade, SUM(oi.subtotal) AS Receita
                       FROM order_items oi JOIN orders o ON o.id=oi.order_id
                       WHERE o.status!='Cancelado' AND substr(COALESCE(o.created_at,''),1,10) BETWEEN ? AND ?
                       GROUP BY oi.product_name ORDER BY Quantidade DESC LIMIT 8""", (ini, fim))
        if top.empty:
            st.caption("Sem vendas no período.")
        else:
            st.dataframe(top, width="stretch", hide_index=True)
    with b:
        st.markdown("#### Formas de pagamento")
        if valid.empty:
            st.caption("Sem vendas no período.")
        else:
            pay = valid.groupby("payment")["total"].sum().reset_index().rename(columns={"payment": "Pagamento", "total": "Total"})
            st.dataframe(pay, width="stretch", hide_index=True)
    st.markdown("#### Atenção ao estoque")
    lowdf = prods[prods["stock"] <= 5][["name", "stock"]].rename(columns={"name": "Produto", "stock": "Estoque"})
    if lowdf.empty:
        st.success("Nenhum produto com estoque baixo.")
    else:
        st.dataframe(lowdf, width="stretch", hide_index=True)


# ---------------------------------------------------------------------
# PDV: NOVA VENDA NO BALCAO
# ---------------------------------------------------------------------
def tab_pdv():
    st.markdown("### Nova venda no balcão")
    if st.session_state.get("pdv_receipt"):
        st.success("Venda registrada. Estoque e financeiro já foram atualizados.")
        st.code(st.session_state.pdv_receipt, language=None)
        if st.button("Iniciar nova venda", type="primary", key="pdv_new"):
            st.session_state.pdv_receipt = ""
            st.rerun()
        return
    if "pdv_cart" not in st.session_state:
        st.session_state.pdv_cart = {}
    if "pdv_n" not in st.session_state:
        st.session_state.pdv_n = 0
    n = st.session_state.pdv_n
    cart = st.session_state.pdv_cart
    prods = query("SELECT id, name, price, stock FROM products WHERE active=1 ORDER BY name")
    pmap = {int(r.id): r for r in prods.itertuples(index=False)}
    avail = [i for i, r in pmap.items() if r.stock > 0]
    left, right = st.columns([3, 2])
    with left:
        if not avail:
            st.info("Nenhum produto com estoque. Cadastre produtos ou dê entrada no estoque.")
        else:
            with st.form("pdv_add", clear_on_submit=True):
                pid = st.selectbox("Produto (clique e digite para buscar)", avail,
                                   format_func=lambda i: f"{pmap[i].name}  |  {money(pmap[i].price)}  |  estoque {int(pmap[i].stock)}")
                qty = st.number_input("Quantidade", min_value=1, value=1, step=1)
                add = st.form_submit_button("Adicionar à venda", type="primary")
            if add:
                have = cart.get(pid, 0)
                if have + int(qty) > int(pmap[pid].stock):
                    st.error(f"Estoque insuficiente. Disponível: {int(pmap[pid].stock)}.")
                else:
                    cart[pid] = have + int(qty)
                    st.rerun()
        st.markdown("#### Itens da venda")
        subtotal = 0.0
        if not cart:
            st.caption("Nenhum item adicionado.")
        for pid, q in list(cart.items()):
            r = pmap.get(pid)
            if r is None:
                del cart[pid]
                continue
            x, y, z = st.columns([5, 2, 1.4])
            x.markdown(f"**{esc(r.name)}**  \n{q} x {money(r.price)}")
            y.markdown(f"**{money(r.price * q)}**")
            if z.button("Remover", key=f"pdvrm_{pid}"):
                del cart[pid]
                st.rerun()
            subtotal += float(r.price) * q
    with right:
        st.markdown("#### Pagamento")
        dkey = f"pdv_disc_{n}"
        if st.session_state.get(dkey, 0.0) > subtotal:
            st.session_state[dkey] = float(subtotal)
        disc = st.number_input("Desconto (R$)", min_value=0.0, max_value=max(float(subtotal), 0.01), value=0.0, step=1.0, format="%.2f", key=dkey)
        total = round(subtotal - disc, 2)
        st.markdown(kpi("Total a cobrar", money(total), dark=True), unsafe_allow_html=True)
        payment = st.selectbox("Forma de pagamento", PDV_PAYMENTS, key=f"pdv_pay_{n}")
        cust = st.text_input("Nome do cliente (opcional)", max_chars=80, key=f"pdv_cust_{n}")
        received = 0.0
        if payment == "Dinheiro":
            received = st.number_input("Valor recebido (R$)", min_value=0.0, value=0.0, step=5.0, format="%.2f", key=f"pdv_rec_{n}")
            if received > 0:
                if received >= total:
                    st.markdown(f"**Troco: {money(received - total)}**")
                else:
                    st.warning("O valor recebido é menor que o total.")
        if st.button("Finalizar venda", type="primary", disabled=not cart, key="pdv_finish", width="stretch"):
            if payment == "Dinheiro" and 0 < received < total:
                st.error("Valor recebido menor que o total.")
            else:
                try:
                    items = list(cart.items())
                    oid = create_order(items, cust.strip() or "Consumidor final", "", "", "Balcão", payment, "", 0.0,
                                       channel="Balcão", discount=float(disc))
                    lines = ["BORA BEBIDAS", f"Venda #{oid}   {now_local():%d/%m/%Y %H:%M}", "-" * 34]
                    for pid, q in items:
                        r = pmap[pid]
                        lines.append(f"{q}x {r.name[:24]}  {money(r.price * q)}")
                    lines += ["-" * 34, f"Subtotal: {money(subtotal)}"]
                    if disc > 0:
                        lines.append(f"Desconto: -{money(disc)}")
                    lines += [f"TOTAL: {money(total)}", f"Pagamento: {payment}"]
                    if payment == "Dinheiro" and received >= total > 0:
                        lines += [f"Recebido: {money(received)}", f"Troco: {money(received - total)}"]
                    st.session_state.pdv_receipt = "\n".join(lines)
                    st.session_state.pdv_cart = {}
                    st.session_state.pdv_n = n + 1
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))
        if cart and st.button("Cancelar venda (limpar itens)", key="pdv_clear", width="stretch"):
            st.session_state.pdv_cart = {}
            st.session_state.pdv_n = n + 1
            st.rerun()


# ---------------------------------------------------------------------
# FINANCEIRO (caixa)
# ---------------------------------------------------------------------
def tab_finance():
    d1, d2 = st.columns(2)
    ini = d1.date_input("De", value=now_local().date() - timedelta(days=30), key="fin_ini")
    fim = d2.date_input("Até", value=now_local().date(), key="fin_fim")
    ini_s, fim_s = str(ini), str(fim)
    f = finance_summary(ini_s, fim_s)
    k = st.columns(4)
    k[0].markdown(kpi("Vendas", money(f["sales"]), dark=True), unsafe_allow_html=True)
    k[1].markdown(kpi("Outras entradas", money(f["other_in"])), unsafe_allow_html=True)
    k[2].markdown(kpi("Saídas (despesas e compras)", money(f["out_all"])), unsafe_allow_html=True)
    k[3].markdown(kpi("Saldo do período", money(f["balance"]), dark=True), unsafe_allow_html=True)
    k = st.columns(3)
    k[0].markdown(kpi("Custo dos produtos vendidos", money(f["cogs"])), unsafe_allow_html=True)
    k[1].markdown(kpi("Lucro bruto", money(f["gross"])), unsafe_allow_html=True)
    k[2].markdown(kpi("Lucro líquido", money(f["net"]), dark=True), unsafe_allow_html=True)
    st.caption("As vendas (balcão e online, exceto canceladas) entram automaticamente. Registre aqui o que não é venda: "
               "compras, aluguel, contas, salários, retiradas do dono, aportes etc. Para o lucro ficar correto, informe o preço de custo dos produtos.")
    st.markdown("### Registrar movimentação")
    with st.form("fin_form", clear_on_submit=True):
        kind = st.radio("Tipo", ["Saída (dinheiro que sai)", "Entrada (dinheiro que entra, fora das vendas)"], horizontal=True)
        c1, c2 = st.columns(2)
        cat = c1.selectbox("Categoria", FIN_CATEGORIES)
        method = c2.selectbox("Forma", ["Dinheiro", "Pix", "Transferência", "Cartão", "Boleto"])
        desc = st.text_input("Descrição (ex.: conta de luz de novembro)", max_chars=120)
        c3, c4 = st.columns(2)
        amount = c3.number_input("Valor (R$)", min_value=0.01, value=10.0, step=1.0, format="%.2f")
        day = c4.date_input("Data", value=now_local().date())
        go = st.form_submit_button("Registrar", type="primary")
    if go:
        add_move("Saída" if kind.startswith("Saída") else "Entrada", cat, desc.strip(), amount, day, method)
        st.success("Movimentação registrada.")
        st.rerun()
    st.markdown("### Movimentações registradas")
    mv = query("SELECT id, created_at AS Data, kind AS Tipo, category AS Categoria, description AS Descrição, method AS Forma, amount AS Valor "
               "FROM finance_moves WHERE substr(created_at,1,10) BETWEEN ? AND ? ORDER BY created_at DESC, id DESC", (ini_s, fim_s))
    if mv.empty:
        st.caption("Nenhuma movimentação no período.")
    else:
        st.dataframe(mv.drop(columns=["id"]), width="stretch", hide_index=True)
        st.download_button("Baixar movimentações (CSV)", mv.to_csv(index=False).encode("utf-8-sig"), "financeiro_bora_bebidas.csv", "text/csv", on_click=audit, args=("exportacao_csv", "financeiro"))
        outs = mv[mv["Tipo"] == "Saída"].groupby("Categoria")["Valor"].sum()
        if not outs.empty:
            st.markdown("#### Saídas por categoria")
            st.bar_chart(outs, color="#171717")
        with st.expander("Excluir um lançamento (errado ou duplicado)"):
            mid = st.selectbox("Lançamento", mv["id"].tolist(), format_func=lambda i: "{} | {} | {} | {}".format(
                mv.loc[mv.id == i, "Data"].iloc[0], mv.loc[mv.id == i, "Categoria"].iloc[0],
                mv.loc[mv.id == i, "Descrição"].iloc[0], money(mv.loc[mv.id == i, "Valor"].iloc[0])))
            sure = st.checkbox("Confirmo a exclusão deste lançamento", key="fin_del_ok")
            if st.button("Excluir lançamento", disabled=not sure, key="fin_del"):
                with conn() as c:
                    c.execute("DELETE FROM finance_moves WHERE id=?", (int(mid),))
                audit("lancamento_excluido", f"#{mid}")
                st.rerun()
    st.markdown("### Fluxo diário (vendas x saídas)")
    sv = query("SELECT substr(created_at,1,10) AS dia, SUM(total) AS Vendas FROM orders WHERE status!='Cancelado' "
               "AND substr(COALESCE(created_at,''),1,10) BETWEEN ? AND ? GROUP BY substr(created_at,1,10)", (ini_s, fim_s))
    sa = query("SELECT substr(created_at,1,10) AS dia, SUM(amount) AS Saídas FROM finance_moves WHERE kind='Saída' "
               "AND substr(created_at,1,10) BETWEEN ? AND ? GROUP BY substr(created_at,1,10)", (ini_s, fim_s))
    flow = sv.merge(sa, on="dia", how="outer").fillna(0).sort_values("dia")
    if flow.empty:
        st.caption("Sem dados no período.")
    else:
        st.bar_chart(flow.set_index("dia")[["Vendas", "Saídas"]], color=["#FFD600", "#171717"])


# ---------------------------------------------------------------------
# CLIENTES
# ---------------------------------------------------------------------
def tab_clients():
    st.caption("Clientes que informaram telefone nos pedidos (online ou balcão), com histórico de compras.")
    txt = st.text_input("Buscar por nome ou telefone", key="cli_search")
    cl = query("""SELECT customer AS Cliente, phone AS Telefone, COUNT(*) AS Pedidos, SUM(total) AS Total_gasto, MAX(created_at) AS Último_pedido
                  FROM orders WHERE status!='Cancelado' AND COALESCE(phone,'')!='' GROUP BY customer, phone ORDER BY Total_gasto DESC""")
    if txt:
        cl = cl[cl["Cliente"].str.contains(txt, case=False, na=False) | cl["Telefone"].str.contains(txt, na=False)]
    if cl.empty:
        st.info("Nenhum cliente encontrado.")
    else:
        st.dataframe(cl, width="stretch", hide_index=True)
        st.download_button("Baixar clientes (CSV)", cl.to_csv(index=False).encode("utf-8-sig"), "clientes_bora_bebidas.csv", "text/csv", on_click=audit, args=("exportacao_csv", "clientes"))
        st.caption("Dados pessoais: use somente para atender pedidos e guarde com cuidado (LGPD).")


# ---------------------------------------------------------------------
# PEDIDOS E VENDAS
# ---------------------------------------------------------------------
def tab_orders():
    c1, c2, c3 = st.columns([2, 2, 3])
    canal = c1.selectbox("Canal", ["Todos", "Online", "Balcão"], key="ord_canal")
    flt = c2.selectbox("Mostrar", ["Em andamento", "Todos"] + STATUSES, key="ord_filter")
    txt = c3.text_input("Buscar por nome do cliente ou número", key="ord_search")
    orders = query("SELECT * FROM orders ORDER BY id DESC")
    orders["channel"] = orders["channel"].fillna("Online")
    if canal != "Todos":
        orders = orders[orders["channel"] == canal]
    if flt == "Em andamento":
        orders = orders[orders["status"].isin(ACTIVE_STATUSES)]
    elif flt != "Todos":
        orders = orders[orders["status"] == flt]
    if txt:
        orders = orders[orders["customer"].str.contains(txt, case=False, na=False) | (orders["id"].astype(str) == txt.strip().lstrip("#"))]
    if orders.empty:
        st.info("Nenhum pedido encontrado.")
        return
    st.caption(f"{len(orders)} registro(s). Mostrando os 50 mais recentes. Cancelar devolve o estoque automaticamente.")
    for _, o in orders.head(50).iterrows():
        oid = int(o["id"])
        with st.expander(f"#{oid}  |  {o['channel']}  |  {o['customer']}  |  {money(o['total'])}  |  {o['status']}"):
            st.write(f"**Canal:** {o['channel']}  |  **Data:** {o['created_at']}")
            if o["channel"] == "Online":
                st.write(f"**Contato:** {clean(o['phone']) or 'Não informado'}")
                st.write(f"**Recebimento:** {o['delivery']} - {clean(o['address']) or 'Retirada'}")
                if clean(o["scheduled_for"]):
                    st.write(f"**Agendado para:** {o['scheduled_for']}")
                st.write(f"**Taxa de entrega:** {money(o['delivery_fee'] or 0)}")
            if (o["discount"] or 0) > 0:
                st.write(f"**Desconto:** {money(o['discount'])}")
            st.write(f"**Pagamento:** {o['payment']}")
            if clean(o["notes"]):
                st.write(f"**Observações:** {o['notes']}")
            st.dataframe(query("SELECT product_name AS Produto, quantity AS Qtd, subtotal AS Subtotal FROM order_items WHERE order_id=?", (oid,)),
                         width="stretch", hide_index=True)
            cur = STATUSES.index(o["status"]) if o["status"] in STATUSES else 0
            new = st.selectbox("Atualizar andamento", STATUSES, index=cur, key=f"status_{oid}")
            if st.button("Salvar andamento", key=f"save_{oid}", type="primary"):
                try:
                    st.success(change_status(oid, new))
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))


# ---------------------------------------------------------------------
# PAINEL: ENTREGAS
# ---------------------------------------------------------------------
def delivery_card(o):
    links = ""
    if clean(o["address"]):
        links += f'<a class="linkbtn" href="{maps_link(o["address"])}" target="_blank">Abrir no mapa</a>'
    if digits(o["phone"]):
        msg = f"Olá {o['customer']}! Aqui é da BORA BEBIDAS sobre o seu pedido #{int(o['id'])}."
        links += f'<a class="linkbtn" href="{whatsapp_link(o["phone"], msg)}" target="_blank">WhatsApp</a>'
    html_card = (f'<div class="dcard"><b>#{int(o["id"])} · {esc(o["customer"])}</b><br>'
                 f'<span class="small">{esc(o["address"]) or "Retirada no depósito"}</span><br>'
                 f'<span class="small">{esc(o["phone"])} · {esc(o["payment"])} · <b>{money(o["total"])}</b></span>')
    if clean(o["notes"]):
        html_card += f'<br><span class="small">{esc(o["notes"])}</span>'
    if clean(o.get("scheduled_for")):
        html_card += f'<br><span class="small"><b>Agendado para: {esc(o["scheduled_for"])}</b></span>'
    html_card += f'<br>{links}</div>'
    st.markdown(html_card, unsafe_allow_html=True)


def tab_deliveries():
    st.caption("Central de entregas: prepare, saia para a rota e confirme a entrega.")
    orders = query("SELECT * FROM orders ORDER BY id")
    deliv = orders[(orders["delivery"] == "Entrega") & (orders["status"].isin(ACTIVE_STATUSES))]
    prep = deliv[deliv["status"].isin(["Recebido", "Em preparação"])]
    route = deliv[deliv["status"] == "Saiu para entrega"]
    today = now_local().strftime("%Y-%m-%d")
    done = orders[(orders["delivery"] == "Entrega") & (orders["status"] == "Entregue") &
                  (orders["delivered_at"].fillna("").astype(str).str.startswith(today))]
    k = st.columns(3)
    k[0].markdown(kpi("Para preparar", len(prep)), unsafe_allow_html=True)
    k[1].markdown(kpi("Em rota", len(route), dark=True), unsafe_allow_html=True)
    k[2].markdown(kpi("Entregues hoje", len(done)), unsafe_allow_html=True)
    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown("#### Preparar")
        if prep.empty:
            st.caption("Nada para preparar.")
        for _, o in prep.iterrows():
            oid = int(o["id"])
            delivery_card(o)
            drv = st.text_input("Entregador", value=clean(o["driver"]), key=f"drv_{oid}")
            fee = st.number_input("Taxa de entrega (R$)", min_value=0.0, value=float(o["delivery_fee"] or 0), step=1.0, format="%.2f", key=f"fee_{oid}")
            if o["status"] == "Recebido":
                if st.button("Iniciar preparo", key=f"prep_{oid}", width="stretch"):
                    update_delivery(oid, drv, fee)
                    change_status(oid, "Em preparação")
                    st.rerun()
            if st.button("Saiu para entrega", key=f"out_{oid}", type="primary", width="stretch"):
                update_delivery(oid, drv, fee)
                change_status(oid, "Saiu para entrega")
                st.rerun()
            st.divider()
    with col2:
        st.markdown("#### Em rota")
        if route.empty:
            st.caption("Nenhuma entrega em rota.")
        for _, o in route.iterrows():
            oid = int(o["id"])
            delivery_card(o)
            if clean(o["driver"]):
                st.caption(f"Entregador: {o['driver']}")
            if st.button("Marcar como entregue", key=f"done_{oid}", type="primary", width="stretch"):
                change_status(oid, "Entregue")
                st.rerun()
            st.divider()
    with col3:
        st.markdown("#### Entregues hoje")
        if done.empty:
            st.caption("Nenhuma entrega concluída hoje.")
        for _, o in done.iterrows():
            st.markdown(f'<div class="dcard"><b>#{int(o["id"])} · {esc(o["customer"])}</b><br><span class="small">{money(o["total"])} · {esc(o["driver"]) or "sem entregador"}</span></div>',
                        unsafe_allow_html=True)
    st.markdown("#### Retiradas no depósito")
    pick = orders[(orders["delivery"] != "Entrega") & (orders["status"].isin(ACTIVE_STATUSES))]
    if pick.empty:
        st.caption("Nenhuma retirada pendente.")
    for _, o in pick.iterrows():
        oid = int(o["id"])
        delivery_card(o)
        if st.button("Cliente retirou", key=f"pick_{oid}", type="primary"):
            change_status(oid, "Entregue")
            st.rerun()

# ---------------------------------------------------------------------
# PAINEL: PRODUTOS
# ---------------------------------------------------------------------
def category_options():
    existing = query("SELECT DISTINCT category FROM products WHERE category IS NOT NULL")["category"].tolist()
    return sorted(set(CATEGORIES) | set(existing))


def tab_products():
    allp = query("SELECT * FROM products ORDER BY active DESC, category, name")
    t1, t2, t3 = st.tabs(["Catálogo", "Adicionar produto", "Editar ou remover"])
    with t1:
        if allp.empty:
            st.info("Nenhum produto cadastrado.")
        else:
            view = allp.copy()
            view["Foto"] = view["image"].apply(lambda v: "Sim" if img_uri(v) else "Não")
            view["Situação"] = view["active"].apply(lambda v: "Ativo" if v else "Removido da loja")
            view["cost"] = view["cost"].fillna(0)
            view["Margem"] = view.apply(lambda r: f"{(r['price'] - r['cost']) / r['price'] * 100:.0f}%" if r["price"] > 0 else "-", axis=1)
            st.dataframe(view[["name", "brand", "category", "cost", "price", "Margem", "stock", "Foto", "Situação"]].rename(
                columns={"name": "Produto", "brand": "Marca", "category": "Categoria", "cost": "Custo", "price": "Venda", "stock": "Estoque"}),
                width="stretch", hide_index=True)
    with t2:
        with st.form("product_form", clear_on_submit=True):
            name = st.text_input("Nome do produto (ex.: Coca-Cola 2L)", max_chars=100)
            brand = st.text_input("Marca (opcional)", max_chars=60)
            x, y = st.columns(2)
            category = x.selectbox("Categoria", category_options())
            new_cat = y.text_input("Ou crie uma categoria nova (opcional)", max_chars=40)
            a, b, c = st.columns(3)
            price = a.number_input("Preço de venda (R$)", min_value=0.01, value=5.00, step=0.5, format="%.2f")
            cost = b.number_input("Preço de custo (R$)", min_value=0.0, value=0.0, step=0.5, format="%.2f")
            stock = c.number_input("Estoque inicial", min_value=0, value=10, step=1)
            photo = st.file_uploader("Foto do produto (JPG, PNG ou WEBP, até 5 MB)", type=["jpg", "jpeg", "png", "webp"])
            go = st.form_submit_button("Cadastrar produto", type="primary")
        if go:
            if not name.strip():
                st.error("Informe o nome do produto.")
            else:
                try:
                    fname = save_image(photo) if photo else None
                    cat = new_cat.strip() or category
                    with conn() as cn:
                        cur = cn.execute("INSERT INTO products(name,category,price,cost,stock,emoji,brand,image,active) VALUES(?,?,?,?,?,?,?,?,1)",
                                         (name.strip(), cat, float(price), float(cost), int(stock), "", brand.strip(), fname))
                        if int(stock) > 0:
                            log_stock(cn, cur.lastrowid, name.strip(), int(stock), "Estoque inicial")
                    audit("produto_criado", name.strip())
                    st.success("Produto cadastrado!")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))
    with t3:
        if allp.empty:
            st.info("Cadastre um produto primeiro.")
            return
        ids = allp["id"].tolist()
        pid = st.selectbox("Escolha o produto", ids, key="edit_pick",
                           format_func=lambda i: f"{allp.loc[allp.id == i, 'name'].iloc[0]}" + ("" if allp.loc[allp.id == i, 'active'].iloc[0] else "  (removido)"))
        p = allp[allp["id"] == pid].iloc[0]
        pid = int(pid)
        cur_uri = img_uri(p["image"])
        if cur_uri:
            st.image(IMG_DIR / Path(clean(p["image"])).name, width=160, caption="Foto atual")
        cats = category_options()
        with st.form(f"edit_{pid}"):
            name = st.text_input("Nome", value=p["name"], max_chars=100)
            brand = st.text_input("Marca", value=clean(p["brand"]), max_chars=60)
            x, y = st.columns(2)
            category = x.selectbox("Categoria", cats, index=cats.index(p["category"]) if p["category"] in cats else 0)
            new_cat = y.text_input("Ou categoria nova (opcional)", max_chars=40)
            pc1, pc2 = st.columns(2)
            price = pc1.number_input("Preço de venda (R$)", min_value=0.01, value=float(p["price"]), step=0.5, format="%.2f")
            cost = pc2.number_input("Preço de custo (R$)", min_value=0.0, value=float(p["cost"]) if pd.notna(p["cost"]) else 0.0, step=0.5, format="%.2f")
            photo = st.file_uploader("Trocar foto (opcional)", type=["jpg", "jpeg", "png", "webp"], key=f"up_{pid}")
            rm_photo = st.checkbox("Remover foto atual", key=f"rm_{pid}") if cur_uri else False
            go = st.form_submit_button("Salvar alterações", type="primary")
        st.caption("Para alterar a quantidade em estoque, use a aba Estoque.")
        if go:
            if not name.strip():
                st.error("O nome não pode ficar vazio.")
            else:
                try:
                    fname = clean(p["image"]) or None
                    if photo:
                        newf = save_image(photo)
                        delete_image(fname)
                        fname = newf
                    elif rm_photo:
                        delete_image(fname)
                        fname = None
                    with conn() as cn:
                        cn.execute("UPDATE products SET name=?,brand=?,category=?,price=?,cost=?,image=? WHERE id=?",
                                   (name.strip(), brand.strip(), new_cat.strip() or category, float(price), float(cost), fname, pid))
                    audit("produto_editado", f"#{pid} {name.strip()}")
                    st.success("Produto atualizado!")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))
        st.markdown("##### Remover da loja")
        if p["active"]:
            if st.button("Remover da loja (esconde dos clientes, mantém o histórico)", key=f"hide_{pid}"):
                with conn() as cn:
                    cn.execute("UPDATE products SET active=0 WHERE id=?", (pid,))
                audit("produto_ocultado", f"#{pid}")
                st.rerun()
        else:
            if st.button("Reativar na loja", key=f"show_{pid}", type="primary"):
                with conn() as cn:
                    cn.execute("UPDATE products SET active=1 WHERE id=?", (pid,))
                audit("produto_reativado", f"#{pid}")
                st.rerun()
        sold = query("SELECT COUNT(*) n FROM order_items WHERE product_id=?", (pid,))["n"][0]
        if sold == 0:
            sure = st.checkbox("Confirmo que quero EXCLUIR DEFINITIVAMENTE este produto", key=f"sure_{pid}")
            if st.button("Excluir definitivamente", key=f"del_{pid}", disabled=not sure):
                delete_image(p["image"])
                with conn() as cn:
                    cn.execute("DELETE FROM products WHERE id=?", (pid,))
                audit("produto_excluido", f"#{pid}")
                st.rerun()
        else:
            st.caption("Este produto já foi vendido, por isso só pode ser removido da loja (o histórico de vendas fica preservado).")

# ---------------------------------------------------------------------
# PAINEL: ESTOQUE
# ---------------------------------------------------------------------
def tab_stock():
    prods = query("SELECT * FROM products WHERE active=1 ORDER BY stock, name")
    limit = st.slider("Considerar estoque baixo até (unidades)", 1, 30, 5)
    def status(s):
        return "Esgotado" if s <= 0 else ("Baixo" if s <= limit else "OK")
    if prods.empty:
        st.info("Nenhum produto ativo.")
    else:
        v = prods[["name", "category", "stock"]].copy()
        v["Situação"] = v["stock"].apply(status)
        st.dataframe(v.rename(columns={"name": "Produto", "category": "Categoria", "stock": "Estoque"}), width="stretch", hide_index=True)
        st.markdown("#### Ajustar estoque")
        with st.form("stock_form", clear_on_submit=True):
            pid = st.selectbox("Produto", prods["id"].tolist(), format_func=lambda i: f"{prods.loc[prods.id == i, 'name'].iloc[0]} (atual: {int(prods.loc[prods.id == i, 'stock'].iloc[0])})")
            mode = st.radio("Tipo de ajuste", ["Entrada (+)", "Saída (−)", "Definir novo saldo"], horizontal=True)
            qty = st.number_input("Quantidade", min_value=0, value=1, step=1)
            reason = st.text_input("Motivo (opcional: compra de fornecedor, quebra, contagem...)", max_chars=100)
            paid = st.number_input("Valor pago ao fornecedor (R$) - só para ENTRADA, opcional", min_value=0.0, value=0.0, step=1.0, format="%.2f")
            upd_cost = st.checkbox("Atualizar o preço de custo do produto com este valor (valor pago / quantidade)", value=True)
            go = st.form_submit_button("Aplicar ajuste", type="primary")
        if go:
            try:
                old, new = set_stock(pid, mode, int(qty), reason)
                pname = prods.loc[prods.id == pid, "name"].iloc[0]
                if mode == "Entrada (+)" and paid > 0:
                    add_move("Saída", FIN_PURCHASE, f"Entrada de estoque: {pname} ({int(qty)} un.)", paid)
                    if upd_cost and qty > 0:
                        with conn() as cn:
                            cn.execute("UPDATE products SET cost=? WHERE id=?", (round(paid / qty, 2), int(pid)))
                st.success(f"Estoque alterado de {old} para {new}.")
                st.rerun()
            except ValueError as e:
                st.error(str(e))
    pend = query("SELECT COUNT(*) n FROM orders WHERE status='Cancelado' AND COALESCE(stock_restored,0)=0")["n"][0]
    if pend:
        st.warning(f"Existem {int(pend)} pedido(s) cancelado(s) antigos cujo estoque nunca foi devolvido.")
        if st.button("Devolver estoque dos cancelados antigos"):
            st.success(f"{restore_old_cancelled()} pedido(s) corrigido(s).")
            st.rerun()
    st.markdown("#### Histórico de movimentações")
    hist = query("SELECT created_at AS Data, product_name AS Produto, change AS Alteração, reason AS Motivo FROM stock_log ORDER BY id DESC LIMIT 100")
    if hist.empty:
        st.caption("As movimentações aparecerão aqui.")
    else:
        st.dataframe(hist, width="stretch", hide_index=True)

# ---------------------------------------------------------------------
# PAINEL: RELATÓRIOS
# ---------------------------------------------------------------------
def tab_reports():
    d1, d2 = st.columns(2)
    ini = d1.date_input("De", value=now_local().date() - timedelta(days=30), key="rep_ini")
    fim = d2.date_input("Até", value=now_local().date(), key="rep_fim")
    orders = query("SELECT * FROM orders WHERE substr(created_at,1,10) BETWEEN ? AND ? ORDER BY id DESC", (str(ini), str(fim)))
    if orders.empty:
        st.info("Sem pedidos no período.")
        return
    valid = orders[orders["status"] != "Cancelado"]
    st.write(f"**{len(valid)}** pedidos válidos · faturamento **{money(valid['total'].sum())}**")
    st.dataframe(orders[["id", "channel", "customer", "delivery", "payment", "discount", "total", "status", "created_at"]], width="stretch", hide_index=True)
    items = query("""SELECT o.id AS pedido, o.created_at, o.status, oi.product_name, oi.quantity, oi.unit_price, oi.subtotal
                     FROM order_items oi JOIN orders o ON o.id=oi.order_id
                     WHERE substr(o.created_at,1,10) BETWEEN ? AND ? ORDER BY o.id DESC""", (str(ini), str(fim)))
    c1, c2, c3 = st.columns(3)
    c1.download_button("Baixar pedidos (CSV)", orders.to_csv(index=False).encode("utf-8-sig"), "pedidos_bora_bebidas.csv", "text/csv", on_click=audit, args=("exportacao_csv", "pedidos"))
    c2.download_button("Baixar itens vendidos (CSV)", items.to_csv(index=False).encode("utf-8-sig"), "itens_bora_bebidas.csv", "text/csv", on_click=audit, args=("exportacao_csv", "itens vendidos"))
    c3.download_button("Baixar produtos (CSV)", query("SELECT id,name,brand,category,price,stock,active FROM products").to_csv(index=False).encode("utf-8-sig"),
                       "produtos_bora_bebidas.csv", "text/csv", on_click=audit, args=("exportacao_csv", "produtos"))
    st.caption("Os arquivos de pedidos contêm dados pessoais de clientes (nome, telefone, endereço). Guarde-os com cuidado.")

# ---------------------------------------------------------------------
# PAINEL: SEGURANÇA E BACKUP
# ---------------------------------------------------------------------
MAX_RESTORE_BYTES = 300 * 1024 * 1024
IMG_EXTS = (".jpg", ".jpeg", ".png")


def _copy_db(src_path, dst_path):
    """Cópia consistente de um banco SQLite (usa a API de backup do próprio SQLite)."""
    a = sqlite3.connect(src_path, timeout=15)
    b = sqlite3.connect(dst_path, timeout=15)
    try:
        a.backup(b)
    finally:
        b.close()
        a.close()


def make_full_backup():
    """ZIP com o banco de dados + todas as fotos (logo, banner e produtos)."""
    require_admin()
    buf = io.BytesIO()
    tmp = DATA_DIR / f"_bk_{uuid.uuid4().hex}.db"
    try:
        _copy_db(DB, tmp)
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(tmp, "giro.db")
            for f in sorted(IMG_DIR.glob("*")):
                if f.is_file() and f.suffix.lower() in IMG_EXTS:
                    z.write(f, f"images/{f.name}")
    finally:
        tmp.unlink(missing_ok=True)
    return buf.getvalue()


def restore_backup(uploaded):
    """Troca os dados atuais pelos de um backup (.zip completo ou só giro.db). Guarda antes uma cópia do estado atual."""
    require_admin()
    data = uploaded.getvalue()
    if len(data) > MAX_RESTORE_BYTES:
        raise ValueError("O arquivo é grande demais (máximo 300 MB).")
    tmp_db = DATA_DIR / f"_restore_{uuid.uuid4().hex}.db"
    images = []
    try:
        if data[:2] == b"PK":
            try:
                z = zipfile.ZipFile(io.BytesIO(data))
                infos = z.infolist()
            except zipfile.BadZipFile:
                raise ValueError("O arquivo ZIP está corrompido.")
            if sum(i.file_size for i in infos) > MAX_RESTORE_BYTES * 2:
                raise ValueError("O conteúdo do ZIP é grande demais.")
            if "giro.db" not in z.namelist():
                raise ValueError("Este ZIP não tem o arquivo giro.db. Use um backup completo gerado pelo sistema.")
            tmp_db.write_bytes(z.read("giro.db"))
            for i in infos:
                n = i.filename
                if i.is_dir() or not n.startswith("images/"):
                    continue
                base = n[len("images/"):]
                # só nome simples dentro de images/ (bloqueia "../" e subpastas) e só formatos de imagem
                if base and base == Path(base).name and "\\" not in base and Path(base).suffix.lower() in IMG_EXTS:
                    images.append((base, z.read(i)))
        elif data[:16] == b"SQLite format 3\x00":
            tmp_db.write_bytes(data)
        else:
            raise ValueError("Envie um backup .zip (completo) ou um arquivo giro.db.")
        try:
            chk = sqlite3.connect(tmp_db)
            try:
                ok = chk.execute("PRAGMA integrity_check").fetchone()[0]
                tables = {r[0] for r in chk.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            finally:
                chk.close()
        except sqlite3.DatabaseError:
            raise ValueError("O arquivo não é um banco de dados válido.")
        if ok != "ok" or not {"products", "orders", "order_items"} <= tables:
            raise ValueError("Este arquivo não parece um backup da BORA BEBIDAS.")
        keep = DATA_DIR / "backups"
        keep.mkdir(exist_ok=True)
        _copy_db(DB, keep / f"antes_da_restauracao_{now_local():%Y%m%d_%H%M%S}.db")
        _copy_db(tmp_db, DB)
        for name, blob in images:
            (IMG_DIR / name).write_bytes(blob)
    finally:
        tmp_db.unlink(missing_ok=True)
    init()      # completa tabelas/colunas novas se o backup for de uma versão antiga
    return len(images)


def _dt(v):
    return clean(v).replace("T", " ")


def tab_security():
    t = st.tabs(["Visão geral", "Sessões", "Acessos", "Atividades", "Senha e recuperação", "Backup"])
    with t[0]:
        alerts = security_alerts()
        if not alerts:
            st.success("Nenhum alerta. Os acessos recentes parecem normais.")
        for lvl, msg in alerts:
            {"error": st.error, "warning": st.warning, "info": st.info}[lvl](msg)
        since = (now_local() - timedelta(hours=24)).isoformat(timespec="seconds")
        fails = int(query("SELECT COUNT(*) n FROM login_attempts WHERE success=0 AND created_at>=?", (since,))["n"][0])
        oks = query("SELECT created_at, ip FROM login_attempts WHERE success=1 AND kind='login' ORDER BY id DESC LIMIT 2")
        prev = _dt(oks.iloc[1]["created_at"]) if len(oks) > 1 else "primeiro acesso"
        k1, k2, k3, k4 = st.columns(4)
        k1.markdown(kpi("Login anterior", prev), unsafe_allow_html=True)
        k2.markdown(kpi("Falhas em 24 h", str(fails)), unsafe_allow_html=True)
        k3.markdown(kpi("Sessões ativas", str(len(active_sessions()))), unsafe_allow_html=True)
        k4.markdown(kpi("Códigos de recuperação", str(recovery_codes_left())), unsafe_allow_html=True)
        st.markdown("#### Proteções aplicadas no servidor")
        st.markdown(f"""- A senha é guardada só como **hash** (PBKDF2-SHA256); nunca em texto puro.
- Cada ação do painel confere a sessão no banco. Cliente comum **não** tem sessão de administrador e é recusado.
- Sessão expira após **{SESSION_IDLE_MIN} min** sem uso (máximo de **{SESSION_MAX_HOURS} h**) e pode ser encerrada à distância.
- **{MAX_FAILS_IP} erros** de senha bloqueiam a origem por até **{LOCK_WINDOW_S // 60} min**; o bloqueio vale mesmo recarregando a página.
- Tudo é registrado: logins, falhas, trocas de senha e alterações no painel.""")
        st.caption("Hoje existe uma única conta de administrador (acesso total). Contas separadas por função (ex.: atendente só com pedidos e suporte) ainda não existem.")
    with t[1]:
        st.caption("Sessões do painel abertas agora. Encerrar uma sessão a desconecta na hora.")
        sess = active_sessions()
        me = st.session_state.get("admin_sid")
        for _, r in sess.iterrows():
            mine = r["id"] == me
            a, b = st.columns([5, 1.5])
            a.markdown(f'<div class="dcard"><b>{"Esta sessão" if mine else "Outra sessão"}</b><div class="small">Início {esc(_dt(r["created_at"]))} · '
                       f'última atividade {esc(_dt(r["last_seen"]))}<br>Origem {esc(r["ip"])} · {esc(clean(r["agent"])[:70])}</div></div>', unsafe_allow_html=True)
            if not mine and b.button("Encerrar", key=f"kill_{r['id']}"):
                revoke_session(r["id"])
                st.rerun()
        if len(sess) > 1 and st.button("Encerrar todas as outras sessões", key="kill_all"):
            revoke_other_sessions("encerradas pelo admin")
            audit("sessoes_encerradas", "todas as outras")
            st.rerun()
    with t[2]:
        flt = st.radio("Filtro", ["Todos", "Só falhas", "Só sucessos"], horizontal=True, key="sec_flt")
        where = {"Todos": "", "Só falhas": "WHERE success=0", "Só sucessos": "WHERE success=1"}[flt]
        att = query(f"SELECT created_at, kind, success, reason, ip, agent FROM login_attempts {where} ORDER BY id DESC LIMIT 300")
        if att.empty:
            st.info("Nenhum registro.")
        else:
            att["created_at"] = att["created_at"].map(_dt)
            att["success"] = att["success"].map({1: "Sucesso", 0: "Falha"})
            att = att.rename(columns={"created_at": "Data", "kind": "Tipo", "success": "Resultado", "reason": "Motivo", "ip": "Origem", "agent": "Aparelho"})
            st.dataframe(att, width="stretch", hide_index=True)
            st.download_button("Baixar acessos (CSV)", att.to_csv(index=False).encode("utf-8-sig"), "acessos_bora_bebidas.csv", "text/csv",
                               on_click=audit, args=("exportacao_csv", "acessos"))
    with t[3]:
        st.caption("Tudo o que foi feito no painel: pedidos, estoque, produtos, financeiro, aparência e segurança.")
        txt = st.text_input("Filtrar atividades", key="aud_flt")
        aud = query("SELECT created_at, event, detail, ip FROM admin_audit ORDER BY id DESC LIMIT 500")
        if txt:
            aud = aud[aud.apply(lambda r: txt.lower() in (clean(r["event"]) + " " + clean(r["detail"])).lower(), axis=1)]
        if aud.empty:
            st.info("Nenhuma atividade registrada.")
        else:
            aud["created_at"] = aud["created_at"].map(_dt)
            aud["event"] = aud["event"].str.replace("_", " ")
            aud = aud.rename(columns={"created_at": "Data", "event": "Ação", "detail": "Detalhe", "ip": "Origem"})
            st.dataframe(aud, width="stretch", hide_index=True)
            st.download_button("Baixar atividades (CSV)", aud.to_csv(index=False).encode("utf-8-sig"), "atividades_bora_bebidas.csv", "text/csv",
                               on_click=audit, args=("exportacao_csv", "atividades"))
    with t[4]:
        st.markdown("#### Trocar senha")
        with st.form("chg_pw", clear_on_submit=True):
            cur = st.text_input("Senha atual", type="password")
            n1 = st.text_input("Nova senha (mín. 10 caracteres, com letras e números)", type="password")
            n2 = st.text_input("Repita a nova senha", type="password")
            go = st.form_submit_button("Trocar senha", type="primary")
        if go:
            ip, agent = client_info()
            if lock_remaining(ip):
                st.error("Muitas tentativas. Aguarde um pouco.")
            elif not verify_password(cur, get_pw_hash()):
                record_attempt(ip, agent, "troca_senha", False, "senha atual incorreta")
                audit("troca_senha_falhou", "senha atual incorreta")
                st.error("A senha atual está incorreta.")
            elif n1 != n2:
                st.error("As senhas novas não conferem.")
            elif password_problem(n1, get_pw_hash()):
                st.error(password_problem(n1, get_pw_hash()))
            else:
                change_password(n1)
                st.success("Senha alterada. As outras sessões foram encerradas.")
        st.markdown("#### Códigos de recuperação")
        st.caption(f"Você tem **{recovery_codes_left()}** código(s) válido(s). Gerar novos invalida os anteriores.")
        with st.form("new_codes", clear_on_submit=True):
            cur2 = st.text_input("Confirme sua senha", type="password")
            go2 = st.form_submit_button("Gerar novos códigos")
        if go2:
            ip, agent = client_info()
            if lock_remaining(ip):
                st.error("Muitas tentativas. Aguarde um pouco.")
            elif not verify_password(cur2, get_pw_hash()):
                record_attempt(ip, agent, "troca_senha", False, "senha incorreta ao gerar códigos")
                st.error("Senha incorreta.")
            else:
                st.session_state["show_codes"] = new_recovery_codes()
                audit("codigos_recuperacao_gerados", "")
                st.rerun()
    with t[5]:
        st.markdown("#### Endereços do sistema")
        st.markdown(f"""- **Loja dos clientes:** o endereço do sistema, sem nada no final (ex.: `http://localhost:8501`).
- **Painel do dono:** o mesmo endereço terminando com `?area={ACCESS_CODE}`. Não divulgue; mesmo descoberto, exige senha.""")
        st.markdown("#### Backup do banco de dados")
        st.caption("Baixe uma cópia regularmente. Em hospedagem gratuita (Streamlit Cloud) os arquivos podem ser apagados.")
        if DB.exists():
            st.download_button("Baixar backup (giro.db)", DB.read_bytes(), f"backup_bora_{now_local():%Y%m%d_%H%M}.db", "application/octet-stream",
                               on_click=audit, args=("backup_baixado", "giro.db"))
        st.warning("Antes de uso comercial: use HTTPS na hospedagem, mantenha backups e informe aos clientes como os dados são usados (LGPD).")

        st.markdown("#### Backup completo (banco + fotos)")
        st.caption("Inclui pedidos, clientes, produtos, logo, banner e todas as fotos. É o arquivo certo para guardar e para levar o sistema a outro lugar.")
        if st.button("Preparar backup completo", key="bk_prep"):
            st.session_state["bk_zip"] = make_full_backup()
            audit("backup_preparado", "zip completo")
        if st.session_state.get("bk_zip"):
            st.download_button("Baixar backup completo (.zip)", st.session_state["bk_zip"], f"backup_completo_bora_{now_local():%Y%m%d_%H%M}.zip",
                               "application/zip", on_click=audit, args=("backup_baixado", "zip completo"))
        st.markdown("#### Restaurar backup")
        st.caption("Substitui TODOS os dados atuais pelos do arquivo. O estado atual é guardado antes numa cópia de segurança dentro da pasta de dados.")
        up = st.file_uploader("Backup (.zip completo ou giro.db)", type=["zip", "db"], key="restore_up")
        sure = st.checkbox("Entendo que isto substitui os dados atuais", key="restore_sure")
        rpw = st.text_input("Sua senha", type="password", key="restore_pw")
        if st.button("Restaurar agora", key="restore_go", type="primary", disabled=not (up and sure)):
            ip, agent = client_info()
            if lock_remaining(ip):
                st.error("Muitas tentativas. Aguarde um pouco.")
            elif not verify_password(rpw, get_pw_hash()):
                record_attempt(ip, agent, "restauracao", False, "senha incorreta")
                audit("restauracao_falhou", "senha incorreta")
                st.error("Senha incorreta.")
            else:
                try:
                    n = restore_backup(up)
                except ValueError as e:
                    st.error(str(e))
                else:
                    audit("backup_restaurado", f"{n} foto(s) restaurada(s)")
                    st.session_state["session_msg"] = "Backup restaurado. Entre novamente (vale a senha do backup)."
                    _drop_local_session()
                    st.session_state.pop("bk_zip", None)
                    st.rerun()


def new_orders_banner():
    n = int(query("SELECT COUNT(*) n FROM orders WHERE status='Recebido' AND COALESCE(channel,'Online')='Online'")["n"][0])
    if n:
        st.markdown(f'<div class="alertbar">{n} pedido(s) online aguardando atendimento. Abra "Pedidos e vendas" ou "Entregas".</div>',
                    unsafe_allow_html=True)


if hasattr(st, "fragment"):
    new_orders_banner = st.fragment(run_every=30)(new_orders_banner)

def tab_appearance():
    st.caption("Logo e banner exibidos na loja dos clientes. As imagens são validadas e redimensionadas automaticamente.")
    kinds = (("logo", "Logo", "PNG com fundo transparente funciona melhor. Até 5 MB."),
             ("banner", "Banner da página inicial", "Imagem horizontal, ideal 1600 x 600. Até 5 MB."))
    for kind, title, hint in kinds:
        st.markdown(f"#### {title}")
        cur = get_setting(kind)
        uri = img_uri(cur)
        if uri:
            bg = "#171717" if kind == "logo" else "transparent"
            st.markdown(f'<div style="background:{bg};padding:10px;border-radius:12px;display:inline-block;max-width:100%">'
                        f'<img src="{uri}" style="max-width:100%;max-height:160px;border-radius:8px"></div>', unsafe_allow_html=True)
        else:
            st.info("Nenhuma imagem definida. A loja usa o visual padrão.")
        ver = st.session_state.get(f"up_ver_{kind}", 0)
        up = st.file_uploader(hint, type=["png", "jpg", "jpeg", "webp"], key=f"up_{kind}_{ver}")
        b1, b2 = st.columns(2)
        if b1.button("Salvar imagem", key=f"save_{kind}", type="primary", disabled=up is None, width="stretch"):
            try:
                name = save_brand_image(up, kind)
                set_setting(kind, name)
                delete_image(cur)
                audit("aparencia_alterada", kind)
                st.session_state[f"up_ver_{kind}"] = ver + 1
                st.rerun()
            except ValueError as e:
                st.error(str(e))
        if cur and b2.button("Remover imagem", key=f"rm_{kind}", width="stretch"):
            set_setting(kind, "")
            delete_image(cur)
            audit("aparencia_removida", kind)
            st.rerun()
        st.divider()


ADMIN_PAGES = {"Painel": "Painel", "Vender": "Nova venda", "Pedidos": "Pedidos e vendas", "Entregas": "Entregas",
               "Produtos": "Produtos", "Estoque": "Estoque", "Financeiro": "Financeiro", "Clientes": "Clientes",
               "Relatorios": "Relatórios", "Aparencia": "Aparência", "Seguranca": "Segurança"}


def codes_screen():
    st.markdown("## Códigos de recuperação")
    st.warning("Anote estes códigos agora e guarde fora do computador da loja. Cada um vale uma única vez e NÃO será mostrado de novo.")
    st.code("\n".join(st.session_state["show_codes"]))
    if st.button("Anotei os códigos", type="primary", key="codes_ok"):
        del st.session_state["show_codes"]
        st.rerun()


def force_change_screen():
    st.markdown("## Defina sua senha")
    st.info("Você entrou com a senha inicial. Por segurança, crie uma senha só sua para continuar.")
    with st.form("force_pw"):
        n1 = st.text_input("Nova senha (mín. 10 caracteres, com letras e números)", type="password")
        n2 = st.text_input("Repita a nova senha", type="password")
        go = st.form_submit_button("Salvar senha", type="primary")
    if go:
        if n1 != n2:
            st.error("As senhas não conferem.")
        elif password_problem(n1, get_pw_hash()):
            st.error(password_problem(n1, get_pw_hash()))
        else:
            change_password(n1)
            st.session_state["show_codes"] = new_recovery_codes()
            audit("codigos_recuperacao_gerados", "primeiro acesso")
            st.rerun()


def page_admin():
    if not session_is_valid():
        admin_login()
        return
    if st.session_state.get("show_codes"):
        codes_screen()
        return
    if must_change_password():
        force_change_screen()
        return
    h1, h2 = st.columns([6, 1])
    h1.markdown("## Painel de gestão")
    if h2.button("Sair", key="logout"):
        end_session()
        st.rerun()
    alerts = [a for a in security_alerts() if a[0] in ("error", "warning")]
    if alerts:
        st.markdown(f'<div class="alertbar">Segurança: {esc(alerts[0][1])}' + (f" (+{len(alerts) - 1})" if len(alerts) > 1 else "") +
                    ' Abra "Segurança".</div>', unsafe_allow_html=True)
    new_orders_banner()
    pend = int(query("SELECT COUNT(*) n FROM orders WHERE status='Recebido' AND COALESCE(channel,'Online')='Online'")["n"][0])
    key = st.radio("Seção", list(ADMIN_PAGES.keys()), horizontal=True, key="adm_page", label_visibility="collapsed",
                   format_func=lambda k: ADMIN_PAGES[k] + (f" ({pend})" if k == "Pedidos" and pend else ""))
    {"Painel": tab_dashboard, "Vender": tab_pdv, "Pedidos": tab_orders, "Entregas": tab_deliveries,
     "Produtos": tab_products, "Estoque": tab_stock, "Financeiro": tab_finance, "Clientes": tab_clients,
     "Relatorios": tab_reports, "Aparencia": tab_appearance, "Seguranca": tab_security}[key]()


# ---------------------------------------------------------------------
# PROGRAMA PRINCIPAL
# ---------------------------------------------------------------------
init()
if "cart" not in st.session_state:
    st.session_state.cart = {}

if st.query_params.get("area", "") == ACCESS_CODE:
    # ---- PAINEL DO DONO (acesso só por ?area=gestao + senha)
    st.markdown(topbar("PAINEL DE GESTÃO"), unsafe_allow_html=True)
    page_admin()
else:
    # ---- LOJA DO CLIENTE (não mostra nada do painel)
    PAGES = {"Loja": "Loja", "Carrinho": "Meu carrinho", "Acompanhar": "Acompanhar pedido"}
    cart_n = sum(st.session_state.cart.values())
    st.markdown(topbar("BEBIDAS GELADAS E CONVENIÊNCIA"), unsafe_allow_html=True)
    page = st.radio("MENU", list(PAGES.keys()), horizontal=True, key="page", label_visibility="collapsed",
                    format_func=lambda k: PAGES[k] + (f" ({cart_n})" if k == "Carrinho" and cart_n else ""))
    if page == "Loja":
        page_store()
    elif page == "Carrinho":
        page_cart()
    else:
        page_track()

st.markdown('<div class="footer">BORA BEBIDAS</div>', unsafe_allow_html=True)
