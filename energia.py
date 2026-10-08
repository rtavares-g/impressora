#!/usr/bin/env python3
"""Liga a tomada Tuya da impressora quando chega trabalho no CUPS (ou o painel
de digitalização pede) e desliga depois de um tempo sem uso.

Uso:
  energia.py               serviço (roda em loop)
  energia.py configurar    escolhe a tomada (depois do `tinytuya wizard`)
  energia.py estado|ligar|desligar
"""
import json
import subprocess
import sys
import time
from pathlib import Path

import tinytuya

FILA = "Samsung_SCX4200"
USB_ID = ("04e8", "341b")  # Samsung SCX-4200
PASTA = Path.home() / ".config" / "impressora-energia"
CONFIG = PASTA / "config.json"
EM_USO = PASTA / "em-uso"  # o painel escaner/ toca enquanto usa o scanner
INTERVALO = 3          # segundos entre verificações da fila
RETENTAR_LIGAR = 30    # segundos até tentar ligar de novo se a USB não aparecer
USO_RECENTE = 15       # segundos em que um toque em EM_USO conta como trabalho


def log(msg):
    print(msg, flush=True)


def ler_config():
    if not CONFIG.exists():
        return None
    return json.loads(CONFIG.read_text())


def tomada(cfg):
    d = tinytuya.OutletDevice(cfg["id"], cfg["ip"], cfg["key"], version=float(cfg["version"]))
    d.set_socketTimeout(5)
    d.set_socketRetryLimit(2)
    return d


def comandar(cfg, ligar):
    """Liga/desliga; se o IP mudou (DHCP), procura de novo e salva."""
    for tentativa in range(2):
        r = tomada(cfg).set_status(ligar, cfg.get("dps", 1))
        if r and "Error" not in r:
            return True
        log(f"Falha ao {'ligar' if ligar else 'desligar'} a tomada: {r}")
        if tentativa == 0 and not redescobrir(cfg):
            break
    return False


def redescobrir(cfg):
    achado = tinytuya.find_device(cfg["id"])
    if not achado or not achado.get("ip"):
        return False
    if achado["ip"] != cfg["ip"]:
        log(f"Tomada mudou de IP: {cfg['ip']} -> {achado['ip']}")
        cfg["ip"] = achado["ip"]
        CONFIG.write_text(json.dumps(cfg, indent=2) + "\n")
    return True


def impressora_na_usb():
    for dev in Path("/sys/bus/usb/devices").glob("*/idVendor"):
        try:
            if (dev.read_text().strip(), (dev.parent / "idProduct").read_text().strip()) == USB_ID:
                return True
        except OSError:
            pass
    return False


def reabilitar_fila():
    """O udev-configure-printer desabilita a fila quando a impressora sai da
    USB e não consegue reabilitar quando ela volta. Só reabilita se foi esse
    o motivo: falta de papel e outros erros continuam parados."""
    r = subprocess.run(["lpstat", "-p", FILA], capture_output=True, text=True,
                       env={"LC_ALL": "C", "LANG": "C", "PATH": "/usr/bin:/bin"})
    if "disabled" in r.stdout and "Unplugged or turned off" in r.stdout:
        subprocess.run(["cupsenable", FILA])
        log("Fila reabilitada.")


def tem_trabalhos():
    r = subprocess.run(["lpstat", "-o", FILA], capture_output=True, text=True)
    return bool(r.stdout.strip())


def scanner_em_uso():
    try:
        return time.time() - EM_USO.stat().st_mtime < USO_RECENTE
    except OSError:
        return False


def servico():
    cfg = None
    while cfg is None:
        cfg = ler_config()
        if cfg is None:
            log(f"Sem {CONFIG}: rode `energia.py configurar`. Tentando de novo em 60s.")
            time.sleep(60)

    desligar_apos = cfg.get("desligar_apos_min", 10) * 60
    ultima_atividade = time.monotonic()
    ultima_tentativa_ligar = 0.0
    estava_na_usb = impressora_na_usb()
    log(f"Monitorando a fila {FILA}; desliga após {desligar_apos // 60} min sem trabalhos.")

    while True:
        agora = time.monotonic()
        na_usb = impressora_na_usb()
        if na_usb != estava_na_usb:
            log("Impressora apareceu na USB." if na_usb else "Impressora saiu da USB.")
            estava_na_usb = na_usb
            if na_usb:
                ultima_atividade = agora
                time.sleep(3)  # deixa o udev terminar antes de mexer na fila
                reabilitar_fila()

        if tem_trabalhos() or scanner_em_uso():
            ultima_atividade = agora
            if not na_usb and agora - ultima_tentativa_ligar > RETENTAR_LIGAR:
                log("Trabalho na fila ou scanner em uso e impressora desligada: ligando a tomada.")
                comandar(cfg, True)
                ultima_tentativa_ligar = agora
        elif na_usb and agora - ultima_atividade > desligar_apos:
            log(f"{desligar_apos // 60} min sem trabalhos: desligando a tomada.")
            if comandar(cfg, False):
                ultima_atividade = agora
            else:
                ultima_atividade = agora - desligar_apos + 60  # tenta de novo em 1 min

        time.sleep(INTERVALO)


def detectar_versao(cfg, preferida=None):
    """Testa as versões do protocolo até a tomada responder. A busca por
    broadcast (find_device) não atravessa VLANs, então não dá para confiar
    na versão que ela informa: a tomada fica na rede IoT."""
    for v in dict.fromkeys(x for x in (preferida, 3.5, 3.4, 3.3) if x):
        d = tomada({**cfg, "version": v})
        d.set_socketRetryLimit(1)
        r = d.status()
        if r and "dps" in r:
            return float(v)
        print(f"  versão {v}: {r.get('Error') if r else 'sem resposta'}")
    return None


def configurar():
    devices = PASTA / "devices.json"
    if not devices.exists():
        sys.exit(f"Rode antes, dentro de {PASTA}:\n  cd {PASTA} && {sys.executable} -m tinytuya wizard")
    lista = [d for d in json.loads(devices.read_text()) if d.get("key")]
    for i, d in enumerate(lista, 1):
        print(f"{i}) {d.get('name')}  ({d.get('product_name', '')})")
    d = lista[int(input("Número da tomada da impressora: ")) - 1]

    print("Procurando a tomada na rede...")
    achado = tinytuya.find_device(d["id"]) or {}
    ip = achado.get("ip") or input("Não achei na rede. IP da tomada: ").strip()
    cfg = {"nome": d.get("name"), "id": d["id"], "key": d["key"], "ip": ip,
           "dps": 1, "desligar_apos_min": 10}
    print(f"Testando o protocolo em {ip}...")
    version = detectar_versao(cfg, achado.get("version") or d.get("version"))
    if version is None:
        sys.exit("A tomada não respondeu em nenhuma versão: confira o IP e a regra de "
                 "firewall do UniFi (Pi -> tomada, TCP 6668). Nada foi salvo.")
    cfg["version"] = version
    if old := ler_config():
        cfg["desligar_apos_min"] = old.get("desligar_apos_min", 10)
    CONFIG.write_text(json.dumps(cfg, indent=2) + "\n")
    CONFIG.chmod(0o600)
    print(f"Salvo em {CONFIG}. Estado atual: {tomada(cfg).status()}")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "servico"
    PASTA.mkdir(parents=True, exist_ok=True)
    if cmd == "servico":
        servico()
    elif cmd == "configurar":
        configurar()
    elif cmd in ("estado", "ligar", "desligar"):
        cfg = ler_config() or sys.exit(f"Sem {CONFIG}: rode `energia.py configurar`.")
        if cmd != "estado":
            comandar(cfg, cmd == "ligar")
        print("tomada:", tomada(cfg).status())
        print("impressora na USB:", impressora_na_usb())
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
