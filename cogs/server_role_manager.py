"""
PlayNESTI LAN Party Cog
=======================
Cog all-in-one para gestão da LAN Party PlayNESTI.

Funcionalidades:
  - Importar CSV com equipas, chefes, participantes e @discord
  - Dashboard web (Flask) integrado para ver quem está/não está no servidor
  - Criar cargos por equipa automaticamente ("Nome da equipa - Jogo")
  - Atribuir cargos de "Chefe de equipa (Jogo)" aos líderes
  - Atribuir cargos de equipa a todos os participantes

Requisitos:
  pip install discord.py flask flask-cors aiohttp

Uso:
  Adiciona o cog ao teu bot:
    bot.load_extension("playnesti_cog")   # ou
    await bot.add_cog(PlayNESTI(bot))

Comandos (prefix padrão !):
  !playnesti carregar <ficheiro.csv>   — carrega/actualiza o CSV
  !playnesti status                    — mostra resumo no Discord
  !playnesti criar_cargos              — cria cargos e atribui aos membros
  !playnesti dashboard                 — mostra URL do dashboard web
  !playnesti limpar                    — remove todos os cargos criados pelo bot

Formato esperado do CSV (cabeçalho flexível, ver COLUMN_ALIASES abaixo):
  equipa,jogo,chefe,participantes,discord_handles
  "Team Alpha","League of Legends","João Silva","João Silva;Maria Costa;Pedro Ramos","joao#1234;maria#5678;pedro#9012"

  OU com colunas separadas por participante (uma linha por participante):
  equipa,jogo,nome,discord,chefe
  "Team Alpha","LoL","João Silva","joao#1234","sim"
"""

import asyncio
import csv
import io
import json
import logging
import os
import threading
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands, tasks
from discord import app_commands

# ── Flask (dashboard web) ──────────────────────────────────────────────────────
try:
    from flask import Flask, jsonify, render_template_string, request
    from flask_cors import CORS
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False
    logging.warning("Flask não instalado. Dashboard desactivado. Instala com: pip install flask flask-cors")

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURAÇÃO
# ══════════════════════════════════════════════════════════════════════════════

DASHBOARD_HOST = "0.0.0.0"
DASHBOARD_PORT = 5050
DATA_FILE = Path("playnesti_data.json")   # persistência local

# Aliases de colunas aceites no CSV (case-insensitive)
COLUMN_ALIASES = {
    "equipa":        ["equipa", "team", "nome_equipa", "team_name", "grupo"],
    "jogo":          ["jogo", "game", "modalidade", "categoria"],
    "chefe":         ["chefe", "chefe_equipa", "captain", "lider", "líder", "team_lead"],
    "participante":  ["participante", "nome", "player", "jogador", "membro"],
    "discord":       ["discord", "discord_handle", "discord_tag", "username", "@discord",
                      "discord_username", "discord_user"],
    "is_chefe":      ["is_chefe", "chefe?", "captain?", "e_chefe", "é_chefe"],
}

# Cores dos cargos (cicla por equipa)
ROLE_COLORS = [
    discord.Color.blue(),
    discord.Color.green(),
    discord.Color.red(),
    discord.Color.gold(),
    discord.Color.purple(),
    discord.Color.orange(),
    discord.Color.teal(),
    discord.Color.magenta(),
]

log = logging.getLogger("playnesti")

# ══════════════════════════════════════════════════════════════════════════════
# MODELOS DE DADOS
# ══════════════════════════════════════════════════════════════════════════════

class Participant:
    def __init__(self, nome: str, discord_handle: str, is_chefe: bool = False):
        self.nome = nome.strip()
        self.discord_handle = discord_handle.strip().lstrip("@")
        self.is_chefe = is_chefe

    def to_dict(self):
        return {"nome": self.nome, "discord": self.discord_handle, "is_chefe": self.is_chefe}

    @classmethod
    def from_dict(cls, d):
        return cls(d["nome"], d["discord"], d.get("is_chefe", False))


class Equipa:
    def __init__(self, nome: str, jogo: str):
        self.nome = nome.strip()
        self.jogo = jogo.strip()
        self.participantes: list[Participant] = []

    @property
    def role_name(self):
        return f"{self.nome} - {self.jogo}"

    @property
    def chefe_role_name(self):
        return f"Chefe de equipa ({self.jogo})"

    @property
    def chefes(self):
        return [p for p in self.participantes if p.is_chefe]

    def to_dict(self):
        return {
            "nome": self.nome,
            "jogo": self.jogo,
            "participantes": [p.to_dict() for p in self.participantes],
        }

    @classmethod
    def from_dict(cls, d):
        e = cls(d["nome"], d["jogo"])
        e.participantes = [Participant.from_dict(p) for p in d.get("participantes", [])]
        return e


# ══════════════════════════════════════════════════════════════════════════════
# PARSER DE CSV
# ══════════════════════════════════════════════════════════════════════════════

def _resolve_col(headers: list[str], field: str) -> Optional[str]:
    """Devolve o nome real da coluna ou None."""
    aliases = COLUMN_ALIASES.get(field, [field])
    for h in headers:
        if h.lower().strip() in aliases:
            return h
    return None


def parse_csv(content: str) -> list[Equipa]:
    """
    Suporta dois formatos:
      A) Uma linha por participante  → colunas: equipa, jogo, participante/nome, discord, [is_chefe / chefe]
      B) Uma linha por equipa        → colunas: equipa, jogo, chefe, participantes (;-sep), discord_handles (;-sep)
    """
    reader = csv.DictReader(io.StringIO(content))
    headers = reader.fieldnames or []

    c_equipa       = _resolve_col(headers, "equipa")
    c_jogo         = _resolve_col(headers, "jogo")
    c_participante = _resolve_col(headers, "participante")
    c_discord      = _resolve_col(headers, "discord")
    c_chefe        = _resolve_col(headers, "chefe")
    c_is_chefe     = _resolve_col(headers, "is_chefe")

    if not c_equipa or not c_jogo:
        raise ValueError("CSV deve ter colunas 'equipa' e 'jogo' (ou equivalentes).")

    equipas: dict[str, Equipa] = {}

    for row in reader:
        equipa_nome = row.get(c_equipa, "").strip()
        jogo        = row.get(c_jogo, "").strip()
        if not equipa_nome or not jogo:
            continue

        key = f"{equipa_nome}|{jogo}"
        if key not in equipas:
            equipas[key] = Equipa(equipa_nome, jogo)
        eq = equipas[key]

        # Formato B: colunas de listas separadas por ";"
        if c_chefe and not c_participante:
            chefe_nome   = row.get(c_chefe, "").strip()
            # participantes e discords podem ser ; separados
            outros_nomes  = [n.strip() for n in row.get("participantes", "").split(";") if n.strip()]
            outros_disc   = [d.strip() for d in row.get("discord_handles", "").split(";") if d.strip()]
            # chefe é o primeiro
            chefe_discord = outros_disc[0] if outros_disc else ""
            eq.participantes.append(Participant(chefe_nome, chefe_discord, is_chefe=True))
            for i, nome in enumerate(outros_nomes):
                disc = outros_disc[i + 1] if i + 1 < len(outros_disc) else ""
                eq.participantes.append(Participant(nome, disc, is_chefe=False))

        # Formato A: uma linha por participante
        else:
            nome    = row.get(c_participante or c_chefe or "", "").strip()
            discord = row.get(c_discord or "", "").strip()
            is_ch   = False
            if c_is_chefe:
                val = row.get(c_is_chefe, "").strip().lower()
                is_ch = val in ("sim", "yes", "1", "true", "s", "y")
            elif c_chefe:
                # se a coluna chefe tem o nome desta pessoa
                is_ch = row.get(c_chefe, "").strip().lower() == nome.lower()
            if nome:
                eq.participantes.append(Participant(nome, discord, is_chefe=is_ch))

    return list(equipas.values())


# ══════════════════════════════════════════════════════════════════════════════
# DASHBOARD WEB (HTML inline)
# ══════════════════════════════════════════════════════════════════════════════

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="pt">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>PlayNESTI — Dashboard</title>
<style>
@font-face {
  font-family: 'Ethnocentric';
  src: url('./EthnocentricRg.woff2') format('woff2');
  font-display: swap;
}

@font-face {
  font-family: 'Coolvetica';
  src: url('./CoolveticaRg.woff2') format('woff2');
  font-display: swap;
}

/* ═══════════════════════════════════════════════════════════════
   TOKENS
═══════════════════════════════════════════════════════════════ */
:root {
  --bg:      #0a0a0a;
  --surface: #111111;
  --surface2:#181818;
  --surface3:#202020;

  --border:  #252525;
  --border2: #303030;

  --accent:  #c0392b;
  --accent2: #e74c3c;

  --green:   #2ecc71;
  --yellow:  #f39c12;

  --text:    #f0f0f0;
  --dim:     #666;

  --r4: 4px;
  --r8: 8px;
  --r12:12px;
}

*, *::before, *::after {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

/* ═══════════════════════════════════════════════════════════════
   BASE
═══════════════════════════════════════════════════════════════ */
body {
  background: var(--bg);
  color: var(--text);
  font-family: 'Coolvetica', system-ui, sans-serif;
  min-height: 100vh;
  padding: 0 0 60px;
  overflow-x: hidden;
  font-size: 14px;
}

/* Scrollbar */
::-webkit-scrollbar { width: 5px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb {
  background: var(--border2);
  border-radius: 3px;
}

/* ═══════════════════════════════════════════════════════════════
   HEADER
═══════════════════════════════════════════════════════════════ */
header {
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  padding: 14px 24px;
  display: flex;
  align-items: center;
  gap: 18px;
  position: sticky;
  top: 0;
  z-index: 100;
  backdrop-filter: blur(8px);
}

header .logo {
  font-family: 'Ethnocentric', monospace;
  font-size: 1.2rem;
  letter-spacing: .12em;
  color: var(--accent);
}

header .logo span {
  color: var(--accent2);
}

header .subtitle {
  font-size: .68rem;
  color: var(--dim);
  letter-spacing: .16em;
  margin-top: 2px;
}

header .refresh-btn {
  margin-left: auto;
  background: var(--surface3);
  border: 1px solid var(--border2);
  color: var(--text);
  padding: 9px 16px;
  border-radius: var(--r8);
  font-family: inherit;
  font-size: .78rem;
  cursor: pointer;
  letter-spacing: .08em;
  transition: .15s;
}

header .refresh-btn:hover {
  background: var(--border2);
  border-color: var(--accent);
}

.last-updated {
  color: var(--dim);
  font-size: .72rem;
  margin-left: auto;
  letter-spacing: .08em;
}

/* ═══════════════════════════════════════════════════════════════
   STATS BAR
═══════════════════════════════════════════════════════════════ */
.stats-bar {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px,1fr));
  gap: 12px;
  padding: 18px 24px;
}

.stat-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r12);
  padding: 18px;
  position: relative;
  overflow: hidden;
  transition: .15s;
}

.stat-card:hover {
  border-color: var(--border2);
  background: var(--surface2);
}

.stat-card::before {
  content:'';
  position:absolute;
  top:0;
  left:0;
  right:0;
  height:3px;
}

.stat-card.total::before   { background: var(--accent); }
.stat-card.online::before  { background: var(--green); }
.stat-card.offline::before { background: var(--accent2); }
.stat-card.teams::before   { background: var(--yellow); }

.stat-card .val {
  font-family: 'Ethnocentric', monospace;
  font-size: 2rem;
  line-height: 1;
  margin-bottom: 10px;
}

.stat-card.total .val   { color: var(--accent2); }
.stat-card.online .val  { color: var(--green); }
.stat-card.offline .val { color: var(--accent2); }
.stat-card.teams .val   { color: var(--yellow); }

.stat-card .lbl {
  color: var(--dim);
  font-size: .7rem;
  letter-spacing: .12em;
  text-transform: uppercase;
}

/* ═══════════════════════════════════════════════════════════════
   TABS
═══════════════════════════════════════════════════════════════ */
.tabs {
  display: flex;
  gap: 8px;
  padding: 0 24px 18px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 24px;
  flex-wrap: wrap;
}

.tab-btn {
  background: var(--surface3);
  border: 1px solid var(--border2);
  color: var(--dim);
  border-radius: var(--r8);
  font-family: inherit;
  font-size: .78rem;
  letter-spacing: .12em;
  padding: 10px 18px;
  cursor: pointer;
  transition: .15s;
}

.tab-btn.active {
  background: #1a0a08;
  border-color: var(--accent);
  color: #fff;
}

.tab-btn:hover:not(.active) {
  background: var(--border2);
  color: var(--text);
}

/* ═══════════════════════════════════════════════════════════════
   PANELS
═══════════════════════════════════════════════════════════════ */
.panel {
  display: none;
  padding: 0 24px;
}

.panel.active {
  display: block;
}

/* ═══════════════════════════════════════════════════════════════
   SEARCH
═══════════════════════════════════════════════════════════════ */
.search-row {
  display: flex;
  gap: 12px;
  margin-bottom: 20px;
  flex-wrap: wrap;
}

.search-input,
.filter-select {
  background: var(--surface);
  border: 1px solid var(--border2);
  border-radius: var(--r8);
  color: var(--text);
  font-family: inherit;
  font-size: .82rem;
  padding: 10px 14px;
  outline: none;
  transition: border-color .2s;
}

.search-input {
  flex: 1;
  min-width: 240px;
}

.search-input:focus,
.filter-select:focus {
  border-color: var(--accent);
}

.filter-select {
  cursor: pointer;
}

/* ═══════════════════════════════════════════════════════════════
   TABLE
═══════════════════════════════════════════════════════════════ */
.data-table {
  width: 100%;
  border-collapse: collapse;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r12);
  overflow: hidden;
}

.data-table th {
  text-align: left;
  padding: 12px 14px;
  color: var(--dim);
  font-size: .68rem;
  letter-spacing: .14em;
  border-bottom: 1px solid var(--border);
  background: var(--surface2);
  white-space: nowrap;
  text-transform: uppercase;
}

.data-table td {
  padding: 12px 14px;
  border-bottom: 1px solid rgba(255,255,255,.04);
  vertical-align: middle;
  font-size: .82rem;
}

.data-table tr:hover td {
  background: rgba(255,255,255,.02);
}

/* ═══════════════════════════════════════════════════════════════
   BADGES
═══════════════════════════════════════════════════════════════ */
.badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: .68rem;
  letter-spacing: .05em;
  border: 1px solid transparent;
}

.badge.online {
  background: rgba(46,204,113,.12);
  color: var(--green);
  border-color: rgba(46,204,113,.22);
}

.badge.offline {
  background: rgba(231,76,60,.12);
  color: var(--accent2);
  border-color: rgba(231,76,60,.22);
}

.badge.chefe {
  background: rgba(243,156,18,.12);
  color: var(--yellow);
  border-color: rgba(243,156,18,.25);
  font-size: .62rem;
}

.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  display: inline-block;
}

.dot.online {
  background: var(--green);
  box-shadow: 0 0 6px var(--green);
}

.dot.offline {
  background: var(--accent2);
}

/* ═══════════════════════════════════════════════════════════════
   TEAM CARDS
═══════════════════════════════════════════════════════════════ */
.teams-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 16px;
}

.team-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r12);
  overflow: hidden;
  transition: .15s;
}

.team-card:hover {
  background: var(--surface2);
  border-color: var(--border2);
  transform: translateY(-1px);
}

.team-header {
  padding: 16px 18px;
  border-bottom: 1px solid var(--border);
  display: flex;
  justify-content: space-between;
  gap: 10px;
}

.team-name {
  font-family: 'Ethnocentric', monospace;
  font-size: .78rem;
  color: var(--accent2);
  letter-spacing: .06em;
  line-height: 1.4;
}

.team-game {
  font-size: .68rem;
  color: var(--dim);
  margin-top: 4px;
  letter-spacing: .08em;
}

.team-presence {
  text-align: right;
  font-size: .68rem;
}

.team-presence .frac {
  font-family: 'Ethnocentric', monospace;
  font-size: 1rem;
}

.team-presence .frac .n {
  color: var(--green);
}

.team-presence .frac .d {
  color: var(--dim);
}

.team-members {
  padding: 10px 0;
}

.member-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 18px;
  font-size: .78rem;
}

.member-row:hover {
  background: rgba(255,255,255,.02);
}

.member-name {
  flex: 1;
}

.member-discord {
  color: var(--dim);
  font-size: .72rem;
}

/* ═══════════════════════════════════════════════════════════════
   PROGRESS
═══════════════════════════════════════════════════════════════ */
.progress-wrap {
  padding: 0 18px 18px;
}

.progress-bar {
  height: 4px;
  background: var(--surface3);
  border-radius: 999px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--green), var(--accent2));
  border-radius: 999px;
  transition: width .4s ease;
}

/* ═══════════════════════════════════════════════════════════════
   UPLOAD ZONE
═══════════════════════════════════════════════════════════════ */
.upload-zone {
  border: 2px dashed var(--border2);
  border-radius: 16px;
  background: var(--surface);
  padding: 54px 24px;
  text-align: center;
  cursor: pointer;
  transition: .2s;
  max-width: 620px;
  margin: 0 auto 28px;
}

.upload-zone:hover,
.upload-zone.drag-over {
  border-color: var(--accent);
  background: #1a0a08;
}

.upload-zone .icon {
  font-size: 2.8rem;
  margin-bottom: 14px;
}

.upload-zone p {
  color: var(--dim);
  font-size: .84rem;
}

.upload-zone strong {
  color: var(--accent2);
}

/* ═══════════════════════════════════════════════════════════════
   SPINNER
═══════════════════════════════════════════════════════════════ */
.spinner {
  display: inline-block;
  width: 18px;
  height: 18px;
  border: 2px solid var(--border2);
  border-top-color: var(--accent2);
  border-radius: 50%;
  animation: spin .7s linear infinite;
  vertical-align: middle;
  margin-right: 8px;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

/* ═══════════════════════════════════════════════════════════════
   TOAST
═══════════════════════════════════════════════════════════════ */
#toast {
  position: fixed;
  bottom: 24px;
  right: 24px;
  background: var(--surface2);
  border: 1px solid var(--border2);
  color: var(--text);
  padding: 12px 18px;
  border-radius: var(--r8);
  font-size: .82rem;
  opacity: 0;
  transform: translateY(12px);
  transition: opacity .25s, transform .25s;
  pointer-events: none;
  z-index: 999;
  max-width: 340px;
  box-shadow: 0 4px 20px rgba(0,0,0,.45);
}

#toast.show {
  opacity: 1;
  transform: translateY(0);
}

#toast:not(.error) {
  border-left: 3px solid var(--green);
}

#toast.error {
  border-left: 3px solid var(--accent2);
}

/* ═══════════════════════════════════════════════════════════════
   EMPTY
═══════════════════════════════════════════════════════════════ */
.empty-state {
  text-align: center;
  color: var(--dim);
  padding: 60px 20px;
  font-size: .85rem;
  letter-spacing: .08em;
}

.empty-state .big {
  font-size: 2.6rem;
  margin-bottom: 14px;
}

/* ═══════════════════════════════════════════════════════════════
   RESPONSIVE
═══════════════════════════════════════════════════════════════ */
@media (max-width: 900px) {
  header {
    flex-wrap: wrap;
    padding: 14px 18px;
  }

  .stats-bar,
  .tabs,
  .panel {
    padding-left: 18px;
    padding-right: 18px;
  }

  .teams-grid {
    grid-template-columns: 1fr;
  }

  .data-table {
    font-size: .75rem;
  }
}

@media (max-width: 640px) {
  .search-row {
    flex-direction: column;
  }

  .filter-select,
  .search-input {
    width: 100%;
  }

  .stats-bar {
    grid-template-columns: 1fr 1fr;
  }
}
</style>
</head>
<body>

<header>
  <div>
    <div class="logo">PLAY<span>NESTI</span></div>
    <div class="subtitle">LAN PARTY — GESTÃO DE Discord</div>
  </div>
  <span id="lastUpdated" class="last-updated"></span>
  <button class="refresh-btn" onclick="loadData()">↻ ACTUALIZAR</button>
</header>

<div class="stats-bar">
  <div class="stat-card total">  <div class="val" id="sTotal">—</div>  <div class="lbl">PARTICIPANTES</div></div>
  <div class="stat-card online"> <div class="val" id="sOnline">—</div> <div class="lbl">NO SERVIDOR</div></div>
  <div class="stat-card offline"><div class="val" id="sOffline">—</div><div class="lbl">AUSENTES</div></div>
  <div class="stat-card teams">  <div class="val" id="sTeams">—</div>  <div class="lbl">EQUIPAS</div></div>
</div>

<div class="tabs">
  <button class="tab-btn active" onclick="switchTab('participants')">PARTICIPANTES</button>
  <button class="tab-btn" onclick="switchTab('teams')">EQUIPAS</button>
  <button class="tab-btn" onclick="switchTab('upload')">IMPORTAR CSV</button>
</div>

<!-- Panel: Participants -->
<div id="panel-participants" class="panel active">
  <div class="search-row">
    <input class="search-input" id="searchInput" placeholder="Pesquisar nome ou @discord..." oninput="filterTable()"/>
    <select class="filter-select" id="filterStatus" onchange="filterTable()">
      <option value="all">Todos</option>
      <option value="online">No servidor</option>
      <option value="offline">Ausentes</option>
    </select>
    <select class="filter-select" id="filterGame" onchange="filterTable()">
      <option value="all">Todos os jogos</option>
    </select>
  </div>
  <table class="data-table" id="participantsTable">
    <thead>
      <tr>
        <th>ESTADO</th>
        <th>NOME</th>
        <th>@DISCORD</th>
        <th>EQUIPA</th>
        <th>JOGO</th>
        <th>CARGO</th>
      </tr>
    </thead>
    <tbody id="participantsTbody"></tbody>
  </table>
</div>

<!-- Panel: Teams -->
<div id="panel-teams" class="panel">
  <div class="teams-grid" id="teamsGrid"></div>
</div>

<!-- Panel: Upload -->
<div id="panel-upload" class="panel">
  <div class="upload-zone" id="uploadZone" onclick="document.getElementById('csvFile').click()">
    <div class="icon">📄</div>
    <p><strong>Clica ou arrasta</strong> o ficheiro CSV aqui</p>
    <p style="margin-top:6px;font-size:.72rem">Formato: equipa, jogo, participante, discord, chefe</p>
  </div>
  <input type="file" id="csvFile" accept=".csv,text/csv" style="display:none" onchange="uploadCSV(this)"/>
  <div id="uploadStatus" style="text-align:center;margin-top:16px;"></div>
</div>

<div id="toast"></div>

<script>
let allData = { equipas: [], server_members: [] };

function switchTab(name) {
  document.querySelectorAll('.tab-btn').forEach((b,i) => {
    const names = ['participants','teams','upload'];
    b.classList.toggle('active', names[i] === name);
  });
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.getElementById('panel-' + name).classList.add('active');
}

async function loadData() {
  try {
    const r = await fetch('/api/data');
    const d = await r.json();
    allData = d;
    renderStats(d);
    renderParticipants(d);
    renderTeams(d);
    populateGameFilter(d);
    document.getElementById('lastUpdated').textContent =
      'Actualizado: ' + new Date().toLocaleTimeString('pt-PT');
  } catch(e) {
    showToast('Erro ao carregar dados: ' + e.message, true);
  }
}

function renderStats(d) {
  const all = d.equipas.flatMap(e => e.participantes);
  const onlineSet = new Set(d.server_members.map(m => m.discord_handle?.toLowerCase()));
  const online = all.filter(p => onlineSet.has(p.discord?.toLowerCase())).length;
  document.getElementById('sTotal').textContent  = all.length;
  document.getElementById('sOnline').textContent = online;
  document.getElementById('sOffline').textContent = all.length - online;
  document.getElementById('sTeams').textContent  = d.equipas.length;
}

function populateGameFilter(d) {
  const sel = document.getElementById('filterGame');
  const games = [...new Set(d.equipas.map(e => e.jogo))].sort();
  const cur = sel.value;
  sel.innerHTML = '<option value="all">Todos os jogos</option>';
  games.forEach(g => {
    const o = document.createElement('option');
    o.value = g; o.textContent = g;
    if (g === cur) o.selected = true;
    sel.appendChild(o);
  });
}

function renderParticipants(d) {
  filterTable(d);
}

function filterTable(d) {
  d = d || allData;
  const q       = document.getElementById('searchInput').value.toLowerCase();
  const status  = document.getElementById('filterStatus').value;
  const game    = document.getElementById('filterGame').value;
  const onlineSet = new Set(d.server_members.map(m => m.discord_handle?.toLowerCase()));
  const tbody = document.getElementById('participantsTbody');
  tbody.innerHTML = '';

  let rows = [];
  d.equipas.forEach(eq => {
    if (game !== 'all' && eq.jogo !== game) return;
    eq.participantes.forEach(p => {
      const isOnline = onlineSet.has(p.discord?.toLowerCase());
      if (status === 'online'  && !isOnline) return;
      if (status === 'offline' &&  isOnline) return;
      if (q && !p.nome.toLowerCase().includes(q) && !p.discord?.toLowerCase().includes(q)) return;
      rows.push({ p, eq, isOnline });
    });
  });

  if (!rows.length) {
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--dim);padding:40px">Sem resultados</td></tr>';
    return;
  }

  rows.forEach(({ p, eq, isOnline }) => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><span class="badge ${isOnline?'online':'offline'}"><span class="dot ${isOnline?'online':'offline'}"></span>${isOnline?'Online':'Ausente'}</span></td>
      <td>${esc(p.nome)} ${p.is_chefe ? '<span class="badge chefe">👑 Chefe</span>' : ''}</td>
      <td style="color:var(--dim)">@${esc(p.discord)}</td>
      <td>${esc(eq.nome)}</td>
      <td>${esc(eq.jogo)}</td>
      <td style="font-size:.72rem;color:var(--dim)">${esc(eq.nome)} - ${esc(eq.jogo)}</td>
    `;
    tbody.appendChild(tr);
  });
}

function renderTeams(d) {
  const grid = document.getElementById('teamsGrid');
  grid.innerHTML = '';
  const onlineSet = new Set(d.server_members.map(m => m.discord_handle?.toLowerCase()));

  if (!d.equipas.length) {
    grid.innerHTML = '<div class="empty-state" style="grid-column:1/-1"><div class="big">📂</div>Nenhuma equipa importada</div>';
    return;
  }

  d.equipas.forEach(eq => {
    const total  = eq.participantes.length;
    const online = eq.participantes.filter(p => onlineSet.has(p.discord?.toLowerCase())).length;
    const pct    = total ? Math.round(online / total * 100) : 0;

    const card = document.createElement('div');
    card.className = 'team-card';
    card.innerHTML = `
      <div class="team-header">
        <div><div class="team-name">${esc(eq.nome)}</div><div class="team-game">${esc(eq.jogo)}</div></div>
        <div class="team-presence">
          <div class="frac"><span class="n">${online}</span><span class="d">/${total}</span></div>
          <div style="color:var(--dim);font-size:.65rem;margin-top:2px">${pct}% online</div>
        </div>
      </div>
      <div class="team-members">
        ${eq.participantes.map(p => {
          const on = onlineSet.has(p.discord?.toLowerCase());
          return `<div class="member-row">
            <span class="dot ${on?'online':'offline'}"></span>
            <span class="member-name">${esc(p.nome)} ${p.is_chefe ? '👑' : ''}</span>
            <span class="member-discord">@${esc(p.discord)}</span>
          </div>`;
        }).join('')}
      </div>
      <div class="progress-wrap">
        <div class="progress-bar"><div class="progress-fill" style="width:${pct}%"></div></div>
      </div>
    `;
    grid.appendChild(card);
  });
}

async function uploadCSV(input) {
  const file = input.files[0];
  if (!file) return;
  const status = document.getElementById('uploadStatus');
  status.innerHTML = '<span class="spinner"></span> A processar...';
  try {
    const text = await file.text();
    const r = await fetch('/api/upload_csv', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ csv: text, filename: file.name })
    });
    const d = await r.json();
    if (d.error) throw new Error(d.error);
    status.innerHTML = `<span style="color:var(--green)">✓ ${d.equipas} equipas e ${d.participantes} participantes importados</span>`;
    showToast(`CSV importado: ${d.equipas} equipas, ${d.participantes} participantes`);
    await loadData();
  } catch(e) {
    status.innerHTML = `<span style="color:var(--accent2)">✗ ${e.message}</span>`;
    showToast(e.message, true);
  }
  input.value = '';
}

// Drag & drop
const zone = document.getElementById('uploadZone');
zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('drag-over'); });
zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
zone.addEventListener('drop', e => {
  e.preventDefault(); zone.classList.remove('drag-over');
  const f = e.dataTransfer.files[0];
  if (f) { const inp = document.getElementById('csvFile'); inp.files = e.dataTransfer.files; uploadCSV(inp); }
});

function esc(s) {
  if (!s) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function showToast(msg, error=false) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'show' + (error ? ' error' : '');
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.className = '', 3500);
}

loadData();
setInterval(loadData, 30000);
</script>
</body>
</html>
"""


# ══════════════════════════════════════════════════════════════════════════════
# COG PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

class PlayNESTI(commands.Cog, name="PlayNESTI"):
    """Cog all-in-one para a LAN Party PlayNESTI."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.equipas: list[Equipa] = []
        self.created_roles: dict[int, list[int]] = {}  # guild_id → [role_ids]
        self._flask_thread: Optional[threading.Thread] = None
        self._flask_app: Optional[Flask] = None
        self._load_data()
        if FLASK_AVAILABLE:
            self._start_dashboard()
    
    async def cog_load(self):
        """Called when cog is loaded."""
        log.info("[PlayNESTI] Cog loaded with slash commands.")

    # ── Persistência ──────────────────────────────────────────────────────────

    def _load_data(self):
        if DATA_FILE.exists():
            try:
                raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
                self.equipas = [Equipa.from_dict(e) for e in raw.get("equipas", [])]
                self.created_roles = {int(k): v for k, v in raw.get("created_roles", {}).items()}
                log.info(f"[PlayNESTI] Dados carregados: {len(self.equipas)} equipas.")
            except Exception as e:
                log.error(f"[PlayNESTI] Erro ao carregar dados: {e}")

    def _save_data(self):
        data = {
            "equipas": [e.to_dict() for e in self.equipas],
            "created_roles": {str(k): v for k, v in self.created_roles.items()},
            "updated_at": datetime.utcnow().isoformat(),
        }
        DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── Dashboard Flask ────────────────────────────────────────────────────────

    def _start_dashboard(self):
        app = Flask(__name__)
        CORS(app)
        self._flask_app = app

        cog_ref = self  # referência ao cog para os endpoints

        @app.route("/")
        def index():
            return render_template_string(DASHBOARD_HTML)

        @app.route("/api/data")
        def api_data():
            server_members = []
            for guild in cog_ref.bot.guilds:
                for member in guild.members:
                    handle = str(member) if member.discriminator == "0" else f"{member.name}#{member.discriminator}"
                    server_members.append({
                        "id": str(member.id),
                        "discord_handle": handle,
                        "display_name": member.display_name,
                    })
            return jsonify({
                "equipas": [e.to_dict() for e in cog_ref.equipas],
                "server_members": server_members,
            })

        @app.route("/api/upload_csv", methods=["POST"])
        def api_upload_csv():
            body = request.get_json(silent=True) or {}
            csv_content = body.get("csv", "")
            if not csv_content:
                return jsonify({"error": "CSV vazio"}), 400
            try:
                equipas = parse_csv(csv_content)
                cog_ref.equipas = equipas
                cog_ref._save_data()
                total_p = sum(len(e.participantes) for e in equipas)
                return jsonify({"equipas": len(equipas), "participantes": total_p})
            except Exception as ex:
                return jsonify({"error": str(ex)}), 400

        self._flask_thread = threading.Thread(
            target=lambda: app.run(host=DASHBOARD_HOST, port=DASHBOARD_PORT, debug=False, use_reloader=False),
            daemon=True,
            name="playnesti-dashboard",
        )
        self._flask_thread.start()
        log.info(f"[PlayNESTI] Dashboard disponível em http://localhost:{DASHBOARD_PORT}")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _find_member(self, guild: discord.Guild, discord_handle: str) -> Optional[discord.Member]:
        """Tenta encontrar um membro pelo @discord handle."""
        handle = discord_handle.lstrip("@").strip()
        # Tenta por username#discriminator
        for member in guild.members:
            full = f"{member.name}#{member.discriminator}"
            if full.lower() == handle.lower():
                return member
            # Discord novo (sem discriminador)
            if member.name.lower() == handle.lower():
                return member
            # Por display name
            if member.display_name.lower() == handle.lower():
                return member
        return None

    async def _get_or_create_role(
        self,
        guild: discord.Guild,
        name: str,
        color: discord.Color,
        reason: str = "PlayNESTI Discord manager",
    ) -> discord.Role:
        existing = discord.utils.get(guild.roles, name=name)
        if existing:
            return existing
        role = await guild.create_role(name=name, color=color, reason=reason)
        self.created_roles.setdefault(guild.id, []).append(role.id)
        return role

    # ── Slash Commands ───────────────────────────────────────────────────────

    playnesti_group = app_commands.Group(
        name="playnesti",
        description="Comandos de gestão PlayNESTI LAN Party",
        default_permissions=discord.Permissions(manage_roles=True)
    )

    @playnesti_group.command(name="carregar", description="Carrega o CSV enviado como anexo")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def carregar_slash(self, interaction: discord.Interaction):
        """Carrega o CSV em anexo na mensagem."""
        # Slash commands don't support file uploads directly in the command
        # We'll use a modal or fallback to a message-based approach
        embed = discord.Embed(
            title="📤 Carregar CSV",
            description="Para carregar um CSV, envie o arquivo em anexo numa mensagem e reaja com ✅.",
            color=discord.Color.blue(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @playnesti_group.command(name="status", description="Mostra quem está e quem não está no servidor")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def status_slash(self, interaction: discord.Interaction):
        """Mostra quem está e quem não está no servidor."""
        if not self.equipas:
            await interaction.response.send_message("⚠️ Nenhum CSV carregado. Use `/playnesti carregar` primeiro.", ephemeral=True)
            return

        guild = interaction.guild
        ausentes, presentes = [], []

        for eq in self.equipas:
            for p in eq.participantes:
                member = self._find_member(guild, p.discord_handle)
                info = f"`@{p.discord_handle}` ({p.nome}) [{eq.nome} - {eq.jogo}]"
                (presentes if member else ausentes).append(info)

        embed = discord.Embed(
            title="📊 Estado dos Participantes",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name=f"✅ No servidor ({len(presentes)})",
            value="\n".join(presentes[:15]) or "—",
            inline=False,
        )
        if len(presentes) > 15:
            embed.add_field(name="", value=f"… e mais {len(presentes)-15}", inline=False)

        embed.add_field(
            name=f"❌ Ausentes ({len(ausentes)})",
            value="\n".join(ausentes[:15]) or "—",
            inline=False,
        )
        if len(ausentes) > 15:
            embed.add_field(name="", value=f"… e mais {len(ausentes)-15}", inline=False)

        await interaction.response.send_message(embed=embed)

    @playnesti_group.command(name="criarcargos", description="Cria e atribui cargos automaticamente")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def criar_cargos_slash(self, interaction: discord.Interaction):
        """Cria cargos de equipa e de chefe, e atribui aos membros encontrados."""
        if not self.equipas:
            await interaction.response.send_message("⚠️ Nenhum CSV carregado. Use `/playnesti carregar` primeiro.", ephemeral=True)
            return

        await interaction.response.defer()
        guild = interaction.guild

        resultados = {
            "cargos_criados": [],
            "atribuidos": [],
            "nao_encontrados": [],
            "erros": [],
        }

        # Agrupa chefes por jogo para o cargo "Chefe de equipa (Jogo)"
        chefe_roles: dict[str, discord.Role] = {}

        for i, eq in enumerate(self.equipas):
            color = ROLE_COLORS[i % len(ROLE_COLORS)]

            # ── Cargo de equipa ──
            try:
                team_role = await self._get_or_create_role(guild, eq.role_name, color)
                resultados["cargos_criados"].append(eq.role_name)
            except discord.Forbidden:
                resultados["erros"].append(f"Sem permissão para criar cargo: {eq.role_name}")
                continue

            # ── Cargo de chefe (por jogo, partilhado entre equipas do mesmo jogo) ──
            chefe_role_name = eq.chefe_role_name
            if chefe_role_name not in chefe_roles:
                try:
                    chefe_role = await self._get_or_create_role(
                        guild, chefe_role_name, discord.Color.gold()
                    )
                    chefe_roles[chefe_role_name] = chefe_role
                    if chefe_role_name not in resultados["cargos_criados"]:
                        resultados["cargos_criados"].append(chefe_role_name)
                except discord.Forbidden:
                    resultados["erros"].append(f"Sem permissão para criar cargo: {chefe_role_name}")
                    chefe_roles[chefe_role_name] = None

            chefe_role = chefe_roles.get(chefe_role_name)

            # ── Atribuir cargos ──
            for p in eq.participantes:
                member = self._find_member(guild, p.discord_handle)
                if not member:
                    resultados["nao_encontrados"].append(f"@{p.discord_handle} ({p.nome})")
                    continue
                try:
                    roles_to_add = [team_role]
                    if p.is_chefe and chefe_role:
                        roles_to_add.append(chefe_role)
                    await member.add_roles(*roles_to_add, reason="PlayNESTI LAN Party")
                    label = f"{member.display_name} → {eq.role_name}"
                    if p.is_chefe:
                        label += f" + {chefe_role_name}"
                    resultados["atribuidos"].append(label)
                except discord.Forbidden:
                    resultados["erros"].append(f"Sem permissão para atribuir cargo a {member}")
                except Exception as e:
                    resultados["erros"].append(str(e))

            await asyncio.sleep(0.3)  # rate-limit amigável

        self._save_data()

        embed = discord.Embed(title="Cargos Criados e Atribuídos", color=discord.Color.green())
        embed.add_field(
            name=f"✅ Cargos criados ({len(resultados['cargos_criados'])})",
            value="\n".join(f"• {r}" for r in resultados["cargos_criados"]) or "—",
            inline=False,
        )
        embed.add_field(
            name=f"👤 Atribuídos ({len(resultados['atribuidos'])})",
            value="\n".join(resultados["atribuidos"][:20]) or "—",
            inline=False,
        )
        if resultados["nao_encontrados"]:
            embed.add_field(
                name=f"❓ Não encontrados ({len(resultados['nao_encontrados'])})",
                value="\n".join(resultados["nao_encontrados"][:15]),
                inline=False,
            )
        if resultados["erros"]:
            embed.add_field(
                name="⚠️ Erros",
                value="\n".join(resultados["erros"][:10]),
                inline=False,
            )
        await interaction.followup.send(embed=embed)

    @playnesti_group.command(name="dashboard", description="Mostra o URL do dashboard web")
    async def dashboard_slash(self, interaction: discord.Interaction):
        """Mostra o URL do dashboard web."""
        if not FLASK_AVAILABLE:
            await interaction.response.send_message("❌ Flask não instalado. Instala com `pip install flask flask-cors`.", ephemeral=True)
            return
        embed = discord.Embed(
            title="🌐 Dashboard PlayNESTI",
            description=(
                f"Acede ao dashboard em:\n"
                f"**http://<ip-do-servidor>:{DASHBOARD_PORT}**\n\n"
                f"O dashboard actualiza automaticamente de 30 em 30 segundos."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Funcionalidades", value=(
            "• Ver todos os participantes e estado (online/offline)\n"
            "• Filtrar por jogo, equipa, estado\n"
            "• Cards por equipa com progresso de presença\n"
            "• Importar novo CSV directamente pelo browser"
        ))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @playnesti_group.command(name="limpar", description="Remove todos os cargos criados pelo bot")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def limpar_slash(self, interaction: discord.Interaction):
        """Remove todos os cargos criados pelo bot neste servidor."""
        guild = interaction.guild
        role_ids = self.created_roles.get(guild.id, [])
        if not role_ids:
            await interaction.response.send_message("Não há cargos registados para remover.", ephemeral=True)
            return

        await interaction.response.defer()
        removed, errors = 0, 0
        for rid in role_ids:
            role = guild.get_role(rid)
            if role:
                try:
                    await role.delete(reason="PlayNESTI cleanup")
                    removed += 1
                except Exception:
                    errors += 1
        self.created_roles[guild.id] = []
        self._save_data()
        result = f"✅ {removed} cargos removidos" + (f" ({errors} erros)" if errors else "") + "."
        await interaction.followup.send(result)

    # ── Listeners ─────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_ready(self):
        log.info(f"[PlayNESTI] Cog pronto. {len(self.equipas)} equipas em memória.")

    # ── Message attachment listener for CSV upload ────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen for CSV uploads in a specific channel for PlayNESTI management."""
        # Skip bot messages and DMs
        if message.author.bot or not message.guild:
            return

        # Only respond to users with manage_roles permission
        if not message.author.guild_permissions.manage_roles:
            return

        # Check for CSV attachments in a designated management channel
        # (optional: could add env var for this)
        for attachment in message.attachments:
            if attachment.filename.endswith(".csv"):
                try:
                    raw = await attachment.read()
                    try:
                        content = raw.decode("utf-8-sig")
                    except UnicodeDecodeError:
                        content = raw.decode("latin-1")

                    self.equipas = parse_csv(content)
                    self._save_data()

                    total_p = sum(len(e.participantes) for e in self.equipas)
                    embed = discord.Embed(
                        title="✅ CSV Carregado",
                        description=f"**{len(self.equipas)}** equipas · **{total_p}** participantes",
                        color=discord.Color.green(),
                    )
                    for eq in self.equipas[:10]:
                        chefes = ", ".join(c.nome for c in eq.chefes) or "—"
                        embed.add_field(
                            name=f"{eq.nome} — {eq.jogo}",
                            value=f"{len(eq.participantes)} membros | Chefe(s): {chefes}",
                            inline=False,
                        )
                    if len(self.equipas) > 10:
                        embed.set_footer(text=f"… e mais {len(self.equipas) - 10} equipas")
                    await message.reply(embed=embed)
                except ValueError as e:
                    await message.reply(f"❌ Erro no CSV: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# SETUP
# ══════════════════════════════════════════════════════════════════════════════

async def setup(bot: commands.Bot):
    await bot.add_cog(PlayNESTI(bot))