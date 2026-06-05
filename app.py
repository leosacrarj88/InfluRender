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
    :root {
        --bg:#050816; --panel:#0b1120; --panel2:#0f172a; --border:rgba(255,255,255,.08);
        --text:#f8fafc; --muted:#94a3b8; --muted2:#cbd5e1; --purple:#8b5cf6; --blue:#3b82f6;
        --green:#10b981; --red:#ef4444; --orange:#f59e0b;
    }
    *{box-sizing:border-box} body{margin:0;font-family:Inter,Segoe UI,Arial,sans-serif;background:
    radial-gradient(circle at 85% 5%, rgba(139,92,246,.20), transparent 26%),
    radial-gradient(circle at 5% 75%, rgba(16,185,129,.10), transparent 28%),var(--bg);color:var(--text)}
    a{color:inherit} .layout{display:grid;grid-template-columns:260px minmax(0,1fr);min-height:100vh}
    aside{background:#06111f;border-right:1px solid var(--border);padding:22px;position:sticky;top:0;height:100vh}
    main{padding:24px;max-width:1500px;width:100%;margin:0 auto}.brand{display:flex;gap:12px;align-items:center;margin-bottom:22px}
    .brand-icon{width:50px;height:50px;border-radius:16px;background:linear-gradient(135deg,#2f5bff,#7c3aed);display:flex;align-items:center;justify-content:center;font-size:25px}
    .brand strong{display:block;font-size:18px;line-height:1.05}.brand span{font-size:13px;color:var(--muted)}
    .nav a{display:block;padding:12px 14px;border-radius:14px;text-decoration:none;color:#e2e8f0;margin-bottom:8px;font-weight:700;font-size:14px}
    .nav a.active,.nav a:hover{background:rgba(255,255,255,.07)}h1{font-size:30px;margin:0 0 4px} .caption{color:var(--muted);margin-bottom:18px}
    .grid4{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.grid5{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:14px}
    .card{background:rgba(11,17,32,.88);border:1px solid var(--border);border-radius:20px;padding:18px;box-shadow:0 24px 60px rgba(0,0,0,.20)}
    .metric{display:flex;gap:14px;align-items:center}.metric .ico{width:54px;height:54px;border-radius:16px;display:flex;align-items:center;justify-content:center;font-size:24px;background:rgba(139,92,246,.16)}
    .metric label{display:block;color:var(--muted);font-size:12px;font-weight:700}.metric b{display:block;font-size:28px;margin-top:2px}.metric small{color:var(--muted2);font-weight:700}
    .section{margin-top:16px}.two{display:grid;grid-template-columns:1.55fr 1fr;gap:16px}.table-wrap{overflow:auto;border-radius:16px;border:1px solid var(--border)}
    table{width:100%;border-collapse:collapse;background:rgba(15,23,42,.45)}th,td{text-align:left;padding:12px;border-bottom:1px solid var(--border);font-size:13px;vertical-align:middle}th{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.04em}tr:last-child td{border-bottom:none}
    .pill{display:inline-block;padding:5px 10px;border-radius:999px;background:rgba(139,92,246,.14);border:1px solid rgba(139,92,246,.32);color:#c4b5fd;text-decoration:none;font-weight:800;font-size:12px;white-space:nowrap}
    .btn{display:inline-flex;align-items:center;justify-content:center;border:0;border-radius:13px;padding:11px 14px;background:linear-gradient(90deg,#3558ff,#7c3aed);color:white;text-decoration:none;font-weight:800;cursor:pointer;min-height:42px}
    .btn.secondary{background:rgba(255,255,255,.08);border:1px solid var(--border)}.btn.danger{background:linear-gradient(90deg,#dc2626,#ef4444)}
    .form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.form-row{margin-bottom:12px}label{display:block;font-size:13px;color:var(--muted2);font-weight:700;margin-bottom:6px}
    input,textarea,select{width:100%;background:#071020;color:white;border:1px solid var(--border);border-radius:13px;padding:12px;font:inherit;outline:none}textarea{min-height:90px;resize:vertical}.full{grid-column:1/-1}
    .alert{padding:13px 15px;border-radius:14px;margin:12px 0;font-weight:700}.alert.error{background:rgba(239,68,68,.12);border:1px solid rgba(239,68,68,.30);color:#fecaca}.alert.ok{background:rgba(16,185,129,.12);border:1px solid rgba(16,185,129,.30);color:#bbf7d0}.alert.warn{background:rgba(245,158,11,.12);border:1px solid rgba(245,158,11,.30);color:#fde68a}
    .avatar{width:34px;height:34px;border-radius:50%;background:linear-gradient(135deg,#203cff,#8b5cf6);display:inline-flex;align-items:center;justify-content:center;font-weight:900;color:#fff;margin-right:8px;vertical-align:middle}.muted{color:var(--muted)}
    @media(max-width:960px){.layout{grid-template-columns:1fr}aside{position:relative;height:auto}.grid4,.grid5,.two,.form-grid{grid-template-columns:1fr}main{padding:16px}}
    """


def layout(scope: dict, title: str, body: str, active: str = "dashboard") -> bytes:
    nav = [
        ("dashboard", "/", "Dashboard"),
        ("influenciadores", "/influenciadores", "Influenciadores"),
        ("exportar", "/exportar", "Exportar CSV"),
    ]
    links = "".join(
        f'<a class="{"active" if key == active else ""}" href="{href}">{label}</a>'
        for key, href, label in nav
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
  <div class="brand"><div class="brand-icon">🔗</div><div><strong>Gerenciador<br>de Links</strong><span>Influenciadores</span></div></div>
  <div class="nav">{links}</div>
  <div class="card" style="margin-top:20px;padding:14px;color:#94a3b8;font-size:13px;">Administrador<br><span class="muted">SuplemeX</span></div>
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
        top_rows += f"""
        <tr><td>{idx}</td><td><span class="avatar">{h(get_initials(name))}</span>{h(name)}</td><td><a class="pill" href="{h(influencer_link(scope, slug))}" target="_blank">/go/{h(slug)}</a></td><td><b>{fmt_int(count)}</b></td></tr>
        """
    if not top_rows:
        top_rows = '<tr><td colspan="4" class="muted">Ainda não há cliques registrados.</td></tr>'

    recent_rows = ""
    for row in clicks[:12]:
        slug = str(row.get("slug") or "")
        inf = inf_by_slug.get(slug, {})
        name = inf.get("name") or slug or "Influenciador"
        recent_rows += f"""
        <tr><td>{h(fmt_dt(row.get('created_at')))}</td><td>{h(name)}</td><td>{h(row.get('ip_address',''))}</td><td>{h(str(row.get('device_user_agent',''))[:80])}</td></tr>
        """
    if not recent_rows:
        recent_rows = '<tr><td colspan="4" class="muted">Nenhuma atividade recente.</td></tr>'

    alerts = ""
    if inf_error:
        alerts += f'<div class="alert error">Erro ao carregar influenciadores: {h(inf_error)}</div>'
    if clicks_error:
        alerts += f'<div class="alert error">Erro ao carregar cliques: {h(clicks_error)}</div>'

    body = f"""
    <h1>Dashboard</h1>
    <div class="caption">Visão geral dos links, clientes e cliques dos influenciadores</div>
    {alerts}
    <div class="grid5">
      <div class="card metric"><div class="ico">👥</div><div><label>Clientes únicos</label><b>{fmt_int(clients_unique)}</b><small>Por IP/navegador ou telefone antigo</small></div></div>
      <div class="card metric"><div class="ico">🖱️</div><div><label>Total de cliques</label><b>{fmt_int(total_clicks)}</b><small>Todos os acessos</small></div></div>
      <div class="card metric"><div class="ico">📅</div><div><label>Cliques hoje</label><b>{fmt_int(clicks_today)}</b><small>Data Brasil</small></div></div>
      <div class="card metric"><div class="ico">📈</div><div><label>Média por influencer</label><b>{fmt_int(avg_clicks)}</b><small>Cliques / cadastro</small></div></div>
      <div class="card metric"><div class="ico">🔗</div><div><label>Influenciadores</label><b>{fmt_int(total_influencers)}</b><small>{fmt_int(active_influencers)} ativos</small></div></div>
    </div>

    <div class="two section">
      <div class="card">
        <h2>Ranking por cliques</h2>
        <div class="table-wrap"><table><thead><tr><th>#</th><th>Influenciador</th><th>Link</th><th>Cliques</th></tr></thead><tbody>{top_rows}</tbody></table></div>
      </div>
      <div class="card">
        <h2>Ações rápidas</h2>
        <p class="muted">Cadastre influenciadores e copie links rastreáveis que abrem o WhatsApp automaticamente.</p>
        <p><a class="btn" href="/influenciadores">Gerenciar influenciadores</a></p>
        <p><a class="btn secondary" href="/exportar">Exportar relatórios</a></p>
      </div>
    </div>

    <div class="card section">
      <h2>Atividade recente</h2>
      <div class="table-wrap"><table><thead><tr><th>Data/Hora</th><th>Influenciador</th><th>IP</th><th>Navegador</th></tr></thead><tbody>{recent_rows}</tbody></table></div>
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
        rows += f"""
        <tr>
          <td><span class="avatar">{h(get_initials(name))}</span><b>{h(name)}</b><br><span class="muted">{h(format_instagram_handle(item.get('instagram','')))}</span></td>
          <td><a class="pill" href="{h(link)}" target="_blank">{h(link)}</a></td>
          <td>{h(format_phone_br(item.get('phone','')))}</td>
          <td>{'Ativo' if active else 'Inativo'}</td>
          <td><a class="btn secondary" href="/influenciadores?edit={h(item.get('id',''))}">Editar</a></td>
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
        <form method="post" action="/influenciadores/excluir" onsubmit="return confirm('Excluir este influenciador?')" style="margin-top:10px;">
          <input type="hidden" name="id" value="{h(edit_item.get('id',''))}">
          <button class="btn danger" type="submit">Excluir influenciador</button>
          <a class="btn secondary" href="/influenciadores">Cancelar edição</a>
        </form>
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
          <div class="form-row"><label>Nome</label><input name="name" value="{h(name_val)}" required></div>
          <div class="form-row"><label>Slug</label><input name="slug" value="{h(slug_val)}" placeholder="ana-carolina" required></div>
          <div class="form-row"><label>Instagram</label><input name="instagram" value="{h(instagram_val)}" placeholder="@perfil"></div>
          <div class="form-row"><label>WhatsApp do influenciador</label><input name="influencer_whatsapp" value="{h(influencer_whatsapp_val)}" placeholder="(21) 99999-9999"></div>
          <div class="form-row"><label>WhatsApp de redirecionamento</label><input name="phone" value="{h(phone_val)}" placeholder="(21) 99999-9999" required></div>
          <div class="form-row"><label>URL da foto</label><input name="photo_url" value="{h(photo_val)}" placeholder="https://..."></div>
          <div class="form-row full"><label>Mensagem automática</label><textarea name="message" placeholder="Olá! Vim pelo link da parceria.">{h(message_val)}</textarea></div>
          <div class="form-row"><label><input type="checkbox" name="active" value="true" {checked} style="width:auto;margin-right:8px;"> Ativo</label></div>
        </div>
        <button class="btn" type="submit">{submit}</button>
      </form>
      {delete_form}
    </div>
    """

    body = f"""
    <h1>Influenciadores</h1>
    <div class="caption">Cadastro, edição e geração de links rastreáveis com redirecionamento automático para WhatsApp</div>
    {alert}
    <div class="two">
      {form_html}
      <div class="card">
        <h2>Como usar</h2>
        <p class="muted">Copie o link da tabela. Quando alguém clicar, o sistema registra o clique no Supabase e responde HTTP 302 direto para o WhatsApp.</p>
        <p class="muted"><b>Formato:</b> /go/nome-do-influenciador</p>
      </div>
    </div>
    <div class="card section">
      <h2>Links gerados</h2>
      <div class="table-wrap"><table><thead><tr><th>Influenciador</th><th>Link rastreável</th><th>WhatsApp destino</th><th>Status</th><th>Ações</th></tr></thead><tbody>{rows}</tbody></table></div>
    </div>
    """
    return layout(scope, APP_TITLE, body, active="influenciadores")


def render_export(scope: dict) -> bytes:
    body = """
    <h1>Exportar CSV</h1>
    <div class="caption">Baixe os dados atuais da base para análise externa</div>
    <div class="grid4">
      <div class="card"><h2>Influenciadores</h2><p class="muted">Cadastro completo sem imagens embutidas.</p><a class="btn" href="/export/influenciadores.csv">Baixar CSV</a></div>
      <div class="card"><h2>Cliques</h2><p class="muted">Histórico de cliques e clientes.</p><a class="btn" href="/export/cliques.csv">Baixar CSV</a></div>
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
