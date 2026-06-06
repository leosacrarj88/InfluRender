# -*- coding: utf-8 -*-
"""
Influencers_19_ASGI_PORTA_AUTOMATICA.py

Aplicação Python em arquivo único para:
- Dashboard de influenciadores, cliques e clientes;
- Cadastro, edição e exclusão de influenciadores;
- Link rastreável por influenciador;
- Redirecionamento automático HTTP 302 real para WhatsApp;
- Uso da base Supabase existente: influencers e clicks.

Formato dos links:
    https://seudominio.com/go/ana-carolina
    https://seudominio.com/?go=ana-carolina

Execução local:
    python Influencers_19_ASGI_PORTA_AUTOMATICA.py

Execução ASGI/Uvicorn:
    uvicorn Influencers_19_ASGI_PORTA_AUTOMATICA:app --host 0.0.0.0 --port 8080

Variáveis opcionais:
    SUPABASE_URL
    SUPABASE_SERVICE_KEY ou SUPABASE_KEY
    PUBLIC_BASE_URL
    PORT
"""

from __future__ import annotations

import csv
import html
import json
import os
import re
import traceback
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from io import StringIO
from typing import Any
from zoneinfo import ZoneInfo

BR_TZ = ZoneInfo("America/Sao_Paulo")

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://rcegpvcmgknrnndynkht.supabase.co").strip().rstrip("/")
SUPABASE_KEY = (
    os.getenv("SUPABASE_SERVICE_KEY")
    or os.getenv("SUPABASE_KEY")
    or "sb_publishable_dbWOrEzdoB4eDdloIhREIw__xhT3Vs_"
).strip()
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
DEFAULT_PORT = int(os.getenv("PORT", "8080"))

APP_TITLE = "Gerenciador de Influenciadores | SuplemeX"


# =========================================================
# UTILIDADES GERAIS
# =========================================================
def h(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def now_br() -> datetime:
    return datetime.now(BR_TZ)


def now_iso() -> str:
    return now_br().isoformat()


def fmt_int(value: Any) -> str:
    try:
        return f"{int(value):,}".replace(",", ".")
    except Exception:
        return "0"


def normalize_phone(phone: Any) -> str:
    return "".join(ch for ch in str(phone or "") if ch.isdigit())


def normalize_whatsapp_number(phone: Any) -> str:
    digits = normalize_phone(phone)
    if digits.startswith("55") and len(digits) in (12, 13):
        digits = digits[2:]
    while len(digits) > 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits


def is_valid_br_phone(phone: Any) -> bool:
    return len(normalize_whatsapp_number(phone)) in (10, 11)


def build_whatsapp_redirect_number(phone: Any) -> str:
    local_number = normalize_whatsapp_number(phone)
    if len(local_number) in (10, 11):
        return f"55{local_number}"
    return normalize_phone(phone)


def format_phone_br(phone: Any) -> str:
    digits = normalize_whatsapp_number(phone)
    if not digits:
        return ""
    if len(digits) == 11:
        return f"({digits[:2]}) {digits[2:7]}-{digits[7:]}"
    if len(digits) == 10:
        return f"({digits[:2]}) {digits[2:6]}-{digits[6:]}"
    return digits


def slugify(value: Any) -> str:
    raw = str(value or "").strip().lower()
    raw = raw.replace(" ", "-")
    raw = re.sub(r"[^a-z0-9_-]+", "-", raw)
    raw = re.sub(r"-+", "-", raw).strip("-")
    return raw


def normalize_instagram_handle(handle: Any) -> str:
    value = str(handle or "").strip()
    if not value:
        return ""
    value = value.replace("https://", "").replace("http://", "").strip()
    if value.lower().startswith("www."):
        value = value[4:]
    if "instagram.com/" in value.lower():
        value = value.split("instagram.com/", 1)[1]
    value = value.split("?", 1)[0].split("#", 1)[0].strip("/")
    return value.lstrip("@").strip()


def format_instagram_handle(handle: Any) -> str:
    value = normalize_instagram_handle(handle)
    return f"@{value}" if value else ""


def parse_bool(value: Any, default: bool = True) -> bool:
    text = str(value or "").strip().lower()
    if text in {"1", "true", "sim", "yes", "on", "ativo"}:
        return True
    if text in {"0", "false", "não", "nao", "no", "off", "inativo"}:
        return False
    return default


def parse_datetime_br(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=BR_TZ)
        return dt.astimezone(BR_TZ)
    except Exception:
        return None


def fmt_dt(value: Any) -> str:
    dt = parse_datetime_br(value)
    return dt.strftime("%d/%m/%Y %H:%M") if dt else ""


def get_initials(name: Any) -> str:
    parts = str(name or "IN").strip().split()
    return "".join(part[:1] for part in parts[:2]).upper() or "IN"


def parse_query_string(raw_qs: bytes) -> dict[str, list[str]]:
    return urllib.parse.parse_qs(raw_qs.decode("utf-8", errors="ignore"), keep_blank_values=True)


def first_param(qs: dict[str, list[str]], name: str, default: str = "") -> str:
    values = qs.get(name) or []
    return str(values[0]) if values else default


def parse_form(body: bytes) -> dict[str, str]:
    parsed = urllib.parse.parse_qs(body.decode("utf-8", errors="ignore"), keep_blank_values=True)
    return {k: str(v[0]) if v else "" for k, v in parsed.items()}


def get_header(scope: dict, name: str) -> str:
    target = name.lower().encode()
    for k, v in scope.get("headers", []):
        if k.lower() == target:
            return v.decode("latin1", errors="ignore")
    return ""


def get_client_ip(scope: dict) -> str:
    for key in ("cf-connecting-ip", "true-client-ip", "x-real-ip", "x-forwarded-for"):
        value = get_header(scope, key)
        if value:
            return value.split(",", 1)[0].strip()[:80]
    client = scope.get("client") or None
    if client and isinstance(client, (tuple, list)) and client:
        return str(client[0])[:80]
    return ""


def request_base_url(scope: dict) -> str:
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL

    host = get_header(scope, "x-forwarded-host") or get_header(scope, "host")
    proto = get_header(scope, "x-forwarded-proto") or scope.get("scheme") or "https"
    if not host:
        return ""
    return f"{proto}://{host}".rstrip("/")


def influencer_link(scope: dict, slug: Any) -> str:
    base = request_base_url(scope)
    clean_slug = urllib.parse.quote(str(slug or "").strip())
    if base:
        return f"{base}/go/{clean_slug}"
    return f"/go/{clean_slug}"


def build_whatsapp_url(phone: Any, message: Any) -> str:
    number = build_whatsapp_redirect_number(phone)
    msg = urllib.parse.quote(str(message or ""), safe="")
    return f"https://api.whatsapp.com/send?phone={number}&text={msg}"


# =========================================================
# SUPABASE REST
# =========================================================
def supabase_request(method: str, table: str, query: str = "", payload: Any = None, prefer: str = "return=representation") -> Any:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_URL e SUPABASE_SERVICE_KEY/SUPABASE_KEY não configurados.")

    url = f"{SUPABASE_URL}/rest/v1/{table}{query}"
    data = None
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Accept": "application/json",
    }
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if prefer:
        headers["Prefer"] = prefer

    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=18) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
            if not raw.strip():
                return []
            try:
                return json.loads(raw)
            except Exception:
                return raw
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Supabase HTTP {e.code}: {detail}") from e
    except Exception as e:
        raise RuntimeError(f"Falha ao conectar no Supabase: {e}") from e


def table_select(table: str, select: str = "*", extra: str = "") -> list[dict]:
    q = f"?select={urllib.parse.quote(select, safe='*,()')}{extra}"
    data = supabase_request("GET", table, q)
    return data if isinstance(data, list) else []


def load_influencers() -> tuple[list[dict], str]:
    try:
        rows = table_select("influencers", "*", "&order=created_at.desc")
        return rows, ""
    except Exception as e:
        return [], str(e)


def load_clicks(limit: int = 10000) -> tuple[list[dict], str]:
    try:
        rows = table_select("clicks", "*", f"&order=created_at.desc&limit={int(limit)}")
        return rows, ""
    except Exception as e:
        return [], str(e)


def get_influencer_by_slug(slug: str) -> dict | None:
    clean = slugify(slug)
    if not clean:
        return None
    q = f"?select=*&slug=eq.{urllib.parse.quote(clean)}&limit=1"
    rows = supabase_request("GET", "influencers", q)
    if isinstance(rows, list) and rows:
        return rows[0]
    return None


def insert_influencer(payload: dict) -> None:
    payload = payload.copy()
    payload.setdefault("created_at", now_iso())
    try:
        supabase_request("POST", "influencers", "", payload, prefer="return=minimal")
    except Exception as e:
        text = str(e)
        for optional in ("created_at", "photo_url", "instagram", "influencer_whatsapp"):
            if optional in payload and optional in text:
                payload.pop(optional, None)
                supabase_request("POST", "influencers", "", payload, prefer="return=minimal")
                return
        raise


def update_influencer(influencer_id: str, payload: dict) -> None:
    q = f"?id=eq.{urllib.parse.quote(str(influencer_id))}"
    try:
        supabase_request("PATCH", "influencers", q, payload, prefer="return=minimal")
    except Exception as e:
        text = str(e)
        reduced = payload.copy()
        changed = False
        for optional in ("photo_url", "instagram", "influencer_whatsapp"):
            if optional in reduced and optional in text:
                reduced.pop(optional, None)
                changed = True
        if changed:
            supabase_request("PATCH", "influencers", q, reduced, prefer="return=minimal")
            return
        raise


def delete_influencer(influencer_id: str) -> None:
    q = f"?id=eq.{urllib.parse.quote(str(influencer_id))}"
    supabase_request("DELETE", "influencers", q, prefer="return=minimal")


def build_click_metadata(raw_user_agent: str = "") -> str:
    safe_ua = urllib.parse.quote(str(raw_user_agent or "")[:260])
    return f"GLM_META_V2|ua={safe_ua}"


def parse_click_metadata(raw_value: Any) -> dict[str, str]:
    raw_text = str(raw_value or "")
    default = {"visitor_name": "", "visitor_phone": "", "device_user_agent": raw_text}
    if not raw_text.startswith("GLM_META_V"):
        return default
    parsed: dict[str, str] = {}
    for part in raw_text.split("|")[1:]:
        if "=" in part:
            k, v = part.split("=", 1)
            parsed[k] = urllib.parse.unquote(v)
    return {
        "visitor_name": parsed.get("name", ""),
        "visitor_phone": normalize_phone(parsed.get("phone", ""))[:20],
        "device_user_agent": parsed.get("ua", raw_text),
    }


def parse_user_agent(ua: str) -> tuple[str, str, str]:
    """
    Retorna (device_icon, device_label, browser_label) a partir de um User-Agent string.
    Exemplos:
      ("📱", "iPhone", "Safari")
      ("🤖", "Android", "Chrome")
      ("💻", "PC", "Firefox")
    """
    ua_lower = (ua or "").lower()

    # ── Dispositivo ──
    if "ipad" in ua_lower:
        device_icon, device_label = "📱", "iPad"
    elif "iphone" in ua_lower:
        device_icon, device_label = "📱", "iPhone"
    elif "android" in ua_lower:
        if "tablet" in ua_lower or "pad" in ua_lower:
            device_icon, device_label = "📱", "Android Tablet"
        else:
            device_icon, device_label = "🤖", "Android"
    elif "macintosh" in ua_lower or "mac os x" in ua_lower:
        device_icon, device_label = "🖥️", "Mac"
    elif "windows" in ua_lower:
        device_icon, device_label = "💻", "Windows"
    elif "linux" in ua_lower:
        device_icon, device_label = "🐧", "Linux"
    elif "cros" in ua_lower:
        device_icon, device_label = "💻", "ChromeOS"
    elif not ua_lower:
        device_icon, device_label = "❓", "Desconhecido"
    else:
        device_icon, device_label = "🌐", "Outro"

    # ── Navegador ── (ordem importa: mais específico primeiro)
    if "edg/" in ua_lower or "edge/" in ua_lower:
        browser_label = "Edge"
    elif "opr/" in ua_lower or "opera" in ua_lower:
        browser_label = "Opera"
    elif "brave" in ua_lower:
        browser_label = "Brave"
    elif "samsungbrowser" in ua_lower:
        browser_label = "Samsung Internet"
    elif "instagram" in ua_lower:
        browser_label = "Instagram"
    elif "fban" in ua_lower or "fbav" in ua_lower or "facebook" in ua_lower:
        browser_label = "Facebook"
    elif "whatsapp" in ua_lower:
        browser_label = "WhatsApp"
    elif "tiktok" in ua_lower:
        browser_label = "TikTok"
    elif "twitter" in ua_lower or "twitterandroid" in ua_lower:
        browser_label = "Twitter/X"
    elif "yabrowser" in ua_lower:
        browser_label = "Yandex"
    elif "ucbrowser" in ua_lower:
        browser_label = "UC Browser"
    elif "chrome" in ua_lower and "chromium" not in ua_lower:
        browser_label = "Chrome"
    elif "firefox" in ua_lower:
        browser_label = "Firefox"
    elif "safari" in ua_lower and "chrome" not in ua_lower:
        browser_label = "Safari"
    elif "chromium" in ua_lower:
        browser_label = "Chromium"
    elif not ua_lower:
        browser_label = "?"
    else:
        browser_label = "Outro"

    return device_icon, device_label, browser_label


def format_device_cell(raw_ua: str) -> str:
    """Retorna HTML amigável para exibir na coluna Dispositivo/Navegador."""
    icon, device, browser = parse_user_agent(raw_ua)
    return (
        f'<span style="display:inline-flex;align-items:center;gap:6px">'
        f'<span style="font-size:16px" title="{h(raw_ua[:200])}">{icon}</span>'
        f'<span>'
        f'<span style="font-weight:600;font-size:13px">{h(device)}</span>'
        f'<span style="color:var(--muted);font-size:11.5px;display:block">{h(browser)}</span>'
        f'</span></span>'
    )


def register_click(influencer: dict, target_url: str, scope: dict) -> None:
    payload = {
        "slug": influencer.get("slug", ""),
        "target_url": target_url,
        "user_agent": build_click_metadata(get_header(scope, "user-agent")),
        "created_at": now_iso(),
    }
    if influencer.get("id"):
        payload["influencer_id"] = influencer.get("id")
    ip_addr = get_client_ip(scope)
    if ip_addr:
        payload["ip_address"] = ip_addr

    try:
        supabase_request("POST", "clicks", "", payload, prefer="return=minimal")
    except Exception:
        payload.pop("created_at", None)
        try:
            supabase_request("POST", "clicks", "", payload, prefer="return=minimal")
        except Exception:
            pass


# =========================================================
# HTML
# =========================================================
def base_css() -> str:
    return """
    @import url('https://fonts.googleapis.com/css2?family=Syne:wght@400;700;800&family=DM+Sans:wght@300;400;500;600&display=swap');
    :root {
        --bg:#04080f; --panel:#080f1c; --panel2:#0c1424; --border:rgba(255,255,255,.07);
        --border-hover:rgba(255,255,255,.14); --text:#eef2ff; --muted:#7c8ba1; --muted2:#a8b6c8;
        --accent:#5b8dee; --accent2:#7c3aed; --green:#0ecb7a; --red:#f04747; --orange:#f5a623;
        --card-bg:rgba(8,15,28,.85); --sidebar-bg:#050c18;
        --gradient:linear-gradient(135deg,#5b8dee,#7c3aed);
        --font-head:'Syne',sans-serif; --font-body:'DM Sans',sans-serif;
    }
    *{box-sizing:border-box;-webkit-font-smoothing:antialiased}
    body{margin:0;font-family:var(--font-body);background:var(--bg);color:var(--text);
        background-image:
            radial-gradient(ellipse 70% 40% at 90% -5%, rgba(91,141,238,.18) 0%, transparent 60%),
            radial-gradient(ellipse 50% 35% at -10% 80%, rgba(124,58,237,.12) 0%, transparent 55%),
            radial-gradient(ellipse 40% 30% at 50% 110%, rgba(14,203,122,.07) 0%, transparent 50%);
    }
    a{color:inherit;text-decoration:none}
    /* ── Layout ── */
    .layout{display:grid;grid-template-columns:256px minmax(0,1fr);min-height:100vh}
    aside{background:var(--sidebar-bg);border-right:1px solid var(--border);padding:24px 20px;position:sticky;top:0;height:100vh;overflow-y:auto;
        display:flex;flex-direction:column;gap:0}
    main{padding:32px 28px;max-width:1480px;width:100%;margin:0 auto}
    /* ── Brand ── */
    .brand{display:flex;gap:14px;align-items:center;margin-bottom:32px;padding-bottom:24px;border-bottom:1px solid var(--border)}
    .brand-icon{width:44px;height:44px;border-radius:14px;background:var(--gradient);display:flex;align-items:center;justify-content:center;font-size:22px;flex-shrink:0;box-shadow:0 0 24px rgba(91,141,238,.35)}
    .brand strong{display:block;font-family:var(--font-head);font-size:15px;font-weight:800;line-height:1.15;letter-spacing:-.01em}
    .brand span{font-size:11px;color:var(--muted);font-weight:500;letter-spacing:.04em;text-transform:uppercase}
    /* ── Nav ── */
    .nav{display:flex;flex-direction:column;gap:4px;flex:1}
    .nav a{display:flex;align-items:center;gap:10px;padding:11px 13px;border-radius:12px;color:var(--muted2);font-weight:600;font-size:13.5px;transition:all .18s ease;border:1px solid transparent}
    .nav a .nav-icon{font-size:16px;width:20px;text-align:center}
    .nav a:hover{background:rgba(255,255,255,.05);color:var(--text);border-color:var(--border)}
    .nav a.active{background:rgba(91,141,238,.13);color:#93b8f8;border-color:rgba(91,141,238,.22);font-weight:700}
    .nav a.active .nav-icon{filter:drop-shadow(0 0 6px rgba(91,141,238,.7))}
    /* ── Sidebar footer ── */
    .sidebar-footer{margin-top:auto;padding-top:20px;border-top:1px solid var(--border)}
    .sidebar-user{display:flex;align-items:center;gap:10px;padding:10px;border-radius:12px;background:rgba(255,255,255,.03)}
    .sidebar-user-dot{width:8px;height:8px;border-radius:50%;background:var(--green);box-shadow:0 0 8px var(--green);flex-shrink:0}
    .sidebar-user-info strong{display:block;font-size:13px;font-weight:600}
    .sidebar-user-info span{font-size:11px;color:var(--muted)}
    /* ── Page header ── */
    h1{font-family:var(--font-head);font-size:28px;font-weight:800;margin:0 0 4px;letter-spacing:-.02em}
    h2{font-family:var(--font-head);font-size:17px;font-weight:700;margin:0 0 14px;letter-spacing:-.01em}
    .caption{color:var(--muted);margin-bottom:24px;font-size:14px;font-weight:400;line-height:1.5}
    /* ── Grids ── */
    .grid4{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}
    .grid5{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:14px}
    /* ── Cards ── */
    .card{background:var(--card-bg);border:1px solid var(--border);border-radius:18px;padding:20px;
        box-shadow:0 2px 4px rgba(0,0,0,.3),0 20px 60px rgba(0,0,0,.2);
        transition:border-color .2s ease}
    .card:hover{border-color:var(--border-hover)}
    /* ── Metric cards ── */
    .metric{display:flex;gap:16px;align-items:flex-start}
    .metric .ico{width:48px;height:48px;border-radius:14px;display:flex;align-items:center;justify-content:center;font-size:22px;
        background:rgba(91,141,238,.12);border:1px solid rgba(91,141,238,.18);flex-shrink:0}
    .metric-body label{display:block;color:var(--muted);font-size:11px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;margin-bottom:4px}
    .metric-body b{display:block;font-family:var(--font-head);font-size:26px;font-weight:800;letter-spacing:-.02em;line-height:1}
    .metric-body small{display:block;color:var(--muted);font-size:11px;margin-top:4px;font-weight:500}
    /* ── Section / two-col ── */
    .section{margin-top:18px}
    .two{display:grid;grid-template-columns:1.6fr 1fr;gap:16px}
    /* ── Table ── */
    .table-wrap{overflow:auto;border-radius:14px;border:1px solid var(--border)}
    table{width:100%;border-collapse:collapse;background:rgba(4,8,15,.4)}
    th{color:var(--muted);font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;
        padding:12px 14px;border-bottom:1px solid var(--border);white-space:nowrap}
    td{text-align:left;padding:12px 14px;border-bottom:1px solid var(--border);font-size:13px;vertical-align:middle}
    tr:last-child td{border-bottom:none}
    tr:hover td{background:rgba(255,255,255,.02)}
    /* ── Avatar ── */
    .avatar{width:36px;height:36px;border-radius:50%;background:var(--gradient);display:inline-flex;align-items:center;justify-content:center;
        font-weight:800;color:#fff;font-size:13px;margin-right:10px;vertical-align:middle;flex-shrink:0;
        border:2px solid rgba(255,255,255,.08);font-family:var(--font-head)}
    .avatar-img{width:36px;height:36px;border-radius:50%;object-fit:cover;margin-right:10px;vertical-align:middle;flex-shrink:0;
        border:2px solid rgba(255,255,255,.12);display:inline-block}
    .influencer-cell{display:flex;align-items:center}
    .influencer-cell-text{display:flex;flex-direction:column;gap:2px}
    .influencer-cell-name{font-weight:600;font-size:13.5px}
    .influencer-cell-handle{color:var(--muted);font-size:11.5px}
    /* ── Status badge ── */
    .badge{display:inline-flex;align-items:center;gap:5px;padding:4px 10px;border-radius:999px;font-size:11px;font-weight:700;letter-spacing:.04em}
    .badge-active{background:rgba(14,203,122,.12);border:1px solid rgba(14,203,122,.25);color:#34d399}
    .badge-inactive{background:rgba(148,163,184,.08);border:1px solid rgba(148,163,184,.2);color:var(--muted)}
    .badge-dot{width:6px;height:6px;border-radius:50%}
    .badge-active .badge-dot{background:#34d399;box-shadow:0 0 6px #34d399}
    .badge-inactive .badge-dot{background:var(--muted)}
    /* ── Pill link ── */
    .pill{display:inline-flex;align-items:center;gap:5px;padding:5px 11px;border-radius:999px;
        background:rgba(91,141,238,.1);border:1px solid rgba(91,141,238,.25);
        color:#93b8f8;font-weight:700;font-size:11.5px;white-space:nowrap;transition:all .15s ease}
    .pill:hover{background:rgba(91,141,238,.18);border-color:rgba(91,141,238,.4)}
    /* ── Buttons ── */
    .btn{display:inline-flex;align-items:center;justify-content:center;gap:7px;border:0;border-radius:12px;
        padding:10px 18px;background:var(--gradient);color:white;font-family:var(--font-body);
        font-weight:700;font-size:13.5px;cursor:pointer;min-height:40px;transition:all .18s ease;
        box-shadow:0 4px 16px rgba(91,141,238,.25)}
    .btn:hover{transform:translateY(-1px);box-shadow:0 6px 24px rgba(91,141,238,.4)}
    .btn:active{transform:translateY(0)}
    .btn.secondary{background:rgba(255,255,255,.07);border:1px solid var(--border);box-shadow:none;color:var(--muted2)}
    .btn.secondary:hover{background:rgba(255,255,255,.11);color:var(--text);box-shadow:none}
    .btn.danger{background:linear-gradient(135deg,#c92020,#ef4444);box-shadow:0 4px 16px rgba(239,68,68,.25)}
    .btn.danger:hover{box-shadow:0 6px 24px rgba(239,68,68,.4)}
    .btn.sm{padding:7px 13px;min-height:32px;font-size:12.5px;border-radius:10px}
    /* ── Forms ── */
    .form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
    .form-row{display:flex;flex-direction:column;gap:6px}
    label{font-size:12px;color:var(--muted2);font-weight:600;letter-spacing:.03em}
    input,textarea,select{width:100%;background:rgba(4,8,15,.6);color:var(--text);
        border:1px solid var(--border);border-radius:12px;padding:11px 14px;
        font:inherit;outline:none;transition:border-color .18s ease;font-size:13.5px}
    input:focus,textarea:focus,select:focus{border-color:rgba(91,141,238,.5);background:rgba(8,14,26,.9)}
    input::placeholder,textarea::placeholder{color:var(--muted)}
    textarea{min-height:90px;resize:vertical}
    .full{grid-column:1/-1}
    /* ── Photo preview ── */
    .photo-preview-wrap{position:relative}
    .photo-preview{width:56px;height:56px;border-radius:50%;object-fit:cover;border:2px solid rgba(91,141,238,.35);display:none;vertical-align:middle}
    /* ── Alerts ── */
    .alert{padding:13px 16px;border-radius:13px;margin:14px 0;font-weight:600;font-size:13.5px;display:flex;align-items:center;gap:10px}
    .alert::before{font-size:16px}
    .alert.error{background:rgba(240,71,71,.1);border:1px solid rgba(240,71,71,.28);color:#fca5a5}
    .alert.error::before{content:'⚠️'}
    .alert.ok{background:rgba(14,203,122,.1);border:1px solid rgba(14,203,122,.28);color:#6ee7b7}
    .alert.ok::before{content:'✓'}
    .alert.warn{background:rgba(245,166,35,.1);border:1px solid rgba(245,166,35,.28);color:#fcd34d}
    .alert.warn::before{content:'ℹ️'}
    /* ── Rank badge ── */
    .rank{display:inline-flex;align-items:center;justify-content:center;width:24px;height:24px;border-radius:8px;
        font-family:var(--font-head);font-size:12px;font-weight:800;
        background:rgba(255,255,255,.06);color:var(--muted)}
    .rank-1{background:rgba(255,193,7,.15);color:#fbbf24;border:1px solid rgba(255,193,7,.25)}
    .rank-2{background:rgba(148,163,184,.1);color:#cbd5e1;border:1px solid rgba(148,163,184,.2)}
    .rank-3{background:rgba(180,83,9,.15);color:#fb923c;border:1px solid rgba(180,83,9,.25)}
    /* ── Quick actions card ── */
    .quick-actions p{line-height:1.6;margin-bottom:14px;font-size:13.5px}
    .quick-actions .btn{width:100%;margin-bottom:10px;justify-content:flex-start}
    /* ── Utility ── */
    .muted{color:var(--muted)}
    .fw{font-weight:600}
    /* ── Animations ── */
    @keyframes fadeIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
    .card,.grid5 .card,.grid4 .card{animation:fadeIn .35s ease both}
    .grid5 .card:nth-child(1){animation-delay:.04s}.grid5 .card:nth-child(2){animation-delay:.08s}
    .grid5 .card:nth-child(3){animation-delay:.12s}.grid5 .card:nth-child(4){animation-delay:.16s}
    .grid5 .card:nth-child(5){animation-delay:.20s}
    /* ── Responsive ── */
    @media(max-width:980px){
        .layout{grid-template-columns:1fr}
        aside{position:relative;height:auto;flex-direction:row;flex-wrap:wrap;padding:16px;gap:16px}
        .brand{margin-bottom:0;padding-bottom:0;border-bottom:none}
        .nav{flex-direction:row;flex:none}
        .nav a{padding:8px 12px}
        .sidebar-footer{display:none}
        .grid4,.grid5,.two,.form-grid{grid-template-columns:1fr}
        main{padding:16px}
    }
    """


def layout(scope: dict, title: str, body: str, active: str = "dashboard") -> bytes:
    nav = [
        ("dashboard", "/", "📊", "Dashboard"),
        ("influenciadores", "/influenciadores", "👤", "Influenciadores"),
        ("cliques", "/cliques", "🖱️", "Todos os Cliques"),
        ("exportar", "/exportar", "📥", "Exportar CSV"),
    ]
    links = "".join(
        f'<a class="{"active" if key == active else ""}" href="{href}"><span class="nav-icon">{icon}</span>{label}</a>'
        for key, href, icon, label in nav
    )
    html_doc = f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{h(title)}</title>
<style>{base_css()}</style>
</head>
<body>
<div class="layout">
<aside>
  <div class="brand">
    <div class="brand-icon">🔗</div>
    <div><strong>SuplemeX</strong><span>Influenciadores</span></div>
  </div>
  <div class="nav">{links}</div>
  <div class="sidebar-footer">
    <div class="sidebar-user">
      <div class="sidebar-user-dot"></div>
      <div class="sidebar-user-info"><strong>Administrador</strong><span>SuplemeX</span></div>
    </div>
  </div>
</aside>
<main>{body}</main>
</div>
</body>
</html>"""
    return html_doc.encode("utf-8")


def enrich_clicks(clicks: list[dict]) -> list[dict]:
    result = []
    for row in clicks:
        item = dict(row)
        meta = parse_click_metadata(item.get("user_agent", ""))
        item.update(meta)
        dt = parse_datetime_br(item.get("created_at"))
        item["_dt"] = dt
        item["_date"] = dt.date().isoformat() if dt else ""
        result.append(item)
    return result


def client_key(row: dict) -> str:
    phone = normalize_phone(row.get("visitor_phone", ""))
    if phone:
        return f"phone:{phone}"
    ip = str(row.get("ip_address") or "").strip()
    ua = str(row.get("device_user_agent") or row.get("user_agent") or "").strip()[:80]
    if ip:
        return f"ip:{ip}|ua:{ua}"
    slug = str(row.get("slug") or "")
    return f"ua:{ua}|slug:{slug}"


def build_sparkline_chart(clicks: list[dict], days: int = 30) -> str:
    """Gera um SVG de área com cliques por dia nos últimos N dias."""
    today = now_br().date()
    date_labels = [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    counts_by_date: dict[str, int] = defaultdict(int)
    for row in clicks:
        d = row.get("_date", "")
        if d in set(date_labels):
            counts_by_date[d] += 1

    values = [counts_by_date.get(d, 0) for d in date_labels]
    max_val = max(values) if any(v > 0 for v in values) else 1
    total_in_period = sum(values)

    W, H, PAD_L, PAD_R, PAD_T, PAD_B = 800, 180, 44, 12, 16, 36
    chart_w = W - PAD_L - PAD_R
    chart_h = H - PAD_T - PAD_B
    n = len(values)

    def x_pos(i: int) -> float:
        return PAD_L + (i / (n - 1)) * chart_w if n > 1 else PAD_L

    def y_pos(v: float) -> float:
        return PAD_T + chart_h - (v / max_val) * chart_h

    # Linhas horizontais de guia (4 níveis)
    guide_lines = ""
    for level in [0.25, 0.5, 0.75, 1.0]:
        y = y_pos(max_val * level)
        label_val = int(max_val * level)
        guide_lines += (
            f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{W - PAD_R}" y2="{y:.1f}" '
            f'stroke="rgba(255,255,255,.06)" stroke-width="1"/>'
            f'<text x="{PAD_L - 6}" y="{y + 4:.1f}" text-anchor="end" '
            f'fill="#7c8ba1" font-size="10" font-family="DM Sans,sans-serif">{label_val}</text>'
        )

    # Poly points para a área e a linha
    pts = [(x_pos(i), y_pos(v)) for i, v in enumerate(values)]
    line_pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area_pts = (
        f"{PAD_L:.1f},{PAD_T + chart_h:.1f} "
        + " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        + f" {W - PAD_R:.1f},{PAD_T + chart_h:.1f}"
    )

    # Labels de datas no eixo X (a cada ~7 dias)
    x_labels = ""
    step = max(1, n // 6)
    for i in range(0, n, step):
        x = x_pos(i)
        d = date_labels[i]
        parts = d.split("-")
        label = f"{parts[2]}/{parts[1]}" if len(parts) == 3 else d
        x_labels += (
            f'<text x="{x:.1f}" y="{H - 6}" text-anchor="middle" '
            f'fill="#7c8ba1" font-size="10" font-family="DM Sans,sans-serif">{label}</text>'
        )

    # Pontos de dados (somente onde há cliques)
    dots = ""
    for i, (x, y) in enumerate(pts):
        if values[i] > 0:
            dots += (
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="#5b8dee" '
                f'stroke="rgba(4,8,15,.8)" stroke-width="2">'
                f'<title>{date_labels[i]}: {values[i]} clique{"s" if values[i] != 1 else ""}</title>'
                f'</circle>'
            )

    # Identificar pico
    peak_idx = values.index(max_val)
    peak_x, peak_y = pts[peak_idx]
    peak_label = ""
    if max_val > 0:
        peak_label = (
            f'<circle cx="{peak_x:.1f}" cy="{peak_y:.1f}" r="5" fill="#5b8dee" '
            f'stroke="rgba(4,8,15,.9)" stroke-width="2"/>'
            f'<text x="{peak_x:.1f}" y="{peak_y - 9:.1f}" text-anchor="middle" '
            f'fill="#93b8f8" font-size="10" font-weight="700" font-family="DM Sans,sans-serif">{max_val}</text>'
        )

    subtitle = f"{total_in_period} cliques nos últimos {days} dias"

    svg = f"""
    <div style="margin-bottom:8px;display:flex;justify-content:space-between;align-items:baseline">
      <span style="font-size:12px;color:var(--muted);font-weight:600;letter-spacing:.05em;text-transform:uppercase">Evolução diária</span>
      <span style="font-size:12px;color:var(--muted)">{subtitle}</span>
    </div>
    <svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto;display:block;overflow:visible">
      <defs>
        <linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#5b8dee" stop-opacity="0.25"/>
          <stop offset="100%" stop-color="#5b8dee" stop-opacity="0.01"/>
        </linearGradient>
      </defs>
      {guide_lines}
      <polygon points="{area_pts}" fill="url(#areaGrad)"/>
      <polyline points="{line_pts}" fill="none" stroke="#5b8dee" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
      {dots}
      {peak_label}
      {x_labels}
    </svg>
    """
    return svg


def render_dashboard(scope: dict) -> bytes:
    influencers, inf_error = load_influencers()
    clicks_raw, clicks_error = load_clicks()
    clicks = enrich_clicks(clicks_raw)

    today = now_br().date().isoformat()
    total_influencers = len(influencers)
    active_influencers = sum(1 for item in influencers if parse_bool(item.get("active", True), True))
    total_clicks = len(clicks)
    clients_unique = len({client_key(row) for row in clicks}) if clicks else 0
    clicks_today = sum(1 for row in clicks if row.get("_date") == today)
    avg_clicks = round(total_clicks / total_influencers) if total_influencers else 0

    inf_by_slug = {str(i.get("slug") or ""): i for i in influencers}
    rank = Counter(str(row.get("slug") or "") for row in clicks if row.get("slug"))
    top_rows = ""
    for idx, (slug, count) in enumerate(rank.most_common(10), start=1):
        inf = inf_by_slug.get(slug, {})
        name = inf.get("name") or slug or "Influenciador"
        photo_url = str(inf.get("photo_url") or "").strip()
        rank_cls = f"rank-{idx}" if idx <= 3 else ""
        if photo_url:
            avatar_html = f'<img class="avatar-img" src="{h(photo_url)}" alt="{h(name)}" onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'inline-flex\'">'
            avatar_html += f'<span class="avatar" style="display:none">{h(get_initials(name))}</span>'
        else:
            avatar_html = f'<span class="avatar">{h(get_initials(name))}</span>'
        top_rows += f"""
        <tr>
          <td><span class="rank {rank_cls}">{idx}</span></td>
          <td>
            <div class="influencer-cell">
              {avatar_html}
              <span class="influencer-cell-name">{h(name)}</span>
            </div>
          </td>
          <td><a class="pill" href="{h(influencer_link(scope, slug))}" target="_blank">🔗 /go/{h(slug)}</a></td>
          <td><b style="font-family:var(--font-head);font-size:16px">{fmt_int(count)}</b></td>
        </tr>
        """
    if not top_rows:
        top_rows = '<tr><td colspan="4" class="muted">Ainda não há cliques registrados.</td></tr>'

    recent_rows = ""
    for row in clicks[:12]:
        slug = str(row.get("slug") or "")
        inf = inf_by_slug.get(slug, {})
        name = inf.get("name") or slug or "Influenciador"
        raw_ua = str(row.get("device_user_agent") or "")
        recent_rows += f"""
        <tr>
          <td style="white-space:nowrap;color:var(--muted);font-size:12.5px">{h(fmt_dt(row.get('created_at')))}</td>
          <td><span style="font-weight:600">{h(name)}</span></td>
          <td style="font-size:12px;color:var(--muted);font-family:monospace">{h(row.get('ip_address','') or '—')}</td>
          <td>{format_device_cell(raw_ua)}</td>
        </tr>
        """
    if not recent_rows:
        recent_rows = '<tr><td colspan="4" class="muted">Nenhuma atividade recente.</td></tr>'

    alerts = ""
    if inf_error:
        alerts += f'<div class="alert error">Erro ao carregar influenciadores: {h(inf_error)}</div>'
    if clicks_error:
        alerts += f'<div class="alert error">Erro ao carregar cliques: {h(clicks_error)}</div>'

    chart_svg = build_sparkline_chart(clicks, days=30)

    body = f"""
    <h1>Dashboard</h1>
    <div class="caption">Visão geral dos links, clientes e cliques dos influenciadores</div>
    {alerts}
    <div class="grid5">
      <div class="card metric"><div class="ico">👥</div><div class="metric-body"><label>Clientes únicos</label><b>{fmt_int(clients_unique)}</b><small>Por IP/navegador ou telefone</small></div></div>
      <div class="card metric"><div class="ico">🖱️</div><div class="metric-body"><label>Total de cliques</label><b>{fmt_int(total_clicks)}</b><small>Todos os acessos</small></div></div>
      <div class="card metric"><div class="ico">📅</div><div class="metric-body"><label>Cliques hoje</label><b>{fmt_int(clicks_today)}</b><small>Horário de Brasília</small></div></div>
      <div class="card metric"><div class="ico">📈</div><div class="metric-body"><label>Média por influencer</label><b>{fmt_int(avg_clicks)}</b><small>Cliques / cadastro</small></div></div>
      <div class="card metric"><div class="ico">🔗</div><div class="metric-body"><label>Influenciadores</label><b>{fmt_int(total_influencers)}</b><small>{fmt_int(active_influencers)} ativos</small></div></div>
    </div>

    <div class="card section">
      <h2>📈 Cliques por dia — últimos 30 dias</h2>
      {chart_svg}
    </div>

    <div class="two section">
      <div class="card">
        <h2>🏆 Ranking por cliques</h2>
        <div class="table-wrap"><table><thead><tr><th>#</th><th>Influenciador</th><th>Link</th><th>Cliques</th></tr></thead><tbody>{top_rows}</tbody></table></div>
      </div>
      <div class="card quick-actions">
        <h2>⚡ Ações rápidas</h2>
        <p class="muted">Cadastre influenciadores e copie links rastreáveis que abrem o WhatsApp automaticamente.</p>
        <a class="btn" href="/influenciadores">👤 Gerenciar influenciadores</a>
        <a class="btn secondary" href="/cliques">🖱️ Ver todos os cliques</a>
        <a class="btn secondary" href="/exportar">📥 Exportar relatórios</a>
      </div>
    </div>

    <div class="card section">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
        <h2 style="margin:0">🕐 Atividade recente</h2>
        <a class="btn sm secondary" href="/cliques">Ver tudo →</a>
      </div>
      <div class="table-wrap"><table><thead><tr><th>Data/Hora</th><th>Influenciador</th><th>IP</th><th>Dispositivo</th></tr></thead><tbody>{recent_rows}</tbody></table></div>
    </div>
    """
    return layout(scope, APP_TITLE, body, active="dashboard")


def render_influencers(scope: dict, qs: dict[str, list[str]]) -> bytes:
    influencers, error = load_influencers()
    status = first_param(qs, "ok")
    deleted = first_param(qs, "deleted")

    alert = ""
    if error:
        alert += f'<div class="alert error">Erro ao carregar influenciadores: {h(error)}</div>'
    if status:
        alert += '<div class="alert ok">Registro salvo com sucesso.</div>'
    if deleted:
        alert += '<div class="alert ok">Registro removido com sucesso.</div>'

    rows = ""
    for item in influencers:
        slug = str(item.get("slug") or "")
        name = str(item.get("name") or "")
        link = influencer_link(scope, slug)
        active = parse_bool(item.get("active", True), True)
        photo_url = str(item.get("photo_url") or "").strip()
        instagram = format_instagram_handle(item.get("instagram", ""))
        status_badge = (
            '<span class="badge badge-active"><span class="badge-dot"></span>Ativo</span>'
            if active else
            '<span class="badge badge-inactive"><span class="badge-dot"></span>Inativo</span>'
        )
        if photo_url:
            avatar_html = f'<img class="avatar-img" src="{h(photo_url)}" alt="{h(name)}" onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'inline-flex\'">'
            avatar_html += f'<span class="avatar" style="display:none">{h(get_initials(name))}</span>'
        else:
            avatar_html = f'<span class="avatar">{h(get_initials(name))}</span>'
        rows += f"""
        <tr>
          <td>
            <div class="influencer-cell">
              {avatar_html}
              <div class="influencer-cell-text">
                <span class="influencer-cell-name">{h(name)}</span>
                {'<span class="influencer-cell-handle">' + h(instagram) + '</span>' if instagram else ''}
              </div>
            </div>
          </td>
          <td><a class="pill" href="{h(link)}" target="_blank">🔗 /go/{h(slug)}</a></td>
          <td>{h(format_phone_br(item.get('phone','')))}</td>
          <td>{status_badge}</td>
          <td><a class="btn sm secondary" href="/influenciadores?edit={h(item.get('id',''))}">✏️ Editar</a></td>
        </tr>
        """
    if not rows:
        rows = '<tr><td colspan="5" class="muted">Nenhum influenciador cadastrado.</td></tr>'

    edit_id = first_param(qs, "edit")
    edit_item = None
    if edit_id:
        for item in influencers:
            if str(item.get("id")) == edit_id:
                edit_item = item
                break

    if edit_item:
        form_title = "Editar influenciador"
        action = "/influenciadores/editar"
        submit = "Salvar alterações"
        hidden = f'<input type="hidden" name="id" value="{h(edit_item.get("id",""))}">'
        name_val = edit_item.get("name", "")
        slug_val = edit_item.get("slug", "")
        instagram_val = format_instagram_handle(edit_item.get("instagram", ""))
        influencer_whatsapp_val = format_phone_br(edit_item.get("influencer_whatsapp", "")) or str(edit_item.get("influencer_whatsapp", "") or "")
        phone_val = format_phone_br(edit_item.get("phone", "")) or str(edit_item.get("phone", "") or "")
        message_val = edit_item.get("message", "")
        photo_val = edit_item.get("photo_url", "")
        active_val = parse_bool(edit_item.get("active", True), True)
        delete_form = f"""
        <div style="margin-top:16px;padding-top:16px;border-top:1px solid var(--border)">
          <p class="muted" style="font-size:12.5px;margin-bottom:10px">Zona de perigo — esta ação não pode ser desfeita.</p>
          <form method="post" action="/influenciadores/excluir" onsubmit="return confirm('Tem certeza? Este influenciador será removido permanentemente.')">
            <input type="hidden" name="id" value="{h(edit_item.get('id',''))}">
            <button class="btn danger sm" type="submit">🗑️ Excluir influenciador</button>
          </form>
        </div>
        """
    else:
        form_title = "Novo influenciador"
        action = "/influenciadores/novo"
        submit = "Salvar influenciador"
        hidden = ""
        name_val = slug_val = instagram_val = influencer_whatsapp_val = phone_val = message_val = photo_val = ""
        active_val = True
        delete_form = ""

    checked = "checked" if active_val else ""
    form_html = f"""
    <div class="card">
      <h2>{form_title}</h2>
      <form method="post" action="{action}">
        {hidden}
        <div class="form-grid">
          <div class="form-row"><label>Nome *</label><input name="name" value="{h(name_val)}" placeholder="Ana Carolina" required></div>
          <div class="form-row"><label>Slug *</label><input name="slug" value="{h(slug_val)}" placeholder="ana-carolina" required></div>
          <div class="form-row"><label>Instagram</label><input name="instagram" value="{h(instagram_val)}" placeholder="@perfil"></div>
          <div class="form-row"><label>WhatsApp do influenciador</label><input name="influencer_whatsapp" value="{h(influencer_whatsapp_val)}" placeholder="(21) 99999-9999"></div>
          <div class="form-row"><label>WhatsApp de redirecionamento *</label><input name="phone" value="{h(phone_val)}" placeholder="(21) 99999-9999" required></div>
          <div class="form-row photo-preview-wrap">
            <label>URL da foto</label>
            <div style="display:flex;align-items:center;gap:10px">
              <img id="photo-preview" class="photo-preview" src="{h(photo_val)}" style="{'display:inline-block' if photo_val else 'display:none'}">
              <input name="photo_url" id="photo-url-input" value="{h(photo_val)}" placeholder="https://..." oninput="updatePreview(this.value)" style="flex:1">
            </div>
          </div>
          <div class="form-row full"><label>Mensagem automática</label><textarea name="message" placeholder="Olá! Vim pelo link da parceria.">{h(message_val)}</textarea></div>
          <div class="form-row full">
            <label style="display:flex;align-items:center;gap:8px;cursor:pointer">
              <input type="checkbox" name="active" value="true" {checked} style="width:auto;accent-color:var(--accent)"> Influenciador ativo
            </label>
          </div>
        </div>
        <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:6px">
          <button class="btn" type="submit">💾 {submit}</button>
          {'<a class="btn secondary" href="/influenciadores">✕ Cancelar</a>' if edit_item else ''}
        </div>
      </form>
      {delete_form}
    </div>
    <script>
    function updatePreview(url) {{
      var img = document.getElementById('photo-preview');
      if(url) {{ img.src = url; img.style.display = 'inline-block'; }}
      else {{ img.style.display = 'none'; }}
    }}
    </script>
    """

    body = f"""
    <h1>Influenciadores</h1>
    <div class="caption">Cadastro, edição e geração de links rastreáveis com redirecionamento automático para WhatsApp</div>
    {alert}
    <div class="two">
      {form_html}
      <div class="card" style="align-self:start">
        <h2>📖 Como usar</h2>
        <p class="muted" style="font-size:13.5px;line-height:1.7">Copie o link da tabela abaixo. Quando alguém clicar, o sistema registra o clique no Supabase e redireciona via HTTP 302 direto para o WhatsApp.</p>
        <div style="background:rgba(91,141,238,.07);border:1px solid rgba(91,141,238,.18);border-radius:12px;padding:13px;margin-top:10px">
          <p style="margin:0 0 6px;font-size:11.5px;color:var(--muted);font-weight:700;letter-spacing:.05em;text-transform:uppercase">Formato do link</p>
          <code style="font-size:13px;color:#93b8f8;font-family:monospace">/go/nome-do-influenciador</code>
        </div>
        <div style="background:rgba(14,203,122,.06);border:1px solid rgba(14,203,122,.16);border-radius:12px;padding:13px;margin-top:10px">
          <p style="margin:0 0 4px;font-size:11.5px;color:var(--muted);font-weight:700;letter-spacing:.05em;text-transform:uppercase">Dica</p>
          <p style="margin:0;font-size:13px;color:var(--muted2);line-height:1.6">Adicione a URL de uma foto de perfil para ela aparecer na tabela e no ranking do dashboard.</p>
        </div>
      </div>
    </div>
    <div class="card section">
      <h2>🔗 Links gerados</h2>
      <div class="table-wrap"><table><thead><tr><th>Influenciador</th><th>Link rastreável</th><th>WhatsApp destino</th><th>Status</th><th>Ações</th></tr></thead><tbody>{rows}</tbody></table></div>
    </div>
    """
    return layout(scope, APP_TITLE, body, active="influenciadores")


def render_clicks(scope: dict, qs: dict[str, list[str]]) -> bytes:
    influencers, _ = load_influencers()
    inf_by_slug = {str(i.get("slug") or ""): i for i in influencers}
    clicks_raw, error = load_clicks(limit=50000)
    clicks = enrich_clicks(clicks_raw)

    # ── Filtros recebidos via GET ──
    f_slug   = first_param(qs, "slug").strip()
    f_from   = first_param(qs, "de").strip()
    f_to     = first_param(qs, "ate").strip()
    f_device = first_param(qs, "device").strip()   # "mobile" | "desktop" | ""

    # ── Aplicar filtros ──
    filtered = clicks
    if f_slug:
        filtered = [r for r in filtered if str(r.get("slug") or "") == f_slug]
    if f_from:
        filtered = [r for r in filtered if r.get("_date", "") >= f_from]
    if f_to:
        filtered = [r for r in filtered if r.get("_date", "") <= f_to]
    if f_device == "mobile":
        def is_mobile(r: dict) -> bool:
            ua = str(r.get("device_user_agent") or "").lower()
            return "iphone" in ua or "android" in ua or "ipad" in ua
        filtered = [r for r in filtered if is_mobile(r)]
    elif f_device == "desktop":
        def is_desktop(r: dict) -> bool:
            ua = str(r.get("device_user_agent") or "").lower()
            return "iphone" not in ua and "android" not in ua and "ipad" not in ua
        filtered = [r for r in filtered if is_desktop(r)]

    total_filtered = len(filtered)

    # Monta lista de influenciadores para o select
    influencer_options = '<option value="">Todos os influenciadores</option>'
    for inf in sorted(influencers, key=lambda x: str(x.get("name") or "")):
        sl = h(str(inf.get("slug") or ""))
        nm = h(str(inf.get("name") or sl))
        sel = 'selected' if sl == h(f_slug) else ''
        influencer_options += f'<option value="{sl}" {sel}>{nm}</option>'

    alert_html = ""
    if error:
        alert_html = f'<div class="alert error">Erro ao carregar cliques: {h(error)}</div>'

    # Monta linhas da tabela (máx 500 exibidas)
    rows_html = ""
    page_limit = 500
    shown = filtered[:page_limit]
    for row in shown:
        slug = str(row.get("slug") or "")
        inf  = inf_by_slug.get(slug, {})
        name = inf.get("name") or slug or "—"
        photo_url = str(inf.get("photo_url") or "").strip()
        raw_ua = str(row.get("device_user_agent") or "")
        ip = str(row.get("ip_address") or "") or "—"
        if photo_url:
            av = (f'<img class="avatar-img" src="{h(photo_url)}" alt="{h(name)}" '
                  f'onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'inline-flex\'">'
                  f'<span class="avatar" style="display:none">{h(get_initials(name))}</span>')
        else:
            av = f'<span class="avatar">{h(get_initials(name))}</span>'
        rows_html += f"""
        <tr>
          <td style="white-space:nowrap;color:var(--muted);font-size:12.5px">{h(fmt_dt(row.get('created_at')))}</td>
          <td>
            <div class="influencer-cell">
              {av}
              <div class="influencer-cell-text">
                <span class="influencer-cell-name">{h(name)}</span>
                <span class="influencer-cell-handle">/go/{h(slug)}</span>
              </div>
            </div>
          </td>
          <td style="font-size:12px;color:var(--muted);font-family:monospace">{h(ip)}</td>
          <td>{format_device_cell(raw_ua)}</td>
        </tr>"""
    if not rows_html:
        rows_html = '<tr><td colspan="4" class="muted" style="text-align:center;padding:24px">Nenhum clique encontrado para este filtro.</td></tr>'

    truncation_notice = ""
    if total_filtered > page_limit:
        truncation_notice = (
            f'<div class="alert warn" style="margin-top:12px">'
            f'Exibindo os {page_limit} registros mais recentes de {fmt_int(total_filtered)} encontrados. '
            f'Use o filtro de datas para refinar ou exporte o CSV completo.</div>'
        )

    # Atalhos de período
    today_iso = now_br().date().isoformat()
    week_ago  = (now_br().date() - timedelta(days=6)).isoformat()
    month_ago = (now_br().date() - timedelta(days=29)).isoformat()

    body = f"""
    <h1>Todos os Cliques</h1>
    <div class="caption">Histórico completo de acessos com filtros por período, influenciador e tipo de dispositivo</div>
    {alert_html}

    <div class="card section" style="margin-top:0">
      <form method="get" action="/cliques" style="display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end">
        <div style="flex:1;min-width:180px">
          <label>Influenciador</label>
          <select name="slug">{influencer_options}</select>
        </div>
        <div style="min-width:140px">
          <label>De</label>
          <input type="date" name="de" value="{h(f_from)}" max="{today_iso}">
        </div>
        <div style="min-width:140px">
          <label>Até</label>
          <input type="date" name="ate" value="{h(f_to)}" max="{today_iso}">
        </div>
        <div style="min-width:140px">
          <label>Dispositivo</label>
          <select name="device">
            <option value="" {'selected' if not f_device else ''}>Todos</option>
            <option value="mobile" {'selected' if f_device == 'mobile' else ''}>📱 Mobile</option>
            <option value="desktop" {'selected' if f_device == 'desktop' else ''}>💻 Desktop</option>
          </select>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="btn" type="submit">🔍 Filtrar</button>
          <a class="btn secondary" href="/cliques">✕ Limpar</a>
        </div>
      </form>

      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:14px">
        <span style="font-size:12px;color:var(--muted);align-self:center;margin-right:4px">Atalhos:</span>
        <a class="btn sm secondary" href="/cliques?de={today_iso}&ate={today_iso}">Hoje</a>
        <a class="btn sm secondary" href="/cliques?de={week_ago}&ate={today_iso}">Últimos 7 dias</a>
        <a class="btn sm secondary" href="/cliques?de={month_ago}&ate={today_iso}">Últimos 30 dias</a>
        <a class="btn sm secondary" href="/export/cliques.csv" style="margin-left:auto">⬇️ Exportar CSV</a>
      </div>
    </div>

    <div class="card section">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px">
        <h2 style="margin:0">🖱️ Cliques</h2>
        <span style="font-size:13px;color:var(--muted)">{fmt_int(total_filtered)} registro{"s" if total_filtered != 1 else ""} encontrado{"s" if total_filtered != 1 else ""}</span>
      </div>
      {truncation_notice}
      <div class="table-wrap">
        <table>
          <thead><tr><th>Data/Hora</th><th>Influenciador</th><th>IP</th><th>Dispositivo</th></tr></thead>
          <tbody>{rows_html}</tbody>
        </table>
      </div>
    </div>
    """
    return layout(scope, APP_TITLE, body, active="cliques")



    body = """
    <h1>Exportar CSV</h1>
    <div class="caption">Baixe os dados atuais da base para análise externa</div>
    <div class="grid4">
      <div class="card">
        <div style="font-size:28px;margin-bottom:12px">👤</div>
        <h2>Influenciadores</h2>
        <p class="muted" style="font-size:13.5px;line-height:1.6">Cadastro completo com todos os campos, sem imagens embutidas.</p>
        <a class="btn" href="/export/influenciadores.csv" style="margin-top:10px">⬇️ Baixar CSV</a>
      </div>
      <div class="card">
        <div style="font-size:28px;margin-bottom:12px">🖱️</div>
        <h2>Cliques</h2>
        <p class="muted" style="font-size:13.5px;line-height:1.6">Histórico completo de cliques, IPs e dados de navegador.</p>
        <a class="btn" href="/export/cliques.csv" style="margin-top:10px">⬇️ Baixar CSV</a>
      </div>
    </div>
    """
    return layout(scope, APP_TITLE, body, active="exportar")


def render_error(scope: dict, message: str, status: int = 500) -> bytes:
    body = f"""
    <div class="card" style="max-width:920px;margin:40px auto;">
      <h1>Erro</h1>
      <div class="alert error">{h(message)}</div>
      <p><a class="btn secondary" href="/">Voltar ao dashboard</a></p>
    </div>
    """
    return layout(scope, "Erro", body, active="dashboard")


def rows_to_csv(rows: list[dict], drop_cols: set[str] | None = None) -> bytes:
    drop_cols = drop_cols or set()
    output = StringIO()
    if not rows:
        return b""
    keys = []
    for row in rows:
        for key in row.keys():
            if key not in keys and key not in drop_cols and not key.startswith("_"):
                keys.append(key)
    writer = csv.DictWriter(output, fieldnames=keys, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k, "") for k in keys})
    return output.getvalue().encode("utf-8-sig")


# =========================================================
# ASGI APP
# =========================================================
async def read_body(receive) -> bytes:
    chunks: list[bytes] = []
    while True:
        message = await receive()
        if message.get("type") != "http.request":
            break
        chunks.append(message.get("body", b""))
        if not message.get("more_body"):
            break
    return b"".join(chunks)


async def send_response(send, status: int, body: bytes = b"", content_type: str = "text/html; charset=utf-8", headers: list[tuple[bytes, bytes]] | None = None):
    final_headers = [(b"content-type", content_type.encode("latin1")), (b"content-length", str(len(body)).encode())]
    if headers:
        final_headers.extend(headers)
    await send({"type": "http.response.start", "status": status, "headers": final_headers})
    await send({"type": "http.response.body", "body": body})


async def redirect(send, location: str, status: int = 302):
    body = f'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url={h(location)}"><a href="{h(location)}">Redirecionando...</a>'.encode("utf-8")
    await send_response(send, status, body, headers=[(b"location", location.encode("latin1", errors="ignore"))])


class InfluencerASGIApp:
    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await send_response(send, 404, b"Not found", "text/plain; charset=utf-8")
            return

        method = scope.get("method", "GET").upper()
        path = scope.get("path", "/") or "/"
        qs = parse_query_string(scope.get("query_string", b""))

        try:
            if method == "GET" and path == "/healthz":
                await send_response(send, 200, b"ok", "text/plain; charset=utf-8")
                return

            if method == "GET" and path == "/":
                go_slug = first_param(qs, "go").strip()
                if go_slug:
                    await self.handle_go(scope, send, go_slug)
                    return
                await send_response(send, 200, render_dashboard(scope))
                return

            if method == "GET" and path.startswith("/go/"):
                slug = urllib.parse.unquote(path.split("/go/", 1)[1].strip("/"))
                await self.handle_go(scope, send, slug)
                return

            if method == "GET" and path == "/influenciadores":
                await send_response(send, 200, render_influencers(scope, qs))
                return

            if method == "GET" and path == "/cliques":
                await send_response(send, 200, render_clicks(scope, qs))
                return

            if method == "GET" and path == "/exportar":
                await send_response(send, 200, render_export(scope))
                return

            if method == "GET" and path == "/export/influenciadores.csv":
                rows, _ = load_influencers()
                csv_body = rows_to_csv(rows, drop_cols={"photo_url"})
                await send_response(
                    send, 200, csv_body, "text/csv; charset=utf-8",
                    headers=[(b"content-disposition", b'attachment; filename="influenciadores.csv"')],
                )
                return

            if method == "GET" and path == "/export/cliques.csv":
                rows, _ = load_clicks(limit=50000)
                csv_body = rows_to_csv(enrich_clicks(rows))
                await send_response(
                    send, 200, csv_body, "text/csv; charset=utf-8",
                    headers=[(b"content-disposition", b'attachment; filename="cliques.csv"')],
                )
                return

            if method == "POST" and path in {"/influenciadores/novo", "/influenciadores/editar", "/influenciadores/excluir"}:
                body = await read_body(receive)
                form = parse_form(body)
                if path == "/influenciadores/novo":
                    await self.handle_create(send, form)
                    return
                if path == "/influenciadores/editar":
                    await self.handle_update(send, form)
                    return
                if path == "/influenciadores/excluir":
                    await self.handle_delete(send, form)
                    return

            await send_response(send, 404, render_error(scope, "Página não encontrada.", 404))
        except Exception as e:
            traceback.print_exc()
            await send_response(send, 500, render_error(scope, f"Erro interno: {e}\n\n{traceback.format_exc()}"))

    async def handle_go(self, scope: dict, send, slug: str):
        influencer = get_influencer_by_slug(slug)
        if not influencer:
            await send_response(send, 404, render_error(scope, "Link não encontrado ou influenciador inexistente.", 404))
            return
        if not parse_bool(influencer.get("active", True), True):
            await send_response(send, 403, render_error(scope, "Este link está inativo.", 403))
            return

        phone = influencer.get("phone", "")
        if not build_whatsapp_redirect_number(phone):
            await send_response(send, 400, render_error(scope, "WhatsApp de redirecionamento inválido neste cadastro.", 400))
            return

        target_url = build_whatsapp_url(phone, influencer.get("message", ""))
        register_click(influencer, target_url, scope)
        await redirect(send, target_url, status=302)

    async def handle_create(self, send, form: dict[str, str]):
        name = form.get("name", "").strip()
        slug = slugify(form.get("slug", ""))
        phone = form.get("phone", "").strip()
        if not name or not slug or not phone:
            await redirect(send, "/influenciadores?erro=campos")
            return
        if not is_valid_br_phone(phone):
            await redirect(send, "/influenciadores?erro=telefone")
            return
        influencer_whatsapp = form.get("influencer_whatsapp", "").strip()
        payload = {
            "name": name,
            "slug": slug,
            "instagram": normalize_instagram_handle(form.get("instagram", "")),
            "influencer_whatsapp": normalize_whatsapp_number(influencer_whatsapp),
            "phone": normalize_whatsapp_number(phone),
            "message": form.get("message", "").strip(),
            "active": bool(form.get("active")),
            "created_at": now_iso(),
        }
        photo_url = form.get("photo_url", "").strip()
        if photo_url:
            payload["photo_url"] = photo_url
        insert_influencer(payload)
        await redirect(send, "/influenciadores?ok=1")

    async def handle_update(self, send, form: dict[str, str]):
        influencer_id = form.get("id", "").strip()
        name = form.get("name", "").strip()
        slug = slugify(form.get("slug", ""))
        phone = form.get("phone", "").strip()
        if not influencer_id or not name or not slug or not phone:
            await redirect(send, "/influenciadores?erro=campos")
            return
        if not is_valid_br_phone(phone):
            await redirect(send, f"/influenciadores?edit={urllib.parse.quote(influencer_id)}&erro=telefone")
            return
        influencer_whatsapp = form.get("influencer_whatsapp", "").strip()
        payload = {
            "name": name,
            "slug": slug,
            "instagram": normalize_instagram_handle(form.get("instagram", "")),
            "influencer_whatsapp": normalize_whatsapp_number(influencer_whatsapp),
            "phone": normalize_whatsapp_number(phone),
            "message": form.get("message", "").strip(),
            "active": bool(form.get("active")),
        }
        photo_url = form.get("photo_url", "").strip()
        if photo_url:
            payload["photo_url"] = photo_url
        update_influencer(influencer_id, payload)
        await redirect(send, "/influenciadores?ok=1")

    async def handle_delete(self, send, form: dict[str, str]):
        influencer_id = form.get("id", "").strip()
        if influencer_id:
            delete_influencer(influencer_id)
        await redirect(send, "/influenciadores?deleted=1")


app = InfluencerASGIApp()


def _is_port_free(host: str, port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, int(port)))
            return True
        except OSError:
            return False


def _choose_port(start_port: int, host: str, max_attempts: int = 40) -> int:
    forced_port = os.getenv("PORT")
    if forced_port:
        return int(forced_port)

    for candidate in [start_port, 8080, 8081, 8082, 8502, 8503, 8504, 9000, 9001]:
        if _is_port_free(host, candidate):
            return candidate

    for candidate in range(max(1024, start_port), max(1024, start_port) + max_attempts):
        if _is_port_free(host, candidate):
            return candidate

    raise RuntimeError("Nenhuma porta livre foi encontrada para iniciar o servidor.")


def run() -> None:
    try:
        import uvicorn
    except Exception as exc:
        raise SystemExit(
            "Instale o uvicorn para rodar este arquivo diretamente: pip install uvicorn"
        ) from exc

    is_cloud_or_hosted = bool(os.getenv("PORT"))
    host = "0.0.0.0" if is_cloud_or_hosted else "127.0.0.1"
    port = _choose_port(DEFAULT_PORT, host)

    local_url = f"http://127.0.0.1:{port}"
    print("=" * 70)
    print("Gerenciador de Influenciadores - SuplemeX")
    print(f"Acesse: {local_url}")
    print("IMPORTANTE: execute com python ou uvicorn, não com streamlit run.")
    print("=" * 70)

    if not is_cloud_or_hosted:
        try:
            import threading
            import time as _time
            import webbrowser

            def _open_browser():
                _time.sleep(1.2)
                webbrowser.open(local_url)

            threading.Thread(target=_open_browser, daemon=True).start()
        except Exception:
            pass

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    run()
