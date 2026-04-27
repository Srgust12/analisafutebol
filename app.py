"""
AnalisaFutebol - Plataforma de Análise de Futebol
Autor: AnalisaFutebol
Versão: 1.0.0
"""

import os
import json
import requests
from datetime import datetime
from flask import Flask, jsonify, render_template_string

# ─────────────────────────────────────────
#  CONFIGURAÇÕES
# ─────────────────────────────────────────
API_KEY  = "76e4bc07922c4d65a6a8ad3ddbd559796318cfbb"
BASE_URL = "https://sports.bzzoiro.com/api"
HEADERS  = {"Authorization": f"Token {API_KEY}"}

app = Flask(__name__)

# ─────────────────────────────────────────
#  FUNÇÕES AUXILIARES
# ─────────────────────────────────────────

def api_get(endpoint, params=None):
    """Faz GET na API com tratamento robusto de erros."""
    try:
        url = f"{BASE_URL}/{endpoint.lstrip('/')}/"
        resp = requests.get(url, headers=HEADERS, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        print(f"[API ERROR] {endpoint}: {e}")
        return None


def get_team_name(event, side: str) -> str:
    """
    Extrai nome do time de forma robusta.
    Tenta home_team_obj/away_team_obj primeiro, depois home_team/away_team,
    depois event['event'][side], etc.
    """
    # O evento pode vir direto ou aninhado em 'event'
    ev = event.get("event", event)
    obj_key = f"{side}_team_obj"
    str_key = f"{side}_team"

    obj = ev.get(obj_key)
    if obj and isinstance(obj, dict):
        return obj.get("name") or obj.get("short_name") or "Desconhecido"
    return ev.get(str_key) or "Desconhecido"


def normalize_prob(value) -> float:
    """
    Normaliza probabilidade para 0-100.
    Aceita: 0.52 → 52 | 52 → 52 | 5200 → 52
    """
    if value is None:
        return 0.0
    v = float(value)
    if v <= 1.0:
        return round(v * 100, 1)
    if v > 100:
        return round(v / 100, 1)
    return round(v, 1)


def format_date(date_str: str) -> str:
    """Formata data ISO para DD/MM/YYYY HH:MM (horário de Brasília ~UTC-3)."""
    if not date_str:
        return "—"
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        # Converte para horário de Brasília (UTC-3)
        from datetime import timezone, timedelta
        brt = timezone(timedelta(hours=-3))
        dt_brt = dt.astimezone(brt)
        return dt_brt.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return date_str[:16].replace("T", " ")


def tip_label(home: float, draw: float, away: float) -> dict:
    """Retorna dica visual baseada nas probabilidades."""
    if home >= 55:
        return {"label": "Casa Forte 🏠", "cls": "tip-home"}
    if away >= 55:
        return {"label": "Fora Forte ✈️", "cls": "tip-away"}
    if home > away and home >= 45:
        return {"label": "Favorito Casa", "cls": "tip-fav"}
    if away > home and away >= 45:
        return {"label": "Favorito Fora", "cls": "tip-fav"}
    return {"label": "Equilibrado ⚖️", "cls": "tip-draw"}


# ─────────────────────────────────────────
#  INTELIGÊNCIA / ANÁLISE
# ─────────────────────────────────────────

# Países considerados "Brasil" (Bolão Nacional)
BR_COUNTRIES = {"brazil", "brasil", "br"}

# Países sul-americanos relevantes (CONMEBOL)
SULAMERICA = {
    "argentina", "uruguay", "paraguay", "chile", "colombia",
    "peru", "ecuador", "bolivia", "venezuela"
}

# Top ligas europeias (peso extra de qualidade)
TOP_EUROPE = {
    "england", "spain", "italy", "germany", "france",
    "portugal", "netherlands", "belgium"
}

def classify_region(country: str) -> str:
    """Classifica país em: br | sulamerica | europa | outros."""
    if not country:
        return "outros"
    c = country.strip().lower()
    if c in BR_COUNTRIES:
        return "br"
    if c in SULAMERICA:
        return "sulamerica"
    if c in TOP_EUROPE:
        return "europa"
    return "outros"


def best_pick(ph: float, pd: float, pa: float) -> dict:
    """
    Decide o palpite ideal (1, X, 2) com base nas probabilidades.
    Retorna dict com: pick (1/X/2), pick_label, pick_prob.
    """
    items = [("1", "Vitória Casa", ph), ("X", "Empate", pd), ("2", "Vitória Fora", pa)]
    items.sort(key=lambda x: x[2], reverse=True)
    code, label, prob = items[0]
    return {"pick": code, "pick_label": label, "pick_prob": prob}


def confidence_score(ph: float, pd: float, pa: float, conf_api: float,
                     prob_over: float, prob_btts: float,
                     xg_home: float, xg_away: float) -> dict:
    """
    Calcula score de confiança 0-100 para o jogo, combinando vários sinais.
    Retorna também classificação textual.
    """
    # 1. Margem do favorito (quanto mais dominante, melhor)
    max_p = max(ph, pa)
    min_p = min(ph, pa)
    margin = max_p - min_p   # 0..100

    # 2. Confiança original da API (já em 0-100)
    api_conf = conf_api if conf_api else 0

    # 3. Convicção em mercados secundários (over/btts)
    # Quanto mais perto de 50/50, menos convicção. Quanto mais perto de 70+ ou 30-, mais.
    over_conv = abs(prob_over - 50) * 2     # 0..100
    btts_conv = abs(prob_btts - 50) * 2     # 0..100

    # 4. xG total razoável (jogos com xG muito baixo são imprevisíveis)
    xg_total = xg_home + xg_away
    xg_factor = min(xg_total / 3.0, 1.0) * 100   # 0..100

    # Pesos
    score = (
        margin     * 0.35 +
        api_conf   * 0.25 +
        over_conv  * 0.10 +
        btts_conv  * 0.10 +
        xg_factor  * 0.20
    )
    score = round(min(max(score, 0), 100), 1)

    if score >= 75:
        tier = {"label": "🔥 Altíssima", "cls": "tier-elite"}
    elif score >= 60:
        tier = {"label": "✅ Alta", "cls": "tier-high"}
    elif score >= 45:
        tier = {"label": "⚠️ Média", "cls": "tier-mid"}
    else:
        tier = {"label": "🎲 Arriscado", "cls": "tier-low"}

    return {"score": score, "tier": tier}


def build_auto_bet(pred: dict) -> dict:
    """
    Monta o palpite automático completo do jogo:
    Resultado + Placar + Over/Under 2.5 + Ambos Marcam.
    """
    pick = best_pick(pred["prob_home"], pred["prob_draw"], pred["prob_away"])

    # Over/Under
    if pred["prob_over_25"] >= 55:
        ou = {"label": "Over 2.5 ⬆️", "cls": "ou-over", "prob": pred["prob_over_25"]}
    elif pred["prob_over_25"] <= 45:
        ou = {"label": "Under 2.5 ⬇️", "cls": "ou-under", "prob": round(100 - pred["prob_over_25"], 1)}
    else:
        ou = {"label": "Over/Under indefinido", "cls": "ou-draw", "prob": pred["prob_over_25"]}

    # Ambos marcam
    if pred["prob_btts"] >= 55:
        btts = {"label": "Ambos Marcam: SIM", "cls": "btts-yes", "prob": pred["prob_btts"]}
    elif pred["prob_btts"] <= 45:
        btts = {"label": "Ambos Marcam: NÃO", "cls": "btts-no", "prob": round(100 - pred["prob_btts"], 1)}
    else:
        btts = {"label": "BTTS indefinido", "cls": "btts-draw", "prob": pred["prob_btts"]}

    return {
        "pick": pick,
        "score": pred.get("most_likely_score", "—"),
        "ou": ou,
        "btts": btts,
    }


# ─────────────────────────────────────────
#  ROTAS DE API (usadas pelo JS via fetch)
# ─────────────────────────────────────────

@app.route("/api/live")
def api_live():
    """Retorna jogos ao vivo processados."""
    data = api_get("live", params={"tz": "America/Sao_Paulo"})
    games = []
    if data and "results" in data:
        for g in data["results"]:
            ev = g.get("event", g)
            home = get_team_name(g, "home")
            away = get_team_name(g, "away")

            # Estatísticas ao vivo
            stats = g.get("live_stats") or {}
            home_stats = stats.get("home") or {}
            away_stats = stats.get("away") or {}

            # Incidents (gols, cartões)
            incidents = g.get("incidents") or []
            processed_incidents = []
            for inc in incidents:
                t = inc.get("type", "")
                minute = inc.get("minute", "?")
                player = inc.get("player_name", "")
                is_home = inc.get("is_home", True)
                card_type = inc.get("card_type", "")
                if t == "goal":
                    icon = "⚽"
                elif t == "card":
                    icon = "🟨" if card_type == "yellow" else "🟥"
                elif t == "substitution":
                    icon = "🔄"
                    player = f"{inc.get('player_in','?')} ↔ {inc.get('player_out','?')}"
                else:
                    icon = "ℹ️"
                processed_incidents.append({
                    "icon": icon, "minute": minute,
                    "player": player, "is_home": is_home
                })

            # IDs para logos
            home_id = (g.get("home_team_obj") or ev.get("home_team_obj") or {}).get("id")
            away_id = (g.get("away_team_obj") or ev.get("away_team_obj") or {}).get("id")

            games.append({
                "id": g.get("id"),
                "home": home,
                "away": away,
                "home_score": g.get("home_score", 0) or 0,
                "away_score": g.get("away_score", 0) or 0,
                "minute": g.get("current_minute"),
                "period": g.get("period", ""),
                "status": g.get("status", ""),
                "league": (g.get("league") or {}).get("name", ""),
                "league_country": (g.get("league") or {}).get("country", ""),
                "home_possession": home_stats.get("ball_possession", "—"),
                "away_possession": away_stats.get("ball_possession", "—"),
                "home_shots": home_stats.get("total_shots", "—"),
                "away_shots": away_stats.get("total_shots", "—"),
                "home_shots_ot": home_stats.get("shots_on_target", "—"),
                "away_shots_ot": away_stats.get("shots_on_target", "—"),
                "incidents": processed_incidents,
                "home_id": home_id,
                "away_id": away_id,
            })
    return jsonify(games)


@app.route("/api/predictions")
def api_predictions():
    """Retorna previsões futuras ordenadas por maior probabilidade de vitória."""
    data = api_get("predictions", params={
        "upcoming": "true",
        "tz": "America/Sao_Paulo"
    })
    preds = []
    if data and "results" in data:
        for p in data["results"]:
            ev = p.get("event") or {}

            home = get_team_name(p, "home")
            away = get_team_name(p, "away")

            ph = normalize_prob(p.get("prob_home_win"))
            pd = normalize_prob(p.get("prob_draw"))
            pa = normalize_prob(p.get("prob_away_win"))

            # Maior probabilidade (casa ou fora, ignora empate para ordenação)
            max_win = max(ph, pa)
            highlight = max_win >= 55

            tip = tip_label(ph, pd, pa)
            date_str = ev.get("event_date", "")

            # Logos
            home_id = (ev.get("home_team_obj") or {}).get("id")
            away_id = (ev.get("away_team_obj") or {}).get("id")

            preds.append({
                "id": p.get("id"),
                "event_id": ev.get("id"),
                "home": home,
                "away": away,
                "home_id": home_id,
                "away_id": away_id,
                "prob_home": ph,
                "prob_draw": pd,
                "prob_away": pa,
                "max_win": max_win,
                "highlight": highlight,
                "tip": tip,
                "predicted_result": p.get("predicted_result", ""),
                "expected_home_goals": round(float(p.get("expected_home_goals") or 0), 1),
                "expected_away_goals": round(float(p.get("expected_away_goals") or 0), 1),
                "most_likely_score": p.get("most_likely_score", "—"),
                "confidence": round(float(p.get("confidence") or 0) * 100, 0),
                "prob_over_25": normalize_prob(p.get("prob_over_25")),
                "prob_btts": normalize_prob(p.get("prob_btts_yes")),
                "over_25_recommend": p.get("over_25_recommend", False),
                "btts_recommend": p.get("btts_recommend", False),
                "league": (ev.get("league") or {}).get("name", ""),
                "league_country": (ev.get("league") or {}).get("country", ""),
                "date": format_date(date_str),
            })

    # Ordena por maior probabilidade de vitória (casa ou fora)
    preds.sort(key=lambda x: x["max_win"], reverse=True)
    return jsonify(preds)


@app.route("/api/leagues")
def api_leagues():
    """Retorna lista única de ligas/países disponíveis nas previsões."""
    data = api_get("predictions", params={"upcoming": "true", "tz": "America/Sao_Paulo"})
    leagues_map = {}
    if data and "results" in data:
        for p in data["results"]:
            ev = p.get("event") or {}
            lg = (ev.get("league") or {}).get("name", "")
            country = (ev.get("league") or {}).get("country", "")
            if not lg:
                continue
            key = f"{country}|{lg}"
            if key not in leagues_map:
                leagues_map[key] = {
                    "league": lg,
                    "country": country,
                    "region": classify_region(country),
                    "count": 0
                }
            leagues_map[key]["count"] += 1
    leagues = sorted(leagues_map.values(), key=lambda x: (-x["count"], x["league"]))
    return jsonify(leagues)


@app.route("/api/bolao-inteligente")
def api_bolao_inteligente():
    """
    Bolão Inteligente Unificado: todas as ligas (nacionais + internacionais)
    com palpite automático, score de confiança e análise integrada.
    Aceita query params:
      - region: br | sulamerica | europa | outros | all (default)
      - min_score: filtro mínimo de confiança (default 0)
      - limit: limita número de resultados
    """
    from flask import request
    region_filter = request.args.get("region", "all").lower()
    min_score = float(request.args.get("min_score", 0))
    limit = request.args.get("limit", type=int)

    data = api_get("predictions", params={
        "upcoming": "true",
        "tz": "America/Sao_Paulo"
    })
    games = []
    if data and "results" in data:
        for p in data["results"]:
            ev = p.get("event") or {}
            home = get_team_name(p, "home")
            away = get_team_name(p, "away")

            ph = normalize_prob(p.get("prob_home_win"))
            pd = normalize_prob(p.get("prob_draw"))
            pa = normalize_prob(p.get("prob_away_win"))

            prob_over = normalize_prob(p.get("prob_over_25"))
            prob_btts = normalize_prob(p.get("prob_btts_yes"))
            xg_home = round(float(p.get("expected_home_goals") or 0), 2)
            xg_away = round(float(p.get("expected_away_goals") or 0), 2)
            conf_api = round(float(p.get("confidence") or 0) * 100, 0)

            country = (ev.get("league") or {}).get("country", "")
            region = classify_region(country)

            # Filtro de região
            if region_filter != "all" and region != region_filter:
                continue

            home_id = (ev.get("home_team_obj") or {}).get("id")
            away_id = (ev.get("away_team_obj") or {}).get("id")

            base = {
                "id": p.get("id"),
                "event_id": ev.get("id"),
                "home": home,
                "away": away,
                "home_id": home_id,
                "away_id": away_id,
                "prob_home": ph,
                "prob_draw": pd,
                "prob_away": pa,
                "prob_over_25": prob_over,
                "prob_btts": prob_btts,
                "expected_home_goals": xg_home,
                "expected_away_goals": xg_away,
                "most_likely_score": p.get("most_likely_score", "—"),
                "league": (ev.get("league") or {}).get("name", ""),
                "league_country": country,
                "region": region,
                "date": format_date(ev.get("event_date", "")),
                "raw_date": ev.get("event_date", ""),
            }

            # Análise / palpite automático
            confidence = confidence_score(ph, pd, pa, conf_api, prob_over, prob_btts, xg_home, xg_away)
            base["confidence_score"] = confidence["score"]
            base["confidence_tier"] = confidence["tier"]
            base["auto_bet"] = build_auto_bet(base)

            if confidence["score"] < min_score:
                continue

            games.append(base)

    # Ordena por score de confiança (mais alto primeiro)
    games.sort(key=lambda x: x["confidence_score"], reverse=True)
    if limit:
        games = games[:limit]
    return jsonify(games)


@app.route("/api/bolao-top")
def api_bolao_top():
    """
    Top 10 Apostas do Dia — os jogos mais confiáveis (qualquer liga, mundo todo).
    """
    from flask import request
    n = int(request.args.get("n", 10))

    data = api_get("predictions", params={"upcoming": "true", "tz": "America/Sao_Paulo"})
    games = []
    if data and "results" in data:
        for p in data["results"]:
            ev = p.get("event") or {}
            home = get_team_name(p, "home")
            away = get_team_name(p, "away")

            ph = normalize_prob(p.get("prob_home_win"))
            pd = normalize_prob(p.get("prob_draw"))
            pa = normalize_prob(p.get("prob_away_win"))
            prob_over = normalize_prob(p.get("prob_over_25"))
            prob_btts = normalize_prob(p.get("prob_btts_yes"))
            xg_home = round(float(p.get("expected_home_goals") or 0), 2)
            xg_away = round(float(p.get("expected_away_goals") or 0), 2)
            conf_api = round(float(p.get("confidence") or 0) * 100, 0)
            country = (ev.get("league") or {}).get("country", "")

            home_id = (ev.get("home_team_obj") or {}).get("id")
            away_id = (ev.get("away_team_obj") or {}).get("id")

            base = {
                "id": p.get("id"),
                "home": home,
                "away": away,
                "home_id": home_id,
                "away_id": away_id,
                "prob_home": ph,
                "prob_draw": pd,
                "prob_away": pa,
                "prob_over_25": prob_over,
                "prob_btts": prob_btts,
                "expected_home_goals": xg_home,
                "expected_away_goals": xg_away,
                "most_likely_score": p.get("most_likely_score", "—"),
                "league": (ev.get("league") or {}).get("name", ""),
                "league_country": country,
                "region": classify_region(country),
                "date": format_date(ev.get("event_date", "")),
            }
            confidence = confidence_score(ph, pd, pa, conf_api, prob_over, prob_btts, xg_home, xg_away)
            base["confidence_score"] = confidence["score"]
            base["confidence_tier"] = confidence["tier"]
            base["auto_bet"] = build_auto_bet(base)
            games.append(base)

    games.sort(key=lambda x: x["confidence_score"], reverse=True)
    return jsonify(games[:n])


@app.route("/api/odds")
def api_odds():
    """Retorna melhores odds disponíveis nos próximos dias."""
    data = api_get("odds/best", params={
        "market": "1x2",
        "days": "3"
    })
    results = []
    if data and "results" in data:
        for item in data["results"]:
            ev = item.get("event") or item
            home = ev.get("home_team", "")
            away = ev.get("away_team", "")
            if not home or not away:
                continue

            markets = item.get("markets") or {}
            m1x2 = markets.get("1x2") or {}
            home_data = m1x2.get(home) or {}
            away_data = m1x2.get(away) or {}
            draw_data = m1x2.get("Draw") or {}

            results.append({
                "home": home,
                "away": away,
                "league": item.get("league", ev.get("league", "")),
                "date": format_date(ev.get("event_date", item.get("event_date", ""))),
                "best_home_odds": home_data.get("best_odds", "—"),
                "best_home_bookie": home_data.get("best_bookmaker", "—"),
                "best_draw_odds": draw_data.get("best_odds", "—"),
                "best_draw_bookie": draw_data.get("best_bookmaker", "—"),
                "best_away_odds": away_data.get("best_odds", "—"),
                "best_away_bookie": away_data.get("best_bookmaker", "—"),
                "home_ai_prob": round(float(home_data.get("ai_probability") or 0) * 100, 1),
                "away_ai_prob": round(float(away_data.get("ai_probability") or 0) * 100, 1),
            })
    return jsonify(results)


# ─────────────────────────────────────────
#  TEMPLATE HTML
# ─────────────────────────────────────────

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AnalisaFutebol ⚽</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Oswald:wght@400;500;600;700&family=Barlow:wght@300;400;500;600&family=Barlow+Condensed:wght@500;600;700&display=swap" rel="stylesheet">
<style>
  /* ─── RESET & VARS ─── */
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg:        #0a0d10;
    --surface:   #111620;
    --card:      #161d2b;
    --card2:     #1a2235;
    --border:    #1e2a3d;
    --accent:    #00e676;
    --accent2:   #1de9b6;
    --gold:      #ffd600;
    --red:       #ff1744;
    --blue:      #2979ff;
    --muted:     #4a5568;
    --txt:       #e8edf5;
    --txt2:      #8899aa;
    --txt3:      #5a6880;
    --highlight: rgba(0,230,118,0.08);
    --glow:      0 0 24px rgba(0,230,118,0.15);
  }

  html { scroll-behavior: smooth; }
  body {
    background: var(--bg);
    color: var(--txt);
    font-family: 'Barlow', sans-serif;
    font-size: 14px;
    line-height: 1.5;
    min-height: 100vh;
  }

  /* ─── SCROLLBAR ─── */
  ::-webkit-scrollbar { width: 6px; background: var(--surface); }
  ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }

  /* ─── NAVBAR ─── */
  nav {
    position: fixed; top: 0; left: 0; right: 0; z-index: 100;
    background: rgba(10,13,16,0.95);
    backdrop-filter: blur(12px);
    border-bottom: 1px solid var(--border);
    display: flex; align-items: center; justify-content: space-between;
    padding: 0 24px; height: 60px;
  }
  .nav-brand {
    font-family: 'Oswald', sans-serif;
    font-size: 22px; font-weight: 700;
    color: var(--accent);
    letter-spacing: 1px;
    display: flex; align-items: center; gap: 8px;
  }
  .nav-brand span { color: var(--txt); }
  .nav-tabs { display: flex; gap: 4px; }
  .nav-tab {
    padding: 7px 18px;
    border-radius: 6px;
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 13px; font-weight: 600;
    letter-spacing: 0.5px; text-transform: uppercase;
    cursor: pointer; border: none;
    background: transparent; color: var(--txt2);
    transition: all 0.2s;
  }
  .nav-tab:hover { color: var(--txt); background: var(--surface); }
  .nav-tab.active {
    background: var(--accent);
    color: #000;
  }
  .live-dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--red);
    animation: blink 1.2s infinite;
    display: inline-block;
    margin-right: 4px;
  }
  @keyframes blink {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.2; }
  }
  .nav-status {
    font-size: 12px; color: var(--txt3);
    display: flex; align-items: center; gap: 6px;
  }

  /* ─── MAIN ─── */
  main {
    padding-top: 80px;
    max-width: 1200px;
    margin: 0 auto;
    padding-left: 16px; padding-right: 16px;
    padding-bottom: 60px;
  }

  /* ─── SECTIONS ─── */
  .section { display: none; }
  .section.active { display: block; }

  .section-header {
    display: flex; align-items: center; gap: 12px;
    margin-bottom: 24px; padding-bottom: 12px;
    border-bottom: 1px solid var(--border);
  }
  .section-header h2 {
    font-family: 'Oswald', sans-serif;
    font-size: 26px; font-weight: 600;
    color: var(--txt); letter-spacing: 0.5px;
  }
  .section-header .count-badge {
    background: var(--accent);
    color: #000;
    font-size: 11px; font-weight: 700;
    padding: 2px 8px; border-radius: 12px;
    font-family: 'Barlow Condensed', sans-serif;
  }
  .refresh-info {
    margin-left: auto;
    font-size: 12px; color: var(--txt3);
    display: flex; align-items: center; gap: 6px;
  }
  .refresh-btn {
    background: var(--surface); border: 1px solid var(--border);
    color: var(--txt2); padding: 5px 12px;
    border-radius: 5px; cursor: pointer;
    font-size: 12px; transition: all 0.2s;
  }
  .refresh-btn:hover { border-color: var(--accent); color: var(--accent); }

  /* ─── LOADING ─── */
  .loading-state {
    display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    padding: 80px 20px; gap: 16px;
    color: var(--txt3);
  }
  .spinner {
    width: 40px; height: 40px;
    border: 3px solid var(--border);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* ─── EMPTY STATE ─── */
  .empty-state {
    text-align: center; padding: 80px 20px;
    color: var(--txt3);
  }
  .empty-state .icon { font-size: 48px; margin-bottom: 12px; }
  .empty-state p { font-size: 16px; }

  /* ─── BOLÃO IA (FILTROS) ─── */
  .ia-filters {
    display: flex;
    flex-wrap: wrap;
    gap: 18px;
    margin-bottom: 22px;
    padding: 14px 18px;
    background: linear-gradient(135deg, var(--card) 0%, var(--card2) 100%);
    border: 1px solid var(--border);
    border-radius: 12px;
  }
  .filter-group {
    display: flex; align-items: center; flex-wrap: wrap; gap: 6px;
  }
  .filter-label {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 11px; font-weight: 700;
    letter-spacing: 1px;
    color: var(--txt3);
    margin-right: 4px;
  }
  .filter-chip {
    padding: 5px 12px;
    border-radius: 16px;
    background: var(--surface);
    color: var(--txt2);
    border: 1px solid var(--border);
    font-family: 'Barlow', sans-serif;
    font-size: 12px; font-weight: 500;
    cursor: pointer;
    transition: all 0.18s;
  }
  .filter-chip:hover {
    color: var(--txt);
    border-color: var(--accent2);
  }
  .filter-chip.active {
    background: var(--accent);
    color: #000;
    border-color: var(--accent);
    font-weight: 700;
  }

  /* ─── CARDS BOLÃO IA ─── */
  .ia-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(380px, 1fr));
    gap: 16px;
  }
  .ia-card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 14px;
    overflow: hidden;
    position: relative;
    transition: all 0.25s;
    animation: fadeIn 0.4s ease;
  }
  .ia-card:hover {
    transform: translateY(-3px);
    border-color: var(--accent2);
    box-shadow: 0 12px 36px rgba(0,0,0,0.5);
  }
  .ia-card.tier-elite { border-color: rgba(255,214,0,0.5); }
  .ia-card.tier-elite::before {
    content: '';
    position: absolute; top:0; left:0; right:0; height:3px;
    background: linear-gradient(90deg, var(--gold), var(--accent), var(--gold));
  }
  .ia-card.tier-high { border-color: rgba(0,230,118,0.4); }
  .ia-card.tier-high::before {
    content: '';
    position: absolute; top:0; left:0; right:0; height:3px;
    background: var(--accent);
  }

  .ia-card-header {
    padding: 10px 14px;
    background: var(--card2);
    display: flex; align-items: center; justify-content: space-between;
    border-bottom: 1px solid var(--border);
    gap: 8px;
  }
  .ia-meta {
    display: flex; flex-direction: column; gap: 2px; min-width: 0;
  }
  .ia-league {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 11px; font-weight: 600;
    letter-spacing: 0.5px;
    color: var(--txt2);
    text-transform: uppercase;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    max-width: 230px;
  }
  .ia-date { font-size: 11px; color: var(--txt3); }

  .confidence-badge {
    display: flex; flex-direction: column;
    align-items: flex-end; gap: 2px;
    min-width: 80px;
  }
  .conf-score {
    font-family: 'Oswald', sans-serif;
    font-size: 20px; font-weight: 700;
    line-height: 1;
  }
  .conf-tier {
    font-size: 10px; font-weight: 600;
    font-family: 'Barlow Condensed', sans-serif;
    letter-spacing: 0.5px;
    padding: 2px 8px; border-radius: 10px;
  }
  .tier-elite .conf-score { color: var(--gold); }
  .tier-elite .conf-tier { background: rgba(255,214,0,0.15); color: var(--gold); }
  .tier-high  .conf-score { color: var(--accent); }
  .tier-high  .conf-tier { background: rgba(0,230,118,0.15); color: var(--accent); }
  .tier-mid   .conf-score { color: var(--blue); }
  .tier-mid   .conf-tier { background: rgba(41,121,255,0.15); color: var(--blue); }
  .tier-low   .conf-score { color: var(--txt3); }
  .tier-low   .conf-tier { background: rgba(90,104,128,0.18); color: var(--txt3); }

  .ia-body { padding: 14px 16px; }

  .ia-matchup {
    display: grid;
    grid-template-columns: 1fr auto 1fr;
    gap: 10px; align-items: center;
    margin-bottom: 14px;
  }
  .ia-team {
    display: flex; flex-direction: column; align-items: center; gap: 6px;
    text-align: center;
  }
  .ia-team-name {
    font-family: 'Oswald', sans-serif;
    font-size: 14px; font-weight: 500;
    color: var(--txt);
    line-height: 1.2;
  }
  .ia-team-xg {
    font-size: 10px; color: var(--txt3);
    font-family: 'Barlow Condensed', sans-serif;
  }
  .ia-vs {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 13px; font-weight: 700;
    color: var(--txt3); letter-spacing: 1px;
  }

  /* ─── PROB BAR DENTRO IA ─── */
  .ia-prob-row {
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    gap: 4px; margin-bottom: 12px;
  }
  .prob-cell {
    text-align: center;
    padding: 8px 4px;
    background: var(--surface);
    border-radius: 8px;
    border: 1px solid var(--border);
  }
  .prob-cell.win {
    background: rgba(0,230,118,0.12);
    border-color: rgba(0,230,118,0.4);
  }
  .prob-cell-label {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 10px; font-weight: 600;
    color: var(--txt3); letter-spacing: 0.5px;
  }
  .prob-cell-val {
    font-family: 'Oswald', sans-serif;
    font-size: 17px; font-weight: 600;
    color: var(--txt);
  }
  .prob-cell.win .prob-cell-val { color: var(--accent); }

  /* ─── PALPITE AUTOMÁTICO ─── */
  .auto-bet {
    background: linear-gradient(135deg, rgba(0,230,118,0.06), rgba(29,233,182,0.04));
    border: 1px dashed rgba(0,230,118,0.3);
    border-radius: 10px;
    padding: 10px 12px;
    margin-top: 6px;
  }
  .auto-bet-title {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 11px; font-weight: 700;
    color: var(--accent);
    letter-spacing: 1px;
    margin-bottom: 8px;
    display: flex; align-items: center; gap: 6px;
  }
  .auto-bet-rows { display: flex; flex-direction: column; gap: 6px; }
  .auto-bet-row {
    display: flex; align-items: center; justify-content: space-between;
    font-size: 12px;
    padding: 4px 8px;
    background: rgba(0,0,0,0.25);
    border-radius: 6px;
  }
  .auto-bet-row .ab-label {
    color: var(--txt2);
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 11px; letter-spacing: 0.5px;
    text-transform: uppercase;
  }
  .auto-bet-row .ab-val {
    color: var(--txt);
    font-weight: 600;
    font-family: 'Oswald', sans-serif;
    font-size: 13px;
  }
  .auto-bet-row .ab-prob {
    font-size: 10px; color: var(--accent2);
    margin-left: 6px;
    font-family: 'Barlow Condensed', sans-serif;
  }

  .region-flag {
    display: inline-flex; align-items: center;
    padding: 1px 6px; border-radius: 8px;
    font-size: 10px;
    background: var(--surface);
    color: var(--txt3);
    margin-right: 6px;
    border: 1px solid var(--border);
  }
  .region-flag.br { color: var(--accent2); border-color: rgba(29,233,182,0.3); }
  .region-flag.europa { color: var(--blue); border-color: rgba(41,121,255,0.3); }
  .region-flag.sulamerica { color: var(--gold); border-color: rgba(255,214,0,0.3); }

  /* ─── TOP RANKING ─── */
  .top-list {
    display: flex; flex-direction: column;
    gap: 12px;
  }
  .top-row {
    display: grid;
    grid-template-columns: 56px 1fr 90px;
    gap: 14px;
    align-items: center;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 14px 16px;
    transition: all 0.2s;
    animation: fadeIn 0.4s ease;
  }
  .top-row:hover {
    border-color: var(--accent);
    transform: translateX(4px);
  }
  .top-rank {
    font-family: 'Oswald', sans-serif;
    font-size: 28px; font-weight: 700;
    color: var(--txt3);
    text-align: center;
  }
  .top-row:nth-child(1) .top-rank { color: var(--gold); }
  .top-row:nth-child(2) .top-rank { color: #c0c0c0; }
  .top-row:nth-child(3) .top-rank { color: #cd7f32; }
  .top-info { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
  .top-teams {
    font-family: 'Oswald', sans-serif;
    font-size: 15px; font-weight: 500;
    color: var(--txt);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .top-detail {
    display: flex; flex-wrap: wrap; gap: 8px;
    font-size: 11px; color: var(--txt2);
  }
  .top-pick-tag {
    background: rgba(0,230,118,0.15);
    color: var(--accent);
    padding: 2px 8px; border-radius: 8px;
    font-family: 'Barlow Condensed', sans-serif;
    font-weight: 700; letter-spacing: 0.5px;
  }
  .top-score {
    text-align: center;
    display: flex; flex-direction: column; gap: 3px; align-items: center;
  }
  .top-score-num {
    font-family: 'Oswald', sans-serif;
    font-size: 22px; font-weight: 700;
    color: var(--accent);
    line-height: 1;
  }
  .top-score-tier {
    font-size: 10px; font-family: 'Barlow Condensed', sans-serif;
    letter-spacing: 0.5px; color: var(--txt3);
  }

  /* ─── LIVE CARDS ─── */
  .games-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
    gap: 16px;
  }
  .live-card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px;
    overflow: hidden;
    transition: transform 0.2s, box-shadow 0.2s;
    animation: fadeIn 0.4s ease;
  }
  .live-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 8px 32px rgba(0,0,0,0.4);
  }
  @keyframes fadeIn {
    from { opacity: 0; transform: translateY(8px); }
    to { opacity: 1; transform: translateY(0); }
  }
  .card-header {
    background: var(--card2);
    padding: 8px 14px;
    display: flex; align-items: center; justify-content: space-between;
  }
  .card-league {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 11px; font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.5px;
    color: var(--txt3);
  }
  .live-badge {
    font-size: 10px; font-weight: 700;
    font-family: 'Barlow Condensed', sans-serif;
    letter-spacing: 0.5px;
    background: rgba(255,23,68,0.15);
    color: var(--red);
    padding: 2px 8px; border-radius: 10px;
    border: 1px solid rgba(255,23,68,0.3);
    display: flex; align-items: center; gap: 4px;
  }
  .card-body { padding: 16px; }
  .score-row {
    display: flex; align-items: center;
    justify-content: space-between;
    gap: 8px; margin-bottom: 14px;
  }
  .team-side {
    flex: 1; display: flex; flex-direction: column;
    align-items: flex-start; gap: 6px;
  }
  .team-side.away { align-items: flex-end; }
  .team-logo {
    width: 36px; height: 36px;
    object-fit: contain;
    filter: drop-shadow(0 2px 6px rgba(0,0,0,0.6));
  }
  .team-logo-placeholder {
    width: 36px; height: 36px;
    background: var(--surface);
    border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    font-size: 16px;
  }
  .team-name {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 15px; font-weight: 600;
    line-height: 1.2;
    color: var(--txt);
  }
  .score-center {
    display: flex; flex-direction: column;
    align-items: center; gap: 4px;
    min-width: 90px;
  }
  .score-display {
    font-family: 'Oswald', sans-serif;
    font-size: 40px; font-weight: 700;
    line-height: 1; color: var(--txt);
    letter-spacing: -1px;
  }
  .score-sep {
    color: var(--muted); margin: 0 4px;
    font-weight: 300;
  }
  .minute-tag {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 12px; font-weight: 600;
    color: var(--accent);
    background: rgba(0,230,118,0.1);
    padding: 2px 10px; border-radius: 10px;
  }
  .stat-row {
    display: grid; grid-template-columns: 1fr 80px 1fr;
    gap: 4px; margin-bottom: 6px;
    font-size: 12px;
  }
  .stat-row .val { font-weight: 600; color: var(--txt); }
  .stat-row .val.right { text-align: right; }
  .stat-row .label {
    text-align: center; color: var(--txt3);
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 11px; text-transform: uppercase;
  }
  .incidents-list {
    margin-top: 10px; padding-top: 10px;
    border-top: 1px solid var(--border);
    display: flex; flex-wrap: wrap; gap: 4px;
  }
  .incident-chip {
    font-size: 11px; color: var(--txt2);
    background: var(--surface);
    padding: 2px 8px; border-radius: 10px;
  }

  /* ─── PREDICTION CARDS ─── */
  .pred-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
    gap: 16px;
  }
  .pred-card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 12px; overflow: hidden;
    transition: transform 0.2s, box-shadow 0.2s;
    animation: fadeIn 0.4s ease;
  }
  .pred-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 8px 32px rgba(0,0,0,0.4);
  }
  .pred-card.highlight {
    border-color: var(--accent);
    box-shadow: var(--glow);
    background: var(--highlight);
  }
  .pred-card-header {
    background: var(--card2);
    padding: 8px 14px;
    display: flex; align-items: center; justify-content: space-between;
  }
  .pred-date {
    font-size: 11px; color: var(--txt3);
    font-family: 'Barlow Condensed', sans-serif;
  }
  .tip-badge {
    font-size: 11px; font-weight: 700;
    font-family: 'Barlow Condensed', sans-serif;
    padding: 2px 10px; border-radius: 10px;
  }
  .tip-home  { background: rgba(0,230,118,0.15); color: var(--accent); border: 1px solid rgba(0,230,118,0.3); }
  .tip-away  { background: rgba(41,121,255,0.15); color: #7ea9ff; border: 1px solid rgba(41,121,255,0.3); }
  .tip-fav   { background: rgba(255,214,0,0.12); color: var(--gold); border: 1px solid rgba(255,214,0,0.3); }
  .tip-draw  { background: rgba(255,255,255,0.06); color: var(--txt2); border: 1px solid var(--border); }

  .pred-body { padding: 14px; }
  .pred-matchup {
    display: flex; align-items: center;
    justify-content: space-between; gap: 8px;
    margin-bottom: 14px;
  }
  .pred-team {
    flex: 1; display: flex; flex-direction: column;
    align-items: flex-start; gap: 5px;
  }
  .pred-team.right-team { align-items: flex-end; }
  .pred-team-name {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 16px; font-weight: 600; color: var(--txt);
  }
  .pred-vs {
    font-family: 'Oswald', sans-serif;
    font-size: 14px; color: var(--txt3);
    white-space: nowrap;
  }
  .pred-xg {
    font-size: 11px; color: var(--txt3);
  }

  /* Barra de probabilidade */
  .prob-bar-wrap {
    margin-bottom: 12px;
  }
  .prob-labels {
    display: flex; justify-content: space-between;
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 12px; font-weight: 600;
    margin-bottom: 4px;
  }
  .prob-labels .ph { color: var(--accent); }
  .prob-labels .pd { color: var(--txt3); }
  .prob-labels .pa { color: #7ea9ff; }
  .prob-bar {
    height: 8px; border-radius: 4px;
    background: var(--surface);
    display: flex; overflow: hidden; gap: 1px;
  }
  .prob-segment {
    height: 100%; transition: width 0.6s ease;
    border-radius: 2px;
  }
  .seg-home { background: var(--accent); }
  .seg-draw { background: var(--muted); }
  .seg-away { background: #2979ff; }

  .pred-extras {
    display: flex; gap: 8px; flex-wrap: wrap;
    margin-top: 10px; padding-top: 10px;
    border-top: 1px solid var(--border);
  }
  .extra-chip {
    font-size: 11px; color: var(--txt2);
    background: var(--surface); padding: 3px 9px;
    border-radius: 8px;
    display: flex; align-items: center; gap: 4px;
  }
  .extra-chip.rec {
    background: rgba(0,230,118,0.1);
    color: var(--accent);
    border: 1px solid rgba(0,230,118,0.2);
  }
  .confidence-chip {
    margin-left: auto;
    font-size: 11px; color: var(--gold);
    display: flex; align-items: center; gap: 3px;
  }

  /* ─── ODDS ─── */
  .odds-grid {
    display: grid; gap: 12px;
  }
  .odds-card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 10px; overflow: hidden;
    animation: fadeIn 0.4s ease;
  }
  .odds-header {
    background: var(--card2); padding: 10px 16px;
    display: flex; align-items: center; justify-content: space-between;
  }
  .odds-matchup {
    font-family: 'Barlow Condensed', sans-serif;
    font-size: 15px; font-weight: 600; color: var(--txt);
  }
  .odds-body {
    padding: 12px 16px;
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 10px;
  }
  .odds-col { text-align: center; }
  .odds-col-label {
    font-size: 11px; color: var(--txt3);
    text-transform: uppercase;
    font-family: 'Barlow Condensed', sans-serif;
    margin-bottom: 4px;
  }
  .odds-value {
    font-family: 'Oswald', sans-serif;
    font-size: 26px; font-weight: 600;
    color: var(--gold);
  }
  .odds-bookie {
    font-size: 11px; color: var(--txt3); margin-top: 2px;
  }
  .odds-ai {
    font-size: 11px; color: var(--accent); margin-top: 2px;
  }

  /* ─── RESPONSIVE ─── */
  @media (max-width: 640px) {
    .nav-tabs { gap: 2px; }
    .nav-tab { padding: 6px 12px; font-size: 12px; }
    .nav-brand { font-size: 18px; }
    .games-grid, .pred-grid { grid-template-columns: 1fr; }
    .score-display { font-size: 32px; }
    .odds-body { grid-template-columns: 1fr; }
  }

  /* ─── FOOTER ─── */
  footer {
    position: fixed; bottom: 0; left: 0; right: 0;
    background: rgba(10,13,16,0.95);
    border-top: 1px solid var(--border);
    display: flex; align-items: center; justify-content: center;
    height: 36px;
    font-size: 11px; color: var(--txt3);
    gap: 8px;
  }

  /* ─── TOAST ─── */
  #toast {
    position: fixed; bottom: 50px; right: 20px;
    background: var(--surface); border: 1px solid var(--accent);
    color: var(--txt); padding: 10px 18px;
    border-radius: 8px; font-size: 13px;
    opacity: 0; transition: opacity 0.3s;
    pointer-events: none; z-index: 200;
  }
  #toast.show { opacity: 1; }

  /* ─── PROGRESS BAR ─── */
  #progress {
    position: fixed; top: 60px; left: 0;
    height: 2px; background: var(--accent);
    width: 0; transition: width 25s linear;
    z-index: 99;
  }
</style>
</head>
<body>

<nav>
  <div class="nav-brand">⚽ <span>Analisa</span>Futebol</div>
  <div class="nav-tabs">
    <button class="nav-tab active" onclick="showTab('live')">
      <span class="live-dot"></span>Ao Vivo
    </button>
    <button class="nav-tab" onclick="showTab('bolaoIA')">🤖 Bolão IA</button>
    <button class="nav-tab" onclick="showTab('top')">🏆 Top Apostas</button>
    <button class="nav-tab" onclick="showTab('bolao')">Bolão Futuro</button>
    <button class="nav-tab" onclick="showTab('odds')">Melhores Odds</button>
  </div>
  <div class="nav-status" id="navStatus">
    <span id="lastUpdate">—</span>
  </div>
</nav>

<div id="progress"></div>

<main>
  <!-- AO VIVO -->
  <section id="sec-live" class="section active">
    <div class="section-header">
      <h2>⚡ Ao Vivo</h2>
      <span class="count-badge" id="liveCount">0</span>
      <div class="refresh-info">
        <span id="nextRefresh"></span>
        <button class="refresh-btn" onclick="loadLive()">↻ Atualizar</button>
      </div>
    </div>
    <div id="liveContent">
      <div class="loading-state"><div class="spinner"></div><p>Carregando jogos ao vivo…</p></div>
    </div>
  </section>

  <!-- BOLÃO IA (NOVO - INTELIGENTE UNIFICADO) -->
  <section id="sec-bolaoIA" class="section">
    <div class="section-header">
      <h2>🤖 Bolão Inteligente</h2>
      <span class="count-badge" id="bolaoIACount">0</span>
      <div class="refresh-info">
        <button class="refresh-btn" onclick="loadBolaoIA()">↻ Atualizar</button>
      </div>
    </div>

    <!-- FILTROS -->
    <div class="ia-filters">
      <div class="filter-group">
        <span class="filter-label">🌍 REGIÃO</span>
        <button class="filter-chip active" data-region="all" onclick="setRegion('all')">Todas</button>
        <button class="filter-chip" data-region="br" onclick="setRegion('br')">🇧🇷 Brasil</button>
        <button class="filter-chip" data-region="sulamerica" onclick="setRegion('sulamerica')">🌎 Sul-América</button>
        <button class="filter-chip" data-region="europa" onclick="setRegion('europa')">🇪🇺 Europa</button>
        <button class="filter-chip" data-region="outros" onclick="setRegion('outros')">🌏 Outros</button>
      </div>
      <div class="filter-group">
        <span class="filter-label">🎯 CONFIANÇA MÍN.</span>
        <button class="filter-chip active" data-score="0" onclick="setMinScore(0)">Tudo</button>
        <button class="filter-chip" data-score="45" onclick="setMinScore(45)">≥ 45</button>
        <button class="filter-chip" data-score="60" onclick="setMinScore(60)">≥ 60 ✅</button>
        <button class="filter-chip" data-score="75" onclick="setMinScore(75)">≥ 75 🔥</button>
      </div>
    </div>

    <div id="bolaoIAContent">
      <div class="loading-state"><div class="spinner"></div><p>Calculando palpites inteligentes…</p></div>
    </div>
  </section>

  <!-- TOP APOSTAS DO DIA -->
  <section id="sec-top" class="section">
    <div class="section-header">
      <h2>🏆 Top 10 Apostas do Dia</h2>
      <span class="count-badge" id="topCount">0</span>
      <div class="refresh-info">
        <button class="refresh-btn" onclick="loadTop()">↻ Atualizar</button>
      </div>
    </div>
    <p style="color:var(--txt2);font-size:13px;margin-bottom:18px">
      Os 10 jogos com maior score de confiança da IA, considerando todas as ligas — nacionais e internacionais.
    </p>
    <div id="topContent">
      <div class="loading-state"><div class="spinner"></div><p>Selecionando os melhores palpites…</p></div>
    </div>
  </section>

  <!-- BOLÃO FUTURO -->
  <section id="sec-bolao" class="section">
    <div class="section-header">
      <h2>🔮 Bolão Futuro</h2>
      <span class="count-badge" id="bolaoCount">0</span>
      <div class="refresh-info">
        <button class="refresh-btn" onclick="loadBolao()">↻ Atualizar</button>
      </div>
    </div>
    <div id="bolaoContent">
      <div class="loading-state"><div class="spinner"></div><p>Carregando previsões…</p></div>
    </div>
  </section>

  <!-- ODDS -->
  <section id="sec-odds" class="section">
    <div class="section-header">
      <h2>💰 Melhores Odds</h2>
      <span class="count-badge" id="oddsCount">0</span>
      <div class="refresh-info">
        <button class="refresh-btn" onclick="loadOdds()">↻ Atualizar</button>
      </div>
    </div>
    <div id="oddsContent">
      <div class="loading-state"><div class="spinner"></div><p>Carregando odds…</p></div>
    </div>
  </section>
</main>

<div id="toast"></div>

<footer>
  <span>⚽ AnalisaFutebol</span>
  <span>•</span>
  <span>Dados via sports.bzzoiro.com</span>
  <span>•</span>
  <span id="footerTime"></span>
</footer>

<script>
// ─── ESTADO ───
let currentTab = 'live';
let liveInterval = null;
let countdownInterval = null;
let secondsToRefresh = 25;
const LIVE_INTERVAL = 25;

// ─── UTILS ───
function logoUrl(id) {
  if (!id) return null;
  return `https://sports.bzzoiro.com/img/team/${id}/`;
}
function logoImg(id, name, size=36) {
  if (!id) return `<div class="team-logo-placeholder">⚽</div>`;
  return `<img class="team-logo" src="${logoUrl(id)}" alt="${name}"
    style="width:${size}px;height:${size}px"
    onerror="this.style.display='none';this.nextSibling.style.display='flex'">
    <div class="team-logo-placeholder" style="display:none">⚽</div>`;
}
function fmtTime() {
  return new Date().toLocaleTimeString('pt-BR', {hour:'2-digit',minute:'2-digit',second:'2-digit'});
}
function toast(msg) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 2800);
}
function setCount(id, n) { document.getElementById(id).textContent = n; }

// ─── CLOCK ───
setInterval(() => {
  document.getElementById('footerTime').textContent = fmtTime();
}, 1000);

// ─── TAB ───
function showTab(tab) {
  currentTab = tab;
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.nav-tab').forEach(b => b.classList.remove('active'));
  document.getElementById('sec-' + tab).classList.add('active');
  // Ordem das tabs: live, bolaoIA, top, bolao, odds
  const idx = {live:0, bolaoIA:1, top:2, bolao:3, odds:4}[tab];
  document.querySelectorAll('.nav-tab')[idx].classList.add('active');

  // Lazy load
  if (tab === 'live' && document.getElementById('liveContent').querySelector('.loading-state')) loadLive();
  if (tab === 'bolaoIA' && document.getElementById('bolaoIAContent').querySelector('.loading-state')) loadBolaoIA();
  if (tab === 'top' && document.getElementById('topContent').querySelector('.loading-state')) loadTop();
  if (tab === 'bolao' && document.getElementById('bolaoContent').querySelector('.loading-state')) loadBolao();
  if (tab === 'odds' && document.getElementById('oddsContent').querySelector('.loading-state')) loadOdds();
}

// ─── PROGRESS BAR ───
function startProgress() {
  const bar = document.getElementById('progress');
  bar.style.transition = 'none';
  bar.style.width = '0';
  requestAnimationFrame(() => {
    bar.style.transition = `width ${LIVE_INTERVAL}s linear`;
    bar.style.width = '100%';
  });
}
function resetProgress() {
  const bar = document.getElementById('progress');
  bar.style.transition = 'none';
  bar.style.width = '0';
}

// ─── COUNTDOWN ───
function startCountdown() {
  clearInterval(countdownInterval);
  secondsToRefresh = LIVE_INTERVAL;
  countdownInterval = setInterval(() => {
    secondsToRefresh--;
    document.getElementById('nextRefresh').textContent =
      `Atualiza em ${secondsToRefresh}s`;
    if (secondsToRefresh <= 0) secondsToRefresh = LIVE_INTERVAL;
  }, 1000);
}

// ─────────────────────────────────
//  LIVE
// ─────────────────────────────────
async function loadLive() {
  try {
    resetProgress();
    const res = await fetch('/api/live');
    const games = await res.json();
    const el = document.getElementById('liveContent');
    setCount('liveCount', games.length);
    document.getElementById('lastUpdate').textContent = fmtTime();

    if (games.length === 0) {
      el.innerHTML = `<div class="empty-state">
        <div class="icon">📺</div>
        <p>Nenhum jogo ao vivo no momento.</p>
        <p style="font-size:12px;margin-top:8px;color:var(--txt3)">Atualiza automaticamente a cada ${LIVE_INTERVAL}s</p>
      </div>`;
    } else {
      el.innerHTML = `<div class="games-grid">${games.map(renderLiveCard).join('')}</div>`;
    }
    startProgress();
    startCountdown();
  } catch(e) {
    document.getElementById('liveContent').innerHTML =
      `<div class="empty-state"><div class="icon">⚠️</div><p>Erro ao carregar jogos ao vivo.</p></div>`;
  }
}

function renderLiveCard(g) {
  const statusMap = {
    '1st_half':'1T', '2nd_half':'2T',
    'halftime':'Intervalo', 'inprogress':'Em jogo',
    'finished':'Encerrado', 'notstarted':'Não iniciado'
  };
  const statusLabel = statusMap[g.status] || g.status;
  const minuteLabel = g.minute ? `${g.minute}'` : statusLabel;

  const incHTML = g.incidents.slice(-5).map(i =>
    `<span class="incident-chip">${i.icon} ${i.minute}' ${i.player ? i.player.split(' ').pop() : ''}</span>`
  ).join('');

  return `
  <div class="live-card">
    <div class="card-header">
      <span class="card-league">${g.league_country ? g.league_country+' · ' : ''}${g.league}</span>
      <span class="live-badge"><span class="live-dot"></span>${minuteLabel}</span>
    </div>
    <div class="card-body">
      <div class="score-row">
        <div class="team-side">
          ${logoImg(g.home_id, g.home)}
          <div class="team-name">${g.home}</div>
        </div>
        <div class="score-center">
          <div class="score-display">
            <span style="color:var(--accent)">${g.home_score}</span>
            <span class="score-sep">:</span>
            <span style="color:#7ea9ff">${g.away_score}</span>
          </div>
          <div class="minute-tag">${minuteLabel}</div>
        </div>
        <div class="team-side away">
          ${logoImg(g.away_id, g.away)}
          <div class="team-name">${g.away}</div>
        </div>
      </div>
      ${g.home_possession !== '—' ? `
      <div class="stat-row">
        <span class="val">${g.home_possession}%</span>
        <span class="label">Posse</span>
        <span class="val right">${g.away_possession}%</span>
      </div>
      <div class="stat-row">
        <span class="val">${g.home_shots}</span>
        <span class="label">Finalizações</span>
        <span class="val right">${g.away_shots}</span>
      </div>
      <div class="stat-row">
        <span class="val">${g.home_shots_ot}</span>
        <span class="label">No alvo</span>
        <span class="val right">${g.away_shots_ot}</span>
      </div>` : ''}
      ${g.incidents.length > 0 ? `<div class="incidents-list">${incHTML}</div>` : ''}
    </div>
  </div>`;
}

// ─────────────────────────────────
//  BOLÃO IA (NOVO - INTELIGENTE)
// ─────────────────────────────────
let iaState = { region: 'all', minScore: 0 };

function setRegion(r) {
  iaState.region = r;
  document.querySelectorAll('[data-region]').forEach(b => b.classList.remove('active'));
  document.querySelector(`[data-region="${r}"]`).classList.add('active');
  loadBolaoIA();
}
function setMinScore(s) {
  iaState.minScore = s;
  document.querySelectorAll('[data-score]').forEach(b => b.classList.remove('active'));
  document.querySelector(`[data-score="${s}"]`).classList.add('active');
  loadBolaoIA();
}

async function loadBolaoIA() {
  try {
    const url = `/api/bolao-inteligente?region=${iaState.region}&min_score=${iaState.minScore}`;
    const res = await fetch(url);
    const games = await res.json();
    const el = document.getElementById('bolaoIAContent');
    setCount('bolaoIACount', games.length);

    if (games.length === 0) {
      el.innerHTML = `<div class="empty-state">
        <div class="icon">🤖</div>
        <p>Nenhum jogo encontrado com os filtros atuais.</p>
        <p style="font-size:12px;margin-top:8px;color:var(--txt3)">Tente reduzir o score mínimo ou trocar de região.</p>
      </div>`;
    } else {
      el.innerHTML = `<div class="ia-grid">${games.map(renderIACard).join('')}</div>`;
    }
  } catch(e) {
    console.error(e);
    document.getElementById('bolaoIAContent').innerHTML =
      `<div class="empty-state"><div class="icon">⚠️</div><p>Erro ao carregar bolão inteligente.</p></div>`;
  }
}

function regionFlag(region, country) {
  const map = {
    'br': {emoji:'🇧🇷', label:'Brasil', cls:'br'},
    'sulamerica': {emoji:'🌎', label:'Sul-América', cls:'sulamerica'},
    'europa': {emoji:'🇪🇺', label:'Europa', cls:'europa'},
    'outros': {emoji:'🌏', label:country || 'Internacional', cls:'outros'}
  };
  const r = map[region] || map['outros'];
  return `<span class="region-flag ${r.cls}">${r.emoji} ${r.label}</span>`;
}

function renderIACard(g) {
  const tierCls = g.confidence_tier.cls;
  const ab = g.auto_bet;

  // Identifica qual probabilidade é a vencedora para destacar
  const probMax = Math.max(g.prob_home, g.prob_draw, g.prob_away);
  const cellClass = (val) => val === probMax ? 'prob-cell win' : 'prob-cell';

  return `
  <div class="ia-card ${tierCls}">
    <div class="ia-card-header">
      <div class="ia-meta">
        <div class="ia-league">${regionFlag(g.region, g.league_country)}${g.league}</div>
        <div class="ia-date">📅 ${g.date}</div>
      </div>
      <div class="confidence-badge">
        <div class="conf-score">${g.confidence_score}</div>
        <div class="conf-tier">${g.confidence_tier.label}</div>
      </div>
    </div>

    <div class="ia-body">
      <div class="ia-matchup">
        <div class="ia-team">
          ${logoImg(g.home_id, g.home, 40)}
          <div class="ia-team-name">${g.home}</div>
          <div class="ia-team-xg">xG ${g.expected_home_goals}</div>
        </div>
        <div class="ia-vs">VS</div>
        <div class="ia-team">
          ${logoImg(g.away_id, g.away, 40)}
          <div class="ia-team-name">${g.away}</div>
          <div class="ia-team-xg">xG ${g.expected_away_goals}</div>
        </div>
      </div>

      <div class="ia-prob-row">
        <div class="${cellClass(g.prob_home)}">
          <div class="prob-cell-label">CASA</div>
          <div class="prob-cell-val">${g.prob_home}%</div>
        </div>
        <div class="${cellClass(g.prob_draw)}">
          <div class="prob-cell-label">EMPATE</div>
          <div class="prob-cell-val">${g.prob_draw}%</div>
        </div>
        <div class="${cellClass(g.prob_away)}">
          <div class="prob-cell-label">FORA</div>
          <div class="prob-cell-val">${g.prob_away}%</div>
        </div>
      </div>

      <div class="auto-bet">
        <div class="auto-bet-title">🤖 PALPITE AUTOMÁTICO DA IA</div>
        <div class="auto-bet-rows">
          <div class="auto-bet-row">
            <span class="ab-label">Resultado</span>
            <span class="ab-val">${ab.pick.pick_label} <span class="ab-prob">${ab.pick.pick_prob}%</span></span>
          </div>
          <div class="auto-bet-row">
            <span class="ab-label">Placar Provável</span>
            <span class="ab-val">${ab.score}</span>
          </div>
          <div class="auto-bet-row">
            <span class="ab-label">Total de Gols</span>
            <span class="ab-val">${ab.ou.label} <span class="ab-prob">${ab.ou.prob}%</span></span>
          </div>
          <div class="auto-bet-row">
            <span class="ab-label">Ambos Marcam</span>
            <span class="ab-val">${ab.btts.label} <span class="ab-prob">${ab.btts.prob}%</span></span>
          </div>
        </div>
      </div>
    </div>
  </div>`;
}

// ─────────────────────────────────
//  TOP 10 APOSTAS DO DIA
// ─────────────────────────────────
async function loadTop() {
  try {
    const res = await fetch('/api/bolao-top?n=10');
    const games = await res.json();
    const el = document.getElementById('topContent');
    setCount('topCount', games.length);

    if (games.length === 0) {
      el.innerHTML = `<div class="empty-state">
        <div class="icon">🏆</div>
        <p>Nenhum jogo disponível no momento.</p>
      </div>`;
    } else {
      el.innerHTML = `<div class="top-list">${games.map(renderTopRow).join('')}</div>`;
    }
  } catch(e) {
    document.getElementById('topContent').innerHTML =
      `<div class="empty-state"><div class="icon">⚠️</div><p>Erro ao carregar Top apostas.</p></div>`;
  }
}

function renderTopRow(g, idx) {
  const rank = idx !== undefined ? idx + 1 : '?';
  const ab = g.auto_bet;
  return `
  <div class="top-row">
    <div class="top-rank">#${rank}</div>
    <div class="top-info">
      <div class="top-teams">${g.home} vs ${g.away}</div>
      <div class="top-detail">
        ${regionFlag(g.region, g.league_country)}
        <span>📅 ${g.date}</span>
        <span class="top-pick-tag">${ab.pick.pick_label} (${ab.pick.pick_prob}%)</span>
        <span>🎯 ${ab.score}</span>
        <span>${ab.ou.label}</span>
        <span>${ab.btts.label}</span>
      </div>
    </div>
    <div class="top-score">
      <div class="top-score-num">${g.confidence_score}</div>
      <div class="top-score-tier">${g.confidence_tier.label}</div>
    </div>
  </div>`;
}

// ─────────────────────────────────
//  BOLÃO
// ─────────────────────────────────
async function loadBolao() {
  try {
    const res = await fetch('/api/predictions');
    const preds = await res.json();
    const el = document.getElementById('bolaoContent');
    setCount('bolaoCount', preds.length);

    if (preds.length === 0) {
      el.innerHTML = `<div class="empty-state">
        <div class="icon">🔮</div>
        <p>Nenhuma previsão disponível no momento.</p>
      </div>`;
    } else {
      el.innerHTML = `<div class="pred-grid">${preds.map(renderPredCard).join('')}</div>`;
    }
  } catch(e) {
    document.getElementById('bolaoContent').innerHTML =
      `<div class="empty-state"><div class="icon">⚠️</div><p>Erro ao carregar previsões.</p></div>`;
  }
}

function renderPredCard(p) {
  const hl = p.highlight ? ' highlight' : '';
  const tipHTML = `<span class="tip-badge ${p.tip.cls}">${p.tip.label}</span>`;

  // Barra de probabilidade proporcional
  const total = p.prob_home + p.prob_draw + p.prob_away || 100;
  const wh = (p.prob_home/total*100).toFixed(1);
  const wd = (p.prob_draw/total*100).toFixed(1);
  const wa = (p.prob_away/total*100).toFixed(1);

  const extras = [];
  if (p.over_25_recommend)
    extras.push(`<span class="extra-chip rec">✅ Over 2.5 (${p.prob_over_25}%)</span>`);
  else
    extras.push(`<span class="extra-chip">📈 Over 2.5: ${p.prob_over_25}%</span>`);

  if (p.btts_recommend)
    extras.push(`<span class="extra-chip rec">✅ Ambos Marcam (${p.prob_btts}%)</span>`);
  else
    extras.push(`<span class="extra-chip">⚽ Ambos Marcam: ${p.prob_btts}%</span>`);

  extras.push(`<span class="extra-chip">🎯 Placar: ${p.most_likely_score}</span>`);
  extras.push(`<span class="confidence-chip">⭐ ${p.confidence}%</span>`);

  const hlBadge = p.highlight
    ? `<span style="font-size:10px;color:var(--accent);font-weight:700;font-family:'Barlow Condensed',sans-serif;margin-left:6px">★ DESTAQUE</span>`
    : '';

  return `
  <div class="pred-card${hl}">
    <div class="pred-card-header">
      <span class="pred-date">📅 ${p.date} &nbsp;·&nbsp; ${p.league}${hlBadge}</span>
      ${tipHTML}
    </div>
    <div class="pred-body">
      <div class="pred-matchup">
        <div class="pred-team">
          ${logoImg(p.home_id, p.home, 32)}
          <div class="pred-team-name">${p.home}</div>
          <div class="pred-xg">xG: ${p.expected_home_goals}</div>
        </div>
        <div class="pred-vs">VS</div>
        <div class="pred-team right-team">
          ${logoImg(p.away_id, p.away, 32)}
          <div class="pred-team-name">${p.away}</div>
          <div class="pred-xg">xG: ${p.expected_away_goals}</div>
        </div>
      </div>

      <div class="prob-bar-wrap">
        <div class="prob-labels">
          <span class="ph">Casa ${p.prob_home}%</span>
          <span class="pd">Empate ${p.prob_draw}%</span>
          <span class="pa">Fora ${p.prob_away}%</span>
        </div>
        <div class="prob-bar">
          <div class="prob-segment seg-home" style="width:${wh}%"></div>
          <div class="prob-segment seg-draw" style="width:${wd}%"></div>
          <div class="prob-segment seg-away" style="width:${wa}%"></div>
        </div>
      </div>

      <div class="pred-extras">${extras.join('')}</div>
    </div>
  </div>`;
}

// ─────────────────────────────────
//  ODDS
// ─────────────────────────────────
async function loadOdds() {
  try {
    const res = await fetch('/api/odds');
    const odds = await res.json();
    const el = document.getElementById('oddsContent');
    setCount('oddsCount', odds.length);

    if (odds.length === 0) {
      el.innerHTML = `<div class="empty-state">
        <div class="icon">💰</div>
        <p>Nenhuma odd disponível no momento.</p>
      </div>`;
    } else {
      el.innerHTML = `<div class="odds-grid">${odds.map(renderOddsCard).join('')}</div>`;
    }
  } catch(e) {
    document.getElementById('oddsContent').innerHTML =
      `<div class="empty-state"><div class="icon">⚠️</div><p>Erro ao carregar odds.</p></div>`;
  }
}

function renderOddsCard(o) {
  const fmtOdd = v => (v && v !== '—') ? parseFloat(v).toFixed(2) : '—';
  return `
  <div class="odds-card">
    <div class="odds-header">
      <div>
        <span class="odds-matchup">${o.home} <span style="color:var(--txt3)">vs</span> ${o.away}</span>
      </div>
      <div style="font-size:11px;color:var(--txt3)">${o.league} &nbsp;·&nbsp; ${o.date}</div>
    </div>
    <div class="odds-body">
      <div class="odds-col">
        <div class="odds-col-label">🏠 Casa</div>
        <div class="odds-value">${fmtOdd(o.best_home_odds)}</div>
        <div class="odds-bookie">${o.best_home_bookie}</div>
        ${o.home_ai_prob ? `<div class="odds-ai">IA: ${o.home_ai_prob}%</div>` : ''}
      </div>
      <div class="odds-col">
        <div class="odds-col-label">🤝 Empate</div>
        <div class="odds-value">${fmtOdd(o.best_draw_odds)}</div>
        <div class="odds-bookie">${o.best_draw_bookie}</div>
      </div>
      <div class="odds-col">
        <div class="odds-col-label">✈️ Fora</div>
        <div class="odds-value">${fmtOdd(o.best_away_odds)}</div>
        <div class="odds-bookie">${o.best_away_bookie}</div>
        ${o.away_ai_prob ? `<div class="odds-ai">IA: ${o.away_ai_prob}%</div>` : ''}
      </div>
    </div>
  </div>`;
}

// ─────────────────────────────────
//  AUTO-REFRESH (25s)
// ─────────────────────────────────
function startAutoRefresh() {
  clearInterval(liveInterval);
  liveInterval = setInterval(() => {
    if (currentTab === 'live') {
      loadLive();
    }
  }, LIVE_INTERVAL * 1000);
}

// ─── INICIALIZAÇÃO ───
loadLive();
startAutoRefresh();
document.getElementById('footerTime').textContent = fmtTime();
</script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)


# ─────────────────────────────────────────
#  PONTO DE ENTRADA
# ─────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    print(f"""
╔══════════════════════════════════════════╗
║        AnalisaFutebol v1.0 ⚽            ║
║  http://localhost:{port:<5}                   ║
╚══════════════════════════════════════════╝
    """)
    app.run(host="0.0.0.0", port=port, debug=debug)