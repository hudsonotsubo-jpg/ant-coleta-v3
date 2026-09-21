import os
import io
import base64
import re
import json
import hashlib
import unicodedata
from datetime import datetime

import anthropic
import requests
import streamlit as st
import pandas as pd
import gspread

from PIL import Image
from urllib.parse import urlencode
from bs4 import BeautifulSoup

from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from google.oauth2.credentials import Credentials as UserCredentials

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

st.set_page_config(page_title="APP ANT v3", page_icon="🏆", layout="centered")


def botao_copiar_seguro(texto: str, key: str = "copiar"):
    """
    Botão de copiar que lê o valor atual da textarea editável pelo usuário
    diretamente do DOM do documento pai (via postMessage), garantindo que
    edições feitas antes de clicar sejam incluídas na cópia.

    Fluxo:
    1. O iframe envia postMessage ao pai pedindo o valor da textarea com data-key=key
    2. O pai responde com o valor atual do DOM
    3. O iframe copia o texto recebido para o clipboard
    Se a textarea não for encontrada no pai, usa o texto original como fallback.
    """
    import base64 as _b64
    b64 = _b64.b64encode(texto.encode("utf-8")).decode("ascii")
    uid = key.replace(" ", "_").replace(".", "_").replace("-", "_")

    html = f"""
<style>
  #btn_{uid} {{
    background: #0d6efd;
    color: white;
    border: none;
    padding: 10px 20px;
    font-size: 15px;
    border-radius: 6px;
    cursor: pointer;
    width: 100%;
    margin-top: 4px;
    font-family: sans-serif;
  }}
  #btn_{uid}:active {{ opacity: 0.85; }}
</style>
<button id="btn_{uid}" onclick="copiarTexto_{uid}()">Copiar texto</button>
<script>
function copiarTexto_{uid}() {{
  var btn = document.getElementById('btn_{uid}');
  btn.textContent = 'Copiando...';
  btn.disabled = true;

  function executarCopia(txt) {{
    // Método 1: textarea + execCommand (sem permissão especial)
    try {{
      var t = document.createElement('textarea');
      t.value = txt;
      t.style.position = 'fixed';
      t.style.left = '-9999px';
      t.style.top = '-9999px';
      t.setAttribute('readonly', '');
      document.body.appendChild(t);
      t.focus();
      t.select();
      t.setSelectionRange(0, t.value.length);
      var ok = document.execCommand('copy');
      document.body.removeChild(t);
      if (ok) {{ marcarCopiado(); return; }}
    }} catch(e) {{}}

    // Método 2: navigator.clipboard
    if (navigator.clipboard && window.isSecureContext) {{
      navigator.clipboard.writeText(txt).then(marcarCopiado).catch(marcarErro);
    }} else {{
      marcarErro();
    }}
  }}

  function marcarCopiado() {{
    btn.textContent = 'Copiado!';
    btn.style.background = '#28a745';
    btn.disabled = false;
    setTimeout(function() {{
      btn.textContent = 'Copiar texto';
      btn.style.background = '#0d6efd';
    }}, 2500);
  }}

  function marcarErro() {{
    btn.textContent = 'Erro ao copiar';
    btn.style.background = '#dc3545';
    btn.disabled = false;
    setTimeout(function() {{
      btn.textContent = 'Copiar texto';
      btn.style.background = '#0d6efd';
    }}, 2500);
  }}

  // Tenta ler o valor atual da textarea no documento pai via postMessage
  var fallbackB64 = '{b64}';
  var respondido = false;

  function usarFallback() {{
    if (respondido) return;
    respondido = true;
    var bytes = Uint8Array.from(atob(fallbackB64), function(c) {{ return c.charCodeAt(0); }});
    var txt = new TextDecoder('utf-8').decode(bytes);
    executarCopia(txt);
  }}

  // Listener para receber o texto atual do pai
  function onResposta(e) {{
    if (!e.data || e.data.type !== 'ANT_TEXTAREA_VALUE_{uid}') return;
    if (respondido) return;
    respondido = true;
    window.removeEventListener('message', onResposta);
    executarCopia(e.data.value || '');
  }}
  window.addEventListener('message', onResposta);

  // Solicita o valor atual ao pai
  window.parent.postMessage({{
    type: 'ANT_GET_TEXTAREA_{uid}',
    key:  '{key}'
  }}, '*');

  // Timeout: se o pai não responder em 400ms, usa o fallback
  setTimeout(usarFallback, 400);
}}
</script>
"""
    # Listener no documento pai que responde com o valor atual da textarea
    # Injetado via st.markdown uma vez por key (sobrevive a reruns)
    listener_key = f"_ta_listener_{uid}"
    if listener_key not in st.session_state:
        st.session_state[listener_key] = True

    st.markdown(f"""
<script>
(function() {{
  var lk = '_ant_ta_listener_{uid}';
  if (window[lk]) return;
  window[lk] = true;
  window.addEventListener('message', function(e) {{
    if (!e.data || e.data.type !== 'ANT_GET_TEXTAREA_{uid}') return;
    var key = e.data.key;
    var ta = null;

    // Estratégia 1: busca pelo id do elemento (Streamlit usa key no id)
    var todas = document.querySelectorAll('textarea');
    var keyHifenizado = key.replace(/_/g, '-');
    for (var i = 0; i < todas.length; i++) {{
      var elId = todas[i].id || '';
      if (elId.indexOf(keyHifenizado) >= 0) {{ ta = todas[i]; break; }}
    }}

    // Estratégia 2: busca pelo aria-label (label do st.text_area)
    if (!ta) {{
      for (var i = 0; i < todas.length; i++) {{
        var lbl = (todas[i].getAttribute('aria-label') || '').toLowerCase();
        var keyLower = key.replace(/_/g,' ').toLowerCase();
        if (lbl === keyLower || lbl.indexOf(keyLower) >= 0) {{ ta = todas[i]; break; }}
      }}
    }}

    // Estratégia 3: encontra o iframe que enviou a mensagem e pega
    // a textarea imediatamente anterior a ele no DOM
    if (!ta) {{
      var iframes = document.querySelectorAll('iframe');
      var iframeSrc = null;
      for (var k = 0; k < iframes.length; k++) {{
        try {{
          if (iframes[k].contentWindow === e.source) {{ iframeSrc = iframes[k]; break; }}
        }} catch(ex) {{}}
      }}
      if (iframeSrc) {{
        // Procura a textarea mais próxima ANTES do iframe no DOM
        var allEls = Array.from(document.querySelectorAll('textarea, iframe'));
        var iframeIdx = allEls.indexOf(iframeSrc);
        for (var m = iframeIdx - 1; m >= 0; m--) {{
          if (allEls[m].tagName === 'TEXTAREA' && allEls[m].offsetParent !== null) {{
            ta = allEls[m]; break;
          }}
        }}
      }}
    }}

    // Estratégia 4: última textarea visível (fallback final)
    if (!ta) {{
      for (var j = todas.length - 1; j >= 0; j--) {{
        if (todas[j].offsetParent !== null) {{ ta = todas[j]; break; }}
      }}
    }}

    var valor = ta ? ta.value : null;
    var iframes2 = document.querySelectorAll('iframe');
    for (var k = 0; k < iframes2.length; k++) {{
      try {{
        iframes2[k].contentWindow.postMessage({{
          type: 'ANT_TEXTAREA_VALUE_{uid}',
          value: valor
        }}, '*');
      }} catch(ex) {{}}
    }}
  }});
}})();
</script>
""", unsafe_allow_html=True)

    st.components.v1.html(html, height=55)


def decodificar_texto(texto: str) -> str:
    """Decodifica texto com encoding de URL (%20, %0A, etc.) para texto legível."""
    from urllib.parse import unquote
    return unquote(texto)



# =========================================
# HELPERS DE SECRETS
# =========================================
def obter_secret_obrigatorio(chave):
    try:
        valor = st.secrets[chave]
        if isinstance(valor, str) and not valor.strip():
            raise KeyError
        return valor
    except Exception:
        st.error(f"Secret obrigatório ausente ou vazio: {chave}")
        st.stop()


ANTHROPIC_API_KEY = obter_secret_obrigatorio("ANTHROPIC_API_KEY")
GOOGLE_SHEET_ID_SUL = obter_secret_obrigatorio("GOOGLE_SHEET_ID_SUL")
GOOGLE_SHEET_ID_NORTE = obter_secret_obrigatorio("GOOGLE_SHEET_ID_NORTE")
GOOGLE_SHEET_ID_LOG = obter_secret_obrigatorio("GOOGLE_SHEET_ID_LOG")

FLYERS_JANEIRO_FAZER = st.secrets.get("FLYERS_JANEIRO_FAZER", "")
FLYERS_FEVEREIRO_FAZER = st.secrets.get("FLYERS_FEVEREIRO_FAZER", "")
FLYERS_MARCO_FAZER = st.secrets.get("FLYERS_MARCO_FAZER", "")
FLYERS_ABRIL_FAZER = st.secrets.get("FLYERS_ABRIL_FAZER", "")
FLYERS_MAIO_FAZER = st.secrets.get("FLYERS_MAIO_FAZER", "")
FLYERS_JUNHO_FAZER = st.secrets.get("FLYERS_JUNHO_FAZER", "")
FLYERS_JULHO_FAZER = st.secrets.get("FLYERS_JULHO_FAZER", "")
FLYERS_AGOSTO_FAZER = st.secrets.get("FLYERS_AGOSTO_FAZER", "")
FLYERS_SETEMBRO_FAZER = st.secrets.get("FLYERS_SETEMBRO_FAZER", "")
FLYERS_OUTUBRO_FAZER = st.secrets.get("FLYERS_OUTUBRO_FAZER", "")
FLYERS_NOVEMBRO_FAZER = st.secrets.get("FLYERS_NOVEMBRO_FAZER", "")
FLYERS_DEZEMBRO_FAZER = st.secrets.get("FLYERS_DEZEMBRO_FAZER", "")

TORNEIOS_JANEIRO_SUL = st.secrets.get("TORNEIOS_JANEIRO_SUL", "")
TORNEIOS_FEVEREIRO_SUL = st.secrets.get("TORNEIOS_FEVEREIRO_SUL", "")
TORNEIOS_MARCO_SUL = st.secrets.get("TORNEIOS_MARCO_SUL", "")
TORNEIOS_ABRIL_SUL = st.secrets.get("TORNEIOS_ABRIL_SUL", "")
TORNEIOS_MAIO_SUL = st.secrets.get("TORNEIOS_MAIO_SUL", "")
TORNEIOS_JUNHO_SUL = st.secrets.get("TORNEIOS_JUNHO_SUL", "")
TORNEIOS_JULHO_SUL = st.secrets.get("TORNEIOS_JULHO_SUL", "")
TORNEIOS_AGOSTO_SUL = st.secrets.get("TORNEIOS_AGOSTO_SUL", "")
TORNEIOS_SETEMBRO_SUL = st.secrets.get("TORNEIOS_SETEMBRO_SUL", "")
TORNEIOS_OUTUBRO_SUL = st.secrets.get("TORNEIOS_OUTUBRO_SUL", "")
TORNEIOS_NOVEMBRO_SUL = st.secrets.get("TORNEIOS_NOVEMBRO_SUL", "")
TORNEIOS_DEZEMBRO_SUL = st.secrets.get("TORNEIOS_DEZEMBRO_SUL", "")

TORNEIOS_JANEIRO_NORTE = st.secrets.get("TORNEIOS_JANEIRO_NORTE", "")
TORNEIOS_FEVEREIRO_NORTE = st.secrets.get("TORNEIOS_FEVEREIRO_NORTE", "")
TORNEIOS_MARCO_NORTE = st.secrets.get("TORNEIOS_MARCO_NORTE", "")
TORNEIOS_ABRIL_NORTE = st.secrets.get("TORNEIOS_ABRIL_NORTE", "")
TORNEIOS_MAIO_NORTE = st.secrets.get("TORNEIOS_MAIO_NORTE", "")
TORNEIOS_JUNHO_NORTE = st.secrets.get("TORNEIOS_JUNHO_NORTE", "")
TORNEIOS_JULHO_NORTE = st.secrets.get("TORNEIOS_JULHO_NORTE", "")
TORNEIOS_AGOSTO_NORTE = st.secrets.get("TORNEIOS_AGOSTO_NORTE", "")
TORNEIOS_SETEMBRO_NORTE = st.secrets.get("TORNEIOS_SETEMBRO_NORTE", "")
TORNEIOS_OUTUBRO_NORTE = st.secrets.get("TORNEIOS_OUTUBRO_NORTE", "")
TORNEIOS_NOVEMBRO_NORTE = st.secrets.get("TORNEIOS_NOVEMBRO_NORTE", "")
TORNEIOS_DEZEMBRO_NORTE = st.secrets.get("TORNEIOS_DEZEMBRO_NORTE", "")

GOOGLE_CLIENT_ID = obter_secret_obrigatorio("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = obter_secret_obrigatorio("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = obter_secret_obrigatorio("GOOGLE_REDIRECT_URI")
APP_SECRET_KEY = obter_secret_obrigatorio("APP_SECRET_KEY")

# Cliente Anthropic (substitui OpenAI)
claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

if "ultimo_salvamento_fingerprint" not in st.session_state:
    st.session_state["ultimo_salvamento_fingerprint"] = None

if "drive_token_info" not in st.session_state:
    st.session_state["drive_token_info"] = None

if "drive_oauth_state" not in st.session_state:
    st.session_state["drive_oauth_state"] = None

if "drive_token_carregado_persistencia" not in st.session_state:
    st.session_state["drive_token_carregado_persistencia"] = False

if "gmail_token_info" not in st.session_state:
    st.session_state["gmail_token_info"] = None

if "gmail_oauth_state" not in st.session_state:
    st.session_state["gmail_oauth_state"] = None

if "gmail_token_carregado_persistencia" not in st.session_state:
    st.session_state["gmail_token_carregado_persistencia"] = False


# =========================================
# CONFIG GOOGLE
# =========================================
SERVICE_ACCOUNT_FILE = "credentials/google_service_account.json"

SCOPES_SHEETS = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SCOPES_DRIVE_OAUTH = [
    "https://www.googleapis.com/auth/drive",
]

SCOPES_GMAIL = [
    "https://www.googleapis.com/auth/gmail.modify",
]

GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_URI = "https://oauth2.googleapis.com/revoke"

NOME_ABA_CONFIG = "CONFIG_APP"
CHAVE_TOKEN_DRIVE = "DRIVE_TOKEN_INFO"
CHAVE_TOKEN_GMAIL = "GMAIL_TOKEN_INFO"

# Conta dedicada que recebe os e-mails de formulário já conferidos pelo
# usuário, com o print da postagem anexado, para registro automático.
GMAIL_CONTA_FORMULARIOS = "registroforms.ant@gmail.com"
GMAIL_LABEL_ARQUIVADOS = "Torneios Incluídos"



# =========================================
# QUERY PARAMS
# =========================================
def obter_query_param(nome):
    try:
        valor = st.query_params.get(nome)
        if isinstance(valor, list):
            return valor[0] if valor else None
        return valor
    except Exception:
        params = st.experimental_get_query_params()
        valores = params.get(nome, [])
        return valores[0] if valores else None


def limpar_query_params():
    try:
        st.query_params.clear()
    except Exception:
        st.experimental_set_query_params()


# =========================================
# GOOGLE SHEETS / SERVICE ACCOUNT
# =========================================
def obter_credenciais_service_account():
    try:
        info = dict(st.secrets["gcp_service_account"])
        return ServiceAccountCredentials.from_service_account_info(
            info,
            scopes=SCOPES_SHEETS
        )
    except Exception:
        pass

    if os.path.exists(SERVICE_ACCOUNT_FILE):
        return ServiceAccountCredentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE,
            scopes=SCOPES_SHEETS
        )

    raise RuntimeError(
        "Credenciais da service account não encontradas. "
        "No Streamlit Cloud, adicione [gcp_service_account] nos secrets. "
        "No ambiente local, mantenha o arquivo credentials/google_service_account.json."
    )


def conectar_gsheet():
    creds = obter_credenciais_service_account()
    return gspread.authorize(creds)


def obter_planilha_por_agenda(client_gs, agenda):
    if agenda == "SUL":
        return client_gs.open_by_key(GOOGLE_SHEET_ID_SUL)
    if agenda == "NORTE":
        return client_gs.open_by_key(GOOGLE_SHEET_ID_NORTE)
    raise ValueError("Agenda inválida.")


def obter_planilha_log(client_gs):
    return client_gs.open_by_key(GOOGLE_SHEET_ID_LOG)


def obter_aba_config(client_gs):
    planilha_log = obter_planilha_log(client_gs)

    try:
        aba = planilha_log.worksheet(NOME_ABA_CONFIG)
    except Exception:
        aba = planilha_log.add_worksheet(title=NOME_ABA_CONFIG, rows=50, cols=2)
        aba.update("A1:B1", [["chave", "valor"]])

    valores = aba.get("A1:B2")
    if not valores:
        aba.update("A1:B1", [["chave", "valor"]])
    else:
        primeira_linha = valores[0]
        if len(primeira_linha) < 2 or primeira_linha[0] != "chave" or primeira_linha[1] != "valor":
            aba.update("A1:B1", [["chave", "valor"]])

    return aba


def buscar_linha_por_chave(aba, chave):
    registros = aba.get_all_values()
    for idx, linha in enumerate(registros[1:], start=2):
        if linha and len(linha) >= 1 and linha[0] == chave:
            return idx
    return None


def carregar_token_persistido(chave):
    """Versão genérica: lê o token salvo na aba CONFIG_APP pela chave informada."""
    try:
        client_gs = conectar_gsheet()
        aba = obter_aba_config(client_gs)
        registros = aba.get_all_values()

        for linha in registros[1:]:
            if len(linha) >= 2 and linha[0] == chave and linha[1].strip():
                return json.loads(linha[1])

    except Exception:
        return None

    return None


def salvar_token_persistido(chave, token_info):
    """Versão genérica: salva o token na aba CONFIG_APP sob a chave informada."""
    client_gs = conectar_gsheet()
    aba = obter_aba_config(client_gs)

    valor_json = json.dumps(token_info, ensure_ascii=False)
    linha_existente = buscar_linha_por_chave(aba, chave)

    if linha_existente:
        aba.update(f"A{linha_existente}:B{linha_existente}", [[chave, valor_json]])
    else:
        aba.append_row([chave, valor_json], value_input_option="RAW")


def limpar_token_persistido(chave):
    """Versão genérica: limpa o token salvo na aba CONFIG_APP sob a chave informada."""
    try:
        client_gs = conectar_gsheet()
        aba = obter_aba_config(client_gs)
        linha_existente = buscar_linha_por_chave(aba, chave)

        if linha_existente:
            aba.update(f"A{linha_existente}:B{linha_existente}", [[chave, ""]])
    except Exception:
        pass


# Wrappers mantidos para não alterar nenhum ponto de chamada já existente
# do fluxo do Google Drive (comportamento idêntico ao de antes).
def carregar_token_drive_persistido():
    return carregar_token_persistido(CHAVE_TOKEN_DRIVE)


def salvar_token_drive_persistido(token_info):
    salvar_token_persistido(CHAVE_TOKEN_DRIVE, token_info)


def limpar_token_drive_persistido():
    limpar_token_persistido(CHAVE_TOKEN_DRIVE)


def salvar_linha_na_aba(planilha, nome_aba, linha):
    aba = planilha.worksheet(nome_aba)
    aba.append_row(linha, value_input_option="USER_ENTERED")


def registrar_log(
    client_gs,
    torneio,
    cidade,
    data_evento,
    agenda,
    mes_1,
    mes_2,
    nome_flyer,
    status,
    erro=""
):
    planilha_log = obter_planilha_log(client_gs)
    aba_log = planilha_log.worksheet("LOG")

    timestamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    linha_log = [
        timestamp,
        torneio,
        cidade,
        data_evento,
        agenda,
        mes_1,
        mes_2,
        nome_flyer,
        status,
        erro,
    ]

    aba_log.append_row(linha_log, value_input_option="USER_ENTERED")


# =========================================
# GOOGLE DRIVE (OAuth WEB MANUAL)
# =========================================
def gerar_state_seguro(prefixo="drive"):
    """O prefixo (ex.: 'drive' ou 'gmail') vai embutido no state para que o
    callback único de retorno do OAuth saiba a qual fluxo aquele retorno
    pertence, já que os dois fluxos compartilham o mesmo redirect_uri."""
    base = f"{APP_SECRET_KEY}-{prefixo}-{datetime.now().timestamp()}"
    return f"{prefixo}:{hashlib.sha256(base.encode('utf-8')).hexdigest()}"


def gerar_url_autorizacao_drive():
    state = gerar_state_seguro("drive")
    st.session_state["drive_oauth_state"] = state

    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES_DRIVE_OAUTH),
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": state,
    }

    return f"{GOOGLE_AUTH_URI}?{urlencode(params)}"


def gerar_url_autorizacao_gmail():
    """Fluxo OAuth independente do Drive — autentica a conta dedicada
    registroforms.ant@gmail.com, não a conta pessoal usada no Drive."""
    state = gerar_state_seguro("gmail")
    st.session_state["gmail_oauth_state"] = state

    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES_GMAIL),
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": state,
    }

    return f"{GOOGLE_AUTH_URI}?{urlencode(params)}"


def trocar_code_por_token(code):
    payload = {
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "grant_type": "authorization_code",
    }

    response = requests.post(GOOGLE_TOKEN_URI, data=payload, timeout=30)
    try:
        data = response.json()
    except Exception:
        data = {"raw_text": response.text}

    if response.status_code != 200:
        raise RuntimeError(f"Falha ao obter token: {data}")

    return data


def renovar_token_google(refresh_token):
    payload = {
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }

    response = requests.post(GOOGLE_TOKEN_URI, data=payload, timeout=30)
    try:
        data = response.json()
    except Exception:
        data = {"raw_text": response.text}

    if response.status_code != 200:
        raise RuntimeError(f"Falha ao renovar token: {data}")

    return data


def processar_callback_oauth_google():
    """Callback único de retorno do OAuth do Google. Como os dois fluxos
    (Drive da conta pessoal e Gmail da conta dedicada de formulários)
    compartilham o mesmo redirect_uri, o prefixo embutido no state (ver
    gerar_state_seguro) diz a qual fluxo esse retorno pertence."""
    code = obter_query_param("code")
    state = obter_query_param("state")
    error = obter_query_param("error")

    if error:
        st.error(f"Autorização do Google cancelada ou negada: {error}")
        limpar_query_params()
        return

    if not code:
        return

    prefixo = state.split(":", 1)[0] if state and ":" in state else "drive"

    if prefixo == "gmail":
        state_esperado = st.session_state.get("gmail_oauth_state")
        scopes = SCOPES_GMAIL
        chave_sessao = "gmail_token_info"
        chave_persistida = CHAVE_TOKEN_GMAIL
        chave_state_sessao = "gmail_oauth_state"
        mensagem_sucesso = f"Gmail ({GMAIL_CONTA_FORMULARIOS}) conectado com sucesso."
    else:
        state_esperado = st.session_state.get("drive_oauth_state")
        scopes = SCOPES_DRIVE_OAUTH
        chave_sessao = "drive_token_info"
        chave_persistida = CHAVE_TOKEN_DRIVE
        chave_state_sessao = "drive_oauth_state"
        mensagem_sucesso = "Google Drive conectado com sucesso."

    if state_esperado and state != state_esperado:
        st.error("Falha de segurança no retorno do Google (state inválido).")
        limpar_query_params()
        return

    try:
        token_data = trocar_code_por_token(code)
    except Exception as e:
        st.error("Falha ao trocar o código de autorização pelo token do Google.")
        st.code(repr(e))
        limpar_query_params()
        return

    token_info = {
        "token": token_data.get("access_token"),
        "refresh_token": token_data.get("refresh_token"),
        "token_uri": GOOGLE_TOKEN_URI,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "scopes": scopes,
    }

    st.session_state[chave_sessao] = token_info
    st.session_state[chave_state_sessao] = None
    limpar_query_params()

    try:
        salvar_token_persistido(chave_persistida, token_info)
    except Exception as e:
        st.warning(
            "Conectado, mas não foi possível persistir o token na planilha LOG. "
            "Será necessário reconectar se a página recarregar."
        )
        st.code(f"Erro ao salvar token: {repr(e)}")

    st.success(mensagem_sucesso)
    st.rerun()


def obter_credenciais_drive_usuario():
    token_info = st.session_state.get("drive_token_info")
    if not token_info:
        return None

    if not token_info.get("token"):
        return None

    creds = UserCredentials(
        token=token_info.get("token"),
        refresh_token=token_info.get("refresh_token"),
        token_uri=token_info.get("token_uri"),
        client_id=token_info.get("client_id"),
        client_secret=token_info.get("client_secret"),
        scopes=token_info.get("scopes"),
    )

    if creds.expired and creds.refresh_token:
        try:
            novo_token = renovar_token_google(creds.refresh_token)

            token_atualizado = {
                "token": novo_token.get("access_token"),
                "refresh_token": token_info.get("refresh_token"),
                "token_uri": GOOGLE_TOKEN_URI,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "scopes": SCOPES_DRIVE_OAUTH,
            }

            st.session_state["drive_token_info"] = token_atualizado
            salvar_token_drive_persistido(token_atualizado)

            creds = UserCredentials(
                token=novo_token.get("access_token"),
                refresh_token=token_info.get("refresh_token"),
                token_uri=GOOGLE_TOKEN_URI,
                client_id=GOOGLE_CLIENT_ID,
                client_secret=GOOGLE_CLIENT_SECRET,
                scopes=SCOPES_DRIVE_OAUTH,
            )
        except Exception:
            st.session_state["drive_token_info"] = None
            limpar_token_drive_persistido()
            return None

    if not creds.valid:
        return None

    return creds


def carregar_token_persistido_na_sessao():
    if st.session_state.get("drive_token_carregado_persistencia"):
        return

    st.session_state["drive_token_carregado_persistencia"] = True

    if st.session_state.get("drive_token_info"):
        return

    token_info = carregar_token_drive_persistido()
    if token_info:
        st.session_state["drive_token_info"] = token_info


def drive_conectado():
    carregar_token_persistido_na_sessao()
    creds = obter_credenciais_drive_usuario()
    return creds is not None


def conectar_drive_usuario():
    carregar_token_persistido_na_sessao()
    creds = obter_credenciais_drive_usuario()
    if not creds:
        raise RuntimeError(
            "Google Drive não conectado. Clique em 'Conectar Google Drive' antes de salvar."
        )

    return build("drive", "v3", credentials=creds, cache_discovery=False)


def desconectar_drive_usuario():
    token_info = st.session_state.get("drive_token_info")
    access_token = token_info.get("token") if token_info else None

    if access_token:
        try:
            requests.post(
                GOOGLE_REVOKE_URI,
                params={"token": access_token},
                headers={"content-type": "application/x-www-form-urlencoded"},
                timeout=15
            )
        except Exception:
            pass

    st.session_state["drive_token_info"] = None
    st.session_state["drive_oauth_state"] = None
    limpar_token_drive_persistido()
    limpar_query_params()


# =========================================
# GOOGLE GMAIL (OAuth WEB MANUAL — conta dedicada de formulários)
# =========================================
def obter_credenciais_gmail_usuario():
    token_info = st.session_state.get("gmail_token_info")
    if not token_info:
        return None

    if not token_info.get("token"):
        return None

    creds = UserCredentials(
        token=token_info.get("token"),
        refresh_token=token_info.get("refresh_token"),
        token_uri=token_info.get("token_uri"),
        client_id=token_info.get("client_id"),
        client_secret=token_info.get("client_secret"),
        scopes=token_info.get("scopes"),
    )

    if creds.expired and creds.refresh_token:
        try:
            novo_token = renovar_token_google(creds.refresh_token)

            token_atualizado = {
                "token": novo_token.get("access_token"),
                "refresh_token": token_info.get("refresh_token"),
                "token_uri": GOOGLE_TOKEN_URI,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "scopes": SCOPES_GMAIL,
            }

            st.session_state["gmail_token_info"] = token_atualizado
            salvar_token_persistido(CHAVE_TOKEN_GMAIL, token_atualizado)

            creds = UserCredentials(
                token=novo_token.get("access_token"),
                refresh_token=token_info.get("refresh_token"),
                token_uri=GOOGLE_TOKEN_URI,
                client_id=GOOGLE_CLIENT_ID,
                client_secret=GOOGLE_CLIENT_SECRET,
                scopes=SCOPES_GMAIL,
            )
        except Exception:
            st.session_state["gmail_token_info"] = None
            limpar_token_persistido(CHAVE_TOKEN_GMAIL)
            return None

    if not creds.valid:
        return None

    return creds


def carregar_token_gmail_persistido_na_sessao():
    if st.session_state.get("gmail_token_carregado_persistencia"):
        return

    st.session_state["gmail_token_carregado_persistencia"] = True

    if st.session_state.get("gmail_token_info"):
        return

    token_info = carregar_token_persistido(CHAVE_TOKEN_GMAIL)
    if token_info:
        st.session_state["gmail_token_info"] = token_info


def gmail_conectado():
    carregar_token_gmail_persistido_na_sessao()
    creds = obter_credenciais_gmail_usuario()
    return creds is not None


def conectar_gmail_usuario():
    carregar_token_gmail_persistido_na_sessao()
    creds = obter_credenciais_gmail_usuario()
    if not creds:
        raise RuntimeError(
            f"Gmail ({GMAIL_CONTA_FORMULARIOS}) não conectado. "
            "Clique em 'Conectar Gmail' antes de processar os formulários."
        )

    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def desconectar_gmail_usuario():
    token_info = st.session_state.get("gmail_token_info")
    access_token = token_info.get("token") if token_info else None

    if access_token:
        try:
            requests.post(
                GOOGLE_REVOKE_URI,
                params={"token": access_token},
                headers={"content-type": "application/x-www-form-urlencoded"},
                timeout=15
            )
        except Exception:
            pass

    st.session_state["gmail_token_info"] = None
    st.session_state["gmail_oauth_state"] = None
    limpar_token_persistido(CHAVE_TOKEN_GMAIL)
    limpar_query_params()


def obter_id_pasta_flyers(mes):
    mapa = {
        "1. Janeiro": FLYERS_JANEIRO_FAZER,
        "2. Fevereiro": FLYERS_FEVEREIRO_FAZER,
        "3. Março": FLYERS_MARCO_FAZER,
        "4. Abril": FLYERS_ABRIL_FAZER,
        "5. Maio": FLYERS_MAIO_FAZER,
        "6. Junho": FLYERS_JUNHO_FAZER,
        "7. Julho": FLYERS_JULHO_FAZER,
        "8. Agosto": FLYERS_AGOSTO_FAZER,
        "9. Setembro": FLYERS_SETEMBRO_FAZER,
        "10. Outubro": FLYERS_OUTUBRO_FAZER,
        "11. Novembro": FLYERS_NOVEMBRO_FAZER,
        "12. Dezembro": FLYERS_DEZEMBRO_FAZER,
    }
    return mapa.get(mes, "")


def obter_id_pasta_torneios(mes, agenda):
    agenda = (agenda or "").upper().strip()

    mapa_sul = {
        "1. Janeiro": TORNEIOS_JANEIRO_SUL,
        "2. Fevereiro": TORNEIOS_FEVEREIRO_SUL,
        "3. Março": TORNEIOS_MARCO_SUL,
        "4. Abril": TORNEIOS_ABRIL_SUL,
        "5. Maio": TORNEIOS_MAIO_SUL,
        "6. Junho": TORNEIOS_JUNHO_SUL,
        "7. Julho": TORNEIOS_JULHO_SUL,
        "8. Agosto": TORNEIOS_AGOSTO_SUL,
        "9. Setembro": TORNEIOS_SETEMBRO_SUL,
        "10. Outubro": TORNEIOS_OUTUBRO_SUL,
        "11. Novembro": TORNEIOS_NOVEMBRO_SUL,
        "12. Dezembro": TORNEIOS_DEZEMBRO_SUL,
    }

    mapa_norte = {
        "1. Janeiro": TORNEIOS_JANEIRO_NORTE,
        "2. Fevereiro": TORNEIOS_FEVEREIRO_NORTE,
        "3. Março": TORNEIOS_MARCO_NORTE,
        "4. Abril": TORNEIOS_ABRIL_NORTE,
        "5. Maio": TORNEIOS_MAIO_NORTE,
        "6. Junho": TORNEIOS_JUNHO_NORTE,
        "7. Julho": TORNEIOS_JULHO_NORTE,
        "8. Agosto": TORNEIOS_AGOSTO_NORTE,
        "9. Setembro": TORNEIOS_SETEMBRO_NORTE,
        "10. Outubro": TORNEIOS_OUTUBRO_NORTE,
        "11. Novembro": TORNEIOS_NOVEMBRO_NORTE,
        "12. Dezembro": TORNEIOS_DEZEMBRO_NORTE,
    }

    if agenda == "SUL":
        return mapa_sul.get(mes, "")
    if agenda == "NORTE":
        return mapa_norte.get(mes, "")
    return ""


def upload_arquivo_drive(service, uploaded_file, folder_id, nome_arquivo=None):
    if not folder_id:
        raise ValueError("ID da pasta não encontrado.")

    file_name = nome_arquivo if nome_arquivo else uploaded_file.name

    file_metadata = {
        "name": file_name,
        "parents": [folder_id]
    }

    file_bytes = uploaded_file.getvalue()
    media = MediaIoBaseUpload(
        io.BytesIO(file_bytes),
        mimetype=uploaded_file.type,
        resumable=False
    )

    file = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id,name,parents",
        supportsAllDrives=True
    ).execute()

    return file


def listar_arquivos_pasta_drive(service, folder_id):
    if not folder_id:
        raise ValueError("ID da pasta não encontrado.")

    arquivos = []
    page_token = None

    while True:
        resposta = service.files().list(
            q=f"'{folder_id}' in parents and trashed = false",
            fields="nextPageToken, files(id, name)",
            pageToken=page_token,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()

        arquivos.extend(resposta.get("files", []))
        page_token = resposta.get("nextPageToken")

        if not page_token:
            break

    return arquivos


def excluir_arquivos_pasta_drive(service, folder_id):
    arquivos = listar_arquivos_pasta_drive(service, folder_id)
    quantidade = 0

    for arquivo in arquivos:
        service.files().delete(
            fileId=arquivo["id"],
            supportsAllDrives=True
        ).execute()
        quantidade += 1

    return quantidade


# =========================================
# GMAIL — LEITURA DE E-MAILS DE FORMULÁRIO
# =========================================
class _AnexoGmail:
    """Recipiente simples para um anexo baixado do Gmail, compatível com a
    mesma interface (.name, .type, .getvalue()) que o restante do app já
    espera de um arquivo do st.file_uploader — permite reutilizar sem
    alterações as funções normalizar_imagem_para_api, upload_arquivo_drive
    e gerar_nome_flyer."""
    def __init__(self, nome, mime_type, conteudo_bytes):
        self.name = nome
        self.type = mime_type
        self._conteudo = conteudo_bytes

    def getvalue(self):
        return self._conteudo


def listar_mensagens_caixa_entrada_gmail(service):
    mensagens = []
    page_token = None

    while True:
        resposta = service.users().messages().list(
            userId="me",
            labelIds=["INBOX"],
            pageToken=page_token,
        ).execute()

        mensagens.extend(resposta.get("messages", []))
        page_token = resposta.get("nextPageToken")

        if not page_token:
            break

    return mensagens


def _decodificar_base64url(dado):
    return base64.urlsafe_b64decode(dado.encode("utf-8") + b"==")


def obter_corpo_e_anexos_gmail(service, msg_id):
    """Retorna (assunto, corpo_html, lista_anexos) de uma mensagem.
    lista_anexos: [{"filename":..., "attachment_id":..., "mime_type":...}]
    Não interpreta nada — apenas separa o texto e os anexos brutos."""
    msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()

    assunto = ""
    for header in msg.get("payload", {}).get("headers", []):
        if header.get("name", "").lower() == "subject":
            assunto = header.get("value", "")
            break

    partes_html = []
    partes_texto = []
    anexos = []

    def _percorrer(parte):
        mime_type = parte.get("mimeType", "")
        body = parte.get("body", {})
        filename = parte.get("filename", "")

        if filename:
            anexos.append({
                "filename": filename,
                "attachment_id": body.get("attachmentId"),
                "mime_type": mime_type,
            })
        elif mime_type == "text/html" and body.get("data"):
            partes_html.append(_decodificar_base64url(body["data"]).decode("utf-8", errors="ignore"))
        elif mime_type == "text/plain" and body.get("data"):
            partes_texto.append(_decodificar_base64url(body["data"]).decode("utf-8", errors="ignore"))

        for sub in parte.get("parts", []) or []:
            _percorrer(sub)

    _percorrer(msg.get("payload", {}))

    corpo_html = "\n".join(partes_html)
    corpo_texto = "\n".join(partes_texto)

    return assunto, (corpo_html or corpo_texto), anexos


def baixar_anexo_gmail(service, msg_id, attachment_id):
    anexo = service.users().messages().attachments().get(
        userId="me", messageId=msg_id, id=attachment_id
    ).execute()
    return _decodificar_base64url(anexo["data"])


def identificar_flyer_e_print(service, msg_id, anexos):
    """Classifica os dois anexos de imagem da mensagem pela ORDEM em que
    aparecem no e-mail — não pelo nome do arquivo, que muda de forma
    imprevisível conforme o cliente de e-mail (Outlook, Gmail etc.) usado
    para encaminhar.

    Convenção adotada: o usuário sempre encaminha o e-mail do formulário e
    SÓ DEPOIS anexa o print da postagem. Isso preserva o anexo original do
    formulário na posição em que já estava e acrescenta o print no final.
    Logo: primeira imagem anexada = flyer do organizador; última imagem
    anexada = print da postagem.

    Só é seguro aplicar essa regra quando a mensagem tem exatamente 2
    anexos de imagem. Qualquer outra contagem (0, 1 ou 3+) é ambígua e
    reportada como erro, sem registrar o torneio."""
    imagens = [a for a in anexos if (a.get("mime_type") or "").lower().startswith("image/")]

    if len(imagens) != 2:
        return None, None, [
            f"Esperados exatamente 2 anexos de imagem (flyer + print), mas foram "
            f"encontrados {len(imagens)}. Confira se o e-mail foi encaminhado com o "
            f"flyer original do formulário e o print anexado por último."
        ]

    anexo_flyer = imagens[0]
    anexo_print = imagens[-1]

    flyer_bytes = baixar_anexo_gmail(service, msg_id, anexo_flyer["attachment_id"])
    print_bytes = baixar_anexo_gmail(service, msg_id, anexo_print["attachment_id"])

    arquivo_flyer = _AnexoGmail(anexo_flyer["filename"], anexo_flyer["mime_type"], flyer_bytes)
    arquivo_print = _AnexoGmail(anexo_print["filename"], anexo_print["mime_type"], print_bytes)

    return arquivo_flyer, arquivo_print, []


def obter_ou_criar_label_gmail(service, nome_label):
    resposta = service.users().labels().list(userId="me").execute()
    for label in resposta.get("labels", []):
        if label.get("name") == nome_label:
            return label["id"]

    novo_label = service.users().labels().create(
        userId="me",
        body={
            "name": nome_label,
            "labelListVisibility": "labelShow",
            "messageListVisibility": "show",
        },
    ).execute()
    return novo_label["id"]


def arquivar_email_gmail(service, msg_id, label_id):
    """Remove o e-mail da caixa de entrada e move para a label de
    arquivados — nunca exclui a mensagem."""
    service.users().messages().modify(
        userId="me",
        id=msg_id,
        body={"removeLabelIds": ["INBOX"], "addLabelIds": [label_id]},
    ).execute()


# =========================================
# UTILITÁRIOS GERAIS
# =========================================
def limpar_espacos(texto):
    return " ".join(str(texto).strip().split())


def remover_acentos(texto):
    return "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    )


def _capitalizar_palavra_com_apostrofo(palavra):
    partes = palavra.split("'")
    partes_tratadas = []

    for parte in partes:
        if not parte:
            partes_tratadas.append(parte)
        else:
            parte_lower = parte.lower()
            partes_tratadas.append(parte_lower[:1].upper() + parte_lower[1:])

    return "'".join(partes_tratadas)


def capitalizar_texto_inteligente(texto):
    texto = limpar_espacos(texto)
    if not texto:
        return ""

    minusculas = {
        "de", "da", "do", "das", "dos",
        "e", "em", "na", "no", "nas", "nos"
    }

    separadores = re.split(r"(\s+|/|-)", texto)
    resultado = []
    primeira_palavra_real = True

    for parte in separadores:
        if not parte:
            resultado.append(parte)
            continue

        if re.fullmatch(r"(\s+|/|-)", parte):
            resultado.append(parte)
            continue

        parte_limpa = parte.strip()
        parte_lower = parte_limpa.lower()

        if not primeira_palavra_real and parte_lower in minusculas:
            resultado.append(parte_lower)
        else:
            if re.search(r"[A-Z]{2,}", parte):
                resultado.append(parte)
            else:
                resultado.append(_capitalizar_palavra_com_apostrofo(parte))

        primeira_palavra_real = False

    return "".join(resultado)


def normalizar_imagem_para_api(uploaded_file):
    """
    Normaliza qualquer imagem para JPEG RGB antes de enviar à API Anthropic.
    Resolve problemas com fotos de celular (HEIC, HEIF, PNG com transparência,
    imagens com perfil de cor incompatível, metadados excessivos, etc).
    Aceita: JPEG, PNG, WebP, BMP, HEIC, HEIF e outros formatos suportados pelo Pillow.
    Retorna (bytes_jpeg, "image/jpeg").
    """
    try:
        bytes_originais = uploaded_file.getvalue()

        # Tenta suporte a HEIC/HEIF via pillow-heif (instalado opcionalmente)
        nome = getattr(uploaded_file, "name", "") or ""
        ext = nome.rsplit(".", 1)[-1].lower() if "." in nome else ""
        mime = getattr(uploaded_file, "type", "") or ""
        is_heic = ext in ("heic", "heif") or "heic" in mime or "heif" in mime
        if is_heic:
            try:
                import pillow_heif
                pillow_heif.register_heif_opener()
            except ImportError:
                pass  # tenta abrir com Pillow mesmo assim

        img = Image.open(io.BytesIO(bytes_originais))

        if img.mode in ("RGBA", "LA", "P"):
            fundo = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            fundo.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
            img = fundo
        elif img.mode != "RGB":
            img = img.convert("RGB")

        # Redimensiona imagens para economizar memória no Streamlit Cloud
        # 1200px é suficiente para extração de texto pela API Claude
        MAX_DIM = 1200
        w, h = img.size
        if max(w, h) > MAX_DIM:
            escala = MAX_DIM / max(w, h)
            img = img.resize((int(w * escala), int(h * escala)), Image.LANCZOS)

        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=75, optimize=True)
        buffer.seek(0)
        return buffer.getvalue(), "image/jpeg"

    except Exception:
        bytes_originais = uploaded_file.getvalue()
        mime = uploaded_file.type or "image/jpeg"
        mapa = {
            "image/jpeg":  "image/jpeg",
            "image/jpg":   "image/jpeg",
            "image/png":   "image/png",
            "image/gif":   "image/gif",
            "image/webp":  "image/webp",
            "image/bmp":   "image/jpeg",
            "image/heic":  "image/jpeg",
            "image/heif":  "image/jpeg",
        }
        return bytes_originais, mapa.get(mime, "image/jpeg")


def imagem_para_base64(uploaded_file):
    """Converte imagem normalizada para base64 — formato exigido pela API Anthropic."""
    bytes_img, _ = normalizar_imagem_para_api(uploaded_file)
    return base64.standard_b64encode(bytes_img).decode("utf-8")


def obter_media_type(uploaded_file):
    """Após normalização, o media type é sempre image/jpeg."""
    _, media_type = normalizar_imagem_para_api(uploaded_file)
    return media_type


def normalizar_ano(ano_texto):
    ano_texto = str(ano_texto).strip()
    if not ano_texto:
        return ""
    if len(ano_texto) == 2:
        return f"20{ano_texto}"
    return ano_texto


def ano_4_para_2(ano_texto):
    ano_texto = normalizar_ano(ano_texto)
    return ano_texto[-2:] if ano_texto else ""


def gerar_nome_arquivo(uf, data_evento, cidade):
    if not uf or not data_evento or not cidade:
        return ""
    dias = extrair_dias_para_nome(data_evento)
    cidade_formatada = capitalizar_texto_inteligente(cidade)
    return f"{uf} {dias} {cidade_formatada.strip()}"


def gerar_nome_flyer(uploaded_file, nome_base):
    _, extensao = os.path.splitext(uploaded_file.name)
    extensao = extensao.lower().strip()

    if not extensao:
        mime_map = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
        }
        extensao = mime_map.get(uploaded_file.type, "")

    return f"{nome_base}{extensao}"


def gerar_fingerprint_salvamento(texto_confirmado, agenda, mes_1, mes_2, flyer_final, print_post):
    nome_flyer = flyer_final.name if flyer_final else ""
    nome_print = print_post.name if print_post else ""
    base = "||".join([
        limpar_espacos(texto_confirmado),
        agenda or "",
        mes_1 or "",
        mes_2 or "",
        nome_flyer,
        nome_print
    ])
    return hashlib.md5(base.encode("utf-8")).hexdigest()


def numero_para_coluna_excel(num):
    resultado = ""
    while num > 0:
        num, resto = divmod(num - 1, 26)
        resultado = chr(65 + resto) + resultado
    return resultado


def limpar_aba_mantendo_cabecalho(planilha, nome_aba):
    aba = planilha.worksheet(nome_aba)
    valores = aba.get_all_values()

    if len(valores) <= 1:
        return 0

    ultima_linha = len(valores)
    maior_coluna = max(len(linha) for linha in valores) if valores else 1
    ultima_coluna_letra = numero_para_coluna_excel(maior_coluna)

    intervalo = f"A2:{ultima_coluna_letra}{ultima_linha}"
    aba.batch_clear([intervalo])

    return ultima_linha - 1


def nome_mes_sem_numero(mes):
    if ". " in mes:
        return mes.split(". ", 1)[1]
    return mes


# =========================================
# ESTADOS
# =========================================
def uf_para_estado(uf):
    mapa = {
        "AC": "Acre", "AL": "Alagoas", "AP": "Amapá", "AM": "Amazonas",
        "BA": "Bahia", "CE": "Ceará", "DF": "Distrito Federal",
        "ES": "Espírito Santo", "GO": "Goiás", "MA": "Maranhão",
        "MT": "Mato Grosso", "MS": "Mato Grosso do Sul", "MG": "Minas Gerais",
        "PA": "Pará", "PB": "Paraíba", "PR": "Paraná", "PE": "Pernambuco",
        "PI": "Piauí", "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte",
        "RS": "Rio Grande do Sul", "RO": "Rondônia", "RR": "Roraima",
        "SC": "Santa Catarina", "SP": "São Paulo", "SE": "Sergipe",
        "TO": "Tocantins",
    }
    return mapa.get(uf.strip().upper(), "")


# UFs da agenda SUL (Sul + Sudeste). Todas as demais UFs válidas pertencem à
# agenda NORTE (Norte + Nordeste + Centro-Oeste).
UFS_SUL = {"SP", "RJ", "MG", "ES", "PR", "SC", "RS"}


def regiao_por_uf(uf):
    """Detecta automaticamente a agenda (SUL ou NORTE) a partir da UF."""
    uf = (uf or "").strip().upper()
    if not uf:
        return ""
    return "SUL" if uf in UFS_SUL else "NORTE"


def mes_label_por_numero(numero_str):
    """Converte um número de mês ('10') no rótulo usado pelos seletores
    da ANT ('10. Outubro'). Reaproveita a lista global _meses_global."""
    try:
        n = int(numero_str)
    except (TypeError, ValueError):
        return ""
    if 1 <= n <= 12:
        return _meses_global[n]
    return ""


def detectar_meses_por_datas(data_inicial_completa, data_final_completa):
    """A partir de duas datas completas 'dd/mm/aaaa', detecta o(s) mês(es)
    do torneio no formato usado pelos seletores da ANT.
    Retorna (mes_1_label, mes_2_label, virada_de_mes)."""
    if not data_inicial_completa or "/" not in data_inicial_completa:
        return "", "", False

    mes_ini = data_inicial_completa.split("/")[1]
    mes_fim = mes_ini
    if data_final_completa and "/" in data_final_completa:
        mes_fim = data_final_completa.split("/")[1]

    mes_1_label = mes_label_por_numero(mes_ini)

    if mes_fim != mes_ini:
        mes_2_label = mes_label_por_numero(mes_fim)
        return mes_1_label, mes_2_label, True

    return mes_1_label, "", False


def normalizar_cidade_uf(cidade_uf):
    s = limpar_espacos(cidade_uf)
    if not s:
        return ""

    s = s.replace(" - ", "/").replace(" – ", "/").replace("\\", "/")
    s = s.replace(", ", "/").replace(",", "/")

    if "/" not in s:
        return capitalizar_texto_inteligente(s)

    partes = s.rsplit("/", 1)
    cidade = capitalizar_texto_inteligente(limpar_espacos(partes[0]))
    uf = limpar_espacos(partes[1]).upper()

    if len(uf) > 2:
        mapa_reverso = {
            remover_acentos(v).lower(): k
            for k, v in {
                "AC": "Acre", "AL": "Alagoas", "AP": "Amapá", "AM": "Amazonas",
                "BA": "Bahia", "CE": "Ceará", "DF": "Distrito Federal",
                "ES": "Espírito Santo", "GO": "Goiás", "MA": "Maranhão",
                "MT": "Mato Grosso", "MS": "Mato Grosso do Sul", "MG": "Minas Gerais",
                "PA": "Pará", "PB": "Paraíba", "PR": "Paraná", "PE": "Pernambuco",
                "PI": "Piauí", "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte",
                "RS": "Rio Grande do Sul", "RO": "Rondônia", "RR": "Roraima",
                "SC": "Santa Catarina", "SP": "São Paulo", "SE": "Sergipe",
                "TO": "Tocantins",
            }.items()
        }
        uf = mapa_reverso.get(remover_acentos(uf).lower(), uf[:2].upper())

    return f"{cidade}/{uf}"


def normalizar_cidade_uf_tela2(cidade_uf):
    s = limpar_espacos(cidade_uf)
    if not s:
        return ""

    s = s.replace(" - ", "/").replace(" – ", "/").replace("\\", "/")
    s = s.replace(", ", "/").replace(",", "/")

    if "/" not in s:
        return s

    partes = s.rsplit("/", 1)
    cidade = limpar_espacos(partes[0])
    uf = limpar_espacos(partes[1]).upper()

    return f"{cidade}/{uf}"


def separar_cidade_uf(cidade_uf):
    if "/" not in cidade_uf:
        return cidade_uf.strip(), "", ""

    partes = cidade_uf.rsplit("/", 1)
    cidade = partes[0].strip()
    uf = partes[1].strip().upper()
    estado = uf_para_estado(uf)
    return cidade, uf, estado


# =========================================
# DATAS
# =========================================
def extrair_partes_data(data_texto):
    s = limpar_espacos(str(data_texto).replace("'", ""))
    padrao = r"(\d{1,2})(?:/(\d{1,2}))?(?:/(\d{2,4}))?"
    return re.findall(padrao, s)


def reconstruir_datas_completas(data_texto):
    partes = extrair_partes_data(data_texto)
    if not partes:
        return []

    ano_atual = str(datetime.now().year)

    registros = []
    for dia, mes, ano in partes:
        registros.append({
            "dia": dia.zfill(2),
            "mes": mes.zfill(2) if mes else None,
            "ano": normalizar_ano(ano) if ano else None
        })

    ano_corrente = None
    for i in range(len(registros) - 1, -1, -1):
        if registros[i]["ano"]:
            ano_corrente = registros[i]["ano"]
        else:
            registros[i]["ano"] = ano_corrente

    mes_corrente = None
    for i in range(len(registros) - 1, -1, -1):
        if registros[i]["mes"]:
            mes_corrente = registros[i]["mes"]
        else:
            registros[i]["mes"] = mes_corrente

    for r in registros:
        if not r["ano"]:
            r["ano"] = ano_atual

    datas = []
    for r in registros:
        if r["dia"] and r["mes"] and r["ano"]:
            datas.append(f'{r["dia"]}/{r["mes"]}/{r["ano"]}')

    return datas


def extrair_data_inicial_final(data_texto):
    datas = reconstruir_datas_completas(data_texto)
    if not datas:
        return "", ""
    return datas[0], datas[-1]


def normalizar_data_visual_ant(data_texto):
    s = limpar_espacos(str(data_texto))
    if not s:
        return ""

    datas = reconstruir_datas_completas(s)
    if not datas:
        return f"'{s}"

    if len(datas) == 1:
        d, m, a = datas[0].split("/")
        return f"'{d}/{m}/{a[-2:]}"

    meses = [d.split("/")[1] for d in datas]
    anos = [d.split("/")[2][-2:] for d in datas]

    if len(set(meses)) == 1 and len(set(anos)) == 1:
        mes = meses[0]
        ano2 = anos[0]
        dias = [d.split("/")[0] for d in datas]

        if len(dias) == 2:
            return f"'{dias[0]} e {dias[1]}/{mes}/{ano2}"

        return f"'{', '.join(dias[:-1])} e {dias[-1]}/{mes}/{ano2}"

    blocos = []
    grupos = []
    grupo_atual = {"mes": None, "ano2": None, "dias": []}

    for data in datas:
        dia, mes, ano = data.split("/")
        ano2 = ano[-2:]

        if grupo_atual["mes"] == mes and grupo_atual["ano2"] == ano2:
            grupo_atual["dias"].append(dia)
        else:
            if grupo_atual["dias"]:
                grupos.append(grupo_atual)
            grupo_atual = {"mes": mes, "ano2": ano2, "dias": [dia]}

    if grupo_atual["dias"]:
        grupos.append(grupo_atual)

    for i, g in enumerate(grupos):
        dias_txt = ", ".join(g["dias"])
        if i == len(grupos) - 1:
            blocos.append(f"{dias_txt}/{g['mes']}/{g['ano2']}")
        else:
            blocos.append(f"{dias_txt}/{g['mes']}")

    if len(blocos) == 2:
        return f"'{blocos[0]} e {blocos[1]}"

    return f"'{', '.join(blocos[:-1])} e {blocos[-1]}"


def formatar_data_curta(data_completa):
    if not data_completa:
        return ""
    m = re.match(r"^(\d{2})/(\d{2})/(\d{4})$", data_completa)
    if not m:
        return data_completa
    return f"{m.group(1)}/{m.group(2)}/{m.group(3)[-2:]}"


def extrair_dias_para_nome(data_texto):
    datas = reconstruir_datas_completas(data_texto)
    if not datas:
        return ""
    dias = [d.split("/")[0] for d in datas]
    return " ".join(dias)


# =========================================
# CATEGORIAS
# =========================================
def aplicar_maiusculas_niveis(texto):
    texto = re.sub(
        r"\b([a-z])\+([a-z])\b",
        lambda m: f"{m.group(1).upper()}+{m.group(2).upper()}",
        texto,
        flags=re.IGNORECASE
    )

    texto = re.sub(
        r"\b(a|b|c|d)\b",
        lambda m: m.group(1).upper(),
        texto,
        flags=re.IGNORECASE
    )

    return texto


def normalizar_categoria_individual(cat):
    cat = limpar_espacos(cat)
    if not cat:
        return ""
    # Capitaliza inteligentemente (primeira letra maiúscula, demais minúsculas,
    # exceto preposições) antes de aplicar as regras de níveis (A, B, A+B, etc.)
    cat = capitalizar_texto_inteligente(cat)
    cat = aplicar_maiusculas_niveis(cat)
    return cat


def padronizar_categorias(texto):
    texto = str(texto).replace("Categorias:", "").strip()

    if not texto:
        return "não encontrado"

    texto = texto.replace(" + ", "+")
    texto = re.sub(r"\s*/\s*", ", ", texto)
    texto = re.sub(r"\s*;\s*", ", ", texto)
    texto = re.sub(r"\s+[–-]\s+", ", ", texto)

    partes = [p.strip() for p in texto.split(",") if p.strip()]

    if not partes:
        return "não encontrado"

    partes = [normalizar_categoria_individual(p) for p in partes if p]

    if not partes:
        return "não encontrado"

    if len(partes) == 1:
        return partes[0]

    return ", ".join(partes[:-1]) + " e " + partes[-1]


# =========================================
# CONTATO / INSTAGRAM
# =========================================
def normalizar_contato(contato):
    contato = limpar_espacos(contato)
    if not contato:
        return "não encontrado"

    telefone = re.search(r"\(?\d{2}\)?\s*\d{4,5}[-\s]?\d{4}", contato)
    if telefone:
        numeros = re.sub(r"\D", "", telefone.group(0))
        if len(numeros) == 11:
            return f"({numeros[:2]}) {numeros[2:7]}-{numeros[7:]}"
        if len(numeros) == 10:
            return f"({numeros[:2]}) {numeros[2:6]}-{numeros[6:]}"
        return telefone.group(0)

    instagram = re.search(r"@\w[\w\.]*", contato)
    if instagram:
        return instagram.group(0)

    return contato


def extrair_instagrams_de_texto(texto):
    encontrados = re.findall(r"@\w[\w\.]*", str(texto))
    vistos = set()
    resultado = []

    for item in encontrados:
        chave = item.lower()
        if chave not in vistos:
            vistos.add(chave)
            resultado.append(item)

    return resultado


def formatar_instagrams_bloco(instagrams):
    if not instagrams:
        return "Instagram: não encontrado"
    return "\n".join(instagrams)


# =========================================
# EXTRAÇÃO DE CAMPOS
# =========================================
def extrair_campos_confirmados(texto):
    data = re.search(r"Data:\s*(.*)", texto, re.IGNORECASE)
    torneio = re.search(r"Torneio:\s*(.*)", texto, re.IGNORECASE)
    cidade = re.search(r"Cidade.*:\s*(.*)", texto, re.IGNORECASE)
    local = re.search(r"Local:\s*(.*)", texto, re.IGNORECASE)
    categorias = re.search(r"Categorias:\s*(.*)", texto, re.IGNORECASE)
    contato = re.search(r"Contato:\s*(.*)", texto, re.IGNORECASE)

    return {
        "data": limpar_espacos(data.group(1)) if data else "",
        "torneio": limpar_espacos(torneio.group(1)) if torneio else "",
        "cidade_uf": limpar_espacos(cidade.group(1)) if cidade else "",
        "local": limpar_espacos(local.group(1)) if local else "",
        "categorias": limpar_espacos(categorias.group(1)) if categorias else "",
        "contato": limpar_espacos(contato.group(1)) if contato else "",
    }


def extrair_campos_lote(texto):
    instagrams = re.search(r"Instagrams:\s*(.*)", texto, re.IGNORECASE)
    data = re.search(r"Data:\s*(.*)", texto, re.IGNORECASE)
    torneio = re.search(r"Torneio:\s*(.*)", texto, re.IGNORECASE)
    cidade = re.search(r"Cidade.*:\s*(.*)", texto, re.IGNORECASE)
    local = re.search(r"Local:\s*(.*)", texto, re.IGNORECASE)
    categorias = re.search(r"Categorias:\s*(.*)", texto, re.IGNORECASE)
    contato = re.search(r"Contato:\s*(.*)", texto, re.IGNORECASE)

    contato_val = limpar_espacos(contato.group(1)) if contato else ""
    igs = extrair_instagrams_de_texto(instagrams.group(1)) if instagrams else []

    # Fallback: se o campo Instagrams estiver vazio mas o Contato for um @perfil,
    # usa o contato como Instagram de destino para o direct
    if not igs and contato_val.startswith("@"):
        igs = [contato_val]

    # Garante que o @perfil do contato esteja sempre na lista de instagrams
    # (pode estar no contato mas não ter aparecido no campo Instagrams)
    if contato_val.startswith("@"):
        contato_lower = contato_val.lower()
        if not any(ig.lower() == contato_lower for ig in igs):
            igs.insert(0, contato_val)

    return {
        "instagrams": igs,
        "data": limpar_espacos(data.group(1)) if data else "",
        "torneio": limpar_espacos(torneio.group(1)) if torneio else "",
        "cidade_uf": limpar_espacos(cidade.group(1)) if cidade else "",
        "local": limpar_espacos(local.group(1)) if local else "",
        "categorias": limpar_espacos(categorias.group(1)) if categorias else "",
        "contato": contato_val,
    }


# =========================================
# EXTRAÇÃO LITERAL DE CAMPOS — FORMULÁRIO (SEM IA)
# =========================================
# Mapeia o rótulo exato (normalizado) que aparece no e-mail de notificação
# do formulário para a chave interna correspondente. A extração é sempre
# literal: lê o texto da célula ao lado do rótulo, sem interpretar,
# resumir ou compor a partir de outros campos.
CAMPOS_FORMULARIO_ANT = {
    "nome do evento": "torneio",
    "data do evento": "data",
    "cidade e estado": "cidade_uf",
    "nome do local do evento": "local",
    "categorias": "categorias",
    "contato para inscricoes": "contato",
    "instagram do torneio ou arena": "instagram",
}


def _normalizar_rotulo_formulario(texto):
    texto = remover_acentos(str(texto)).lower()
    # Remove marcações de campo obrigatório (ex.: "Data do evento:*") e
    # pontuação do rótulo, mantendo apenas o texto do nome do campo.
    texto = texto.replace(":", "").replace("*", "").strip()
    texto = re.sub(r"\s+", " ", texto)
    return texto


def extrair_campos_formulario_html(corpo_html):
    """Lê literalmente, célula a célula, o valor ao lado de cada rótulo
    conhecido do e-mail de notificação do formulário (BitForm/WordPress).
    Nunca infere, completa ou adivinha um campo ausente — um rótulo não
    encontrado simplesmente resulta em string vazia."""
    valores = {}

    if corpo_html:
        soup = BeautifulSoup(corpo_html, "html.parser")
        for linha in soup.find_all("tr"):
            celulas = linha.find_all(["td", "th"])
            if len(celulas) < 2:
                continue
            rotulo = _normalizar_rotulo_formulario(celulas[0].get_text(" ", strip=True))
            chave = CAMPOS_FORMULARIO_ANT.get(rotulo)
            if chave and chave not in valores:
                valores[chave] = limpar_espacos(celulas[1].get_text(" ", strip=True))

    return {
        "torneio": valores.get("torneio", ""),
        "data": valores.get("data", ""),
        "cidade_uf": valores.get("cidade_uf", ""),
        "local": valores.get("local", ""),
        "categorias": valores.get("categorias", ""),
        "contato": valores.get("contato", ""),
        "instagram": valores.get("instagram", ""),
    }


def validar_e_montar_torneio_formulario(campos_brutos):
    """Valida os campos extraídos literalmente do e-mail de formulário e
    monta os dados necessários para o registro — reaproveitando as mesmas
    funções que a Tela 3 (Registro final do torneio) já usa hoje, para
    garantir o mesmo comportamento de formatação e nomeação de arquivo.

    Regra absoluta: nenhuma inconsistência é corrigida por suposição.
    Qualquer campo ausente, ambíguo ou não reconhecido gera um erro e o
    torneio NÃO é registrado — o e-mail correspondente permanece na caixa
    de entrada para correção manual."""
    erros = []

    torneio = campos_brutos["torneio"]
    if not torneio:
        erros.append("Campo 'Nome do evento' ausente ou vazio.")

    if not campos_brutos["data"]:
        erros.append("Campo 'Data do evento' ausente ou vazio.")

    cidade_uf = ""
    cidade = ""
    uf = ""
    estado_extenso = ""
    if not campos_brutos["cidade_uf"]:
        erros.append("Campo 'Cidade e Estado' ausente ou vazio.")
    else:
        cidade_uf = normalizar_cidade_uf_tela2(campos_brutos["cidade_uf"])
        if "/" not in cidade_uf:
            erros.append(
                f"Campo 'Cidade e Estado' veio sem a sigla do estado "
                f"(\"{campos_brutos['cidade_uf']}\") — é necessário o formato 'Cidade/UF'. "
                "Não será feita nenhuma suposição da UF a partir do nome da cidade."
            )
        else:
            cidade, uf, estado_extenso = separar_cidade_uf(cidade_uf)
            if not estado_extenso:
                erros.append(f"UF \"{uf}\" não reconhecida no campo 'Cidade e Estado'.")

    local_evento = campos_brutos["local"]
    if not local_evento:
        erros.append("Campo 'Nome do local do evento' ausente ou vazio.")

    categorias = campos_brutos["categorias"]
    if not categorias:
        erros.append("Campo 'Categorias' ausente ou vazio.")

    contato = normalizar_contato(campos_brutos["contato"])
    if contato == "não encontrado":
        if campos_brutos["instagram"]:
            contato = campos_brutos["instagram"].strip()
        else:
            erros.append("Campo 'Contato para inscrições' ausente ou vazio.")

    data_inicial_completa, data_final_completa = ("", "")
    if campos_brutos["data"]:
        data_inicial_completa, data_final_completa = extrair_data_inicial_final(campos_brutos["data"])
        if not data_inicial_completa or not data_final_completa:
            erros.append(f"Não foi possível interpretar a data \"{campos_brutos['data']}\" no formato esperado.")

    data_evento_visual = normalizar_data_visual_ant(campos_brutos["data"]) if campos_brutos["data"] else ""
    data_inicial = formatar_data_curta(data_inicial_completa) if data_inicial_completa else ""
    data_final = formatar_data_curta(data_final_completa) if data_final_completa else ""

    agenda = ""
    mes_1, mes_2, virada_mes = "", "", False
    if estado_extenso and uf:
        agenda = regiao_por_uf(uf)
        if not agenda:
            erros.append(f"Não foi possível determinar a agenda (SUL/NORTE) a partir da UF \"{uf}\".")
    if data_inicial_completa:
        mes_1, mes_2, virada_mes = detectar_meses_por_datas(data_inicial_completa, data_final_completa)
        if not mes_1:
            erros.append(f"Não foi possível determinar o mês do torneio a partir da data \"{campos_brutos['data']}\".")

    nome_arquivo = ""
    if uf and campos_brutos["data"] and cidade:
        nome_arquivo = gerar_nome_arquivo(uf, campos_brutos["data"], cidade)
    if not erros and not nome_arquivo:
        erros.append("Não foi possível gerar o nome automático do arquivo.")

    dados = {
        "torneio": torneio,
        "cidade_uf": cidade_uf,
        "estado_extenso": estado_extenso,
        "local_evento": local_evento,
        "categorias": categorias,
        "contato": contato,
        "data_evento_visual": data_evento_visual,
        "data_inicial": data_inicial,
        "data_final": data_final,
        "agenda": agenda,
        "mes_1": mes_1,
        "mes_2": mes_2,
        "virada_mes": virada_mes,
        "nome_arquivo": nome_arquivo,
    }

    return dados, erros


def processar_formularios_recebidos(gmail_service, drive_service, client_gs):
    """Percorre todos os e-mails da caixa de entrada da conta dedicada de
    formulários, valida e registra cada torneio (planilha + Drive, com a
    mesma lógica de salvamento da Tela 3), e arquiva apenas os e-mails
    processados com sucesso. E-mails com qualquer inconsistência ficam na
    caixa de entrada para correção e nova tentativa.

    Retorna uma lista de relatórios: {"assunto", "status", "torneio", "motivo"}.
    """
    relatorio = []

    label_id = obter_ou_criar_label_gmail(gmail_service, GMAIL_LABEL_ARQUIVADOS)
    mensagens = listar_mensagens_caixa_entrada_gmail(gmail_service)

    for msg_ref in mensagens:
        msg_id = msg_ref["id"]
        assunto, corpo_html, anexos = obter_corpo_e_anexos_gmail(gmail_service, msg_id)

        campos_brutos = extrair_campos_formulario_html(corpo_html)
        dados, erros_campos = validar_e_montar_torneio_formulario(campos_brutos)

        flyer_final, print_post, erros_anexos = identificar_flyer_e_print(gmail_service, msg_id, anexos)

        erros = erros_campos + erros_anexos

        if erros:
            relatorio.append({
                "assunto": assunto,
                "status": "PENDENTE",
                "torneio": dados.get("torneio") or "(não identificado)",
                "motivo": " | ".join(erros),
            })
            continue

        agenda = dados["agenda"]
        mes_1 = dados["mes_1"]
        mes_2 = dados["mes_2"]
        virada_mes = dados["virada_mes"]
        nome_arquivo = dados["nome_arquivo"]

        linha_macro = [
            "",
            dados["data_evento_visual"],
            dados["data_inicial"],
            dados["data_final"],
            dados["torneio"],
            dados["cidade_uf"],
            dados["estado_extenso"],
            dados["local_evento"],
            dados["categorias"],
            dados["contato"],
            "",
        ]

        status_print = "❌"
        status_sheet = "❌"
        status_flyer = "❌"
        erro_print = ""
        erro_sheet = ""
        erro_flyer = ""
        nome_flyer_final = ""

        try:
            nome_print_final = gerar_nome_flyer(print_post, f"{nome_arquivo} - PRINT")
            pasta_torneios_mes_1 = obter_id_pasta_torneios(mes_1, agenda)
            upload_arquivo_drive(drive_service, print_post, pasta_torneios_mes_1, nome_arquivo=nome_print_final)

            if virada_mes and mes_2 and mes_2 != mes_1:
                pasta_torneios_mes_2 = obter_id_pasta_torneios(mes_2, agenda)
                upload_arquivo_drive(drive_service, print_post, pasta_torneios_mes_2, nome_arquivo=nome_print_final)

            status_print = "✅"
        except Exception as e:
            erro_print = repr(e)

        try:
            planilha = obter_planilha_por_agenda(client_gs, agenda)
            salvar_linha_na_aba(planilha, mes_1, linha_macro)

            if virada_mes and mes_2 and mes_2 != mes_1:
                salvar_linha_na_aba(planilha, mes_2, linha_macro)

            status_sheet = "✅"
        except Exception as e:
            erro_sheet = repr(e)

        try:
            nome_flyer_final = gerar_nome_flyer(flyer_final, nome_arquivo)
            pasta_flyers_mes_1 = obter_id_pasta_flyers(mes_1)
            upload_arquivo_drive(drive_service, flyer_final, pasta_flyers_mes_1, nome_arquivo=nome_flyer_final)

            if virada_mes and mes_2 and mes_2 != mes_1:
                pasta_flyers_mes_2 = obter_id_pasta_flyers(mes_2)
                upload_arquivo_drive(drive_service, flyer_final, pasta_flyers_mes_2, nome_arquivo=nome_flyer_final)

            status_flyer = "✅"
        except Exception as e:
            erro_flyer = repr(e)

        erros_consolidados = []
        if erro_print:
            erros_consolidados.append(f"PRINT: {erro_print}")
        if erro_sheet:
            erros_consolidados.append(f"GOOGLE_SHEET: {erro_sheet}")
        if erro_flyer:
            erros_consolidados.append(f"FLYER: {erro_flyer}")

        status_final = "SUCESSO" if (
            status_print == "✅" and status_sheet == "✅" and status_flyer == "✅"
        ) else "ERRO"

        try:
            registrar_log(
                client_gs=client_gs,
                torneio=dados["torneio"],
                cidade=dados["cidade_uf"],
                data_evento=dados["data_evento_visual"],
                agenda=agenda,
                mes_1=mes_1,
                mes_2=mes_2,
                nome_flyer=nome_flyer_final if nome_flyer_final else nome_arquivo,
                status=f"{status_final} (Formulário)",
                erro=" | ".join(erros_consolidados),
            )
        except Exception:
            pass

        if status_final == "SUCESSO":
            try:
                arquivar_email_gmail(gmail_service, msg_id, label_id)
            except Exception as e:
                erros_consolidados.append(f"ARQUIVAMENTO_EMAIL: {repr(e)}")

            relatorio.append({
                "assunto": assunto,
                "status": "REGISTRADO",
                "torneio": dados["torneio"],
                "motivo": "",
            })
        else:
            relatorio.append({
                "assunto": assunto,
                "status": "PENDENTE",
                "torneio": dados["torneio"],
                "motivo": " | ".join(erros_consolidados),
            })

    return relatorio


def montar_mensagem(texto):
    campos = extrair_campos_confirmados(texto)

    data_visual = normalizar_data_visual_ant(campos["data"])
    cidade_uf = normalizar_cidade_uf(campos["cidade_uf"])
    categorias = padronizar_categorias(campos["categorias"])
    contato = normalizar_contato(campos["contato"])
    torneio = capitalizar_texto_inteligente(campos["torneio"])
    local = capitalizar_texto_inteligente(campos["local"])

    return (
        f"Data: {data_visual or 'não encontrado'}\n"
        f"Torneio: {torneio or 'não encontrado'}\n"
        f"Cidade/ES: {cidade_uf or 'não encontrado'}\n"
        f"Local: {local or 'não encontrado'}\n"
        f"Categorias: {categorias}\n"
        f"Contato: {contato}"
    )


def quebrar_telefone(contato: str) -> str:
    """
    Insere um espaço de largura zero (U+200B) entre os dígitos do telefone
    para evitar que o Instagram/WhatsApp gere link clicável automaticamente.
    Aplicado apenas quando o contato parece ser um número de telefone.
    """
    import re as _re
    # Detecta sequências de dígitos com possíveis separadores (espaço, hífen, parênteses)
    def inserir_zwsp(m):
        return "​".join(m.group(0))
    # Aplica apenas em blocos de dígitos dentro do contato
    return _re.sub(r"\d+", inserir_zwsp, contato)


def montar_bloco_informacoes_lote(campos):
    data_visual = normalizar_data_visual_ant(campos["data"])
    cidade_uf = normalizar_cidade_uf(campos["cidade_uf"])
    categorias = padronizar_categorias(campos["categorias"])
    contato_raw = normalizar_contato(campos["contato"])
    torneio = capitalizar_texto_inteligente(campos["torneio"])
    local = capitalizar_texto_inteligente(campos["local"])

    # Quebra o número para evitar link automático do WhatsApp no Instagram
    contato = quebrar_telefone(contato_raw) if contato_raw else "não encontrado"

    return (
        f"Data: {data_visual or 'não encontrado'}\n"
        f"Torneio: {torneio or 'não encontrado'}\n"
        f"Cidade/ES: {cidade_uf or 'não encontrado'}\n"
        f"Local: {local or 'não encontrado'}\n"
        f"Categorias: {categorias or 'não encontrado'}\n"
        f"Contato: {contato}"
    )


def listar_pendencias_lote(campos):
    bloco = {
        "Data": normalizar_data_visual_ant(campos["data"]) if campos["data"] else "não encontrado",
        "Torneio": capitalizar_texto_inteligente(campos["torneio"]) if campos["torneio"] else "não encontrado",
        "Cidade/ES": normalizar_cidade_uf(campos["cidade_uf"]) if campos["cidade_uf"] else "não encontrado",
        "Local": capitalizar_texto_inteligente(campos["local"]) if campos["local"] else "não encontrado",
        "Categorias": padronizar_categorias(campos["categorias"]) if campos["categorias"] else "não encontrado",
        "Contato": normalizar_contato(campos["contato"]) if campos["contato"] else "não encontrado",
    }

    return [
        campo for campo, valor in bloco.items()
        if not valor or valor.strip().lower() in ("não encontrado", "nao encontrado", "não encontrada", "")
    ]


def extrair_mes_do_campo_data(data_val: str) -> str:
    """Extrai o nome do mês a partir da data visual (ex: '10/06/26' → 'junho')."""
    meses = {
        "01": "janeiro", "02": "fevereiro", "03": "março",
        "04": "abril", "05": "maio", "06": "junho",
        "07": "julho", "08": "agosto", "09": "setembro",
        "10": "outubro", "11": "novembro", "12": "dezembro",
    }
    # Tenta extrair o mês do formato dd/mm/yy ou dd/mm/yyyy
    m = re.search(r"\d{1,2}/(\d{2})/\d{2,4}", data_val or "")
    if m:
        return meses.get(m.group(1).zfill(2), "")
    return ""


def montar_paragrafos_direct(campos: dict, tipo: str) -> list:
    """
    Monta a mensagem de direct como lista de parágrafos separados.
    tipo: "recorrente" ou "novo"
    Retorna lista de strings — cada item é um parágrafo para envio separado.
    """
    bloco_info = montar_bloco_informacoes_lote(campos)
    pendencias = listar_pendencias_lote(campos)
    mes = extrair_mes_do_campo_data(campos.get("data", ""))
    mes_txt = f" de {mes}" if mes else ""

    if tipo == "novo":
        abertura = [
            "Fala pessoal!\nTudo bem?",
            "Trabalhamos com a divulgação de torneios de futevôlei de todo o Brasil, "
            "através da Agenda Nacional de Torneios.",
            "Gostariam de divulgar o torneio de vocês na nossa página de forma GRATUITA?",
        ]
    else:  # recorrente
        abertura = [
            "Fala pessoal!\nTudo bem?",
            f"Bora divulgar o torneio{mes_txt} na Agenda Nacional de Torneios?",
        ]

    if not pendencias:
        return abertura + [
            "Preciso apenas que me envie a arte de divulgação do evento para "
            "podermos repostá-la na nossa página e confirme as informações abaixo:",
            bloco_info,
        ]
    else:
        if len(pendencias) == 1:
            falta_txt = f"Falta apenas: {pendencias[0].lower()}."
        else:
            itens = ", ".join(p.lower() for p in pendencias[:-1])
            falta_txt = f"Faltam apenas: {itens} e {pendencias[-1].lower()}."

        return abertura + [
            f"Já peguei quase todas as informações necessárias do post de vocês. {falta_txt}",
            bloco_info,
            "Agradeço se puder me enviar essa informação e a arte de divulgação "
            "do evento para repostarmos aqui!",
        ]


def montar_mensagem_direct_lote(campos, tipo="novo"):
    """Mantém compatibilidade com o fluxo da Tela 2 (texto consolidado)."""
    perfis_txt = formatar_instagrams_bloco(campos.get("instagrams", []))
    paragrafos = montar_paragrafos_direct(campos, tipo)
    corpo = "\n\n".join(paragrafos)
    return f"{perfis_txt}\n\n{corpo}"


# =========================================
# PROMPT DE EXTRAÇÃO (MELHORADO)
# =========================================
PROMPT_SISTEMA_EXTRACAO = """Você é um assistente especializado em extrair informações de torneios de futevôlei a partir de prints de divulgação do Instagram.

Suas respostas devem ser precisas, sem invenções, e sempre no formato exato solicitado.

Regras gerais:
- Se um campo não puder ser identificado com segurança, escreva exatamente: não encontrado
- Nunca invente ou suponha informações ausentes
- Nunca una dois torneios diferentes em uma mesma extração
- Priorize sempre informações explícitas sobre inferências
- Em caso de conflito entre fontes, o texto complementar do usuário prevalece sobre a imagem"""


def prompt_extracao_individual(informacao_complementar: str, ano_2: str) -> str:
    return f"""Você está operando no modo fixo: 1 print = 1 torneio.

Extraia apenas UM torneio das imagens enviadas.

Prioridade obrigatória das fontes:
1. Texto complementar do usuário (prevalece sobre tudo)
2. Imagens enviadas

Extraia exatamente estes campos:
- Data
- Torneio
- Cidade/ES
- Local
- Categorias
- Contato

Regras obrigatórias:
- Se houver número de telefone ou WhatsApp visível, ele tem prioridade absoluta como contato.
- Se não houver telefone, use o @perfil do Instagram do organizador como contato.
- Se um campo não for encontrado, escreva: não encontrado
- Não invente informações. Não una dois torneios.
- Padronize a data no formato ANT. Exemplos:
  10/04/{ano_2}
  10 e 11/04/{ano_2}
  10, 11 e 12/04/{ano_2}
  30, 31/03 e 01/04/{ano_2}
- Se o ano não estiver informado na imagem, use OBRIGATORIAMENTE o ano {ano_2} (é o ano atual — nunca use anos anteriores como 2025).
- Cidade/ES deve sempre estar no formato Cidade/UF (sigla do estado com 2 letras).
- Preserve categorias compostas com +, por exemplo B+C e A+B.
- No nome do torneio, cidade e local, use capitalização inteligente:
  mantenha minúsculas internas em palavras como de, da, do, dos, das, e, em, na, no, nas e nos.

Texto complementar do usuário:
{informacao_complementar.strip() if informacao_complementar.strip() else "nenhum"}

Responda APENAS com o bloco abaixo, sem texto antes ou depois:

Data:
Torneio:
Cidade/ES:
Local:
Categorias:
Contato:"""


def prompt_extracao_lote(ano_2: str) -> str:
    return f"""Você está operando no modo fixo: 1 print = 1 torneio.

Extraia apenas UM torneio desta imagem.

Extraia exatamente estes campos:
- Instagrams
- Data
- Torneio
- Cidade/ES
- Local
- Categorias
- Contato

Regras obrigatórias:
- Em Instagrams, liste TODOS os @perfis de Instagram visíveis na postagem (colabs incluídos), separados por espaço.
- Se não encontrar nenhum perfil, escreva: não encontrado
- Se houver número de telefone ou WhatsApp visível, ele tem prioridade absoluta como contato.
- Se não houver telefone, use um @perfil do Instagram como contato.
- Se um campo não for encontrado, escreva: não encontrado
- Não invente informações. Não una dois torneios.
- Padronize a data no formato ANT. Exemplos:
  10/04/{ano_2}
  10 e 11/04/{ano_2}
  10, 11 e 12/04/{ano_2}
  30, 31/03 e 01/04/{ano_2}
- Se o ano não estiver informado na imagem, use OBRIGATORIAMENTE o ano {ano_2} (é o ano atual — nunca use anos anteriores como 2025).
- Cidade/ES deve sempre estar no formato Cidade/UF (sigla do estado com 2 letras).
- Preserve categorias compostas com +, por exemplo B+C e A+B.
- No nome do torneio, cidade e local, use capitalização inteligente:
  mantenha minúsculas internas em palavras como de, da, do, dos, das, e, em, na, no, nas e nos.

Responda APENAS com o bloco abaixo, sem texto antes ou depois:

Instagrams:
Data:
Torneio:
Cidade/ES:
Local:
Categorias:
Contato:"""


# =========================================
# CHAMADAS À API DO CLAUDE (ANTHROPIC)
# =========================================
def extrair_texto_1_torneio(imagens: list, informacao_complementar: str = "") -> str:
    """Extração individual: múltiplas imagens do mesmo torneio."""
    ano_2 = str(datetime.now().year)[-2:]

    conteudo = []

    # Adiciona todas as imagens
    for img in imagens:
        conteudo.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": obter_media_type(img),
                "data": imagem_para_base64(img),
            },
        })

    # Adiciona o prompt textual após as imagens
    conteudo.append({
        "type": "text",
        "text": prompt_extracao_individual(informacao_complementar, ano_2),
    })

    response = claude.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=512,
        system=PROMPT_SISTEMA_EXTRACAO,
        messages=[{"role": "user", "content": conteudo}],
    )

    from urllib.parse import unquote
    return unquote(response.content[0].text)


def extrair_texto_lote_1_torneio(imagem) -> str:
    """Extração em lote: uma imagem por chamada."""
    ano_2 = str(datetime.now().year)[-2:]

    conteudo = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": obter_media_type(imagem),
                "data": imagem_para_base64(imagem),
            },
        },
        {
            "type": "text",
            "text": prompt_extracao_lote(ano_2),
        },
    ]

    response = claude.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=512,
        system=PROMPT_SISTEMA_EXTRACAO,
        messages=[{"role": "user", "content": conteudo}],
    )

    from urllib.parse import unquote
    return unquote(response.content[0].text)


def gerar_mensagem_organizadores_claude(
    texto_sul: str,
    texto_norte: str,
    link_sul: str,
    link_norte: str,
    total_mes1: str,
    total_mes2: str,
    nome_mes1: str,
    nome_mes2: str,
) -> str:
    """
    Gera a mensagem consolidada para a lista de transmissão de organizadores.
    Substitui o agente do ChatGPT — integrado diretamente no app.
    """
    prompt = f"""Você é um assistente da Agenda Nacional de Torneios (ANT) de futevôlei.

Gere uma mensagem consolidada para enviar à lista de transmissão de ORGANIZADORES DE TORNEIOS, informando sobre a atualização da semana.

Use EXATAMENTE o padrão abaixo como referência de formato e tom. Adapte apenas os dados variáveis.

Padrão de referência:
---
Bom dia, organizadores de torneios!

A Agenda Nacional de Torneios acaba de ser atualizada, com X novos eventos, dos estados abaixo:

Regiões Sul e Sudeste
📍SP. 9 novos torneios
📍MG. 6 novos torneios
📍RS. 4 novos torneios

Regiões Norte, Nordeste e Centro-Oeste
📍DF. 4 novos torneios
📍BA. 2 novos torneios
📍GO. 1 novo torneio

Já são 189 TORNEIOS do mês de março e 69 TORNEIOS do mês de abril divulgados até o momento na ANT.

Clique nos links abaixo e confira os torneios da sua região! 👇

Agenda Sul e Sudeste
[link_sul]

Regiões Norte, Nordeste e Centro-Oeste
[link_norte]
---

Dados para gerar a mensagem desta semana:

TOTAIS POR ESTADO — REGIÃO SUL/SUDESTE (novos torneios esta atualização):
{texto_sul}

TOTAIS POR ESTADO — REGIÃO NORTE/NORDESTE/CENTRO-OESTE (novos torneios esta atualização):
{texto_norte}

Total acumulado: {total_mes1} torneios de {nome_mes1} e {total_mes2} torneios de {nome_mes2}.

Link agenda Sul e Sudeste: {link_sul}
Link agenda Norte/Nordeste/Centro-Oeste: {link_norte}

Instruções:
- Conte os novos torneios de cada estado a partir dos dados acima.
- Liste apenas estados que têm torneios NOVOS nesta atualização.
- Ordene por quantidade de novos torneios (maior para menor) dentro de cada região.
- Use o emoji 📍 antes de cada estado.
- Não inclua estados com zero novos torneios.
- Mantenha o tom informal e animado do padrão.
- Responda APENAS com a mensagem pronta, sem explicações ou comentários."""

    response = claude.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    from urllib.parse import unquote
    return unquote(response.content[0].text)


# =========================================
# UI
# =========================================
st.title("🏆 APP ANT v3")
st.caption("Powered by Claude (Anthropic) · Nova conta Google Drive pronta para configurar")

# Processa callback OAuth e carrega token — dentro da UI para evitar
# chamadas de rede durante o startup (causa segfault no Streamlit Cloud)
processar_callback_oauth_google()
carregar_token_persistido_na_sessao()
carregar_token_gmail_persistido_na_sessao()

# Variáveis globais usadas em múltiplas telas
_meses_global = [
    "",
    "1. Janeiro", "2. Fevereiro", "3. Março", "4. Abril",
    "5. Maio", "6. Junho", "7. Julho", "8. Agosto",
    "9. Setembro", "10. Outubro", "11. Novembro", "12. Dezembro"
]
meses_validos = _meses_global[1:]

_aba_ativa = st.radio(
    "Navegação",
    options=[
        "Extração individual",
        "Extração em lote",
        "Registro final do torneio",
        "Msg. Organizadores",
        "Torneios recebidos (Formulário)",
        "Limpeza pós-atualização",
    ],
    horizontal=True,
    label_visibility="collapsed",
    key="aba_ativa",
)
st.divider()

class _FakeAba:
    def __init__(self, nome): self.nome = nome
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def __bool__(self): return _aba_ativa == self.nome

class _AbaContext:
    def __init__(self, nome):
        self.nome = nome
        self._ativo = (_aba_ativa == nome)
    def __enter__(self):
        return self
    def __exit__(self, *a):
        pass

def _make_aba(nome):
    return _AbaContext(nome)

aba1 = _make_aba("Extração individual")
aba2 = _make_aba("Extração em lote")
aba3 = _make_aba("Registro final do torneio")
aba4 = _make_aba("Msg. Organizadores")
aba6 = _make_aba("Torneios recebidos (Formulário)")
aba5 = _make_aba("Limpeza pós-atualização")

# =========================================
# TELA 1 — EXTRAÇÃO INDIVIDUAL
# =========================================
if _aba_ativa == aba1.nome:
    st.subheader("Tela 1 — Extração individual")
    st.write("Modo 1 print = 1 torneio. Envie um ou mais prints do mesmo torneio.")

    st.divider()

    print_principal = st.file_uploader(
        "Upload do PRINT principal",
        type=["jpg", "jpeg", "png", "webp", "heic", "heif", "bmp"],
        key="print_principal"
    )

    prints_adicionais = st.file_uploader(
        "Uploads adicionais do mesmo torneio (opcional)",
        type=["jpg", "jpeg", "png", "webp", "heic", "heif", "bmp"],
        accept_multiple_files=True,
        key="prints_adicionais"
    )

    informacao_complementar = st.text_area(
        "Texto complementar / legenda / observações (opcional)",
        placeholder="Ex.: Local: Arena Verão | Contato: @arenaverao | Cidade: Sorocaba/SP",
        height=120,
        key="info_complementar"
    )

    st.divider()

    if st.button("Extrair informações", key="btn_extrair"):
        if print_principal is None:
            st.error("Envie o print principal.")
        else:
            with st.spinner("Analisando com Claude..."):
                imagens = [print_principal] + (prints_adicionais if prints_adicionais else [])
                resultado = extrair_texto_1_torneio(
                    imagens=imagens,
                    informacao_complementar=informacao_complementar
                )
            mensagem = decodificar_texto(montar_mensagem(decodificar_texto(resultado)))
            st.session_state["resultado_t1"] = mensagem
            st.session_state["resultado_t1_edit"] = mensagem
            # Libera resultado da API da memória
            del resultado, mensagem

    # Exibe resultado sempre que existir no session_state
    if st.session_state.get("resultado_t1"):
        # Inicializa chave de edição separada da chave de armazenamento
        if "resultado_t1_edit" not in st.session_state or st.session_state.get("_resultado_t1_base") != st.session_state["resultado_t1"]:
            st.session_state["resultado_t1_edit"] = st.session_state["resultado_t1"]
            st.session_state["_resultado_t1_base"] = st.session_state["resultado_t1"]
        st.divider()
        st.subheader("Mensagem pronta")
        st.text_area(
            "Edite se necessário",
            height=260,
            key="resultado_t1_edit",
        )
        botao_copiar_seguro(st.session_state.get("resultado_t1_edit", ""), key="resultado_t1_edit")

# =========================================
# TELA 2 — EXTRAÇÃO EM LOTE
# =========================================
if _aba_ativa == aba2.nome:
    st.subheader("Tela 2 — Extração em lote")
    st.write("Cada print será tratado como 1 torneio.")

    st.divider()

    prints_lote = st.file_uploader(
        "Uploads dos PRINTS dos torneios",
        type=["jpg", "jpeg", "png", "webp", "heic", "heif", "bmp"],
        accept_multiple_files=True,
        key="prints_lote"
    )

    st.divider()

    if st.button("Extrair torneios em lote", key="btn_extrair_lote"):
        if not prints_lote:
            st.error("Envie ao menos um print.")
        else:
            # Limpa dados de extração anterior antes de iniciar nova
            for _k in ["campos_lote_extraidos", "blocos_lote", "fila_directs",
                       "fila_idx", "texto_consolidado_edit", "_ultimo_consolidado",
                       "secao_lote_radio"]:
                st.session_state.pop(_k, None)
            # Limpa chaves de parágrafos e flags de edição de extrações anteriores
            for _k in list(st.session_state.keys()):
                if _k.startswith("direct_paras_") or _k.startswith("_direct_paras_") or                    _k.startswith("forcar_msg_completa_") or _k.startswith("tipo_item_"):
                    st.session_state.pop(_k, None)

            progress = st.progress(0)
            status_extracao = st.empty()
            total = len(prints_lote)
            blocos_lote = []
            campos_lote_extraidos = []

            for i, img in enumerate(prints_lote, start=1):
                status_extracao.caption(f"Extraindo torneio {i} de {total}...")
                tentativas = 0
                max_tentativas = 2
                resultado_ok = False
                nome_img = img.name  # salva o nome antes de liberar a referência

                while tentativas < max_tentativas and not resultado_ok:
                    try:
                        resultado = extrair_texto_lote_1_torneio(img)
                        campos = extrair_campos_lote(resultado)
                        # Libera a string de resultado da API imediatamente
                        del resultado
                        # Valida se a extração retornou algo útil
                        campos_preenchidos = sum(
                            1 for v in campos.values()
                            if v and str(v).strip().lower() not in ("não encontrado", "")
                        )
                        if campos_preenchidos == 0 and tentativas < max_tentativas - 1:
                            tentativas += 1
                            continue
                        mensagem_direct = decodificar_texto(montar_mensagem_direct_lote(campos))
                        campos_lote_extraidos.append(campos)
                        resultado_ok = True
                    except Exception as e:
                        tentativas += 1
                        if tentativas >= max_tentativas:
                            campos = {
                                "instagrams": [], "data": "", "torneio": "",
                                "cidade_uf": "", "local": "", "categorias": "",
                                "contato": f"erro na extração ({repr(e)})"
                            }
                            mensagem_direct = (
                                "Instagram: não encontrado\n\n"
                                "Data: não encontrado\n"
                                "Torneio: não encontrado\n"
                                "Cidade/ES: não encontrado\n"
                                "Local: não encontrado\n"
                                "Categorias: não encontrado\n"
                                f"Contato: erro na extração ({repr(e)})"
                            )
                            campos_lote_extraidos.append(campos)
                            resultado_ok = True

                blocos_lote.append({
                    "arquivo": nome_img,
                    "mensagem": mensagem_direct
                })
                # Libera referência à imagem após processar para economizar memória
                del mensagem_direct
                progress.progress(i / total)

            st.session_state["campos_lote_extraidos"] = campos_lote_extraidos
            st.session_state["blocos_lote"] = blocos_lote
            # Libera listas temporárias e força coleta de lixo
            del campos_lote_extraidos, blocos_lote
            import gc; gc.collect()
            status_extracao.empty()
            st.success(f"Extração concluída — {total} torneio(s) processado(s).")

    # Botão para limpar a extração e voltar ao estado inicial
    if st.session_state.get("campos_lote_extraidos"):
        if st.button("🗑 Limpar extração e iniciar nova", key="btn_limpar_lote"):
            for k in ["campos_lote_extraidos", "blocos_lote", "fila_directs",
                      "fila_idx", "texto_consolidado_edit", "_ultimo_consolidado",
                      "secao_lote_radio"]:
                st.session_state.pop(k, None)
            st.rerun()

    # Exibe resultados se já extraídos
    if st.session_state.get("campos_lote_extraidos"):
        campos_lote_extraidos = st.session_state["campos_lote_extraidos"]
        blocos_lote = st.session_state.get("blocos_lote", [])

        _secao_lote = st.radio(
            "Visualizar:",
            options=["📋 Texto consolidado", "📩 Envio de directs"],
            horizontal=True,
            key="secao_lote_radio",
            label_visibility="collapsed",
        )

        if _secao_lote == "📋 Texto consolidado":
            consolidado = [item["mensagem"] for item in blocos_lote]
            texto_consolidado = "\n\n" + ("\n\n" + ("—" * 40) + "\n\n").join(consolidado)
            if "texto_consolidado_edit" not in st.session_state or st.session_state.get("_ultimo_consolidado") != texto_consolidado:
                st.session_state["texto_consolidado_edit"] = texto_consolidado
                st.session_state["_ultimo_consolidado"] = texto_consolidado

            st.text_area(
                "Edite se necessário",
                height=400,
                key="texto_consolidado_edit",
            )
            botao_copiar_seguro(st.session_state.get("texto_consolidado_edit", ""), key="texto_consolidado_edit")

        elif _secao_lote == "📩 Envio de directs":
            st.write("Abra o direct de cada perfil, verifique o histórico e escolha o tipo de contato.")

            if "fila_directs" not in st.session_state or not st.session_state["fila_directs"]:
                tipo_global = st.radio(
                    "Tipo de contato padrão",
                    options=["Novo contato", "Recorrente"],
                    horizontal=True,
                    key="tipo_contato_global",
                )
                if st.button("Montar fila de directs", key="btn_montar_fila"):
                    fila = []
                    for campos in campos_lote_extraidos:
                        # Inclui todos os torneios, com ou sem @perfil identificado
                        fila.append({
                            "perfis": campos.get("instagrams", []),
                            "campos": campos,
                            "tipo": "novo" if tipo_global == "Novo contato" else "recorrente",
                        })
                    if not fila:
                        st.warning("Nenhum torneio encontrado na extração.")
                    else:
                        st.session_state["fila_directs"] = fila
                        st.session_state["fila_idx"] = 0
                        st.rerun()

            if st.session_state.get("fila_directs"):
                fila = st.session_state["fila_directs"]
                idx = st.session_state.get("fila_idx", 0)
                total_fila = len(fila)

                if idx < total_fila:
                    item = fila[idx]
                    perfis = item["perfis"]
                    campos = item["campos"]

                    torneio_nome = capitalizar_texto_inteligente(campos.get("torneio", "")) or f"Torneio {idx+1}"
                    st.markdown(f"### {idx+1}/{total_fila} — {torneio_nome}")
                    st.progress((idx + 1) / total_fila)

                    # Tipo + navegação
                    col_tipo, col_nav = st.columns([2, 1])
                    with col_tipo:
                        tipo_escolhido = st.radio(
                            "Tipo de contato",
                            options=["Novo contato", "Recorrente"],
                            index=0 if item["tipo"] == "novo" else 1,
                            horizontal=True,
                            key=f"tipo_item_{idx}",
                        )
                        fila[idx]["tipo"] = "novo" if tipo_escolhido == "Novo contato" else "recorrente"

                    with col_nav:
                        c1, c2 = st.columns(2)
                        with c1:
                            if idx > 0:
                                if st.button("← Ant.", key="btn_ant_direct"):
                                    st.session_state["fila_idx"] = idx - 1
                                    st.rerun()
                        with c2:
                            if idx < total_fila - 1:
                                if st.button("Próximo →", key="btn_prox_direct", type="primary"):
                                    st.session_state["fila_idx"] = idx + 1
                                    st.rerun()
                            else:
                                st.success("Último!")

                    # Perfis com link direto
                    st.divider()
                    st.markdown("**Abra o perfil, verifique o histórico e inicie o direct:**")
                    for perfil in perfis:
                        perfil_limpo = perfil.lstrip("@")
                        url_perfil = f"https://www.instagram.com/{perfil_limpo}/"
                        st.link_button(f"👤 Abrir perfil — {perfil}", url_perfil)

                    # Parágrafos separados
                    st.divider()
                    tipo_final = fila[idx]["tipo"]
                    pendencias_item = listar_pendencias_lote(campos)
                    forcar_completa_key = f"forcar_msg_completa_{idx}"

                    if pendencias_item:
                        st.warning(
                            "⚠️ Informações incompletas: **"
                            + ", ".join(pendencias_item)
                            + "**. A mensagem já reflete as pendências."
                        )
                        if st.button(
                            "✅ Já preenchi as informações — usar mensagem para dados completos",
                            key=f"btn_forcar_completa_{idx}",
                        ):
                            st.session_state[forcar_completa_key] = True
                            st.rerun()
                    else:
                        st.success("✅ Todas as informações encontradas.")
                        st.session_state.pop(forcar_completa_key, None)
                    # ── Emojis para curtir/comentar ──
                    st.markdown("**Antes de enviar — curta o post e deixe um comentário:**")
                    emojis_comentario = "🔥👏🏼👏🏼👏🏼"
                    col_emoji, col_btn_emoji = st.columns([4, 1])
                    with col_emoji:
                        st.code(emojis_comentario, language=None)
                    with col_btn_emoji:
                        botao_copiar_seguro(emojis_comentario, key=f"emoji_direct_{idx}")

                    st.divider()

                    # ── Parágrafos editáveis ──
                    # Se o usuário indicou que preencheu as pendências manualmente,
                    # usa a versão de mensagem completa (sem mencionar pendências)
                    if st.session_state.get(forcar_completa_key) and pendencias_item:
                        bloco_info = montar_bloco_informacoes_lote(campos)
                        mes_pc = extrair_mes_do_campo_data(campos.get("data", ""))
                        mes_txt_pc = f" de {mes_pc}" if mes_pc else ""
                        if tipo_final == "novo":
                            abertura_pc = [
                                "Fala pessoal!\nTudo bem?",
                                "Trabalhamos com a divulgação de torneios de futevôlei de todo o Brasil, "
                                "através da Agenda Nacional de Torneios.",
                                "Gostariam de divulgar o torneio de vocês na nossa página de forma GRATUITA?",
                            ]
                        else:
                            abertura_pc = [
                                "Fala pessoal!\nTudo bem?",
                                f"Bora divulgar o torneio{mes_txt_pc} na Agenda Nacional de Torneios?",
                            ]
                        paragrafos = abertura_pc + [
                            "Preciso apenas que me envie a arte de divulgação do evento para "
                            "podermos repostá-la na nossa página e confirme as informações abaixo:",
                            bloco_info,
                        ]
                        if st.button(
                            "↩ Voltar para mensagem com pendências",
                            key=f"btn_voltar_pendencias_{idx}",
                        ):
                            st.session_state.pop(forcar_completa_key, None)
                            st.rerun()
                    else:
                        paragrafos = montar_paragrafos_direct(campos, tipo_final)
                    st.markdown("**Copie e envie parágrafo por parágrafo:**")

                    # Chave base para os parágrafos deste torneio/modo
                    # Inclui o modo (forcar_completa) para que a mudança de modo
                    # gere keys novas, evitando conflito de tamanho de lista
                    modo_para = "completo" if st.session_state.get(forcar_completa_key) else tipo_final
                    para_key_base = f"direct_paras_{idx}_{modo_para}"
                    editado_key = f"_direct_paras_editado_{idx}_{modo_para}"

                    # Inicializa os parágrafos no session_state se ainda não existem
                    # ou se o número de parágrafos mudou (novo modo/tipo)
                    n_paras_salvo = st.session_state.get(f"_direct_paras_n_{idx}_{modo_para}", -1)
                    if n_paras_salvo != len(paragrafos):
                        for i_p, para in enumerate(paragrafos):
                            st.session_state[f"{para_key_base}_{i_p}"] = para
                        st.session_state[f"_direct_paras_n_{idx}_{modo_para}"] = len(paragrafos)
                        st.session_state[editado_key] = False

                    for i_p, paragrafo in enumerate(paragrafos, start=1):
                        st.caption(f"Parágrafo {i_p}")
                        edit_key = f"{para_key_base}_{i_p-1}"

                        def _marcar_editado(k=editado_key):
                            st.session_state[k] = True

                        st.text_area(
                            f"Parágrafo {i_p}",
                            height=120,
                            key=edit_key,
                            label_visibility="collapsed",
                            on_change=_marcar_editado,
                        )
                        # Passa a mesma key da textarea para que o botão
                        # encontre o elemento correto no DOM e leia o valor editado
                        botao_copiar_seguro(
                            st.session_state.get(edit_key, paragrafo),
                            key=edit_key
                        )

                st.divider()
                if st.button("🔄 Reiniciar fila", key="btn_reiniciar_fila"):
                    st.session_state["fila_directs"] = []
                    st.session_state["fila_idx"] = 0
                    st.rerun()

# =========================================
# TELA 3 — REGISTRO FINAL DO TORNEIO
# =========================================
if _aba_ativa == aba3.nome:
    # Garante que o session_state do lote não interfira nesta aba
    if "secao_lote_radio" in st.session_state and not st.session_state.get("campos_lote_extraidos"):
        del st.session_state["secao_lote_radio"]

    st.subheader("Tela 3 — Registro final do torneio")
    st.write("Cole o texto confirmado pelo organizador e salve na planilha e no Drive.")

    st.divider()

    st.markdown("### 1. Texto confirmado")

    # Inicializa chave no session_state se necessário
    if "texto_confirmado" not in st.session_state:
        st.session_state["texto_confirmado"] = ""

    def _decodificar_confirmado():
        raw = st.session_state.get("texto_confirmado", "")
        decoded = decodificar_texto(raw)
        if decoded != raw:
            st.session_state["texto_confirmado"] = decoded

    st.text_area(
        "Cole aqui o texto confirmado pelo organizador",
        height=220,
        key="texto_confirmado",
        on_change=_decodificar_confirmado,
    )
    texto_confirmado = st.session_state.get("texto_confirmado", "")

    # Reprocessa os campos extraídos aqui (antes da seção de agenda) para
    # que a detecção automática de região e mês já tenha os dados prontos.
    campos = extrair_campos_confirmados(texto_confirmado)

    data_evento_visual = normalizar_data_visual_ant(campos["data"])
    torneio = campos["torneio"]
    cidade_uf = normalizar_cidade_uf_tela2(campos["cidade_uf"])
    local_evento = campos["local"]
    categorias = campos["categorias"]
    contato = normalizar_contato(campos["contato"])

    cidade, uf, estado_extenso = separar_cidade_uf(cidade_uf)
    data_inicial_completa, data_final_completa = extrair_data_inicial_final(campos["data"])
    data_inicial = formatar_data_curta(data_inicial_completa)
    data_final = formatar_data_curta(data_final_completa)

    nome_arquivo = gerar_nome_arquivo(uf, campos["data"], cidade)

    agenda_detectada = regiao_por_uf(uf)
    mes_1_detectado, mes_2_detectado, virada_detectada = detectar_meses_por_datas(
        data_inicial_completa, data_final_completa
    )

    # Quando um novo texto é colado, limpa as seleções anteriores de
    # agenda/mês para permitir uma nova detecção automática.
    if st.session_state.get("_texto_ref_deteccao") != texto_confirmado:
        st.session_state["_texto_ref_deteccao"] = texto_confirmado
        st.session_state["agenda_final"] = ""
        st.session_state["mes_1"] = ""
        st.session_state["mes_2"] = ""
        st.session_state["virada_mes"] = False

    st.divider()

    st.markdown("### 2. Flyer final")

    flyer_final = st.file_uploader(
        "Upload do FLYER final",
        type=["jpg", "jpeg", "png", "webp", "heic", "heif", "bmp"],
        key="flyer_final"
    )

    st.markdown("### 2.1 Print do post")

    print_post = st.file_uploader(
        "Upload do PRINT do post",
        type=["jpg", "jpeg", "png", "webp", "heic", "heif", "bmp"],
        key="print_post"
    )

    st.divider()

    st.markdown("### 3. Organização da agenda")

    if not st.session_state.get("agenda_final") and agenda_detectada:
        st.session_state["agenda_final"] = agenda_detectada

    agenda = st.selectbox(
        "Agenda",
        ["", "SUL", "NORTE"],
        key="agenda_final"
    )
    if agenda_detectada:
        st.caption(f"🔎 Detectado automaticamente pela UF: {agenda_detectada}")

    meses = _meses_global

    if not st.session_state.get("mes_1") and mes_1_detectado:
        st.session_state["mes_1"] = mes_1_detectado
    if virada_detectada and not st.session_state.get("virada_mes"):
        st.session_state["virada_mes"] = True

    mes_1 = st.selectbox("Mês principal", meses, key="mes_1")
    if mes_1_detectado:
        st.caption(f"🔎 Detectado automaticamente pela data: {mes_1_detectado}")

    virada_mes = st.checkbox("Torneio em virada de mês?", key="virada_mes")

    mes_2 = ""
    if virada_mes:
        if not st.session_state.get("mes_2") and mes_2_detectado:
            st.session_state["mes_2"] = mes_2_detectado
        mes_2 = st.selectbox("Segundo mês", meses, key="mes_2")
        if mes_2_detectado:
            st.caption(f"🔎 Detectado automaticamente pela data: {mes_2_detectado}")

    st.divider()

    st.markdown("### 3.1 Conexão com Google Drive")

    if drive_conectado():
        st.success("Google Drive conectado.")
        if st.button("Desconectar Google Drive", key="btn_desconectar_drive"):
            desconectar_drive_usuario()
            st.rerun()
    else:
        st.warning("Google Drive ainda não conectado.")
        url_autorizacao = gerar_url_autorizacao_drive()
        st.link_button("Conectar Google Drive", url_autorizacao)

    st.divider()
    st.markdown("#### 🔧 Diagnóstico de conexão com Google Sheets")
    st.caption("Use este botão para identificar erros de acesso às planilhas.")
    if st.button("Testar conexão com Google Sheets", key="btn_diagnostico_sheets"):
        try:
            client_gs = conectar_gsheet()
            st.success("✅ Service Account autenticada com sucesso.")
        except Exception as e:
            st.error("❌ Falha na autenticação da Service Account.")
            st.code(repr(e))
            st.stop()
        try:
            planilha_log = obter_planilha_log(client_gs)
            st.success(f"✅ Planilha LOG encontrada: {planilha_log.title}")
        except Exception as e:
            st.error("❌ Planilha LOG não encontrada. Verifique o GOOGLE_SHEET_ID_LOG e o compartilhamento.")
            st.code(repr(e))
            st.stop()
        try:
            aba = obter_aba_config(client_gs)
            st.success("✅ Aba CONFIG_APP encontrada e acessível.")
        except Exception as e:
            st.error("❌ Aba CONFIG_APP não encontrada ou sem permissão de escrita.")
            st.code(repr(e))
            st.stop()
        try:
            token_info = st.session_state.get("drive_token_info")
            if token_info:
                salvar_token_drive_persistido(token_info)
                st.success("✅ Token do Drive salvo na CONFIG_APP com sucesso.")
            else:
                st.warning("⚠️ Drive não conectado ainda. Conecte o Drive primeiro, depois teste novamente.")
        except Exception as e:
            st.error("❌ Falha ao salvar o token na CONFIG_APP.")
            st.code(repr(e))
        try:
            planilha_sul = obter_planilha_por_agenda(client_gs, "SUL")
            st.success(f"✅ Planilha SUL encontrada: {planilha_sul.title}")
        except Exception as e:
            st.error("❌ Planilha SUL não encontrada.")
            st.code(repr(e))
        try:
            planilha_norte = obter_planilha_por_agenda(client_gs, "NORTE")
            st.success(f"✅ Planilha NORTE encontrada: {planilha_norte.title}")
        except Exception as e:
            st.error("❌ Planilha NORTE não encontrada.")
            st.code(repr(e))

    st.divider()

    st.markdown("### 4. Pré-visualização da linha da macro")

    linha_macro = [
        "",
        data_evento_visual,
        data_inicial,
        data_final,
        torneio,
        cidade_uf,
        estado_extenso,
        local_evento,
        categorias,
        contato,
        ""
    ]

    linha_macro_preview = {
        "Nº": "",
        "Data": data_evento_visual,
        "Data inicial": data_inicial,
        "Data final": data_final,
        "Torneio": torneio,
        "Cidade": cidade_uf,
        "Estado": estado_extenso,
        "Local": local_evento,
        "Categorias": categorias,
        "Contato": contato,
        "Status": ""
    }

    st.write("**Nome sugerido do arquivo:**", nome_arquivo if nome_arquivo else "-")

    df_preview = pd.DataFrame([linha_macro_preview])
    st.dataframe(df_preview, use_container_width=True, hide_index=True)

    st.divider()

    erros = []

    if not texto_confirmado.strip():
        erros.append("Cole o texto confirmado.")
    if flyer_final is None:
        erros.append("Envie o flyer final.")
    if print_post is None:
        erros.append("Envie o print do post.")
    if not agenda:
        erros.append("Selecione a agenda.")
    if not mes_1:
        erros.append("Selecione o mês principal.")
    if virada_mes and not mes_2:
        erros.append("Selecione o segundo mês.")
    if not data_evento_visual:
        erros.append("Não foi possível identificar a data.")
    if not torneio:
        erros.append("Não foi possível identificar o torneio.")
    if not cidade_uf:
        erros.append("Não foi possível identificar Cidade/ES.")
    if not estado_extenso:
        erros.append("Não foi possível identificar o estado por extenso.")
    if not local_evento:
        erros.append("Não foi possível identificar o local.")
    if not categorias or categorias == "não encontrado":
        erros.append("Não foi possível identificar as categorias.")
    if not contato or contato == "não encontrado":
        erros.append("Não foi possível identificar o contato.")
    if not nome_arquivo:
        erros.append("Não foi possível gerar o nome automático do flyer.")
    if not data_inicial:
        erros.append("Não foi possível identificar a data inicial.")
    if not data_final:
        erros.append("Não foi possível identificar a data final.")
    if not drive_conectado():
        erros.append("Conecte o Google Drive antes de salvar.")

    salvamento_atual_fingerprint = gerar_fingerprint_salvamento(
        texto_confirmado=texto_confirmado,
        agenda=agenda,
        mes_1=mes_1,
        mes_2=mes_2,
        flyer_final=flyer_final,
        print_post=print_post
    )

    if st.button("Validar linha final", key="btn_validar_linha_final"):
        if erros:
            st.error("A linha final ainda não está pronta.")
            for erro in erros:
                st.write(f"- {erro}")
        else:
            st.success("Linha validada com sucesso.")

    if st.button("Salvar na Google Sheet e no Drive", key="btn_salvar_completo"):
        if erros:
            st.error("Não foi possível salvar porque ainda há pendências.")
            for erro in erros:
                st.write(f"- {erro}")
        elif st.session_state["ultimo_salvamento_fingerprint"] == salvamento_atual_fingerprint:
            st.warning("Este torneio já foi salvo nesta sessão. Altere algum dado antes de tentar salvar novamente.")
        else:
            client_gs = None

            status_print = "❌"
            status_sheet = "❌"
            status_flyer = "❌"

            erro_print = ""
            erro_sheet = ""
            erro_flyer = ""
            nome_flyer_final = ""
            nome_print_final = ""

            try:
                client_gs = conectar_gsheet()
                drive_service = conectar_drive_usuario()

                # 1. SALVAR PRINT
                try:
                    nome_print_final = gerar_nome_flyer(print_post, f"{nome_arquivo} - PRINT")
                    pasta_torneios_mes_1 = obter_id_pasta_torneios(mes_1, agenda)
                    upload_arquivo_drive(drive_service, print_post, pasta_torneios_mes_1, nome_arquivo=nome_print_final)

                    if virada_mes and mes_2 and mes_2 != mes_1:
                        pasta_torneios_mes_2 = obter_id_pasta_torneios(mes_2, agenda)
                        upload_arquivo_drive(drive_service, print_post, pasta_torneios_mes_2, nome_arquivo=nome_print_final)

                    status_print = "✅"
                except Exception as e:
                    erro_print = repr(e)

                # 2. SALVAR NA PLANILHA
                try:
                    planilha = obter_planilha_por_agenda(client_gs, agenda)
                    salvar_linha_na_aba(planilha, mes_1, linha_macro)

                    if virada_mes and mes_2 and mes_2 != mes_1:
                        salvar_linha_na_aba(planilha, mes_2, linha_macro)

                    status_sheet = "✅"
                except Exception as e:
                    erro_sheet = repr(e)

                # 3. SALVAR FLYER
                try:
                    nome_flyer_final = gerar_nome_flyer(flyer_final, nome_arquivo)
                    pasta_flyers_mes_1 = obter_id_pasta_flyers(mes_1)
                    upload_arquivo_drive(drive_service, flyer_final, pasta_flyers_mes_1, nome_arquivo=nome_flyer_final)

                    if virada_mes and mes_2 and mes_2 != mes_1:
                        pasta_flyers_mes_2 = obter_id_pasta_flyers(mes_2)
                        upload_arquivo_drive(drive_service, flyer_final, pasta_flyers_mes_2, nome_arquivo=nome_flyer_final)

                    status_flyer = "✅"
                except Exception as e:
                    erro_flyer = repr(e)

                # 4. LOG
                erros_consolidados = []
                if erro_print:
                    erros_consolidados.append(f"PRINT: {erro_print}")
                if erro_sheet:
                    erros_consolidados.append(f"GOOGLE_SHEET: {erro_sheet}")
                if erro_flyer:
                    erros_consolidados.append(f"FLYER: {erro_flyer}")

                status_final = "SUCESSO" if (
                    status_print == "✅" and status_sheet == "✅" and status_flyer == "✅"
                ) else "ERRO"

                try:
                    registrar_log(
                        client_gs=client_gs,
                        torneio=torneio,
                        cidade=cidade_uf,
                        data_evento=data_evento_visual,
                        agenda=agenda,
                        mes_1=mes_1,
                        mes_2=mes_2,
                        nome_flyer=nome_flyer_final if nome_flyer_final else nome_arquivo,
                        status=status_final,
                        erro=" | ".join(erros_consolidados)
                    )
                except Exception:
                    pass

                st.session_state["ultimo_salvamento_fingerprint"] = salvamento_atual_fingerprint

                st.divider()
                st.markdown("### Resultado do registro")
                st.write(f'Print salvo na pasta "Torneios" {status_print}')
                st.write(f"Inclusão na Google Sheet {status_sheet}")
                st.write(f'Flyer salvo na pasta "Fazer" {status_flyer}')

                if status_final == "SUCESSO":
                    st.success("Registro concluído com sucesso.")
                else:
                    st.warning("Uma ou mais etapas falharam. Consulte a aba LOG para verificar o motivo.")

            except Exception as e:
                if client_gs is not None:
                    try:
                        registrar_log(
                            client_gs=client_gs,
                            torneio=torneio,
                            cidade=cidade_uf,
                            data_evento=data_evento_visual,
                            agenda=agenda,
                            mes_1=mes_1,
                            mes_2=mes_2,
                            nome_flyer=flyer_final.name if flyer_final else "",
                            status="ERRO",
                            erro=repr(e)
                        )
                    except Exception:
                        pass

                st.error("Erro geral no processo.")
                st.code(repr(e))

# =========================================
# TELA 4 — MENSAGEM DE ORGANIZADORES (NOVO)
# Substitui o agente do ChatGPT
# =========================================
if _aba_ativa == aba4.nome:
    st.subheader("Tela 4 — Mensagem para organizadores")
    st.write(
        "Gera automaticamente a mensagem consolidada para a lista de transmissão de organizadores. "
        "Informe os dados abaixo após postar as agendas."
    )

    st.divider()

    st.markdown("### 1. Links das postagens")

    link_sul_org = st.text_input(
        "Link da postagem — Agenda Sul e Sudeste",
        placeholder="https://www.instagram.com/p/...",
        key="link_sul_org"
    )

    link_norte_org = st.text_input(
        "Link da postagem — Agenda Norte/Nordeste/Centro-Oeste",
        placeholder="https://www.instagram.com/p/...",
        key="link_norte_org"
    )

    st.divider()

    st.markdown("### 2. Totais acumulados na agenda")

    col1, col2 = st.columns(2)

    with col1:
        nome_mes1_org = st.selectbox(
            "Mês 1",
            ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
             "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"],
            key="nome_mes1_org"
        )
        total_mes1_org = st.number_input(
            "Total de torneios do Mês 1 (acumulado na agenda)",
            min_value=0, value=0, step=1, key="total_mes1_org"
        )

    with col2:
        nome_mes2_org = st.selectbox(
            "Mês 2",
            ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
             "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"],
            index=1,
            key="nome_mes2_org"
        )
        total_mes2_org = st.number_input(
            "Total de torneios do Mês 2 (acumulado na agenda)",
            min_value=0, value=0, step=1, key="total_mes2_org"
        )

    st.divider()

    st.markdown("### 3. Novos torneios por estado (cole o conteúdo dos arquivos gerados)")
    st.caption("Cole o conteúdo dos arquivos mensagens_whatsapp_sul.txt e mensagens_whatsapp_norte.txt")

    texto_sul_org = st.text_area(
        "Conteúdo de mensagens_whatsapp_sul.txt",
        height=200,
        key="texto_sul_org",
        placeholder="Cole aqui o conteúdo do arquivo SUL..."
    )

    texto_norte_org = st.text_area(
        "Conteúdo de mensagens_whatsapp_norte.txt",
        height=200,
        key="texto_norte_org",
        placeholder="Cole aqui o conteúdo do arquivo NORTE..."
    )

    st.divider()

    if st.button("Gerar mensagem de organizadores", key="btn_gerar_msg_org"):
        erros_org = []
        if not link_sul_org.strip():
            erros_org.append("Informe o link da agenda Sul.")
        if not link_norte_org.strip():
            erros_org.append("Informe o link da agenda Norte.")
        if not texto_sul_org.strip():
            erros_org.append("Cole o conteúdo do arquivo SUL.")
        if not texto_norte_org.strip():
            erros_org.append("Cole o conteúdo do arquivo NORTE.")

        if erros_org:
            st.error("Preencha todos os campos:")
            for e in erros_org:
                st.write(f"- {e}")
        else:
            with st.spinner("Gerando mensagem com Claude..."):
                mensagem_org = gerar_mensagem_organizadores_claude(
                    texto_sul=texto_sul_org,
                    texto_norte=texto_norte_org,
                    link_sul=link_sul_org.strip(),
                    link_norte=link_norte_org.strip(),
                    total_mes1=str(int(total_mes1_org)),
                    total_mes2=str(int(total_mes2_org)),
                    nome_mes1=nome_mes1_org,
                    nome_mes2=nome_mes2_org,
                )

            st.divider()
            st.subheader("Mensagem pronta para organizadores")
            st.text_area(
                "Copie e envie para a lista de transmissão de organizadores",
                value=mensagem_org,
                height=500,
                key="msg_org_pronta"
            )

# =========================================
# TELA 5 — LIMPEZA PÓS-ATUALIZAÇÃO
# =========================================
if _aba_ativa == aba5.nome:
    st.subheader("Tela 5 — Limpeza pós-atualização")
    st.write("Execute a limpeza somente após concluir toda a atualização da ANT e a organização dos arquivos.")

    st.divider()

    st.markdown("### 1. Meses atualizados na ANT")

    meses_limpeza = st.multiselect(
        "Indique abaixo os meses atualizados na ANT",
        options=meses_validos,
        key="meses_limpeza"
    )

    st.divider()

    st.markdown("### 2. Confirmações obrigatórias")

    conf_sheet = st.checkbox(
        "Confirmo que os dados das Google Sheets SUL e NORTE já foram inseridos na ANT",
        key="conf_sheet_limpeza"
    )

    conf_prints = st.checkbox(
        "Confirmo que os prints dos torneios NORTE e SUL já foram baixados para as pastas de atualização da ANT",
        key="conf_prints_limpeza"
    )

    conf_flyers = st.checkbox(
        "Confirmo que os flyers das pastas Fazer já foram baixados para a pasta Flyers_Montagem",
        key="conf_flyers_limpeza"
    )

    conf_final = st.checkbox(
        "Entendo que esta ação apagará os dados e arquivos dos meses selecionados",
        key="conf_final_limpeza"
    )

    st.divider()

    if st.button("Executar limpeza dos meses selecionados", key="btn_executar_limpeza"):
        erros_limpeza = []

        if not meses_limpeza:
            erros_limpeza.append("Selecione ao menos um mês.")
        if not conf_sheet:
            erros_limpeza.append("Confirme que os dados das Google Sheets já foram inseridos na ANT.")
        if not conf_prints:
            erros_limpeza.append("Confirme que os prints dos torneios já foram baixados.")
        if not conf_flyers:
            erros_limpeza.append("Confirme que os flyers das pastas Fazer já foram baixados.")
        if not conf_final:
            erros_limpeza.append("Marque a confirmação final de limpeza.")
        if not drive_conectado():
            erros_limpeza.append("Conecte o Google Drive antes de executar a limpeza.")

        if erros_limpeza:
            st.error("A limpeza não pode ser executada porque ainda há pendências.")
            for erro in erros_limpeza:
                st.write(f"- {erro}")
        else:
            client_gs = None
            relatorio_limpeza = []
            erros_consolidados = []

            try:
                client_gs = conectar_gsheet()
                drive_service = conectar_drive_usuario()

                planilha_sul = obter_planilha_por_agenda(client_gs, "SUL")
                planilha_norte = obter_planilha_por_agenda(client_gs, "NORTE")

                for mes in meses_limpeza:
                    mes_nome = nome_mes_sem_numero(mes)

                    for agenda_limpeza, planilha_limpeza in [("SUL", planilha_sul), ("NORTE", planilha_norte)]:
                        try:
                            qtd = limpar_aba_mantendo_cabecalho(planilha_limpeza, mes)
                            relatorio_limpeza.append(f"Google Sheet {agenda_limpeza} - {mes_nome} ✅ ({qtd} linha(s) limpa(s))")
                        except Exception as e:
                            relatorio_limpeza.append(f"Google Sheet {agenda_limpeza} - {mes_nome} ❌")
                            erros_consolidados.append(f"GOOGLE_SHEET_{agenda_limpeza}_{mes_nome.upper()}: {repr(e)}")

                        try:
                            pasta_torneios = obter_id_pasta_torneios(mes, agenda_limpeza)
                            qtd_t = excluir_arquivos_pasta_drive(drive_service, pasta_torneios)
                            relatorio_limpeza.append(f"Pasta Torneios {agenda_limpeza} - {mes_nome} ✅ ({qtd_t} arquivo(s) excluído(s))")
                        except Exception as e:
                            relatorio_limpeza.append(f"Pasta Torneios {agenda_limpeza} - {mes_nome} ❌")
                            erros_consolidados.append(f"TORNEIOS_{agenda_limpeza}_{mes_nome.upper()}: {repr(e)}")

                    try:
                        pasta_flyers = obter_id_pasta_flyers(mes)
                        qtd_f = excluir_arquivos_pasta_drive(drive_service, pasta_flyers)
                        relatorio_limpeza.append(f"Pasta Flyers Fazer - {mes_nome} ✅ ({qtd_f} arquivo(s) excluído(s))")
                    except Exception as e:
                        relatorio_limpeza.append(f"Pasta Flyers Fazer - {mes_nome} ❌")
                        erros_consolidados.append(f"FLYERS_FAZER_{mes_nome.upper()}: {repr(e)}")

                status_limpeza = "SUCESSO" if not erros_consolidados else "ERRO"

                try:
                    registrar_log(
                        client_gs=client_gs,
                        torneio="LIMPEZA_POS_ATUALIZACAO",
                        cidade="-",
                        data_evento=", ".join(meses_limpeza),
                        agenda="SUL/NORTE",
                        mes_1=", ".join(meses_limpeza),
                        mes_2="",
                        nome_flyer="-",
                        status=status_limpeza,
                        erro=" | ".join(erros_consolidados)
                    )
                except Exception:
                    pass

                st.divider()
                st.markdown("### Resultado da limpeza")

                for linha in relatorio_limpeza:
                    st.write(linha)

                if status_limpeza == "SUCESSO":
                    st.success("Limpeza concluída com sucesso para todos os meses selecionados.")
                else:
                    st.warning("A limpeza foi executada parcialmente. Consulte a aba LOG para verificar os detalhes.")

            except Exception as e:
                if client_gs is not None:
                    try:
                        registrar_log(
                            client_gs=client_gs,
                            torneio="LIMPEZA_POS_ATUALIZACAO",
                            cidade="-",
                            data_evento=", ".join(meses_limpeza) if meses_limpeza else "",
                            agenda="SUL/NORTE",
                            mes_1=", ".join(meses_limpeza) if meses_limpeza else "",
                            mes_2="",
                            nome_flyer="-",
                            status="ERRO",
                            erro=repr(e)
                        )
                    except Exception:
                        pass

                st.error("Erro geral ao executar a limpeza.")
                st.code(repr(e))


# =========================================
# TELA 6 — TORNEIOS RECEBIDOS POR FORMULÁRIO
# =========================================
if _aba_ativa == aba6.nome:
    st.subheader("Tela 6 — Torneios recebidos por formulário")
    st.write(
        f"Processa os torneios já conferidos pelo usuário e encaminhados (com o print da "
        f"postagem anexado) para **{GMAIL_CONTA_FORMULARIOS}**. A extração dos dados é "
        f"100% literal — sem IA — lida diretamente dos campos do formulário. Nenhuma "
        f"informação ausente ou ambígua é registrada automaticamente."
    )

    st.divider()

    st.markdown("### 1. Conexão com o Gmail")

    if gmail_conectado():
        st.success(f"Gmail conectado ({GMAIL_CONTA_FORMULARIOS}).")
        if st.button("Desconectar Gmail", key="btn_desconectar_gmail"):
            desconectar_gmail_usuario()
            st.rerun()
    else:
        st.warning("Gmail não conectado.")
        st.link_button("Conectar Gmail", gerar_url_autorizacao_gmail())

    st.divider()

    st.markdown("### 2. Processar caixa de entrada")
    st.write(
        "Verifica todos os e-mails na caixa de entrada, registra os torneios "
        "sem pendências (planilha + Drive) e arquiva apenas esses. E-mails com "
        "alguma inconsistência permanecem na caixa de entrada para correção."
    )

    if st.button("Verificar e registrar novos torneios", key="btn_processar_formularios"):
        erros_pre = []
        if not gmail_conectado():
            erros_pre.append("Conecte o Gmail antes de processar.")
        if not drive_conectado():
            erros_pre.append("Conecte o Google Drive antes de processar.")

        if erros_pre:
            st.error("Não foi possível processar.")
            for erro in erros_pre:
                st.write(f"- {erro}")
        else:
            try:
                gmail_service = conectar_gmail_usuario()
                drive_service = conectar_drive_usuario()
                client_gs = conectar_gsheet()

                with st.spinner("Processando e-mails..."):
                    relatorio = processar_formularios_recebidos(gmail_service, drive_service, client_gs)

                st.divider()
                st.markdown("### Resultado")

                registrados = [r for r in relatorio if r["status"] == "REGISTRADO"]
                pendentes = [r for r in relatorio if r["status"] == "PENDENTE"]

                if not relatorio:
                    st.info("Nenhum e-mail encontrado na caixa de entrada.")

                if registrados:
                    st.success(f"{len(registrados)} torneio(s) registrado(s) com sucesso e arquivado(s):")
                    for r in registrados:
                        st.write(f"✅ {r['torneio']} — {r['assunto']}")

                if pendentes:
                    st.warning(
                        f"{len(pendentes)} e-mail(s) com pendência — permanecem na caixa de "
                        "entrada para correção e nova tentativa:"
                    )
                    for r in pendentes:
                        st.write(f"⚠️ {r['torneio']} — {r['assunto']}")
                        st.caption(r["motivo"])

            except Exception as e:
                st.error("Erro geral ao processar os formulários.")
                st.code(repr(e))
