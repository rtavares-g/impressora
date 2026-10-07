#!/usr/bin/env python3
"""Painel web para digitalizar na Samsung SCX-4200, baixar o PDF ou mandar
por e-mail para outra pessoa.

Cada aba do navegador tem um documento (as páginas digitalizadas até agora),
guardado em ~/.cache/escanear/<sessão>/. O scanner é um só: enquanto uma
digitalização roda, as outras esperam (409).

Se a impressora estiver desligada, o painel marca ~/.config/impressora-energia/em-uso
e o serviço impressora-energia liga a tomada, como faz com trabalhos na fila.

O e-mail usa a conta em ~/.config/escanear/smtp.json (fora do git), ver README.
"""
import json
import re
import shutil
import smtplib
import subprocess
import sys
import threading
import time
import unicodedata
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from PIL import Image

PORTA = 8082
AQUI = Path(__file__).resolve().parent
CACHE = Path.home() / ".cache" / "escanear"
SMTP_CONFIG = Path.home() / ".config" / "escanear" / "smtp.json"
EM_USO = Path.home() / ".config" / "impressora-energia" / "em-uso"
USB_ID = ("04e8", "341b")  # Samsung SCX-4200
ESPERA_LIGAR = 120         # segundos esperando a impressora aparecer na USB
VALIDADE_SESSAO = 6 * 3600
MAX_PAGINAS = 40
MAX_DESTINATARIOS = 5
RESOLUCOES = (75, 100, 150, 200, 300)  # 600 dá "Error during device I/O" e trava o scanner
MODOS = {"cinza": "Gray", "cor": "Color", "pb": "Lineart"}

SESSAO_RE = re.compile(r"^[a-f0-9]{16,64}$")
EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[a-z]{2,}$", re.I)
ARQUIVO_RE = re.compile(r"^\d{3}\.jpg$")

scanner = threading.Lock()
estado_lock = threading.Lock()
# Uma digitalização por vez: {"sessao", "fase", "msg"}; fase em
# "ligando" | "digitalizando" | "pronto" | "erro".
trabalho = {"sessao": None, "fase": "parado", "msg": ""}


def log(msg):
    print(msg, flush=True)


def marcar_em_uso():
    """Avisa o impressora-energia que o scanner está em uso (liga a tomada e
    adia o desligamento)."""
    try:
        EM_USO.parent.mkdir(parents=True, exist_ok=True)
        EM_USO.touch()
    except OSError as e:
        log(f"Não consegui marcar {EM_USO}: {e}")


def impressora_na_usb():
    for dev in Path("/sys/bus/usb/devices").glob("*/idVendor"):
        try:
            if (dev.read_text().strip(), (dev.parent / "idProduct").read_text().strip()) == USB_ID:
                return True
        except OSError:
            pass
    return False


def achar_scanner():
    """O nome muda (xerox_mfp:libusb:<barramento>:<dispositivo>) toda vez que a
    impressora liga, então procura de novo a cada digitalização."""
    r = subprocess.run(["scanimage", "-f", "%d\n"], capture_output=True, text=True, timeout=60)
    for linha in r.stdout.splitlines():
        if linha.startswith("xerox_mfp:"):
            return linha.strip()
    return None


def definir(sessao, fase, msg=""):
    with estado_lock:
        trabalho.update(sessao=sessao, fase=fase, msg=msg)


def pasta_sessao(sessao):
    return CACHE / sessao


def paginas(sessao):
    p = pasta_sessao(sessao)
    return sorted(f.name for f in p.glob("*.jpg")) if p.is_dir() else []


def limpar_antigas():
    if not CACHE.is_dir():
        return
    agora = time.time()
    for p in CACHE.iterdir():
        try:
            if p.is_dir() and agora - p.stat().st_mtime > VALIDADE_SESSAO:
                shutil.rmtree(p)
        except OSError:
            pass


def digitalizar(sessao, modo, resolucao):
    try:
        marcar_em_uso()
        if not impressora_na_usb():
            definir(sessao, "ligando", "Ligando a impressora…")
            limite = time.monotonic() + ESPERA_LIGAR
            while not impressora_na_usb():
                if time.monotonic() > limite:
                    raise RuntimeError("A impressora não ligou. Confira a tomada e o cabo USB.")
                marcar_em_uso()
                time.sleep(2)
            time.sleep(5)  # deixa o udev e o scanner terminarem de subir

        definir(sessao, "digitalizando", "Digitalizando…")
        erro = ""
        for tentativa in range(4):  # logo depois de ligar o scanner ainda aquece
            marcar_em_uso()
            dispositivo = achar_scanner()
            if dispositivo:
                r = subprocess.run(
                    ["scanimage", "-d", dispositivo, "--mode", MODOS[modo],
                     "--resolution", str(resolucao), "--format=png"],
                    capture_output=True, timeout=300)
                if r.returncode == 0 and r.stdout:
                    break
                erro = r.stderr.decode(errors="replace").strip()
            else:
                erro = "scanner não encontrado"
            log(f"Tentativa {tentativa + 1} falhou: {erro}")
            time.sleep(5)
        else:
            raise RuntimeError(f"Falha ao digitalizar: {erro}")

        img = Image.open(BytesIO(r.stdout))
        img = img.convert("RGB" if modo == "cor" else "L")
        pasta = pasta_sessao(sessao)
        pasta.mkdir(parents=True, exist_ok=True)
        existentes = paginas(sessao)
        n = int(existentes[-1][:3]) + 1 if existentes else 1
        img.save(pasta / f"{n:03d}.jpg", "JPEG", quality=80, optimize=True, dpi=(resolucao, resolucao))
        marcar_em_uso()
        definir(sessao, "pronto", f"Página {len(existentes) + 1} digitalizada.")
        log(f"Sessão {sessao[:8]}: página {n} ({modo}, {resolucao} dpi)")
    except Exception as e:  # noqa: BLE001 - o erro vai para o painel
        log(f"Erro na digitalização: {e}")
        definir(sessao, "erro", str(e))
    finally:
        scanner.release()


def montar_pdf(sessao):
    arquivos = paginas(sessao)
    if not arquivos:
        return None
    imagens = [Image.open(pasta_sessao(sessao) / f) for f in arquivos]
    dpi = imagens[0].info.get("dpi", (150, 150))[0]
    saida = BytesIO()
    imagens[0].save(saida, "PDF", resolution=float(dpi), save_all=True, append_images=imagens[1:])
    return saida.getvalue()


def nome_arquivo(nome):
    nome = re.sub(r"[^\w\- ]+", "", nome or "", flags=re.UNICODE).strip()
    return (nome or time.strftime("Digitalizacao %Y-%m-%d %H%M")) + ".pdf"


def enviar_email(destinatarios, assunto, nome, pdf):
    if not SMTP_CONFIG.exists():
        raise RuntimeError("o envio por e-mail ainda não foi configurado no Raspberry Pi.")
    cfg = json.loads(SMTP_CONFIG.read_text())
    msg = EmailMessage()
    msg["From"] = cfg.get("remetente", cfg["usuario"])
    msg["To"] = ", ".join(destinatarios)
    msg["Subject"] = assunto
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()
    msg.set_content("Segue em anexo o documento digitalizado.\n")
    msg.add_attachment(pdf, maintype="application", subtype="pdf", filename=nome)
    porta = int(cfg.get("porta", 587))
    if porta == 465:
        conexao = smtplib.SMTP_SSL(cfg["host"], porta, timeout=60)
    else:
        conexao = smtplib.SMTP(cfg["host"], porta, timeout=60)
        conexao.starttls()
    with conexao:
        conexao.login(cfg["usuario"], cfg["senha"])
        conexao.send_message(msg)


def nome_ascii(nome):
    """Nome sem acentos para o filename= simples (alguns navegadores ignoram o filename*)."""
    return unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode().replace('"', "")


class Painel(BaseHTTPRequestHandler):
    server_version = "escanear"

    def log_message(self, fmt, *args):
        pass

    def responder(self, status, corpo=b"", tipo="application/json", extra=None):
        if isinstance(corpo, (dict, list)):
            corpo = json.dumps(corpo).encode()
        self.send_response(status)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(corpo)

    def erro(self, status, msg):
        self.responder(status, {"erro": msg})

    def ler_json(self):
        tamanho = int(self.headers.get("Content-Length") or 0)
        if tamanho > 10_000:
            return None
        try:
            return json.loads(self.rfile.read(tamanho) or b"{}")
        except ValueError:
            return None

    def sessao_valida(self, sessao):
        if not isinstance(sessao, str) or not SESSAO_RE.match(sessao):
            self.erro(HTTPStatus.BAD_REQUEST, "Sessão inválida.")
            return False
        return True

    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        partes = url.path.strip("/").split("/")

        if url.path == "/":
            self.responder(HTTPStatus.OK, (AQUI / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif url.path == "/api/estado":
            sessao = q.get("sessao")
            if not self.sessao_valida(sessao):
                return
            with estado_lock:
                t = dict(trabalho)
            meu = t["sessao"] == sessao
            self.responder(HTTPStatus.OK, {
                "fase": t["fase"] if meu else ("ocupado" if scanner.locked() else "parado"),
                "msg": t["msg"] if meu else "",
                "paginas": paginas(sessao),
                "ligada": impressora_na_usb(),
                "email_configurado": SMTP_CONFIG.exists(),
            })
        elif len(partes) == 4 and partes[:2] == ["api", "pagina"]:
            sessao, arquivo = partes[2], partes[3]
            if not self.sessao_valida(sessao):
                return
            f = pasta_sessao(sessao) / arquivo
            if not ARQUIVO_RE.match(arquivo) or not f.is_file():
                return self.erro(HTTPStatus.NOT_FOUND, "Página não encontrada.")
            self.responder(HTTPStatus.OK, f.read_bytes(), "image/jpeg")
        elif url.path == "/api/pdf":
            sessao = q.get("sessao")
            if not self.sessao_valida(sessao):
                return
            pdf = montar_pdf(sessao)
            if not pdf:
                return self.erro(HTTPStatus.NOT_FOUND, "Nenhuma página digitalizada.")
            nome = nome_arquivo(q.get("nome"))
            self.responder(HTTPStatus.OK, pdf, "application/pdf",
                           {"Content-Disposition": f"attachment; filename=\"{nome_ascii(nome)}\"; "
                                                   f"filename*=UTF-8''{quote(nome)}"})
        else:
            self.erro(HTTPStatus.NOT_FOUND, "Não encontrado.")

    def do_POST(self):
        dados = self.ler_json()
        if dados is None:
            return self.erro(HTTPStatus.BAD_REQUEST, "Pedido inválido.")
        sessao = dados.get("sessao")
        if not self.sessao_valida(sessao):
            return
        caminho = urlparse(self.path).path

        if caminho == "/api/digitalizar":
            modo = dados.get("modo", "cinza")
            resolucao = int(dados.get("resolucao", 300))
            if modo not in MODOS or resolucao not in RESOLUCOES:
                return self.erro(HTTPStatus.BAD_REQUEST, "Modo ou resolução inválidos.")
            if len(paginas(sessao)) >= MAX_PAGINAS:
                return self.erro(HTTPStatus.BAD_REQUEST, f"Limite de {MAX_PAGINAS} páginas por documento.")
            if not scanner.acquire(blocking=False):
                return self.erro(HTTPStatus.CONFLICT, "O scanner está em uso. Tente de novo em instantes.")
            limpar_antigas()
            definir(sessao, "digitalizando", "Preparando…")
            threading.Thread(target=digitalizar, args=(sessao, modo, resolucao), daemon=True).start()
            self.responder(HTTPStatus.ACCEPTED, {"ok": True})

        elif caminho == "/api/apagar":
            arquivo = dados.get("pagina", "")
            f = pasta_sessao(sessao) / arquivo
            if ARQUIVO_RE.match(arquivo) and f.is_file():
                f.unlink()
            self.responder(HTTPStatus.OK, {"paginas": paginas(sessao)})

        elif caminho == "/api/limpar":
            shutil.rmtree(pasta_sessao(sessao), ignore_errors=True)
            with estado_lock:
                if trabalho["sessao"] == sessao and trabalho["fase"] in ("pronto", "erro"):
                    trabalho.update(sessao=None, fase="parado", msg="")
            self.responder(HTTPStatus.OK, {"paginas": []})

        elif caminho == "/api/enviar":
            texto = str(dados.get("para", ""))
            destinatarios = [e.strip() for e in re.split(r"[,;\s]+", texto) if e.strip()]
            if not destinatarios:
                return self.erro(HTTPStatus.BAD_REQUEST, "Digite um endereço de e-mail.")
            invalidos = [e for e in destinatarios if not EMAIL_RE.match(e)]
            if invalidos:
                return self.erro(HTTPStatus.BAD_REQUEST, f"E-mail inválido: {', '.join(invalidos)}")
            if len(destinatarios) > MAX_DESTINATARIOS:
                return self.erro(HTTPStatus.BAD_REQUEST, f"No máximo {MAX_DESTINATARIOS} destinatários.")
            pdf = montar_pdf(sessao)
            if not pdf:
                return self.erro(HTTPStatus.BAD_REQUEST, "Digitalize pelo menos uma página.")
            nome = nome_arquivo(dados.get("nome"))
            usuario = self.headers.get("Cf-Access-Authenticated-User-Email", "?")
            try:
                enviar_email(destinatarios, nome[:-4], nome, pdf)
            except Exception as e:  # noqa: BLE001
                log(f"Falha ao enviar para {destinatarios}: {e}")
                return self.erro(HTTPStatus.BAD_GATEWAY, f"Não foi possível enviar: {e}")
            log(f"{usuario} enviou {nome} ({len(paginas(sessao))} pág., {len(pdf) // 1024} KB) para {destinatarios}")
            self.responder(HTTPStatus.OK, {"ok": True, "kb": len(pdf) // 1024})

        else:
            self.erro(HTTPStatus.NOT_FOUND, "Não encontrado.")


def main():
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    CACHE.mkdir(parents=True, exist_ok=True)
    limpar_antigas()
    log(f"Painel em http://{host}:{PORTA}")
    ThreadingHTTPServer((host, PORTA), Painel).serve_forever()


if __name__ == "__main__":
    main()
